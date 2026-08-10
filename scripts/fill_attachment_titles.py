#!/usr/bin/env python3
"""별표 레코드의 빈 조문제목을 원문에서 추출해 채운다 (T28).

별표 2는 제목이 있고 별표 3·5는 비어 있었다 — 수집 파서가 `#` 헤딩만 제목으로
인정했기 때문이다. 제목이 없으면 소비자가 무엇에 관한 별표인지 표시할 수 없고,
T27의 '본문 절단' 이후에는 절단된 별표를 식별할 방법 자체가 사라진다.

**만들지 않는다. 옮긴다.** 제목은 아래 셋 중 문서에 실제로 있는 문자열에서만 가져온다.
1. 문서 목차 줄  `## [별표 3] 연구비 비목별 계상 및 집행기준 ---- 27`
2. 본문 첫 실질 줄  `연구비 비목별 계상 및 집행기준(제22조 ③항의 관련)`
3. 표 첫 머리셀    `<th colspan="19">물품 기부채납 신청서</th>`
어디에도 없으면 비운 채로 두고 보고한다(추정 금지).

제목 뒤 괄호의 위임 조문(`(제22조 ③항의 관련)`)은 위임조문 필드로 함께 남긴다.
본문·record_id는 건드리지 않는다(저장된 record_id가 우선하므로 식별자는 불변).
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CORPUS = ROOT / "data" / "rules_corpus.json"
MARKDOWN = ROOT / "data" / "markdown"

# 목차 줄: '## [별표 3] 연구비 비목별 계상 및 집행기준 ----------- 27'
TOC_RE = re.compile(
    r"^#*\s*[\[【]\s*(?P<label>별표\s*[^\]】]*?)\s*[\]】]\s*(?P<title>[^\n]*?)\s*[-·.\s]*\d*\s*$",
    re.M,
)
# 표 머리셀: <th ...>물품 기부채납 신청서</th>
TH_RE = re.compile(r"<th[^>]*>(?P<text>[^<]{2,80})</th>")
# 제목 뒤 위임 표기: '(제22조 ③항의 관련)', '(제5조제2항 관련)'
DELEGATION_RE = re.compile(
    r"\(\s*제\s*(?P<article>\d+)\s*조(?:\s*의\s*(?P<sub>\d+))?"
    r"\s*(?:제\s*(?P<clause_num>\d+)\s*항|(?P<clause_mark>[①-⑮])\s*항)?[^)]*관련[^)]*\)"
)
_MARKUP_LINE = re.compile(r"^\s*(?:<|\||!\[|\[|-{3,}|={3,})")


def normalize_label(label: str) -> str:
    return " ".join(str(label).split())


def toc_titles(text: str) -> dict[str, str]:
    """문서 안 목차 줄에서 '별표 N → 제목'을 모은다."""
    found: dict[str, str] = {}
    for match in TOC_RE.finditer(text):
        title = " ".join(match.group("title").split()).strip(" -·.")
        if not title:
            continue
        found.setdefault(normalize_label(match.group("label")), title)
    return found


def title_from_body(body: str) -> str:
    """본문 첫 실질 줄(표·이미지 마크업이 아닌 줄)을 제목 후보로 본다."""
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or _MARKUP_LINE.match(stripped):
            continue
        if len(stripped) > 100:
            return ""
        return stripped
    return ""


def title_from_table(body: str) -> str:
    match = TH_RE.search(body)
    if not match:
        return ""
    text = " ".join(match.group("text").split())
    return text if 2 <= len(text) <= 80 else ""


def strip_delegation(title: str) -> tuple[str, dict | None]:
    """제목에 붙은 '(제22조 ③항의 관련)'을 떼어내고 위임 정보를 돌려준다."""
    match = DELEGATION_RE.search(title)
    if not match:
        return title.strip(), None
    article = f"제{int(match.group('article'))}조"
    if match.group("sub"):
        article += f"의{int(match.group('sub'))}"
    clause = None
    if match.group("clause_num"):
        clause = f"제{int(match.group('clause_num'))}항"
    elif match.group("clause_mark"):
        clause = match.group("clause_mark")
    cleaned = (title[: match.start()] + title[match.end():]).strip()
    return cleaned, {"조문번호": article, "항": clause}


def main() -> int:
    apply_changes = "--apply" in sys.argv
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    toc_cache: dict[str, dict[str, str]] = {}

    filled: list[dict] = []
    delegated: list[dict] = []
    unresolved: list[dict] = []
    for record in corpus:
        if record.get("record_type") != "별표":
            continue
        label = normalize_label(record.get("조문번호", ""))
        title = str(record.get("조문제목", "")).strip()
        source = None
        if not title:
            key = str(record.get("source_key"))
            if key not in toc_cache:
                path = MARKDOWN / f"key_{key}.md"
                toc_cache[key] = (
                    toc_titles(path.read_text(encoding="utf-8", errors="replace"))
                    if path.exists() else {}
                )
            title = toc_cache[key].get(label, "")
            source = "목차" if title else None
            if not title:
                title = title_from_body(record.get("본문", ""))
                source = "본문_첫줄" if title else None
            if not title:
                title = title_from_table(record.get("본문", ""))
                source = "표_머리셀" if title else None

        cleaned, delegation = strip_delegation(title)
        if delegation is None:
            # 목차에서 제목을 가져오면 위임 표기가 빠진다 — 위임은 본문 머리에 있다.
            _, delegation = strip_delegation(title_from_body(record.get("본문", "")))
        if not cleaned:
            unresolved.append({"규정명": record["규정명"], "조문번호": label})
            continue
        if cleaned != str(record.get("조문제목", "")).strip():
            filled.append({
                "규정명": record["규정명"], "조문번호": label,
                "제목": cleaned, "출처": source or "기존",
            })
            record["조문제목"] = cleaned
            record["제목_출처"] = source or "수집시"
        if delegation:
            record["위임조문"] = delegation
            delegated.append({
                "규정명": record["규정명"], "조문번호": label, "위임조문": delegation,
            })

    report = {
        "제목_채움": len(filled),
        "채운_항목": filled,
        "위임조문_기록": delegated,
        "제목_미확인": unresolved,
        "적용": apply_changes,
    }
    if apply_changes and (filled or delegated):
        backup = CORPUS.with_suffix(".json.pre_titles")
        if not backup.exists():
            shutil.copy2(CORPUS, backup)
        CORPUS.write_text(json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

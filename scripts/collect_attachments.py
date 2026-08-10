#!/usr/bin/env python3
"""지침 계층의 별표를 코퍼스에 편입한다 (T22).

배경: 조문 파서가 '제N조' 헤더만 잡아 `[별표]` 블록을 지나쳤다. 그래서 학생 징계
기준·겸직 허가절차처럼 **규정의 핵심 판단 기준이 별표에만 있는 경우** 소비자가
"규정에 없음"으로 답하게 됐다. 원문(HWP→kordoc 마크다운)에는 텍스트가 있으므로
수집이 가능하다 — 없는 내용을 만드는 것이 아니라 이미 있는 것을 옮기는 일이다.

규정 계층(law.go.kr)은 별표를 이미지로 제공해 여기서 다루지 않는다(reason_code=image_only).

각 레코드에 `수집방법: "auto"`를 넣어 수동 입력분과 구별한다.
멱등이다 — 이미 편입된 별표는 다시 넣지 않는다.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.search import prepare_article  # noqa: E402

CORPUS = ROOT / "data" / "rules_corpus.json"
MARKDOWN = ROOT / "data" / "markdown"
# '[별표]', '【별표 1】', '## [별표1] 주차요금표', '<별표 1 > <개정 …>' 등 세 가지 괄호 계열.
# v1.5의 엄격 패턴은 '괄호만 있는 줄'만 인정해 **제목이 같은 줄에 붙은 52건을 통째로
# 놓쳤다**(이해충돌지침 징계양정기준·주차요금표 등 판단 기준이 별표에만 있는 것들).
HEADER_RE = re.compile(
    r"^#{0,4}\s*[\[【<]\s*(?P<label>별표\s*[^\]】>]*?)\s*[\]】>]\s*(?P<inline>[^\n]*)$", re.M
)
TITLE_RE = re.compile(r"^#+\s*(.+)$", re.M)
# 목차 줄: 제목 뒤에 점선·쪽수가 붙는다. 본문 헤더가 아니므로 블록으로 잡으면 안 된다.
TOC_TAIL_RE = re.compile(r"(?:[-.]{3,}\s*\d+|\s\d+)\s*$")
# '[별표 1]과 같다' 같은 문장 안 인용 — 헤더가 아니다.
SENTENCE_TAIL_RE = re.compile(r"(?:같다|따른다|의하다|참조)[.。]?\s*$")


def _normalize_label(label: str) -> str:
    """'별표1', '별표 1.' → '별표 1' (표기 흔들림을 조문번호에서 흡수한다)."""
    text = " ".join(str(label).split()).rstrip(".")
    match = re.fullmatch(r"별표\s*(.*)", text)
    if not match:
        return text
    rest = match.group(1).strip()
    return f"별표 {rest}" if rest else "별표"


def extract(text: str) -> list[tuple[str, str, str]]:
    """(별표 라벨, 제목, 본문) 목록. 다음 별표 헤더 전까지를 한 덩이로 본다."""
    headers = [
        match
        for match in HEADER_RE.finditer(text)
        if not TOC_TAIL_RE.search(match.group("inline").strip())
        and not SENTENCE_TAIL_RE.search(match.group("inline").strip())
    ]
    blocks: list[tuple[str, str, str]] = []
    for index, header in enumerate(headers):
        start = header.end()
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        body = text[start:end].strip()
        inline = header.group("inline").strip().lstrip("#").strip()
        # 제목 뒤 개정 표기 '<개정 2025. 2. 17.>'는 마크업이므로 제목에서 뺀다
        # (본문은 건드리지 않는다 — 표시용 제목만 다듬는다).
        inline = re.sub(r"\s*<[^>]*>\s*$", "", inline).strip()
        title = inline if inline and not inline.startswith("<") else ""
        if not title:
            title_match = TITLE_RE.search(body)
            if title_match and title_match.start() < 80:
                title = title_match.group(1).strip()
                body = (body[: title_match.start()] + body[title_match.end():]).strip()
        if not body:
            continue
        blocks.append((_normalize_label(header.group("label")), title, body))
    # 같은 라벨이 두 번 잡히면(목차 잔재 등) 본문이 긴 쪽을 정본으로 본다.
    best: dict[str, tuple[str, str, str]] = {}
    for label, title, body in blocks:
        if label not in best or len(body) > len(best[label][2]):
            best[label] = (label, title, body)
    order = list(dict.fromkeys(label for label, _, _ in blocks))
    return [best[label] for label in order]


def main() -> int:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    by_key: dict[str, dict] = {}
    for article in corpus:
        by_key.setdefault(str(article.get("source_key")), article)
    existing = {
        (str(a.get("source_key")), str(a.get("조문번호")))
        for a in corpus if a.get("record_type") == "별표"
    }

    added: list[dict] = []
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    for path in sorted(MARKDOWN.glob("key_*.md")):
        key = path.stem.replace("key_", "")
        parent = by_key.get(key)
        if parent is None:
            continue  # 코퍼스에 없는 규정의 마크다운은 건드리지 않는다
        blocks = extract(path.read_text(encoding="utf-8", errors="replace"))
        for order, (label, title, body) in enumerate(blocks, start=1):
            article_no = label if label != "별표" or len(blocks) == 1 else f"{label} {order}"
            if (key, article_no) in existing:
                continue
            record = {
                "규정명": parent["규정명"],
                "편제": parent.get("편제"),
                "조문번호": article_no,
                "조문제목": title,
                "본문": body,
                "source_key": key,
                "source_url": parent.get("source_url"),
                "수집일시": now,
                "record_type": "별표",
                "수집방법": "auto",
                "원문_대조_확인": True,
                "장": None,
                "절": None,
            }
            prepared = prepare_article(record)
            record["record_id"] = prepared["record_id"]
            record["revision"] = prepared["revision"]
            added.append(record)

    if not added:
        print(json.dumps({"추가": 0, "메모": "새로 편입할 별표가 없습니다(멱등)."}, ensure_ascii=False))
        return 0
    if "--dry-run" in sys.argv:
        print(json.dumps({
            "추가_예정": len(added),
            "목록": [f"{a['규정명']} / {a['조문번호']} / {a['조문제목']} ({len(a['본문'])}자)" for a in added],
        }, ensure_ascii=False, indent=1))
        return 0

    backup = CORPUS.with_suffix(".json.pre_attachments")
    if not backup.exists():
        shutil.copy2(CORPUS, backup)
    CORPUS.write_text(json.dumps(corpus + added, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "추가": len(added),
        "규정별": sorted({f"{a['규정명']} / {a['조문번호']}" for a in added}),
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

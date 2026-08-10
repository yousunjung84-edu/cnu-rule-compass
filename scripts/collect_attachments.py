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
# '[별표]', '【별표 1】', '[별표 2의2]' 등
HEADER_RE = re.compile(r"^\s*[\[【]\s*(별표[^\]】]*)\s*[\]】]\s*$", re.M)
TITLE_RE = re.compile(r"^#+\s*(.+)$", re.M)


def extract(text: str) -> list[tuple[str, str, str]]:
    """(별표 라벨, 제목, 본문) 목록. 다음 별표 헤더 전까지를 한 덩이로 본다."""
    headers = list(HEADER_RE.finditer(text))
    blocks: list[tuple[str, str, str]] = []
    for index, header in enumerate(headers):
        start = header.end()
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        body = text[start:end].strip()
        if not body:
            continue
        title_match = TITLE_RE.search(body)
        title = title_match.group(1).strip() if title_match else ""
        if title_match:
            body = (body[: title_match.start()] + body[title_match.end():]).strip()
        label = " ".join(header.group(1).split())
        blocks.append((label, title, body))
    return blocks


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

#!/usr/bin/env python3
"""부칙을 본칙과 분리하고 빈 조문제목을 복원한다 (T21).

증상: 연구소 평가 지침에 '제1조'가 두 건 — 하나는 본칙 목적, 하나는 시행일 조문인데
둘 다 record_type이 '본칙'이고 후자는 조문제목이 비어 있었다. 소비자가 '제1조'를
인용할 때 어느 쪽인지 판별할 수 없고, 시행일을 본칙 조문으로 오인해 인용할 수 있다.

두 가지를 고친다.
1. **부칙 재분류** — 제목 기반 휴리스틱이 놓친 '제목 없는 시행일 조문'을 부칙으로.
2. **제목 복원** — 본문 첫 줄이 제목인 레코드(예: '목적\\n이 지침은 …')에서 첫 줄을
   조문제목으로 올린다. 원문에 있는 문자열을 옮기는 것이지 생성이 아니다.

`record_id`는 이미 코퍼스에 동결돼 있어 바뀌지 않는다(발급된 인용 보존).
멱등이다.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

CORPUS = Path(__file__).resolve().parent.parent / "data" / "rules_corpus.json"
# 부칙 본문의 전형 — '이 지침은 …부터 시행한다', '이 규정은 …' 로 시작한다.
SUPPLEMENTARY_BODY = re.compile(r"^\s*(?:부\s*칙|이\s*(?:지침|규정|학칙|규칙|세칙|정관)은)")
SUPPLEMENTARY_TITLE = {"시행일", "경과조치", "종전지침 폐지", "재검토기한", "적용례", "폐지"}
# '다른 지침의 폐지' 같은 제목 + '이 지침 시행과 동시에 …' 본문은 부칙이다.
# 제목에 '폐지'가 있다고 다 부칙은 아니므로(연구소 폐지 등 본칙 조문 존재)
# 본문 시작 문구를 함께 요구한다.
SUPPLEMENTARY_PAIR_TITLE = re.compile(r"(다른|종전).*(폐지)|폐지$")
SUPPLEMENTARY_PAIR_BODY = re.compile(r"^\s*이\s*(?:지침|규정|학칙|규칙|세칙)\s*(?:의\s*)?시행")
# 첫 줄이 제목인 형태: 짧고 마침표로 끝나지 않으며 다음 줄에 본문이 이어진다.
TITLE_LINE = re.compile(r"^([가-힣A-Za-z0-9()·ㆍ\s]{2,30})\n")


def main() -> int:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    retyped = restored = 0

    for article in corpus:
        title = str(article.get("조문제목", "")).strip()
        body = str(article.get("본문", ""))

        # 1) 제목이 비었는데 본문 첫 줄이 제목처럼 생겼으면 끌어올린다.
        if not title:
            match = TITLE_LINE.match(body)
            if match and not match.group(1).rstrip().endswith("."):
                candidate = match.group(1).strip()
                remainder = body[match.end():].strip()
                if candidate and remainder:
                    article["조문제목"] = candidate
                    article["본문"] = remainder
                    title = candidate
                    restored += 1

        # 2) 부칙 재분류 — 제목 휴리스틱 + 제목 없는 시행일 조문
        is_supplementary = (
            title in SUPPLEMENTARY_TITLE
            or (not title and bool(SUPPLEMENTARY_BODY.match(body)))
            or (
                bool(SUPPLEMENTARY_PAIR_TITLE.search(title))
                and bool(SUPPLEMENTARY_PAIR_BODY.match(body))
            )
        )
        if is_supplementary and article.get("record_type") != "부칙":
            article["record_type"] = "부칙"
            retyped += 1

    empty_main = [
        a for a in corpus
        if not str(a.get("조문제목", "")).strip() and a.get("record_type") == "본칙"
    ]
    backup = CORPUS.with_suffix(".json.pre_recordtype")
    if not backup.exists():
        shutil.copy2(CORPUS, backup)
    CORPUS.write_text(json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "부칙_재분류": retyped,
        "제목_복원": restored,
        "잔여_빈제목_본칙": len(empty_main),
        "잔여_예시": [(a["규정명"], a["조문번호"], a["본문"][:40]) for a in empty_main[:5]],
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

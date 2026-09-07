#!/usr/bin/env python3
"""조문 구조가 없는 '항목식' 지침을 항목 단위로 편입한다 (A안 ①: 현행 한정).

배경: 전량 확장에서 122건이 `제N조 패턴을 찾지 못함`으로 실패했다. 파싱 문제가
아니라 **원문이 조문 구조가 아니다** — `1. 목적 / 2. 근거 / 3. 대상 …` 항목 구조다.
「학부 재입학에 관한 세부지침」이 대표적인데, 재입학의 대상·제외대상·인원 산정·
절차가 전부 여기 있다(학칙 제30조는 허가 권한만 정한다). 담지 않으면 그 질의는
"근거 없음"으로 답하게 된다.

설계 판단 (2026-08-11 박사 결정, A안 ① 범위):
- 항목을 조문처럼 레코드 단위로 쪼갠다 (`record_type: "항목"`). 문서 1건을 통째
  담는 안(B)은 T27에서 막 해결한 검색 잠식을 되부르고 인용 정밀도를 잃는다.
- 조문번호 자리에는 원문 표기 그대로 `3.`을 쓴다. `제3조`로 바꿔 적으면 인용이 틀린다.
- **현행만** 편입한다. 구판본 항목식 문서는 개정 이력이 정비돼 있지 않아
  시점 질의 가치가 낮다 — 필요해지면 --include-past로 언제든 확장한다.

오탐 차단: 번호가 **1부터 순증하는 구간만** 항목 헤더로 인정한다. 본문 안의
`1. 가나다` 열거를 헤더로 잡으면 문서가 잘게 부서진다.

원문에 없는 제목을 만들지 않는다. 제목이 없으면 비운 채 둔다.
"""

from __future__ import annotations

import json
import re
import shutil
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# ★ 코어(wheel 설치본)는 코퍼스를 RULE_COMPASS_DATA_DIR 에서 찾는다 — 없으면
# site-packages 옆을 보고 못 찾는다(2026-09-07 P04). 명시 지정이 있으면 존중한다.
os.environ.setdefault("RULE_COMPASS_DATA_DIR", str(ROOT / "data"))
from core.item_style import PAST_VERSION_RE, extract  # noqa: E402  # 진본 (P07 가′)
from core.search import prepare_article  # noqa: E402

CORPUS = ROOT / "data" / "rules_corpus.json"
MARKDOWN = ROOT / "data" / "markdown"
FAILURES = ROOT / "data" / "failures_full.jsonl"
# ★ 항목 추출 로직은 core/item_style.py가 진본이다 (2026-09-07, P07 가′).
# 대학 무관 순수 함수인데 운영본에만 있었다. 이식 후 전남대 마크다운 477파일
# 1,641항목에서 산출이 글자 단위로 같은 것을 확인했다(불일치 0).
# 여기 남는 것은 이 레포의 몫뿐이다 — 경로, 실패목록 읽기, 코퍼스 병합, 멱등성.


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    include_past = "--include-past" in sys.argv
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    existing = {
        (str(row.get("source_key")), str(row.get("조문번호")))
        for row in corpus if row.get("record_type") == "항목"
    }
    have_keys = {str(row.get("source_key")) for row in corpus}

    failures = [
        json.loads(line) for line in FAILURES.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    added: list[dict] = []
    done: list[str] = []
    skipped: list[str] = []
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    for failure in failures:
        name = failure["규정명"]
        key = str(failure["source_key"])
        if key in have_keys:
            continue  # 이미 다른 경로로 들어온 규정은 건드리지 않는다
        if not include_past and PAST_VERSION_RE.search(name):
            continue
        path = MARKDOWN / f"key_{key}.md"
        if not path.exists():
            skipped.append(f"{name} (마크다운 없음)")
            continue
        items = extract(path.read_text(encoding="utf-8", errors="replace"))
        if not items:
            skipped.append(f"{name} (항목 구조 아님 — 표·서식 계열)")
            continue
        for number, title, body in items:
            if (key, number) in existing:
                continue
            record = {
                "규정명": name,
                "편제": failure["편제"],
                "조문번호": number,
                "조문제목": title,
                "본문": body,
                "source_key": key,
                "source_url": (
                    "https://www.jnu.ac.kr/WebApp/web/HOM/COM/Rule/AdminRule400.aspx"
                    f"?group=&type=&mode=file&key={key}"
                ),
                "수집일시": now,
                "record_type": "항목",
                "수집방법": "auto",
                "원문_대조_확인": True,
                "장": None,
                "절": None,
            }
            prepared = prepare_article(record)
            record["record_id"] = prepared["record_id"]
            record["revision"] = prepared["revision"]
            added.append(record)
        done.append(f"{name} — {len(items)}항목")

    report = {
        "편입_규정": len(done),
        "편입_항목": len(added),
        "규정별": done,
        "제외": skipped,
        "범위": "현행+구판본" if include_past else "현행만",
    }
    if added and not dry_run:
        backup = CORPUS.with_suffix(".json.pre_items")
        if not backup.exists():
            shutil.copy2(CORPUS, backup)
        CORPUS.write_text(
            json.dumps(corpus + added, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

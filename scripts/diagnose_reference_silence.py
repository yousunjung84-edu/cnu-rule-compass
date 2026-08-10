#!/usr/bin/env python3
"""참조 추출기의 '침묵'을 진단한다 (T19 회귀 방지 원칙).

원칙: **추출기가 인식하지 못한 텍스트는 침묵하지 않는다.**
본문에 규정명·법령명 패턴이 나타났는데 어떤 참조 목록에도 담기지 않으면
그 자체를 결함으로 본다. 소비자는 놓친 줄도 모르기 때문이다.

출력: data/t19_reference_silence.json
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.references import ReferenceIndex  # noqa: E402
from src.search import get_default_index  # noqa: E402

# 본문에 이 패턴이 있으면 참조 목록에 흔적이 남아야 한다.
NAME_PATTERN = re.compile(
    # 용어 정의('「업무 정보화」란 …')는 규정 참조가 아니므로 제외한다.
    r"(?:[「｢『《]\s*([^」｣』》\n]{2,50}?)\s*[」｣』》]"
    r"|([가-힣A-Za-z0-9·ㆍ]{3,30}(?:법률|법|시행령|시행규칙|규정|지침|학칙|규칙|세칙|조례)))"
    r"(?!\s*(?:이)?란|\s*이라\s*함은|\s*이라\s*한다)"
)
GENERIC = {"관련 규정", "관계 규정", "이 규정", "본 규정", "해당 규정", "관련 지침",
           "이 지침", "관계 법령", "관련 법령", "이 학칙", "본 학칙", "제 규정",
           "각종 규정", "각종 지침", "타 규정"}
OUTPUT = Path(__file__).resolve().parent.parent / "data" / "t19_reference_silence.json"


def main() -> int:
    index = get_default_index()
    references = ReferenceIndex(index.articles)
    silent: list[dict] = []
    checked = 0

    for row in index.articles:
        body = str(row.get("본문", ""))
        names = {
            (match.group(1) or match.group(2) or "").strip()
            for match in NAME_PATTERN.finditer(body)
        }
        own = row["규정명"].replace(" ", "")
        names = {
            n for n in names
            if n and n not in GENERIC and n.replace(" ", "") != own
            # 자기 규정을 줄여 부른 것도 참조가 아니다('전남대학교 교통관리규정' 안의 '교통관리규정')
            and not own.endswith(n.replace(" ", ""))
        }
        if not names:
            continue
        checked += 1
        outbound, unresolved = references.outbound(row, resolve=False)
        covered = " ".join(entry["raw"] for entry in outbound + unresolved)
        missed = sorted(n for n in names if n not in covered)
        if missed:
            silent.append({
                "규정명": row["규정명"],
                "조문번호": row["조문번호"],
                "record_id": row["record_id"],
                "미포착": missed,
            })

    report = {
        "패턴_보유_조문": checked,
        "침묵_조문": len(silent),
        "침묵률": round(len(silent) / checked, 4) if checked else 0.0,
        "레코드": silent[:200],
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "레코드"}, ensure_ascii=False, indent=1))
    for row in silent[:10]:
        print(f"  {row['규정명'][:24]:26s} {row['조문번호']:9s} {row['미포착'][:3]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

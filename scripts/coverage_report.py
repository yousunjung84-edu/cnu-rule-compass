#!/usr/bin/env python3
"""편제별 '게시 대비 수집' 대조표를 만든다 (T30).

지금까지의 검증은 전부 *수집된 것 안에서 잘 찾는가*였다. 총무과에 계약 규정이
하나도 없다는 관측으로 처음 **수집 자체가 빠졌다**가 드러났다. 이 층위의 공백은
검색 품질로는 메워지지 않는다 — 없는 규정은 찾을 수 없다.

분모는 두 목록 스냅샷이다(둘 다 2026-07-24 수집).
- 지침 계층: data/listing_snapshot_260724.json  (jnu.ac.kr 행정규정, 부서 편제)
- 규정 계층: data/rule_site_listing_260724.json (law.go.kr 학칙공포, 규정집 편제)

**추정하지 않는다.** 규정 계층의 편제는 목록의 분류 번호를 이름으로 옮기지 않고,
source_key로 코퍼스와 조인해 코퍼스가 실제로 붙인 편제만 쓴다. 조인되지 않은
항목은 '미수집'으로 남기고 편제는 미상으로 둔다.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CORPUS = ROOT / "data" / "rules_corpus.json"
GUIDELINE_LISTING = ROOT / "data" / "listing_snapshot_260724.json"
REGULATION_LISTING = ROOT / "data" / "rule_site_listing_260724.json"
LISTING_DATE = "2026-07-24"
ID_RE = re.compile(r"[?&]ID=(\d+)")


def load_corpus() -> tuple[dict[str, str], dict[str, str]]:
    """source_key → 규정명 / source_key → 편제."""
    names: dict[str, str] = {}
    divisions: dict[str, str] = {}
    for row in json.loads(CORPUS.read_text(encoding="utf-8")):
        key = str(row.get("source_key"))
        names.setdefault(key, row.get("규정명", ""))
        divisions.setdefault(key, row.get("편제", ""))
    return names, divisions


def main() -> int:
    names, divisions = load_corpus()

    listed: list[dict] = []
    for row in json.loads(GUIDELINE_LISTING.read_text(encoding="utf-8")):
        listed.append({
            "계층": "지침",
            "key": str(row["key"]),
            "게시명": row["name"],
            "게시편제": row["division"],
        })
    for row in json.loads(REGULATION_LISTING.read_text(encoding="utf-8")):
        match = ID_RE.search(row.get("url", ""))
        listed.append({
            "계층": "규정",
            "key": match.group(1) if match else "",
            "게시명": row["title"],
            "게시편제": None,  # 목록은 분류 번호만 준다 — 이름은 코퍼스 편제로 채운다
        })

    rows: list[dict] = []
    for item in listed:
        collected = item["key"] in names
        rows.append({
            **item,
            "수집": collected,
            "편제": divisions.get(item["key"]) or item["게시편제"] or "(미상)",
        })

    by_division: dict[str, dict] = {}
    for row in rows:
        entry = by_division.setdefault(row["편제"], {
            "편제": row["편제"], "계층": row["계층"], "게시": 0, "수집": 0, "미수집": [],
        })
        entry["게시"] += 1
        if row["수집"]:
            entry["수집"] += 1
        else:
            entry["미수집"].append(row["게시명"])

    # 목록에 없는데 코퍼스에 있는 규정(구판본·목록 갱신 이후 추가분)도 밝힌다.
    listed_keys = {row["key"] for row in rows}
    extra = sorted(
        {names[key] for key in names if key not in listed_keys}
    )

    total_listed = len(rows)
    total_collected = sum(1 for row in rows if row["수집"])
    report = {
        "수집_범위_기준일": LISTING_DATE,
        "목록_출처": {
            "지침": GUIDELINE_LISTING.name,
            "규정": REGULATION_LISTING.name,
        },
        "게시_규정_수": total_listed,
        "수집_규정_수": total_collected,
        "대상_대비_수집률": round(total_collected / total_listed, 4) if total_listed else None,
        "코퍼스_규정_수": len({name for name in names.values() if name}),
        "목록_밖_코퍼스_규정_수": len(extra),
        "편제별": sorted(
            (
                {
                    "편제": entry["편제"],
                    "계층": entry["계층"],
                    "게시": entry["게시"],
                    "수집": entry["수집"],
                    "수집률": round(entry["수집"] / entry["게시"], 4),
                    "미수집": entry["미수집"],
                }
                for entry in by_division.values()
            ),
            key=lambda entry: (entry["수집률"], -entry["게시"]),
        ),
        "목록_밖_코퍼스_규정": extra,
    }
    output = ROOT / "data" / "coverage_report.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"기준일 {LISTING_DATE} | 게시 {total_listed} / 수집 {total_collected} "
          f"({report['대상_대비_수집률']:.1%})")
    print(f"{'편제':<22}{'계층':<6}{'게시':>5}{'수집':>5}{'수집률':>9}")
    for entry in report["편제별"]:
        print(f"{entry['편제']:<22}{entry['계층']:<6}{entry['게시']:>5}{entry['수집']:>5}"
              f"{entry['수집률']:>9.1%}")
        for name in entry["미수집"]:
            print(f"    - 미수집: {name}")
    print(f"\n목록 밖 코퍼스 규정 {len(extra)}건 (구판본·스냅샷 이후 추가분)")
    print(f"보고서: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

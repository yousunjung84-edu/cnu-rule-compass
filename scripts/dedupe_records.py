#!/usr/bin/env python3
"""완전 중복 레코드와 **별표에서 유출된 서식 조항**을 코퍼스에서 걷어낸다.

왜 reparse_articles가 아니라 별도 도구인가: 재파싱은 split_articles만 부르므로
수집 이후의 후처리(apply_structure_titles)를 되돌린다 — 후처리를 거친 판본에
돌리면 장 제목이 본문에 다시 붙는다(2026-09-07 실측 156건). 여기서는 파싱을
다시 하지 않고 **이미 있는 레코드를 지우기만** 한다.

두 부류를 지운다.

1) 완전 중복 — 같은 record_id·같은 본문이 2회 이상. 하나만 남긴다. 손실 0.

2) 별표 유출 — 별표(계약서·서식) 안의 조항이 본칙으로도 적재된 것.
   ⚠️ 판정을 코드가 확인한 뒤에만 지운다. 세 조건을 **모두** 만족해야 한다.
     ① 같은 (source_key, 조문번호)에 **다른 본문**의 본칙이 따로 있다
        (진짜 조문이 남는다 — 지워서 답이 사라지지 않는다)
     ② 그 본문이 같은 규정의 **별표 본문 안에 그대로 들어 있다**
        (원문이 별표에 보존돼 있다 — 내용 손실 0)
     ③ 중복(2회 이상)으로 적재돼 있다
   하나라도 어긋나면 남긴다. 판정이 서면 지우고, 서지 않으면 지우지 않는다.

   ⚠️ ③을 빼면 **판정이 무너진다**(2026-09-07 실측). ①②만으로는 385건이 걸리고
   그중 상당수가 오탐이다 — 조건 ②가 substring 매칭이라 본문이 짧으면 별표
   어딘가에 우연히 포함된다(key 2588은 본문이 '------ <내용동일>'이었다).
   최소 길이 100자를 걸어도 222건, 200자에서도 99건이라 길이로는 갈리지 않는다.
   ③은 「유출의 표지」로서 불완전하지만(유출은 중복 없이도 일어난다 — 연구비
   중앙관리지침 9판본, 그중 현행본 key 4213 포함) **오탐을 막는 값이 더 크다.**
   남은 유출을 지우려면 원문의 별표 구간(`## 연구계약서` ~ `# 용역계약서`)을
   읽어 조문 소속을 판정해야 한다 — substring으로는 안 된다.

실행 뒤 change_gate 승인이 남는다 — 이 도구는 게이트를 대신하지 않는다.

3) `--ids` 로 **명시 지정한 record_id**만 제거 — 자동 판정이 넘칠 때 쓴다.
   별표 구간 offset으로 판정하면 374건이 걸린다(2026-09-07 실측). 그 규모는
   사람이 보고 정할 일이라, 확정한 것만 id로 넘겨 지운다. 지정한 id가 코퍼스에
   없으면 **오류로 멈춘다** — 조용히 0건 처리하지 않는다.

사용:
    python3 scripts/dedupe_records.py --dry-run
    python3 scripts/dedupe_records.py
    python3 scripts/dedupe_records.py --ids rule-4213-... ,rule-3260-... --dry-run
"""
from __future__ import annotations

import collections
import datetime
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("RULE_COMPASS_DATA_DIR", str(ROOT / "data"))
CORPUS = ROOT / "data" / "rules_corpus.json"
STAMP = datetime.datetime.now().strftime("%y%m%d_%H%M%S")
DRY = "--dry-run" in sys.argv


def _explicit_ids() -> set[str] | None:
    for i, a in enumerate(sys.argv):
        if a.startswith("--ids="):
            raw = a.split("=", 1)[1]
        elif a == "--ids":
            if i + 1 >= len(sys.argv) or sys.argv[i + 1].startswith("-"):
                print("[오류] --ids 뒤에 쉼표로 구분한 record_id가 필요하다", file=sys.stderr)
                raise SystemExit(2)
            raw = sys.argv[i + 1]
        else:
            continue
        ids = {x.strip() for x in raw.split(",") if x.strip()}
        if not ids:
            print("[오류] --ids 가 비어 있다", file=sys.stderr)
            raise SystemExit(2)
        return ids
    return None


def main() -> int:
    rows = json.loads(CORPUS.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        print("[중단] 코퍼스가 list가 아니다", file=sys.stderr)
        return 2

    explicit = _explicit_ids()
    if explicit is not None:
        present = {r.get("record_id") for r in rows}
        missing = explicit - present
        if missing:
            print(f"[오류] 코퍼스에 없는 record_id {len(missing)}건: "
                  f"{sorted(missing)[:3]}", file=sys.stderr)
            return 2
        out = [r for r in rows if r.get("record_id") not in explicit]
        dropped = [{"record_id": r.get("record_id"), "source_key": r.get("source_key"),
                    "조문번호": r.get("조문번호"), "조문제목": r.get("조문제목"),
                    "본문_앞": str(r.get("본문", ""))[:70]}
                   for r in rows if r.get("record_id") in explicit]
        print(json.dumps({"이전": len(rows), "이후": len(out),
                          "명시_제거": len(dropped)}, ensure_ascii=False))
        ev = ROOT / "data" / f"dedupe_evidence_{STAMP}_{'dryrun' if DRY else 'applied'}.json"
        ev.write_text(json.dumps({"명시_제거": dropped}, ensure_ascii=False, indent=1),
                      encoding="utf-8")
        print(f"증거: {ev.name}")
        if DRY:
            return 0
        backup = CORPUS.with_suffix(f".json.pre_dedupe_{STAMP}")
        shutil.copy2(CORPUS, backup)
        CORPUS.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"적용 완료 · 백업 {backup.name}")
        print("⚠️ change_gate 승인이 남았다 — 이 도구는 게이트를 대신하지 않는다.")
        return 0

    # ── 규정별 별표 본문 (판정 ②의 근거) ────────────────────────────
    byul = collections.defaultdict(str)
    for r in rows:
        if r.get("record_type") == "별표":
            byul[str(r.get("source_key"))] += "\n" + str(r.get("본문", ""))

    # ── 같은 (key, 조문번호) 본칙의 본문 종수 (판정 ①) ───────────────
    bonchik = collections.defaultdict(set)
    for r in rows:
        if r.get("record_type") == "본칙":
            bonchik[(str(r.get("source_key")), str(r.get("조문번호")))].add(str(r.get("본문")))

    counts = collections.Counter(r.get("record_id") for r in rows)
    out, dropped_dup, dropped_form = [], [], []
    seen: set = set()

    for r in rows:
        rid = r.get("record_id")
        key, no = str(r.get("source_key")), str(r.get("조문번호"))
        body = str(r.get("본문", ""))

        # 2) 별표 유출 — 세 조건을 모두 만족할 때만
        if (r.get("record_type") == "본칙"
                and counts[rid] > 1
                and len(bonchik[(key, no)]) > 1
                and body and body.strip() in byul.get(key, "")):
            dropped_form.append({"source_key": key, "조문번호": no, "record_id": rid,
                                 "조문제목": r.get("조문제목"), "본문_앞": body[:70]})
            continue

        # 1) 완전 중복 — 같은 ID를 두 번째부터 버린다
        if rid in seen:
            dropped_dup.append({"source_key": key, "조문번호": no, "record_id": rid,
                                "record_type": r.get("record_type"),
                                "조문제목": r.get("조문제목")})
            continue
        seen.add(rid)
        out.append(r)

    print(json.dumps({"이전": len(rows), "이후": len(out),
                      "별표유출_제거": len(dropped_form),
                      "완전중복_제거": len(dropped_dup)}, ensure_ascii=False))

    evidence = ROOT / "data" / f"dedupe_evidence_{STAMP}_{'dryrun' if DRY else 'applied'}.json"
    evidence.write_text(json.dumps({"별표유출": dropped_form, "완전중복": dropped_dup},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"증거: {evidence.name}")
    if DRY:
        return 0

    backup = CORPUS.with_suffix(f".json.pre_dedupe_{STAMP}")
    shutil.copy2(CORPUS, backup)
    CORPUS.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"적용 완료 · 백업 {backup.name}")
    print("⚠️ change_gate 승인이 남았다 — 이 도구는 게이트를 대신하지 않는다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

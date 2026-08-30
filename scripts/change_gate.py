#!/usr/bin/env python3
"""change-set 승인 게이트 — 코어 P1의 운영본 이식 (backport #4, 2026-08-31).

코어(rule-compass-core `039e722`)에서 검증된 의미론을 운영본 갱신 흐름에 맞게
다시 쓴 것이다(코드 복사가 아니라 개념 이식 — backport_queue.md 규율 3).

원리: 월간 갱신이 만든 새 코퍼스를 이전 코퍼스와 비교해 **위험 변경**(규정
단위 조문 소실, is_current False→True 승격)이 있으면 채택하지 않는다. 승인은
위험 목록의 fingerprint(정렬 canonical JSON sha256[:12])에 결속된다 — 내용이
바뀌면 지문이 달라져 옛 승인이 자동 무효가 되므로, "목록을 읽지 않은 일괄
통과"가 구조적으로 불가능하다.

ledger(적재 대장)와 역할이 다르다: ledger는 레코드 무결성(빈 본문·거대·중복)
검증이고, 이 게이트는 **이전 상태 대비 변경의 승인**이다.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter


def _per_rule(rows: list[dict]) -> dict[str, int]:
    out: Counter = Counter()
    for r in rows:
        out[str(r.get("source_key"))] += 1
    return dict(out)


def _names(rows: list[dict]) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in rows:
        out.setdefault(str(r.get("source_key")), str(r.get("규정명", "")))
    return out


def change_report(prev_rows: list[dict], new_rows: list[dict]) -> dict:
    """위험(소실·현행성 승격)과 참고 변화를 분류하고 지문을 만든다."""
    pc, nc = _per_rule(prev_rows), _per_rule(new_rows)
    names = {**_names(new_rows), **_names(prev_rows)}
    losses = [{"source_key": k, "규정명": names.get(k, ""),
               "before": pc[k], "after": nc.get(k, 0)}
              for k in sorted(pc) if nc.get(k, 0) < pc[k]]
    prev_cur = {str(r.get("record_id")): bool(r.get("is_current"))
                for r in prev_rows if r.get("record_id")}
    promotions = [{"record_id": rid, "규정명": str(r.get("규정명", "")),
                   "조문번호": str(r.get("조문번호", ""))}
                  for r in new_rows
                  if (rid := str(r.get("record_id")))
                  and prev_cur.get(rid) is False and bool(r.get("is_current"))]
    risks = ([{"type": "article_loss", **l} for l in losses]
             + [{"type": "current_promotion", **p} for p in promotions])
    canonical = json.dumps(risks, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
    return {
        "requires_approval": bool(risks),
        "fingerprint": fingerprint if risks else None,
        "risks": risks,
        "new_rules": sorted(set(nc) - set(pc)),
        "distribution": {
            "record_type": {
                "before": dict(Counter(str(r.get("record_type")) for r in prev_rows)),
                "after": dict(Counter(str(r.get("record_type")) for r in new_rows)),
            },
        },
    }


def guard(prev_rows: list[dict], new_rows: list[dict],
          approve: str | None = None) -> tuple[bool, dict]:
    """(채택 가능 여부, 보고서). 위험이 있으면 지문 일치 승인 없이는 False.

    승인은 현재 change-set의 지문과 정확히 일치해야 한다 — 다른 지문 + 어떤
    플래그 조합으로도 우회할 수 없다(코어 P1과 같은 계약).
    """
    report = change_report(prev_rows, new_rows)
    if not report["requires_approval"]:
        return True, report
    return approve == report["fingerprint"], report

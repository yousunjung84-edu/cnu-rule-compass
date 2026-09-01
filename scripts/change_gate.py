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


def _current_per_rule(rows: list[dict], current: dict[str, bool] | None) -> dict[str, int]:
    """source_key별 **현행** 조문 수.

    현행성은 코퍼스에 저장되지 않는다(is_current 키 0/17,585 — 2026-09-01 실측).
    `src/search.py`가 규정명의 「(YYYY. M. D. 개정전)」 표기로 적재 시점에
    계산한다. 그래서 `current` 맵(엔진이 계산한 record_id→bool)을 받아야 이
    수치가 의미를 갖는다. 맵이 없으면 행의 is_current로 떨어지는데, 그 값은
    코퍼스에 없으므로 결과가 전부 0이 된다 — 그 상태를 통과로 읽지 않도록
    change_report가 current_source를 함께 보고한다.
    """
    out: Counter = Counter()
    for r in rows:
        rid = str(r.get("record_id", ""))
        is_cur = current.get(rid, False) if current is not None else bool(r.get("is_current"))
        if is_cur:
            out[str(r.get("source_key"))] += 1
    return dict(out)


def change_report(prev_rows: list[dict], new_rows: list[dict],
                  prev_current: dict[str, bool] | None = None,
                  new_current: dict[str, bool] | None = None) -> dict:
    """위험(소실·현행성 승격·현행 소실)과 참고 변화를 분류하고 지문을 만든다.

    ★ current_article_loss (2026-09-01 추가)

    2026-09-01 04:30 갱신에서 규정 4건이 게시자 표기를 따라 「…(2026. 8. 19.
    개정전)」으로 개명됐고, 새 현행판은 수집되지 않아 **그 4개 규정의 현행
    조문이 0건**이 됐다. 조문 수·규정 수는 그대로였고 record_id도 그대로여서
    이 게이트는 requires_approval=false로 통과시켰다(62행 강등, 재현 확인).

    개명 자체는 위험으로 보지 않는다 — 게시자가 실제로 바꾼 표기이고, 규정이
    개정될 때마다 일어나므로 위험으로 두면 일상 갱신이 매번 승인을 기다린다.
    위험으로 보는 것은 **규정의 현행 조문이 줄어드는 것**이다.

    ⚠️ 정정(2026-09-01 2회차 교차검증 #6). 처음 여기에 「정상 개정에서는 구판이
    강등되는 동시에 신판이 들어와 현행 수가 유지된다」고 적었는데 **일반적으로
    참이 아니다**. 구판 2개 조문을 신판 1개 통합 조문이 대체하는 정상 개정에서
    2→1로 발화한다(반례 재현 확인). 그래도 위험으로 남긴다 — 이 코퍼스는 행정
    근거로 인용되므로 「현행 근거가 줄었다」는 사실은 사람이 한 번 보는 편이
    맞고, 조용히 통과하는 쪽의 대가가 훨씬 크다.

    대신 severity로 나눠 판단을 싸게 만든다:
      current_zero — 현행이 0건이 됐다. 신판 미수집이 거의 확실한 사고.
      current_drop — 줄었지만 남아 있다. 조문 통합일 수 있으니 확인 대상.
    """
    pc, nc = _per_rule(prev_rows), _per_rule(new_rows)
    names = {**_names(new_rows), **_names(prev_rows)}
    losses = [{"source_key": k, "규정명": names.get(k, ""),
               "before": pc[k], "after": nc.get(k, 0)}
              for k in sorted(pc) if nc.get(k, 0) < pc[k]]
    prev_cur = (prev_current if prev_current is not None else
                {str(r.get("record_id")): bool(r.get("is_current"))
                 for r in prev_rows if r.get("record_id")})
    new_cur = (new_current if new_current is not None else
               {str(r.get("record_id")): bool(r.get("is_current"))
                for r in new_rows if r.get("record_id")})
    promotions = [{"record_id": rid, "규정명": str(r.get("규정명", "")),
                   "조문번호": str(r.get("조문번호", ""))}
                  for r in new_rows
                  if (rid := str(r.get("record_id")))
                  and prev_cur.get(rid) is False and new_cur.get(rid, False)]
    pcc, ncc = (_current_per_rule(prev_rows, prev_current),
                _current_per_rule(new_rows, new_current))
    current_losses = [{"source_key": k, "규정명": names.get(k, ""),
                       "before": pcc[k], "after": ncc.get(k, 0),
                       "severity": "current_zero" if ncc.get(k, 0) == 0
                                   else "current_drop"}
                      for k in sorted(pcc) if ncc.get(k, 0) < pcc[k]]
    # 개명은 위험이 아니라 참고 변화 — 다만 보고서에는 반드시 남긴다.
    prev_names, new_names = _names(prev_rows), _names(new_rows)
    renames = [{"source_key": k, "before": prev_names[k], "after": new_names[k]}
               for k in sorted(set(prev_names) & set(new_names))
               if prev_names[k] != new_names[k]]
    current_source = ("engine" if (prev_current is not None and new_current is not None)
                      else "row_field")
    risks = ([{"type": "article_loss", **l} for l in losses]
             + [{"type": "current_article_loss", **l} for l in current_losses]
             + [{"type": "current_promotion", **p} for p in promotions])
    if current_source != "engine":
        # ★ fail-closed (2026-09-01 2회차 교차검증 #5).
        #
        # 맵 없이 부르면 코퍼스에 없는 is_current를 읽어 현행 수와 승격이 전부
        # 0으로 나온다 — 현행 관련 검사가 꺼진 채 「위험 없음」이 된다. 보고서에
        # current_source를 적는 것만으로는 통과를 막지 못했다. 꺼진 상태 자체를
        # 위험으로 올려 사람이 지문을 승인해야 지나가게 한다.
        risks.append({
            "type": "current_source_missing",
            "detail": "현행 맵이 전달되지 않아 현행 소실·승격 검사가 동작하지 "
                      "않았습니다. 호출자가 엔진으로 record_id→is_current 맵을 "
                      "만들어 prev_current/new_current로 넘겨야 합니다.",
        })
    canonical = json.dumps(risks, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
    return {
        "requires_approval": bool(risks),
        "fingerprint": fingerprint if risks else None,
        "risks": risks,
        "new_rules": sorted(set(nc) - set(pc)),
        "renames": renames,
        # 현행성을 무엇으로 판정했는지 밝힌다. engine이 아니면 현행 관련 위험
        # 검사가 꺼진 것이고, 그 자체가 위 current_source_missing 위험이 된다.
        "current_source": current_source,
        "distribution": {
            "record_type": {
                "before": dict(Counter(str(r.get("record_type")) for r in prev_rows)),
                "after": dict(Counter(str(r.get("record_type")) for r in new_rows)),
            },
            "현행_규정수": {"before": len(pcc), "after": len(ncc)},
        },
    }


def guard(prev_rows: list[dict], new_rows: list[dict],
          approve: str | None = None,
          prev_current: dict[str, bool] | None = None,
          new_current: dict[str, bool] | None = None) -> tuple[bool, dict]:
    """(채택 가능 여부, 보고서). 위험이 있으면 지문 일치 승인 없이는 False.

    승인은 현재 change-set의 지문과 정확히 일치해야 한다 — 다른 지문 + 어떤
    플래그 조합으로도 우회할 수 없다(코어 P1과 같은 계약).
    """
    report = change_report(prev_rows, new_rows, prev_current, new_current)
    if not report["requires_approval"]:
        return True, report
    return approve == report["fingerprint"], report

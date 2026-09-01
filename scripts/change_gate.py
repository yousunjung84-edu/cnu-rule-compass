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

# 지문 산식 버전. 위험 항목의 필드나 canonical 정렬이 바뀌면 올린다 —
# 옛 승인이 안 맞는 이유가 「재실행 드리프트」인지 「포맷 변경」인지
# 사용자가 구별할 수 있어야 한다.
FINGERPRINT_VERSION = 2


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


def _blockers(prev_rows: list[dict], new_rows: list[dict],
              prev_current: dict[str, bool] | None,
              new_current: dict[str, bool] | None) -> list[dict]:
    """**승인으로 넘길 수 없는** 차단 사유. 하나라도 있으면 지문을 발급하지 않는다.

    위험(risk)과 다르다. 위험은 「사람이 보고 받아들일 수 있는 변경」이고,
    차단은 「무엇이 변했는지 판정 자체를 못 한 상태」다. 후자를 승인 가능하게
    두면 검사를 끄는 스위치가 된다.
    """
    out: list[dict] = []
    for label, rows, cur in (("prev", prev_rows, prev_current),
                             ("new", new_rows, new_current)):
        # 맵이 dict가 아니면 아래 검사가 예외로 터진다. 구조화된 차단으로 돌려
        # 보고서에 남긴다 — 배포는 원복되겠지만 사유가 남지 않으면 진단이 안 된다.
        if cur is not None and not isinstance(cur, dict):
            out.append({"type": "current_map_type", "side": label,
                        "detail": f"현행 맵이 dict가 아닙니다({type(cur).__name__})."})
            continue
        if cur is None:
            out.append({"type": "current_map_missing", "side": label,
                        "detail": "현행 맵이 전달되지 않았습니다. 호출자가 엔진으로 "
                                  "record_id→is_current 맵을 만들어 넘겨야 합니다."})
            continue
        # ★ record_id가 없거나 빈 행은 그냥 건너뛰면 안 된다(4회차 교차검증).
        # 건너뛰면 그 행의 현행성은 아무도 판정하지 않은 채 통과한다.
        no_id = sum(1 for r in rows if not str(r.get("record_id") or "").strip())
        if no_id:
            out.append({"type": "record_id_missing", "side": label,
                        "count": no_id, "total": len(rows),
                        "detail": "record_id 없는 행이 있어 현행성을 판정할 수 "
                                  "없습니다."})
        # 중복 ID 자체는 차단하지 않는다. record_id가 내용 해시
        # (source_key|조문번호|record_type|조문제목|본문)이므로 같은 ID는 같은
        # 내용이고, 한 bool이 대표해도 판정이 모호하지 않다. 실측(2026-09-01):
        # 52종 77 초과행, **행 간 필드 불일치 0** — 적재 게이트가 duplicate_record
        # 77건으로 거르는 그 행들이다. 여기서 차단하면 매 실행이 막힌다.
        #
        # 차단할 것은 **내용이 다른 중복**이다. 그때는 어느 쪽 현행성인지
        # 정할 수 없고, ID 산식이 깨졌다는 신호이기도 하다.
        groups: dict[str, list[dict]] = {}
        for r in rows:
            rid = str(r.get("record_id") or "").strip()
            if rid:
                groups.setdefault(rid, []).append(r)
        conflict = sorted(
            rid for rid, rs in groups.items() if len(rs) > 1
            and any(other != rs[0] for other in rs[1:]))
        if conflict:
            out.append({"type": "record_id_conflict", "side": label,
                        "count": len(conflict), "sample": conflict[:5],
                        "detail": "같은 record_id인데 내용이 다른 행이 있습니다 — "
                                  "어느 쪽 현행성인지 정할 수 없습니다."})
        ids = set(groups)
        missing = ids - set(cur)
        if missing:
            out.append({"type": "current_map_incomplete", "side": label,
                        "missing_count": len(missing), "total": len(ids),
                        "sample": sorted(missing)[:5],
                        "detail": "현행 맵이 일부 record_id를 덮지 않습니다. "
                                  "빈 맵이나 부분 맵으로는 현행 검사가 성립하지 "
                                  "않습니다."})
        # rows에 없는 여분 키는 맵이 다른 코퍼스에서 왔다는 신호다.
        extra = set(cur) - ids
        if extra:
            out.append({"type": "current_map_extra", "side": label,
                        "count": len(extra), "sample": sorted(extra)[:5],
                        "detail": "현행 맵에 이 코퍼스에 없는 record_id가 있습니다 "
                                  "— 다른 코퍼스의 맵일 수 있습니다."})
        bad = [k for k, v in cur.items() if not isinstance(v, bool)]
        if bad:
            out.append({"type": "current_map_value_type", "side": label,
                        "count": len(bad), "sample": sorted(map(str, bad))[:5],
                        "detail": "현행 맵의 값은 참/거짓이어야 합니다."})
    # 빈 코퍼스는 비교의 대상이 아니다. 양쪽이 비면 「변경 없음」으로 통과하고,
    # 한쪽만 비면 전멸이 fingerprint 승인 대상이 된다 — 둘 다 판정 불능이다.
    if not prev_rows or not new_rows:
        out.append({"type": "empty_corpus",
                    "prev": len(prev_rows), "new": len(new_rows),
                    "detail": "코퍼스가 비어 있어 변경 판정이 성립하지 않습니다."})
    return out


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
    # ★ 차단부터 판정한다. 위험 계산은 맵이 정상일 때만 의미가 있고, 비정상
    # 입력(맵이 list 등)에서는 예외로 터져 구조화된 보고서조차 못 남긴다.
    blockers = _blockers(prev_rows, new_rows, prev_current, new_current)
    current_source = ("engine" if (prev_current is not None and new_current is not None)
                      else "row_field")
    if blockers:
        return {
            "blocked": True, "blockers": blockers,
            "requires_approval": False, "fingerprint": None, "risks": [],
            "new_rules": [], "renames": [],
            "current_source": current_source,
            "fingerprint_version": FINGERPRINT_VERSION,
            "distribution": {},
        }
    pc, nc = _per_rule(prev_rows), _per_rule(new_rows)
    names = {**_names(new_rows), **_names(prev_rows)}
    prev_cur = (prev_current if prev_current is not None else
                {str(r.get("record_id")): bool(r.get("is_current"))
                 for r in prev_rows if r.get("record_id")})
    new_cur = (new_current if new_current is not None else
               {str(r.get("record_id")): bool(r.get("is_current"))
                for r in new_rows if r.get("record_id")})
    new_ids = {str(r.get("record_id")) for r in new_rows if r.get("record_id")}
    lost_by_key: dict[str, list[str]] = {}
    for r in prev_rows:
        rid = str(r.get("record_id", ""))
        if rid and rid not in new_ids:
            lost_by_key.setdefault(str(r.get("source_key")), []).append(rid)
    losses = [{"source_key": k, "규정명": names.get(k, ""),
               "before": pc[k], "after": nc.get(k, 0),
               "lost_ids": sorted(lost_by_key.get(k, []))}
              for k in sorted(pc) if nc.get(k, 0) < pc[k]]
    promotions = [{"record_id": rid, "규정명": str(r.get("규정명", "")),
                   "조문번호": str(r.get("조문번호", ""))}
                  for r in new_rows
                  if (rid := str(r.get("record_id")))
                  and prev_cur.get(rid) is False and new_cur.get(rid, False)]
    pcc, ncc = (_current_per_rule(prev_rows, prev_current),
                _current_per_rule(new_rows, new_current))
    # ★ 위험 항목은 **무엇이 바뀌었는지**까지 담아야 지문이 내용에 결속된다
    # (4회차 교차검증). before/after 수치만 담으면 같은 규정 안의 서로 다른
    # 강등이 같은 지문을 만들고, 한 번 받은 승인이 다른 변경에 재사용된다.
    # 실측: 조문 a 강등과 조문 b 강등이 둘 다 5dc4823b740b였다.
    demoted_by_key: dict[str, list[str]] = {}
    for r in prev_rows:
        rid = str(r.get("record_id", ""))
        if rid and prev_cur.get(rid) and not new_cur.get(rid, False):
            demoted_by_key.setdefault(str(r.get("source_key")), []).append(rid)
    current_losses = [{"source_key": k, "규정명": names.get(k, ""),
                       "before": pcc[k], "after": ncc.get(k, 0),
                       "severity": "current_zero" if ncc.get(k, 0) == 0
                                   else "current_drop",
                       "demoted_ids": sorted(demoted_by_key.get(k, []))}
                      for k in sorted(pcc) if ncc.get(k, 0) < pcc[k]]
    # 개명은 위험이 아니라 참고 변화 — 다만 보고서에는 반드시 남긴다.
    prev_names, new_names = _names(prev_rows), _names(new_rows)
    renames = [{"source_key": k, "before": prev_names[k], "after": new_names[k]}
               for k in sorted(set(prev_names) & set(new_names))
               if prev_names[k] != new_names[k]]
    risks = ([{"type": "article_loss", **l} for l in losses]
             + [{"type": "current_article_loss", **l} for l in current_losses]
             + [{"type": "current_promotion", **p} for p in promotions])
    # 지문은 **순서에 흔들리지 않아야** 한다(3회차 교차검증). promotions가
    # new_rows 순서를 그대로 담아, 같은 두 승격의 행 순서만 뒤집혀도 지문이
    # 달라졌다 — 재실행 드리프트와 실제 변경을 구별할 수 없게 만든다.
    canonical = json.dumps(
        sorted(risks, key=lambda r: json.dumps(r, ensure_ascii=False, sort_keys=True)),
        ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
    return {
        # 차단이 있으면 승인 절차 자체가 열리지 않는다 — 지문도 발급하지 않는다.
        "blocked": bool(blockers),
        "blockers": blockers,
        "requires_approval": bool(risks) and not blockers,
        "fingerprint": (fingerprint if risks and not blockers else None),
        "risks": risks,
        # 지문 산식이 바뀌면 옛 승인이 조용히 안 맞는다. 사용자가 재실행
        # 드리프트와 포맷 변경을 구별할 수 있게 버전을 박는다.
        "fingerprint_version": FINGERPRINT_VERSION,
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
    if report["blocked"]:
        # 어떤 승인으로도 지나갈 수 없다. 판정을 못 한 상태를 승인 가능하게
        # 두면 그 승인이 검사를 끄는 스위치가 된다.
        return False, report
    if not report["requires_approval"]:
        return True, report
    return approve == report["fingerprint"], report

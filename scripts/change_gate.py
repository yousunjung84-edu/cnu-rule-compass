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
import re
from collections import Counter

# 지문 산식 버전. 위험 항목의 필드나 canonical 정렬이 바뀌면 올린다 —
# 옛 승인이 안 맞는 이유가 「재실행 드리프트」인지 「포맷 변경」인지
# 사용자가 구별할 수 있어야 한다.
FINGERPRINT_VERSION = 5   # v5: 형제 key 상쇄 마스킹 제거(code-review #5) — 위험 산식 변경


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


# ★ 계열(lineage) 단위 판정 — source_key로는 정상 개정과 사고를 구별 못 한다.
#
# 이 학교는 개정 시 구판을 「…(개정전)」으로 개명하고 **신판에 새 key를 준다**.
# 그래서 구판 key는 정상 개정이면 반드시 현행 0이 된다. source_key로 현행
# 소실을 보면 정상 아카이브가 전부 위험으로 잡힌다.
#
#   2026-09-02 실측: source_key 809개 중 현행 0인 key 391개(48%),
#   그중 355개가 이름에 「개정전/이전」이 붙은 정상 아카이브였다.
#   같은 코퍼스를 계열로 묶으면 476계열 중 현행 0은 65계열뿐이다.
#
# 사고(신판 미수집)와 정상 개정의 차이는 **계열에 현행이 남아 있는가**다.
# 8/19 사건 때 4개 규정은 계열 단위로도 0이었고, 신판을 받은 뒤 25·12·17·9로
# 살아났다. 그것이 우리가 잡고 싶은 신호다.
#
# 접미사 규칙은 실측으로 확정했다(규정명 808종). 날짜 + (개정전|이전)을 담은
# **말미 괄호구**만 벗긴다. 「제정」은 제목의 일부일 수 있어(「…수여 규정 제정」)
# 마커에서 뺐고, 「2019. 6월 개정전」 같은 월 표기도 받는다.
_ARCHIVE_SUFFIX = re.compile(
    r"\s*[（(]?\s*(?:19|20)\d\d\s*[.\s]\s*\d{1,2}\s*(?:월)?\s*[.\s]?\s*\d{0,2}"
    r"\s*[.,]?\s*(?:개정전|이전)\s*[)）]?\s*$")


# ★ search.py의 강등 규칙과 **같은 집합**을 벗긴다 (2026-09-02 code-review #4).
#
# lineage_of가 「(날짜 개정전|이전)」만 벗기는 동안 src/search.py는 더 넓게
# 강등했다 — 꼬리 괄호에 제정/개정/폐지/이전이나 연도가 있으면 구판, 연도 접두
# (「2007년도 …」「2013~2014학년도 …」)는 최신 연도판만 현행. 그 차집합(실측
# 35 규정명)은 단독 계열이 되어 정상 개정에서 current_zero를 냈다 — 8e7bb71이
# 없애려던 바로 그 거짓 양성. 아래 두 패턴은 search.py의 것을 그대로 옮긴 것이고,
# 두 모듈의 일치는 tests/test_change_gate.py가 실코퍼스로 잠근다.
_TAIL_NOTE = re.compile(
    r"^(?P<base>.+?)\s*\((?P<note>"
    r"[^)]*(?:제정|개정|폐지|이전)[^)]*"
    r"|\s*\d{4}[^)]*"
    r")\)\s*$")
_YEAR_PREFIX = re.compile(
    r"^\s*(?P<y1>(?:19|20)\d{2})\s*(?:[~\-.]\s*(?P<y2>\d{2,4}))?\s*(?:년도|학년도)?\s*")


def lineage_of(name: str) -> str:
    """규정명에서 판본 표기를 벗겨 계열 이름을 얻는다."""
    prev, cur = None, str(name).strip()
    while prev != cur:                      # 표기가 겹쳐 붙은 이름도 있다
        prev = cur
        cur = _ARCHIVE_SUFFIX.sub("", cur).strip()
        m = _TAIL_NOTE.match(cur)
        if m:
            cur = m.group("base").strip()
        m = _YEAR_PREFIX.match(cur)
        if m and cur[m.end():].strip():     # 이름 전체가 연도뿐이면 판본 표기가 아니다
            cur = cur[m.end():].strip()
    return cur


def _current_per_lineage(rows: list[dict], current: dict[str, bool] | None) -> dict[str, int]:
    """계열별 현행 조문 수."""
    out: Counter = Counter()
    for r in rows:
        rid = str(r.get("record_id", ""))
        is_cur = current.get(rid, False) if current is not None else bool(r.get("is_current"))
        if is_cur:
            out[lineage_of(r.get("규정명", ""))] += 1
    return dict(out)


def _renames(prev_rows: list[dict], new_rows: list[dict]) -> list[dict]:
    """source_key별 규정명 변경. 위험이 아니라 참고 변화다."""
    prev_names, new_names = _names(prev_rows), _names(new_rows)
    return [{"source_key": k, "before": prev_names[k], "after": new_names[k]}
            for k in sorted(set(prev_names) & set(new_names))
            if prev_names[k] != new_names[k]]


def _state_digest(rows: list[dict], current: dict[str, bool]) -> str:
    """코퍼스 상태의 완전 요약. **지문의 재료다.**

    ★ 왜 위험 목록이 아니라 상태에서 뽑는가(5회차 교차검증).

    지문을 risks에서 만들면, 위험 항목이 담지 않은 차이는 지문에 남지 않는다.
    항목마다 필드를 덧붙여 막아 왔는데 계속 샜다:
      · lost_ids를 넣었더니 중복 정리([a,a]→[a] vs [b,b]→[b])에서 둘 다 비어
        같은 지문 97fa0c366166
      · 규정명은 {**new, **prev}라 늘 이전 이름이라 개명이 결속 안 됨
        (NEW-1/NEW-2 개명 둘 다 25ba25cd5d3d)
      · current_promotion에 source_key가 없어 다른 규정의 승격이 같은 지문
    「무엇을 위험으로 부르는가」와 「무엇이 바뀌었는가」는 다른 질문이고,
    승인은 후자에 결속돼야 한다. 여기서는 행 다중집합·규정명·현행성을
    통째로 해시한다 — 한 글자만 달라도 지문이 달라진다.

    record_id별로 (등장 횟수, 규정명, source_key, 조문번호, 현행성)을 담는다.
    본문까지 넣지 않는 것은 record_id가 이미 내용 해시이기 때문이다.
    """
    counts: dict[str, int] = {}
    meta: dict[str, list] = {}
    for r in rows:
        rid = str(r.get("record_id") or "")
        counts[rid] = counts.get(rid, 0) + 1
        meta[rid] = [str(r.get("규정명", "")), str(r.get("source_key", "")),
                     str(r.get("조문번호", ""))]
    payload = [[rid, counts[rid], *meta[rid], bool(current.get(rid, False))]
               for rid in sorted(counts)]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _blockers(prev_rows: list[dict], new_rows: list[dict],
              prev_current: dict[str, bool] | None,
              new_current: dict[str, bool] | None) -> list[dict]:
    """**승인으로 넘길 수 없는** 차단 사유. 하나라도 있으면 지문을 발급하지 않는다.

    위험(risk)과 다르다. 위험은 「사람이 보고 받아들일 수 있는 변경」이고,
    차단은 「무엇이 변했는지 판정 자체를 못 한 상태」다. 후자를 승인 가능하게
    두면 검사를 끄는 스위치가 된다.
    """
    out: list[dict] = []
    for label, rows in (("prev", prev_rows), ("new", new_rows)):
        # 행이 dict가 아니면 아래 r.get에서 예외가 난다 — 구조화된 차단으로 돌린다.
        bad_rows = sum(1 for r in rows if not isinstance(r, dict))
        if bad_rows:
            out.append({"type": "row_type", "side": label, "count": bad_rows,
                        "detail": "행이 dict가 아닙니다."})
    if out:
        return out
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
    # 맵이 dict가 아니면 「엔진 맵을 받았다」고 말할 수 없다 — 진단 메타가
    # 사실과 달라지면 사후 추적이 어긋난다(5회차 교차검증).
    current_source = ("engine"
                      if (isinstance(prev_current, dict)
                          and isinstance(new_current, dict))
                      else "row_field")
    if blockers:
        return {
            "blocked": True, "blockers": blockers,
            "requires_approval": False, "fingerprint": None, "risks": [],
            # 차단이어도 개명은 계산해 남긴다 — 배포 로그가 실제 변경 정보를
            # 잃으면 「왜 막혔는지」를 사람이 재구성할 수 없다(5회차 교차검증).
            "new_rules": sorted(set(_per_rule(new_rows)) - set(_per_rule(prev_rows)))
                         if all(isinstance(r, dict) for r in prev_rows + new_rows) else [],
            "renames": _renames(prev_rows, new_rows)
                       if all(isinstance(r, dict) for r in prev_rows + new_rows) else [],
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
    pcc, ncc = (_current_per_lineage(prev_rows, prev_current),
                _current_per_lineage(new_rows, new_current))
    # ★ 위험 항목은 **무엇이 바뀌었는지**까지 담아야 지문이 내용에 결속된다
    # (4회차 교차검증). before/after 수치만 담으면 같은 규정 안의 서로 다른
    # 강등이 같은 지문을 만들고, 한 번 받은 승인이 다른 변경에 재사용된다.
    # 실측: 조문 a 강등과 조문 b 강등이 둘 다 5dc4823b740b였다.
    demoted_by_lin: dict[str, list[str]] = {}
    for r in prev_rows:
        rid = str(r.get("record_id", ""))
        if rid and prev_cur.get(rid) and not new_cur.get(rid, False):
            demoted_by_lin.setdefault(lineage_of(r.get("규정명", "")), []).append(rid)
    current_losses = [{"계열": k, "before": pcc[k], "after": ncc.get(k, 0),
                       "severity": "current_zero" if ncc.get(k, 0) == 0
                                   else "current_drop",
                       "demoted_ids": sorted(demoted_by_lin.get(k, []))}
                      for k in sorted(pcc) if ncc.get(k, 0) < pcc[k]]
    # ★ 계열 합계만 보면 **형제 key의 상쇄**에 가려진다 (2026-09-02 code-review #5).
    #
    # 같은 규정명을 가진 key가 둘일 때(실측: 2826·3670 「전남대학교 공동지도교수제
    # 시행 지침」 각 5조문) 한쪽의 현행이 전멸해도 다른 쪽이 새 현행 행을 얻으면
    # 계열 합계가 그대로라 위험 0건·지문 없음으로 배포된다 — 규정 하나가 현행
    # 0건인 채로. 정상 개정과의 차이는 **신판이 새 key로 들어왔는가**다(이 학교는
    # 개정 시 신판에 새 key를 준다). 전멸한 key의 계열에 prev에 없던 key가 현행을
    # 갖고 나타났으면 정상 개정이고, 아니면 그 key의 소실을 따로 올린다.
    pck, nck = (_current_per_rule(prev_rows, prev_current),
                _current_per_rule(new_rows, new_current))
    new_names = _names(new_rows)
    successors: dict[str, set[str]] = {}
    for key, n in nck.items():
        if n > 0 and key not in pc:
            successors.setdefault(lineage_of(new_names.get(key, "")), set()).add(key)
    flagged = {l["계열"] for l in current_losses if l["severity"] == "current_zero"}
    demoted_by_key: dict[str, list[str]] = {}
    for r in prev_rows:
        rid = str(r.get("record_id", ""))
        if rid and prev_cur.get(rid) and not new_cur.get(rid, False):
            demoted_by_key.setdefault(str(r.get("source_key")), []).append(rid)
    for key in sorted(pck):
        if pck[key] == 0 or nck.get(key, 0) > 0:
            continue
        lin = lineage_of(names.get(key, ""))
        if lin in flagged or successors.get(lin):
            continue                        # 계열 단위로 이미 잡혔거나, 신판이 새 key로 왔다
        current_losses.append({"계열": lin, "source_key": key,
                               "before": pck[key], "after": 0,
                               "severity": "current_zero",
                               "demoted_ids": sorted(demoted_by_key.get(key, []))})
    # 개명은 위험이 아니라 참고 변화 — 다만 보고서에는 반드시 남긴다.
    renames = _renames(prev_rows, new_rows)
    risks = ([{"type": "article_loss", **l} for l in losses]
             + [{"type": "current_article_loss", **l} for l in current_losses]
             + [{"type": "current_promotion", **p} for p in promotions])
    # 지문은 **순서에 흔들리지 않아야** 한다(3회차 교차검증). promotions가
    # new_rows 순서를 그대로 담아, 같은 두 승격의 행 순서만 뒤집혀도 지문이
    # 달라졌다 — 재실행 드리프트와 실제 변경을 구별할 수 없게 만든다.
    # 지문은 **상태 전이 전체**에 결속한다(v3). risks는 사람이 읽는 요약이고,
    # 승인이 묶여야 하는 것은 「이 코퍼스에서 저 코퍼스로」라는 사실 자체다.
    # 위험이 없으면 지문도 없다 — 승인 절차 자체가 열리지 않기 때문이다.
    canonical = json.dumps(
        {"v": FINGERPRINT_VERSION,
         "prev": _state_digest(prev_rows, prev_cur),
         "new": _state_digest(new_rows, new_cur),
         "risks": sorted(risks, key=lambda r: json.dumps(
             r, ensure_ascii=False, sort_keys=True))},
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
            "현행_계열수": {"before": len(pcc), "after": len(ncc)},
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

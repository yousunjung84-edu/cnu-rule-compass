"""CNU 규정 나침반 MCP 도구 인터페이스.

직접 호출 가능한 함수에는 외부 의존성이 없다. 서버 실행 시에만 ``mcp``를
지연 임포트하므로 패키지가 없는 환경에서도 검색·테스트는 정상 동작한다.
"""

from __future__ import annotations

import json
import os
import sys
from importlib import metadata

from src.profile import active_profile
from src.search import get_default_index, tier_label


try:
    SERVER_VERSION = metadata.version("cnu-rule-compass")
except metadata.PackageNotFoundError:
    # 미설치(PYTHONPATH 직접 실행) 환경 폴백 — pyproject.toml [project].version과 동기.
    SERVER_VERSION = "1.9.5"

TOOL_NAMES = (
    "search_rule",
    "get_article",
    "get_article_as_of",
    "get_related_articles",
    "list_rules",
    "list_articles",
    "get_corpus_stats",
)
_REFERENCE_INDEX = None
MAX_QUERY_LENGTH = 500
_DATE_FORMAT = "YYYY-MM-DD"


def _log_usage(tool: str, **fields) -> None:
    """익명 사용 집계를 stdout에 구조화 JSON으로 남긴다 (2026-08-28, 박사 확정 (나)안).

    **질의 원문을 저장하지 않는다.** 질의는 교직원이 무엇을 몰라 찾았는지를 드러내고,
    인사·징계·연구년 같은 축이 섞이면 개인 추정이 가능하다. 발송한 운영지침의
    '개인정보 최우선' 원칙과 서버가 충돌하면 안 된다.

    `hints.query_terms_unmatched`도 싣지 않는다 — 진단에는 유용하지만 질의어의
    부분집합이라 이름·학번이 그대로 남는다. **개수만** 남긴다.

    남기는 것은 §D 피드백에 필요한 것뿐이다: 어떤 규정이 조회되나, not_found가
    몇 건이나, 어떤 advisory가 자주 뜨나.

    파일에 쓰지 않는다 — Cloud Run 파일시스템은 휘발성이라 재시작하면 사라진다.
    stdout은 Cloud Logging이 자동 수집한다.
    """
    if os.environ.get("RULECOMPASS_USAGE_LOG", "1").strip() == "0":
        return
    try:
        print(json.dumps({"usage": tool, **fields}, ensure_ascii=False), flush=True)
    except Exception:
        # 집계 실패가 도구 응답을 막지 않는다.
        pass


def _invalid_argument(field: str, message: str) -> dict:
    return {
        "status": "invalid_argument",
        "error": {"code": "invalid_argument", "field": field, "message": message},
    }


def search_rule(
    query: str,
    k: int = 5,
    include_superseded: bool = False,
    include_repealed: bool = False,
    include_attachments: bool = False,
) -> dict:
    """자연어 질의와 관련된 공식 규정 조문을 반환한다.

    기본값은 현행·유효 조문만이다. 구판본(is_current=false)이나 삭제된 조문
    (is_repealed=true)은 감사 대응 등 필요한 경우에만 include_* 로 켠다.

    별표(record_type="별표")는 기본 제외한다 — 본문이 조문의 수십 배라 응답을
    잠식한다(T27). 제외한 경우 attachments_omitted·attachments로 무엇이 빠졌는지
    함께 돌려주며, 전문은 get_article로 조회한다. include_attachments=True로 켜면
    검색 결과에 포함하되 본문은 앞 200자만 싣는다.
    """
    if not isinstance(query, str) or not query.strip():
        return _invalid_argument("query", "query는 비어 있지 않은 문자열이어야 합니다.")
    if len(query) > MAX_QUERY_LENGTH:
        return _invalid_argument("query", f"query는 {MAX_QUERY_LENGTH}자 이하여야 합니다.")
    if isinstance(k, bool) or not isinstance(k, int):
        return _invalid_argument("k", "k는 정수여야 합니다.")
    if not 1 <= k <= 20:
        return _invalid_argument("k", "k는 1 이상 20 이하여야 합니다.")
    for name, flag in (
        ("include_superseded", include_superseded),
        ("include_repealed", include_repealed),
        ("include_attachments", include_attachments),
    ):
        if not isinstance(flag, bool):
            return _invalid_argument(name, f"{name}은(는) 참/거짓이어야 합니다.")
    index = get_default_index()
    found = index.search_detailed(
        query,
        k=k,
        include_superseded=include_superseded,
        include_repealed=include_repealed,
        include_attachments=include_attachments,
    )
    results = found["results"]
    response = {
        "query": query,
        "count": len(results),
        "results": results,
        "status": "ok" if results else "not_found",
    }
    if found["attachments_omitted"]:
        # 조용히 빼지 않는다 — 별표에 답이 있는 질의였을 수 있다.
        response["attachments_omitted"] = found["attachments_omitted"]
        response["attachments"] = found["attachments"]
        response["attachments_note"] = (
            "관련도 상위에 별표가 있었으나 본문이 길어 결과에서 제외했습니다. "
            "attachments의 record_id로 get_article을 호출하면 전문을 조회할 수 있습니다."
        )
    advisories = _build_advisories(results)
    if advisories:
        response["advisories"] = advisories
    if len(results) <= 1:
        # 결과가 없거나 빈약할 때, 그것이 '코퍼스에 개념 부재'인지 '검색 실패'인지
        # 소비자가 구별할 수 있게 근거를 준다 (T7).
        from src.search import unmatched_query_terms

        unmatched = unmatched_query_terms(query, index)
        response["hints"] = {
            "query_terms_unmatched": unmatched,
            "suggest": "no_such_concept" if unmatched else "cross_reference",
            "note": (
                "질의어가 코퍼스 어휘에 없습니다. 해당 개념을 다루는 규정이 수집 범위에 없을 수 있습니다."
                if unmatched
                else "어휘는 코퍼스에 있으나 결과가 빈약합니다. get_related_articles로 상호참조를 따라가 보세요."
            ),
        }
    _log_usage(
        "search_rule",
        status=response["status"],
        count=len(results),
        # 규정명은 공개 규정 이름이라 개인 추정에 쓰이지 않는다. 어떤 규정이
        # 자주 조회되는지가 §D 피드백의 핵심 신호다.
        rules=sorted({str(row.get("규정명", "")) for row in results})[:5],
        advisories=[a.get("code") for a in response.get("advisories", [])],
        attachments_omitted=response.get("attachments_omitted", 0),
        # 질의어는 싣지 않는다. 못 찾은 단어의 **개수**만 남긴다.
        unmatched_terms=len((response.get("hints") or {}).get("query_terms_unmatched") or []),
    )
    return response


def _build_advisories(results: list[dict]) -> list[dict]:
    """결과를 보고 **소비자가 밟아야 할 다음 수**를 응답에 실어 보낸다.

    지금까지 회차마다 발견한 함정을 소비자 스킬 문서에 적어 왔다. 그 방식은
    두 가지로 새는데, (1) 코퍼스가 바뀌면 문서가 먼저 틀리고 (2) 문서를 읽지 않은
    소비자에게는 전달되지 않는다. 함정이 **결과에서 판정 가능한 것**이라면
    문서가 아니라 응답이 알리는 편이 정확하다 — hints·attachments_omitted가
    그랬듯이. 여기 실리는 것은 판단이 아니라 신호다.
    """
    advisories: list[dict] = []

    # ① 세부지침(항목식)이 상위에 오면 권한 근거는 상위 규범에 있다.
    #    '재입학' 질의가 세부지침으로 채워지고 학칙 제30조가 밀려난 사례
    #    (v1.8.0). 세부지침만 인용하면 근거를 빠뜨린 답이 된다.
    items = [row for row in results if row.get("record_type") == "항목"]
    if items:
        index = _reference_index()
        upstream: list[dict] = []
        seen: set[tuple] = set()
        for row in items:
            record = index.get(row.get("record_id", ""))
            if record is None:
                continue
            for entry in index.outbound(record, resolve=False)[0]:
                if entry["kind"] != "cross_rule" or not entry["resolved"]:
                    continue
                key = (entry["target_rule"], entry["target_article"])
                if key in seen:
                    continue
                seen.add(key)
                upstream.append({
                    "규정명": entry["target_rule"],
                    "조문번호": entry["target_article"],
                    "record_id": entry["record_id"],
                    "인용한_항목": f'{row["규정명"]} {row["조문번호"]}',
                })
        advisories.append({
            "code": "upstream_norm_check",
            "message": (
                "결과에 세부지침(항목식)이 포함되어 있습니다. 세부지침은 절차를 정하고 "
                "권한의 근거는 상위 규범(학칙·규정)에 있습니다. upstream의 조문을 함께 "
                "확인해 근거와 절차를 같이 제시하세요."
                if upstream
                else "결과에 세부지침(항목식)이 포함되어 있습니다. 상위 규범을 "
                     "get_related_articles로 확인하세요."
            ),
            "upstream": upstream[:8],
        })

    # ② 조문제목이 같은 레코드가 섞이면 제목만으로 구별할 수 없다.
    #    교육혁신본부 운영 지침은 제2~5조 제목이 모두 '업무'이고 센터 구분은
    #    장 필드에만 있다 — 제목으로 인용하면 다른 센터를 지목하게 된다.
    titles: dict[tuple, list[dict]] = {}
    for row in results:
        title = str(row.get("조문제목", "")).strip()
        if title:
            titles.setdefault((row.get("규정명"), title), []).append(row)
    ambiguous = [
        {
            "규정명": rule,
            "조문제목": title,
            "조문": [
                {"조문번호": r.get("조문번호"), "장": r.get("장"), "절": r.get("절")}
                for r in rows
            ],
        }
        for (rule, title), rows in titles.items() if len(rows) > 1
    ]
    if ambiguous:
        advisories.append({
            "code": "duplicate_article_title",
            "message": (
                "같은 규정 안에 조문제목이 동일한 조문이 여럿 있습니다. 제목으로 인용하면 "
                "다른 조문을 지목하게 됩니다. 조문번호와 장/절을 함께 밝히세요."
            ),
            "items": ambiguous[:5],
        })

    # ③ 구판본이 섞였을 때(include_superseded=true) 현행으로 오인하지 않게 한다.
    superseded = [
        {
            "규정명": row.get("규정명"),
            "조문번호": row.get("조문번호"),
            "superseded_by": row.get("superseded_by"),
        }
        for row in results if not row.get("is_current", True)
    ]
    if superseded:
        advisories.append({
            "code": "superseded_included",
            "message": (
                "구판본 조문이 결과에 포함되어 있습니다(include_superseded). 현행 근거로 "
                "인용하지 말고, 인용이 필요하면 '○○ 시점 판본'임을 답변에 명시하세요."
            ),
            "items": superseded[:5],
        })
    return advisories


def get_article(rule_name: str, article_no: str, record_id: str | None = None) -> dict:
    """규정명·조문번호로 단일 공식 레코드를 반환하며 record_id로 판본을 지정한다."""
    if not isinstance(rule_name, str) or not rule_name.strip() or len(rule_name) > 200:
        return _invalid_argument("rule_name", "rule_name은 1자 이상 200자 이하 문자열이어야 합니다.")
    if not isinstance(article_no, str) or not article_no.strip() or len(article_no) > 50:
        return _invalid_argument("article_no", "article_no는 1자 이상 50자 이하 문자열이어야 합니다.")
    if record_id is not None and (not isinstance(record_id, str) or len(record_id) > 100):
        return _invalid_argument("record_id", "record_id는 100자 이하 문자열이어야 합니다.")
    name = str(rule_name).strip()
    number = str(article_no).strip().replace(" ", "")
    search_index = get_default_index()
    matches = [
        dict(article)
        for article in search_index.articles
        if article.get("규정명", "").strip() == name
        and str(article.get("조문번호", "")).replace(" ", "") == number
        and (record_id is None or article.get("record_id") == record_id)
    ]
    matches.sort(
        key=lambda article: (
            # 현행을 먼저 고른다. 이 키가 없어 revision 문자열 순서로 골랐고,
            # 공동지도교수제 시행 지침 제1·2·3·5조가 **구판으로 응답**됐다
            # (2026-08-28 확인, 현행·구판 혼재 5쌍 중 4쌍). v1.9.2에서
            # _annotate_versions가 같은 이름 복수 source_key를 강등하도록 고쳤는데,
            # 이 도구가 그 판정을 읽지 않고 있었다.
            # record_id를 명시한 호출은 위에서 이미 걸러지므로 영향받지 않는다.
            not article.get("is_current", True),
            article.get("record_type") != "본칙",
            str(article.get("revision", "")),
            str(article.get("record_id", "")),
        )
    )
    article = matches[0] if matches else None
    _log_usage(
        "get_article",
        status="ok" if article else "not_found",
        # 규정명·조문번호는 공개 식별자다. 질의 원문이 아니라 도달한 대상을 남긴다.
        rule=name if article else None,
        article_no=number if article else None,
        is_current=bool(article.get("is_current", True)) if article else None,
        by_record_id=record_id is not None,
    )
    return {
        "규정명": name,
        "조문번호": str(article_no).strip(),
        "record_id": article.get("record_id") if article else record_id,
        "revision": article.get("revision") if article else None,
        "article": article,
        "status": "ok" if article else "not_found",
    }


def get_article_as_of(rule_name: str, date: str, keyword: str | None = None) -> dict:
    """지정 날짜에 유효했던 판본의 조문을 반환한다(개정 계열 수집 규정 한정).

    감사 대응처럼 '그 업무 당시 유효 규정'이 필요한 질의를 위한 시점 질의 도구다.
    """
    if not isinstance(rule_name, str) or not rule_name.strip() or len(rule_name) > 200:
        return _invalid_argument("rule_name", "rule_name은 1자 이상 200자 이하 문자열이어야 합니다.")
    if not isinstance(date, str) or len(date) != 10:
        return _invalid_argument("date", f"date는 {_DATE_FORMAT} 형식이어야 합니다.")
    if keyword is not None and (not isinstance(keyword, str) or len(keyword) > MAX_QUERY_LENGTH):
        return _invalid_argument("keyword", f"keyword는 {MAX_QUERY_LENGTH}자 이하 문자열이어야 합니다.")
    from src.lineage import get_default_lineage

    lineage = get_default_lineage()
    resolved = lineage.resolve_rule(rule_name.strip())
    if resolved is None:
        # 오확정된 규정의 조문을 유효 판본으로 인용하지 않도록, 미해결·모호
        # 질의는 확정하지 않고 수집된 계열 목록을 함께 돌려준다.
        return {
            "status": "not_found",
            "reason": "질의로 개정 계열 규정을 확정할 수 없습니다. known_rules에서 규정명을 지정해 다시 질의하세요.",
            "rule": rule_name.strip(),
            "date": date.strip(),
            "known_rules": lineage.rule_names,
        }
    try:
        return lineage.articles_as_of(resolved, date.strip(), keyword=keyword)
    except ValueError as exc:
        return _invalid_argument("date", str(exc))


def list_rules(division: str | None = None, include_superseded: bool = False) -> dict:
    """수집된 규정 목록을 반환한다 (T6 — 정적 코퍼스 지도를 대체한다)."""
    if division is not None and (not isinstance(division, str) or len(division) > 100):
        return _invalid_argument("division", "division은 100자 이하 문자열이어야 합니다.")
    if not isinstance(include_superseded, bool):
        return _invalid_argument("include_superseded", "include_superseded는 참/거짓이어야 합니다.")

    grouped: dict[str, dict] = {}
    for row in get_default_index().articles:
        if not include_superseded and not row.get("is_current", True):
            continue
        if division and division not in str(row.get("편제", "")):
            continue
        entry = grouped.setdefault(row["규정명"], {
            "규정명": row["규정명"],
            "편제": row.get("편제"),
            "source_key": row.get("source_key"),
            "계층": tier_label(row),
            "조문_수": 0,
            "수집일시": row.get("수집일시"),
            "is_current": row.get("is_current", True),
        })
        entry["조문_수"] += 1
    rules = sorted(grouped.values(), key=lambda row: (str(row["편제"]), row["규정명"]))
    return {"count": len(rules), "rules": rules, "status": "ok" if rules else "not_found"}


_ARTICLE_NO_RE = None


def _article_sort_key(article_no: str) -> tuple[int, int]:
    """'제11조의2'가 제11조와 제12조 사이에 오도록 정렬한다."""
    import re as _re

    match = _re.match(r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?", str(article_no))
    if not match:
        return (10**6, 0)
    return (int(match.group(1)), int(match.group(2) or 0))


def list_articles(
    rule_name: str,
    include_repealed: bool = False,
    include_supplementary: bool = False,
) -> dict:
    """한 규정의 조문 목차를 전수 반환한다 (T20).

    search_rule은 관련도 상위 k건만 주므로 '전 조문을 확인했다'는 주장의 근거가 될 수
    없다. 실제로 그 주장을 했다가 학생 징계 규정 제14조(준용 — 교수회의 트랙)를
    통째로 빠뜨린 사고가 있었다. 본문은 넣지 않는다(토큰 절약) — 필요한 조문은
    get_article로 가져간다.
    """
    if not isinstance(rule_name, str) or not rule_name.strip() or len(rule_name) > 200:
        return _invalid_argument("rule_name", "rule_name은 1자 이상 200자 이하 문자열이어야 합니다.")
    for name, flag in (("include_repealed", include_repealed), ("include_supplementary", include_supplementary)):
        if not isinstance(flag, bool):
            return _invalid_argument(name, f"{name}은(는) 참/거짓이어야 합니다.")

    target = rule_name.strip()
    rows = [row for row in get_default_index().articles if row.get("규정명") == target]
    if not rows:
        return {
            "규정명": target,
            "status": "not_found",
            "reason": "해당 규정명의 조문이 코퍼스에 없습니다. list_rules로 정확한 규정명을 확인하세요.",
        }

    supplementary = [row for row in rows if row.get("record_type") == "부칙"]
    main = [row for row in rows if row.get("record_type") != "부칙"]
    selected = main + (supplementary if include_supplementary else [])
    if not include_repealed:
        selected = [row for row in selected if not row.get("is_repealed")]
    selected.sort(key=lambda row: (
        row.get("record_type") == "부칙", _article_sort_key(row.get("조문번호", ""))
    ))
    return {
        "규정명": target,
        "source_key": rows[0].get("source_key"),
        "편제": rows[0].get("편제"),
        "조문_수": len([r for r in main if include_repealed or not r.get("is_repealed")]),
        "부칙_수": len(supplementary),
        "수집일시": rows[0].get("수집일시"),
        "articles": [
            {
                "조문번호": row.get("조문번호"),
                "조문제목": row.get("조문제목"),
                "record_id": row.get("record_id"),
                "record_type": row.get("record_type"),
                "장": row.get("장"),
                "절": row.get("절"),
                "is_repealed": row.get("is_repealed", False),
            }
            for row in selected
        ],
        "status": "ok",
    }


def _coverage_summary() -> dict:
    """게시 목록 대비 수집률 요약을 읽어온다 (scripts/coverage_report.py 산출물)."""
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "data" / "coverage_report.json"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    # 공백 신호는 **현행 기준**으로만 낸다. 구판본 미수집은 답변 품질과 무관하고
    # (is_current=false라 기본 검색에서 빠진다), 섞으면 경고가 잡음이 된다.
    gaps = [
        f"{entry['편제']}({entry['현행_수집']}/{entry['현행_게시']})"
        for entry in report.get("편제별", [])
        if entry.get("현행_수집률") is not None and entry["현행_수집률"] < 1
    ]
    return {
        "수집_범위_기준일": report.get("수집_범위_기준일"),
        "대상_대비_수집률": report.get("대상_대비_수집률"),
        "현행_대비_수집률": report.get("현행_대비_수집률"),
        "게시_규정_수": report.get("게시_규정_수"),
        "현행_게시_규정_수": report.get("현행_게시_규정_수"),
        "수집_공백_편제": gaps,
        "수집_범위_설명": (
            "게시 목록 대비 수집률입니다. 규정·지침 두 계층 모두 게시 전량을 대상으로 하며, "
            "미수집분은 대부분 조문(제N조) 구조가 없는 항목식 문서입니다. "
            "공백 편제의 질의는 '규정 없음'이 아니라 '수집 범위 밖'일 수 있습니다."
        ),
    }


def get_corpus_stats() -> dict:
    """코퍼스 규모·분포와 색인 정합성을 반환한다 (T1 재발 감시용)."""
    index = get_default_index()
    articles = index.articles
    divisions: dict[str, int] = {}
    for row in articles:
        divisions[str(row.get("편제", ""))] = divisions.get(str(row.get("편제", "")), 0) + 1
    collected = sorted(str(row.get("수집일시", "")) for row in articles if row.get("수집일시"))
    indexed = len(index._term_frequencies)
    # 적재 게이트에서 제외된 레코드의 사유별 내역 (T12).
    # '색인에서 빠진 24건'이 T1 재발 통로인지 구별할 수 있어야 한다.
    excluded: dict[str, int] = {}
    for row in index.rejected_articles:
        excluded[row["reason"]] = excluded.get(row["reason"], 0) + 1
    # 의도된 제외: 빈 본문·중복 레코드·본문 초과·비공식 URL. 그 밖은 사고로 본다.
    intended = {"empty_body", "duplicate_record", "oversized_body", "invalid_source_url"}
    unintended = {k: v for k, v in excluded.items() if k not in intended}
    stats = {
        "규정_수": len({row["규정명"] for row in articles}),
        "조문_수": len(articles),
        "색인_문서_수": indexed,
        # 구 필드명 — 한 릴리스 동안 병기한다(v1.2.0 도입, v1.4.0에서 제거 예정)
        "제외_레코드_수": len(index.rejected_articles),
        "적재제외_레코드_수": len(index.rejected_articles),
        "적재제외_사유별": dict(sorted(excluded.items(), key=lambda kv: -kv[1])),
        "적재제외_설명": (
            "적재 게이트가 걸러낸 레코드 수입니다. 색인 누락이 아니라 의도된 제외이며, "
            "조문_수·색인_문서_수에는 처음부터 포함되지 않습니다."
        ),
        "편제별_분포": dict(sorted(divisions.items(), key=lambda kv: -kv[1])),
        "최초_수집일시": collected[0] if collected else None,
        "최신_수집일시": collected[-1] if collected else None,
        "구판본_조문_수": sum(1 for row in articles if not row.get("is_current", True)),
        "삭제_조문_수": sum(1 for row in articles if row.get("is_repealed", False)),
        "문자손상_조문_수": sum(1 for row in articles if row.get("text_integrity")),
        "별표_수": sum(1 for row in articles if row.get("record_type") == "별표"),
        "status": "ok",
    }
    # 분모를 밝힌다 (T30). 규정 수만 보면 그것이 전체의 몇 %인지 알 수 없어,
    # '검색이 못 찾은 것'과 '애초에 수집하지 않은 것'을 구별할 수 없다.
    coverage = _coverage_summary()
    if coverage:
        stats.update(coverage)
    if indexed != len(articles):
        stats["warning"] = f"색인({indexed})과 레코드({len(articles)}) 수가 다릅니다 — 색인 재빌드 필요"
    if unintended:
        stats["warning_excluded"] = f"의도치 않은 제외 사유: {unintended} — T1 절차로 재색인 필요"
    return stats


def _reference_index():
    """참조 그래프는 만드는 비용이 있어 프로세스당 한 번만 만든다."""
    global _REFERENCE_INDEX
    if _REFERENCE_INDEX is None:
        from src.references import ReferenceIndex

        _REFERENCE_INDEX = ReferenceIndex(get_default_index().articles)
    return _REFERENCE_INDEX


def get_related_articles(
    record_id: str, direction: str = "outbound", resolve: bool = True
) -> dict:
    """조문이 인용하는(또는 조문을 인용하는) 다른 조문을 반환한다.

    감사·행정 문의는 조문 하나로 끝나지 않고 인용 사슬을 따라가야 하는 경우가 많다.
    해소하지 못한 참조는 버리지 않고 unresolved에 사유와 함께 담는다.
    """
    if not isinstance(record_id, str) or not record_id.strip() or len(record_id) > 100:
        return _invalid_argument("record_id", "record_id는 1자 이상 100자 이하 문자열이어야 합니다.")
    if direction not in {"outbound", "inbound", "both"}:
        return _invalid_argument("direction", "direction은 outbound, inbound, both 중 하나여야 합니다.")
    if not isinstance(resolve, bool):
        return _invalid_argument("resolve", "resolve는 참/거짓이어야 합니다.")

    index = _reference_index()
    record = index.get(record_id.strip())
    if record is None:
        return {
            "record_id": record_id.strip(),
            "status": "not_found",
            "reason": "해당 record_id의 조문이 코퍼스에 없습니다.",
        }

    outbound: list[dict] = []
    unresolved: list[dict] = []
    inbound: list[dict] = []
    if direction in {"outbound", "both"}:
        outbound, unresolved = index.outbound(record, resolve=resolve)
    if direction in {"inbound", "both"}:
        inbound = index.inbound(record_id.strip(), resolve=resolve)
    return {
        "record_id": record_id.strip(),
        "규정명": record.get("규정명"),
        "조문번호": record.get("조문번호"),
        "outbound": outbound,
        "inbound": inbound,
        "unresolved": unresolved,
        "status": "ok",
    }


def create_server(**settings):
    """FastMCP 서버를 만들며, 패키지가 없으면 설치 안내 오류를 낸다.

    settings는 FastMCP 생성자로 전달된다 (HTTP 진입점의 host/port/보안 설정용).
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError(
            "mcp 패키지가 없습니다. MCP 서버 실행 환경에 'mcp'를 설치한 뒤 "
            "python -m src.mcp_server를 실행하세요."
        ) from exc

    server = FastMCP(active_profile().display_name, **settings)
    # FastMCP는 version 인자를 받지 않아(SDK 1.28 기준) serverInfo.version이
    # SDK 버전으로 보고된다 — 하위 서버에 프로젝트 버전을 직접 지정한다.
    server._mcp_server.version = SERVER_VERSION
    server.tool(name="search_rule")(search_rule)
    server.tool(name="get_article")(get_article)
    server.tool(name="get_article_as_of")(get_article_as_of)
    server.tool(name="get_related_articles")(get_related_articles)
    server.tool(name="list_rules")(list_rules)
    server.tool(name="list_articles")(list_articles)
    server.tool(name="get_corpus_stats")(get_corpus_stats)
    return server


def main() -> int:
    try:
        # 서버를 열기 전에 코퍼스 스키마·URL·본문 게이트를 통과시킨다.
        get_default_index()
        server = create_server()
    except Exception as exc:
        # stdio 전송에서 stdout은 JSON-RPC 전용 — 진단은 stderr로 보낸다.
        print(f"[오류] {exc}", file=sys.stderr)
        return 1
    try:
        server.run()
    except Exception as exc:
        print(f"[오류] MCP 서버 실행 실패: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""CNU 규정 나침반 MCP 도구 인터페이스.

직접 호출 가능한 함수에는 외부 의존성이 없다. 서버 실행 시에만 ``mcp``를
지연 임포트하므로 패키지가 없는 환경에서도 검색·테스트는 정상 동작한다.
"""

from __future__ import annotations

import sys
from importlib import metadata

from src.search import get_default_index


try:
    SERVER_VERSION = metadata.version("cnu-rule-compass")
except metadata.PackageNotFoundError:
    # 미설치(PYTHONPATH 직접 실행) 환경 폴백 — pyproject.toml [project].version과 동기.
    SERVER_VERSION = "1.3.0"

TOOL_NAMES = (
    "search_rule",
    "get_article",
    "get_article_as_of",
    "get_related_articles",
    "list_rules",
    "get_corpus_stats",
)
_REFERENCE_INDEX = None
MAX_QUERY_LENGTH = 500
_DATE_FORMAT = "YYYY-MM-DD"


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
) -> dict:
    """자연어 질의와 관련된 공식 규정 조문을 반환한다.

    기본값은 현행·유효 조문만이다. 구판본(is_current=false)이나 삭제된 조문
    (is_repealed=true)은 감사 대응 등 필요한 경우에만 include_* 로 켠다.
    """
    if not isinstance(query, str) or not query.strip():
        return _invalid_argument("query", "query는 비어 있지 않은 문자열이어야 합니다.")
    if len(query) > MAX_QUERY_LENGTH:
        return _invalid_argument("query", f"query는 {MAX_QUERY_LENGTH}자 이하여야 합니다.")
    if isinstance(k, bool) or not isinstance(k, int):
        return _invalid_argument("k", "k는 정수여야 합니다.")
    if not 1 <= k <= 20:
        return _invalid_argument("k", "k는 1 이상 20 이하여야 합니다.")
    for name, flag in (("include_superseded", include_superseded), ("include_repealed", include_repealed)):
        if not isinstance(flag, bool):
            return _invalid_argument(name, f"{name}은(는) 참/거짓이어야 합니다.")
    index = get_default_index()
    results = index.search(
        query, k=k, include_superseded=include_superseded, include_repealed=include_repealed
    )
    response = {
        "query": query,
        "count": len(results),
        "results": results,
        "status": "ok" if results else "not_found",
    }
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
    return response


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
            article.get("record_type") != "본칙",
            str(article.get("revision", "")),
            str(article.get("record_id", "")),
        )
    )
    article = matches[0] if matches else None
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
            "계층": "규정" if str(row.get("편제", "")).startswith("규정집/") else "지침",
            "조문_수": 0,
            "수집일시": row.get("수집일시"),
            "is_current": row.get("is_current", True),
        })
        entry["조문_수"] += 1
    rules = sorted(grouped.values(), key=lambda row: (str(row["편제"]), row["규정명"]))
    return {"count": len(rules), "rules": rules, "status": "ok" if rules else "not_found"}


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
        "status": "ok",
    }
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

    server = FastMCP("CNU 규정 나침반", **settings)
    # FastMCP는 version 인자를 받지 않아(SDK 1.28 기준) serverInfo.version이
    # SDK 버전으로 보고된다 — 하위 서버에 프로젝트 버전을 직접 지정한다.
    server._mcp_server.version = SERVER_VERSION
    server.tool(name="search_rule")(search_rule)
    server.tool(name="get_article")(get_article)
    server.tool(name="get_article_as_of")(get_article_as_of)
    server.tool(name="get_related_articles")(get_related_articles)
    server.tool(name="list_rules")(list_rules)
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

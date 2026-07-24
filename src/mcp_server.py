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
    SERVER_VERSION = "1.0.0"

TOOL_NAMES = ("search_rule", "get_article", "get_article_as_of")
MAX_QUERY_LENGTH = 500
_DATE_FORMAT = "YYYY-MM-DD"


def _invalid_argument(field: str, message: str) -> dict:
    return {
        "status": "invalid_argument",
        "error": {"code": "invalid_argument", "field": field, "message": message},
    }


def search_rule(query: str, k: int = 5) -> dict:
    """자연어 질의와 관련된 공식 규정 조문을 반환한다."""
    if not isinstance(query, str) or not query.strip():
        return _invalid_argument("query", "query는 비어 있지 않은 문자열이어야 합니다.")
    if len(query) > MAX_QUERY_LENGTH:
        return _invalid_argument("query", f"query는 {MAX_QUERY_LENGTH}자 이하여야 합니다.")
    if isinstance(k, bool) or not isinstance(k, int):
        return _invalid_argument("k", "k는 정수여야 합니다.")
    if not 1 <= k <= 20:
        return _invalid_argument("k", "k는 1 이상 20 이하여야 합니다.")
    results = get_default_index().search(query, k=k)
    return {
        "query": query,
        "count": len(results),
        "results": results,
        "status": "ok" if results else "not_found",
    }


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


def create_server():
    """FastMCP 서버를 만들며, 패키지가 없으면 설치 안내 오류를 낸다."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError(
            "mcp 패키지가 없습니다. MCP 서버 실행 환경에 'mcp'를 설치한 뒤 "
            "python -m src.mcp_server를 실행하세요."
        ) from exc

    server = FastMCP("CNU 규정 나침반")
    # FastMCP는 version 인자를 받지 않아(SDK 1.28 기준) serverInfo.version이
    # SDK 버전으로 보고된다 — 하위 서버에 프로젝트 버전을 직접 지정한다.
    server._mcp_server.version = SERVER_VERSION
    server.tool(name="search_rule")(search_rule)
    server.tool(name="get_article")(get_article)
    server.tool(name="get_article_as_of")(get_article_as_of)
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

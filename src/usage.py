"""익명 사용 집계 — **전남대 운영본만의 관심사**다 (2026-09-06 박사 결정 B).

코어로 옮기지 않은 이유: 옮기면 확산 9개교까지 집계 대상이 되는데, 이 로그를
읽어 분석하는 코드가 아직 어디에도 없다. 「§D 피드백의 핵심 신호」라고 적혀
있지만 그 피드백을 돌리는 것이 없다 — 소비처가 생기기 전에는 Cloud Logging
비용만 는다. 그래서 코어는 순수하게 두고, 여기가 얹는다.

얹는 방법은 `core.create_server(tool_wrapper=...)` 훅이다(코어 0.6.1 신설).
도구 **목록**은 코어가 계속 소유하므로 코어에 도구가 늘어도 전남대가 따라간다.
adapter가 7종을 직접 등록하는 방식을 쓰지 않은 이유가 그것이다.

★ 이 파일은 이식본이다. 원본은 삭제된 `src/mcp_server.py` 안에서 각 도구 함수
**내부**가 호출하던 `_log_usage`였다. 밖에서 감싸면 지역 변수를 못 보므로 두
곳을 반환값·호출인자로 다시 계산하는데, 여기에 **함정이 둘** 있다. 둘 다
삭제 전에 실측으로 떠서(2026-09-07 baseline 20케이스) tests/test_usage_adapter.py가
글자 단위로 잠근다.

  1) get_article의 집계 article_no는 `.replace(" ", "")`를 **거친 값**이고
     응답의 `조문번호`는 거치지 않은 값이다. 응답에서 읽으면 「제 37 조」가
     그대로 실려 baseline과 갈린다.
  2) invalid_argument로 조기 반환한 호출은 **집계를 남기지 않았다.** 반환값만
     보고 무조건 집계하면 없던 로그가 생긴다.
"""

from __future__ import annotations

import functools
import inspect
import json
import os
import sys

# 집계를 실을 도구. 나머지 5종은 원본에서도 집계하지 않았다 —
# 여기 이름을 늘리는 것은 새 신호를 켜는 결정이라 조용히 하지 않는다.
LOGGED_TOOLS = frozenset({"search_rule", "get_article"})

# 스트림 **객체**가 아니라 이름을 들고 있는다. 객체를 잡아 두면 import 시점의
# sys.stderr에 묶여, 나중에 스트림을 갈아끼운 쪽(테스트의 redirect_stderr,
# 로깅 래퍼)에 글이 가지 않는다 — 쓰는 시점에 조회해야 맞다.
_USAGE_CHANNEL = "stderr"


def use_stdout_for_usage() -> None:
    """HTTP 전송에서만 집계를 stdout으로 보낸다 (Cloud Logging 수집 대상).

    ★ 기본은 stderr다(2026-09-01 전송 게이트에서 실측 후 변경).

    stdio 전송에서 stdout은 **JSON-RPC 전용 채널**이다. 집계를 stdout에 쓰면
    도구 호출마다 비-JSON-RPC 라인이 끼어 스트림이 한 줄씩 밀린다 — 로컬
    클라이언트(Claude Desktop·Claude Code MCP 설정)는 첫 호출 이후 응답을
    잘못 읽는다. 실측: tools/call 1회에 stdout 첫 줄이 {"usage": ...}였다.

    Cloud Run 운영에는 영향이 없다 — HTTP 전송이라 이 함수가 호출된다.
    """
    global _USAGE_CHANNEL
    _USAGE_CHANNEL = "stdout"


def use_stderr_for_usage() -> None:
    """테스트가 전송 기본값으로 되돌릴 때 쓴다."""
    global _USAGE_CHANNEL
    _USAGE_CHANNEL = "stderr"


def _usage_stream():
    return sys.stdout if _USAGE_CHANNEL == "stdout" else sys.stderr


def log_usage(tool: str, **fields) -> None:
    """익명 사용 집계를 구조화 JSON 한 줄로 남긴다 (2026-08-28, 박사 확정 (나)안).

    **질의 원문을 저장하지 않는다.** 질의는 교직원이 무엇을 몰라 찾았는지를
    드러내고, 인사·징계·연구년 같은 축이 섞이면 개인 추정이 가능하다. 발송한
    운영지침의 '개인정보 최우선' 원칙과 서버가 충돌하면 안 된다.

    `hints.query_terms_unmatched`도 싣지 않는다 — 진단에는 유용하지만 질의어의
    부분집합이라 이름·학번이 그대로 남는다. **개수만** 남긴다.

    파일에 쓰지 않는다 — Cloud Run 파일시스템은 휘발성이라 재시작하면 사라진다.
    """
    if os.environ.get("RULECOMPASS_USAGE_LOG", "1").strip() == "0":
        return
    try:
        print(json.dumps({"usage": tool, **fields}, ensure_ascii=False),
              file=_usage_stream(), flush=True)
    except Exception:
        # 집계 실패가 도구 응답을 막지 않는다.
        pass


def _search_fields(response: dict) -> dict:
    """search_rule 집계 6필드. 전부 응답에서 재계산할 수 있다."""
    results = response.get("results") or []
    return {
        "status": response.get("status"),
        "count": len(results),
        # 규정명은 공개 규정 이름이라 개인 추정에 쓰이지 않는다. 어떤 규정이
        # 자주 조회되는지가 §D 피드백의 핵심 신호다.
        "rules": sorted({str(row.get("규정명", "")) for row in results})[:5],
        "advisories": [a.get("code") for a in response.get("advisories", [])],
        "attachments_omitted": response.get("attachments_omitted", 0),
        # 질의어는 싣지 않는다. 못 찾은 단어의 **개수**만 남긴다.
        "unmatched_terms": len(
            (response.get("hints") or {}).get("query_terms_unmatched") or []),
    }


def _get_article_fields(response: dict, bound: dict) -> dict:
    """get_article 집계 5필드.

    ⚠️ `article_no`는 응답의 `조문번호`가 아니라 **호출 인자를 원본과 같은
    규칙으로 정규화한 값**이다. 원본은 `str(article_no).strip().replace(" ", "")`로
    매칭 키를 만들고 그것을 집계에 실었다. 응답의 `조문번호`는 `.strip()`까지만
    거치므로 「제 37 조」 같은 입력에서 갈린다 (baseline 실측).
    """
    article = response.get("article")
    return {
        "status": "ok" if article else "not_found",
        # 규정명·조문번호는 공개 식별자다. 질의 원문이 아니라 도달한 대상을 남긴다.
        "rule": str(bound.get("rule_name", "")).strip() if article else None,
        "article_no": (str(bound.get("article_no", "")).strip().replace(" ", "")
                       if article else None),
        "is_current": bool(article.get("is_current", True)) if article else None,
        "by_record_id": bound.get("record_id") is not None,
    }


def _bind(fn, args, kwargs, sig=None) -> dict:
    """호출 인자를 이름 기준으로 정규화한다.

    도구는 위치로도 키워드로도 불린다(MCP 클라이언트는 키워드, 테스트는 위치).
    functools.wraps가 __wrapped__를 세워 두므로 inspect.signature가 **원본**
    시그니처를 준다 — 래퍼의 (*args, **kwargs)가 아니다.
    """
    try:
        bound = (sig or inspect.signature(fn)).bind(*args, **kwargs)
        bound.apply_defaults()
        return dict(bound.arguments)
    except TypeError:
        # 인자가 안 맞으면 도구가 스스로 오류를 낸다. 집계는 비켜선다.
        return {}


def wrap(name: str, fn):
    """core.create_server(tool_wrapper=...)에 넘기는 훅.

    집계 대상이 아닌 도구는 fn을 **그대로** 돌려준다 — 감싸면 그만큼 프레임이
    늘 뿐이고, 원본도 5종에는 집계를 걸지 않았다.
    """
    if name not in LOGGED_TOOLS:
        return fn

    sig = inspect.signature(fn)  # 시그니처는 wrap 시점에 고정 — 매 호출 reflection 불필요

    @functools.wraps(fn)  # ★ 빼면 FastMCP가 입력 스키마를 args/kwargs로 만든다
    def inner(*args, **kwargs):
        response = fn(*args, **kwargs)
        try:
            if isinstance(response, dict) and response.get("status") != "invalid_argument":
                if name == "search_rule":
                    log_usage(name, **_search_fields(response))
                else:
                    bound = _bind(fn, args, kwargs, sig)
                    # 바인딩이 안 되면 호출 인자를 모르는 것이다 — rule=""로 적느니
                    # 안 적는다(리뷰 지적: 성공 응답에 빈 식별자가 남던 경로).
                    if bound:
                        log_usage(name, **_get_article_fields(response, bound))
        except Exception:
            # 집계 실패가 도구 응답을 막지 않는다 — log_usage와 같은 계약이다.
            pass
        return response

    return inner

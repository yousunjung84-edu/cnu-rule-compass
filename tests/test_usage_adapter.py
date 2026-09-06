"""사용집계 adapter 회귀 — 이식으로 값이 갈리지 않았음을 잠근다 (2026-09-07, P04).

배경: 집계는 원래 `src/mcp_server.py`의 각 도구 함수 **안**에서 지역 변수를 보고
찍혔다. 코어 전환으로 엔진이 wheel로 빠지면서, adapter가 도구를 **밖에서**
감싸 반환값·호출인자로 같은 값을 다시 만든다. 그 과정에서 조용히 갈릴 수 있는
지점이 있어서, 삭제 **전에** 실측 baseline 20케이스를 떠서 대조했다(불일치 0).
이 파일은 그 대조를 회귀로 고정한다.

★ baseline에서 확인한 함정 둘 (지어낸 것이 아니라 실행에서 나왔다):

  1) get_article 집계의 article_no는 `.replace(" ", "")`를 **거친 값**이다.
     응답의 `조문번호`는 `.strip()`까지만 거친다. 「제 37 조」로 부르면
     집계는 "제37조", 응답은 "제 37 조" — 응답에서 읽으면 갈린다.
  2) invalid_argument로 조기 반환한 호출은 집계를 **남기지 않았다.**
     반환값만 보고 무조건 집계하면 없던 로그가 생긴다.

코퍼스에 의존하지 않는다 — data/는 .gitignore라 커밋만 가져온 환경에는 없다.
가짜 도구 함수를 wrap에 태워 집계 계산 자체를 검사한다.
"""

from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from src import usage


def _capture(fn, *args, **kwargs):
    """도구 1회 호출에서 나온 집계 라인을 모은다."""
    buf = io.StringIO()
    with redirect_stderr(buf), redirect_stdout(buf):
        out = fn(*args, **kwargs)
    return [json.loads(l) for l in buf.getvalue().splitlines()
            if l.startswith('{"usage"')], out


def fake_search(query: str, k: int = 5, include_superseded: bool = False,
                include_repealed: bool = False, include_attachments: bool = False):
    return fake_search.response


def fake_get_article(rule_name: str, article_no: str, record_id: str | None = None):
    return fake_get_article.response


class SearchFieldsTest(unittest.TestCase):
    def setUp(self) -> None:
        usage.use_stderr_for_usage()

    def test_여섯_필드를_응답에서_그대로_재계산한다(self) -> None:
        """baseline 'search_정상'의 모양이다."""
        fake_search.response = {
            "status": "ok",
            "results": [{"규정명": "전남대학교 학칙"}, {"규정명": "수업관리 지침"},
                        {"규정명": "전남대학교 학칙"}],
            "advisories": [{"code": "upstream_norm_check"}],
            "attachments_omitted": 0,
        }
        lines, _ = _capture(usage.wrap("search_rule", fake_search), "학사경고")
        self.assertEqual(1, len(lines))
        self.assertEqual(
            {"usage": "search_rule", "status": "ok", "count": 3,
             "rules": ["수업관리 지침", "전남대학교 학칙"],
             "advisories": ["upstream_norm_check"],
             "attachments_omitted": 0, "unmatched_terms": 0},
            lines[0])

    def test_규정명은_중복을_접고_다섯_개까지만(self) -> None:
        fake_search.response = {
            "status": "ok",
            "results": [{"규정명": f"규정{i}"} for i in range(9)],
        }
        lines, _ = _capture(usage.wrap("search_rule", fake_search), "질의")
        self.assertEqual(9, lines[0]["count"])       # count는 전량
        self.assertEqual(5, len(lines[0]["rules"]))  # 이름은 5개까지

    def test_미매칭_어휘는_개수만_싣고_원문은_싣지_않는다(self) -> None:
        """스펙 §2.3 — 질의어의 부분집합이라 이름·학번이 그대로 남는다."""
        fake_search.response = {
            "status": "not_found", "results": [],
            "hints": {"query_terms_unmatched": ["홍길동", "20231234"]},
        }
        lines, _ = _capture(usage.wrap("search_rule", fake_search), "홍길동 20231234")
        self.assertEqual(2, lines[0]["unmatched_terms"])
        blob = json.dumps(lines[0], ensure_ascii=False)
        for secret in ("홍길동", "20231234"):
            self.assertNotIn(secret, blob)

    def test_질의_원문이_어디에도_실리지_않는다(self) -> None:
        fake_search.response = {"status": "ok", "results": []}
        lines, _ = _capture(usage.wrap("search_rule", fake_search), "징계 기록 열람")
        self.assertNotIn("징계 기록 열람", json.dumps(lines[0], ensure_ascii=False))

    def test_attachments_omitted가_없으면_0이다(self) -> None:
        """코어는 값이 있을 때만 이 키를 싣는다 — 없을 때 KeyError면 안 된다."""
        fake_search.response = {"status": "ok", "results": []}
        lines, _ = _capture(usage.wrap("search_rule", fake_search), "질의")
        self.assertEqual(0, lines[0]["attachments_omitted"])


class GetArticleFieldsTest(unittest.TestCase):
    def setUp(self) -> None:
        usage.use_stderr_for_usage()

    def test_article_no는_공백을_제거한_호출인자다(self) -> None:
        """함정 1. 응답의 `조문번호`를 읽으면 「제 37 조」가 그대로 실린다."""
        fake_get_article.response = {
            "규정명": "전남대학교 학칙",
            "조문번호": "제 37 조",           # 응답은 strip만 거친다
            "article": {"is_current": True},
            "status": "ok",
        }
        lines, _ = _capture(usage.wrap("get_article", fake_get_article),
                            "전남대학교 학칙", "제 37 조")
        self.assertEqual("제37조", lines[0]["article_no"])
        self.assertNotEqual("제 37 조", lines[0]["article_no"])

    def test_키워드로_불러도_위치로_불러도_같다(self) -> None:
        """MCP 클라이언트는 키워드로, 테스트는 위치로 부른다."""
        fake_get_article.response = {
            "article": {"is_current": True}, "status": "ok"}
        by_pos, _ = _capture(usage.wrap("get_article", fake_get_article),
                             "학칙", "제1조")
        by_kw, _ = _capture(usage.wrap("get_article", fake_get_article),
                            rule_name="학칙", article_no="제1조")
        self.assertEqual(by_pos, by_kw)

    def test_못_찾으면_식별자를_전부_null로_남긴다(self) -> None:
        """baseline 'get_없음' 모양 — 못 찾은 이름을 로그에 남기지 않는다."""
        fake_get_article.response = {"article": None, "status": "not_found"}
        lines, _ = _capture(usage.wrap("get_article", fake_get_article),
                            "없는규정", "제1조")
        self.assertEqual(
            {"usage": "get_article", "status": "not_found", "rule": None,
             "article_no": None, "is_current": None, "by_record_id": False},
            lines[0])

    def test_record_id_지정_여부가_실린다(self) -> None:
        fake_get_article.response = {
            "article": {"is_current": False}, "status": "ok"}
        lines, _ = _capture(usage.wrap("get_article", fake_get_article),
                            "학칙", "제1조", "rec-1")
        self.assertTrue(lines[0]["by_record_id"])
        self.assertFalse(lines[0]["is_current"])


class InvalidArgumentTest(unittest.TestCase):
    """함정 2 — 원본은 인자 검증에서 조기 반환해 집계에 닿지 않았다."""

    def setUp(self) -> None:
        usage.use_stderr_for_usage()

    def test_search의_invalid_argument는_집계를_남기지_않는다(self) -> None:
        fake_search.response = {
            "status": "invalid_argument",
            "error": {"code": "invalid_argument", "field": "query", "message": "…"},
        }
        lines, _ = _capture(usage.wrap("search_rule", fake_search), "")
        self.assertEqual([], lines)

    def test_get_article의_invalid_argument도_남기지_않는다(self) -> None:
        fake_get_article.response = {
            "status": "invalid_argument",
            "error": {"code": "invalid_argument", "field": "article_no", "message": "…"},
        }
        lines, _ = _capture(usage.wrap("get_article", fake_get_article), "학칙", "가" * 51)
        self.assertEqual([], lines)


class ScopeTest(unittest.TestCase):
    def test_나머지_다섯_도구는_감싸지_않고_원본을_돌려준다(self) -> None:
        """원본도 5종에는 집계를 걸지 않았다. 감싸면 프레임만 는다."""
        for name in ("get_article_as_of", "get_related_articles", "list_rules",
                     "list_articles", "get_corpus_stats"):
            self.assertIs(fake_search, usage.wrap(name, fake_search), name)

    def test_집계_대상은_두_종뿐이다(self) -> None:
        """여기 이름을 늘리는 것은 새 신호를 켜는 결정이라 조용히 하지 않는다."""
        self.assertEqual({"search_rule", "get_article"}, set(usage.LOGGED_TOOLS))


class StreamAndSwitchTest(unittest.TestCase):
    def test_기본은_stderr다(self) -> None:
        """stdio 전송에서 stdout은 JSON-RPC 전용 채널이다."""
        usage.use_stderr_for_usage()
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            usage.log_usage("t", a=1)
        self.assertEqual("", out.getvalue())
        self.assertIn('"usage": "t"', err.getvalue())

    def test_HTTP_진입점은_stdout으로_올린다(self) -> None:
        """Cloud Logging이 stdout을 수집한다."""
        usage.use_stdout_for_usage()
        try:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                usage.log_usage("t", a=1)
            self.assertIn('"usage": "t"', out.getvalue())
            self.assertEqual("", err.getvalue())
        finally:
            usage.use_stderr_for_usage()

    def test_끄기_스위치가_침묵시킨다(self) -> None:
        usage.use_stderr_for_usage()
        with mock.patch.dict(os.environ, {"RULECOMPASS_USAGE_LOG": "0"}):
            fake_search.response = {"status": "ok", "results": []}
            lines, out = _capture(usage.wrap("search_rule", fake_search), "질의")
        self.assertEqual([], lines)
        self.assertEqual("ok", out["status"])  # 응답은 그대로 나온다


class ResilienceTest(unittest.TestCase):
    def test_집계가_터져도_도구_응답은_나간다(self) -> None:
        """집계 실패가 교무과 답변을 막으면 안 된다."""
        usage.use_stderr_for_usage()
        fake_search.response = {"status": "ok", "results": []}
        with mock.patch.object(usage, "_search_fields", side_effect=RuntimeError("펑")):
            _, out = _capture(usage.wrap("search_rule", fake_search), "질의")
        self.assertEqual("ok", out["status"])

    def test_dict가_아닌_응답에도_터지지_않는다(self) -> None:
        usage.use_stderr_for_usage()
        fake_search.response = None
        lines, out = _capture(usage.wrap("search_rule", fake_search), "질의")
        self.assertEqual([], lines)
        self.assertIsNone(out)


class SignatureTest(unittest.TestCase):
    """★ 조용한 실패 지점 — FastMCP가 시그니처로 입력 스키마를 만든다."""

    def test_래핑해도_원본_시그니처가_보인다(self) -> None:
        import inspect
        wrapped = usage.wrap("search_rule", fake_search)
        self.assertEqual(list(inspect.signature(fake_search).parameters),
                         list(inspect.signature(wrapped).parameters))
        self.assertIn("query", inspect.signature(wrapped).parameters)


if __name__ == "__main__":
    unittest.main()

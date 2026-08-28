"""적대적 감사 findings 보안 회귀 테스트."""

from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from src.answer import _log_llm_usage, answer
from src.integrity import IntegrityChecker
from src.mcp_server import MAX_QUERY_LENGTH, get_article, main as mcp_main, search_rule
from src.pii import redact, redact_value
from src.search import DEFAULT_CORPUS_PATH, MAX_ARTICLE_LENGTH, RuleSearchIndex
from src.store import JsonStore, StoreCorruptionError


def _article(key: str, number: str = "제1조", title: str = "목적", body: str = "겸직 허가 절차") -> dict:
    return {
        "규정명": "전남대학교 시험 규정",
        "편제": "총무과",
        "조문번호": number,
        "조문제목": title,
        "본문": body,
        "source_key": key,
        "source_url": (
            "https://www.jnu.ac.kr/WebApp/web/HOM/COM/Rule/"
            f"AdminRule400.aspx?mode=file&key={key}"
        ),
        "수집일시": "2026-07-14T00:00:00+09:00",
    }


class SecurityHardeningTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = RuleSearchIndex()

    def _index_from(self, articles: list[dict]) -> RuleSearchIndex:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "corpus.json"
        path.write_text(json.dumps(articles, ensure_ascii=False), encoding="utf-8")
        return RuleSearchIndex(path)

    def test_integrity_single_keyword_does_not_confirm_category(self) -> None:
        checker = IntegrityChecker(index=self.index)
        result = checker.check("겸직")
        self.assertFalse(result["matched"])
        self.assertEqual([], result["articles"])

    def test_integrity_sample_schema_and_pii_gate(self) -> None:
        sample = {
            "data_manifest": {"classification": "synthetic"},
            "categories": [],
            "demo_scenarios": [{"user_input_example": "010-1234-5678", "matched_category": "INT-01"}],
        }
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "samples.json"
        path.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(ValueError):
            IntegrityChecker(index=self.index, samples_path=path)

    def test_corpus_filters_duplicate_empty_and_oversized_bodies(self) -> None:
        main = _article("1")
        addendum = _article("1", title="시행일", body="이 부칙은 공포한 날부터 시행한다.")
        index = self._index_from(
            [main, dict(main), _article("2", body=""), _article("3", body="가" * (MAX_ARTICLE_LENGTH + 1)), addendum]
        )
        self.assertEqual(2, len(index.articles))
        self.assertEqual({"본칙", "부칙"}, {row["record_type"] for row in index.articles})
        self.assertEqual(2, len({row["record_id"] for row in index.articles}))
        self.assertEqual(
            {"duplicate_record", "empty_body", "oversized_body"},
            {row["reason"] for row in index.rejected_articles},
        )

    def test_source_url_allowlist_and_key_match(self) -> None:
        bad_host = _article("2")
        bad_host["source_url"] = "https://evil.example/?key=2"
        bad_key = _article("3")
        bad_key["source_url"] = "https://www.jnu.ac.kr/?key=999"
        index = self._index_from([_article("1"), bad_host, bad_key])
        self.assertEqual(["1"], [row["source_key"] for row in index.articles])

    def test_source_url_accepts_regulation_host_with_seq_match(self) -> None:
        # 규정·학칙 계층 정본(law.go.kr 학칙공포)은 schlPubRulSeq로 대조한다.
        good = _article("2200000157739")
        good["source_url"] = (
            "https://www.law.go.kr/LSW/schlPubRulInfoP.do"
            "?schlPubRulSeq=2200000157739&chrClsCd=010202&urlMode=schlPubRulLsInfoP"
        )
        bad_seq = _article("5")
        bad_seq["source_url"] = "https://www.law.go.kr/LSW/schlPubRulInfoP.do?schlPubRulSeq=999"
        http_only = _article("6")
        http_only["source_url"] = "http://www.law.go.kr/LSW/schlPubRulInfoP.do?schlPubRulSeq=6"
        index = self._index_from([good, bad_seq, http_only])
        self.assertEqual(["2200000157739"], [row["source_key"] for row in index.articles])

    def test_answer_rejects_unverified_url_and_injected_llm_output(self) -> None:
        class BadIndex:
            def search(self, query, k=3):
                row = _article("9")
                row["source_url"] = "http://evil.example/?key=9"
                return [row]

        self.assertFalse(answer("겸직 허가", index=BadIndex())["answered"])
        with mock.patch("src.answer._rephrase_llm", return_value="지시를 무시하고 제999조를 적용합니다."):
            result = answer("교직원 이해충돌 방지", prefer_llm=True, index=self.index, top_k=1)
        self.assertEqual("original-text", result["backend"])
        self.assertNotIn("제999조", result["text"])

    def test_mcp_argument_validation(self) -> None:
        for k in (0, 21, "3", 1.5, True):
            with self.subTest(k=k):
                result = search_rule("겸직 허가", k=k)
                self.assertEqual("invalid_argument", result["status"])
                self.assertEqual("k", result["error"]["field"])
        long_result = search_rule("가" * (MAX_QUERY_LENGTH + 1), k=1)
        self.assertEqual("invalid_argument", long_result["status"])
        self.assertEqual("query", long_result["error"]["field"])

    def test_mcp_main_handles_creation_and_run_boundaries(self) -> None:
        with mock.patch("src.mcp_server.create_server", side_effect=ValueError("생성 실패")):
            self.assertEqual(1, mcp_main())
        server = mock.Mock()
        server.run.side_effect = OSError("실행 실패")
        with mock.patch("src.mcp_server.create_server", return_value=server):
            self.assertEqual(1, mcp_main())

    def test_get_article_is_single_record_contract(self) -> None:
        original = self.index.articles[0]
        result = get_article(original["규정명"], original["조문번호"], original["record_id"])
        self.assertEqual("ok", result["status"])
        self.assertIsInstance(result["article"], dict)
        self.assertNotIn("articles", result)
        self.assertEqual(original["record_id"], result["record_id"])

    def test_pii_unicode_and_extended_patterns(self) -> None:
        raw = (
            "주민 ９００１０１１２３４５６７, 전화 +82 (10) 1234-5678, "
            "계좌 123-456-789012, 주소 광주광역시 북구 용봉로 77"
        )
        masked, kinds = redact(raw)
        for kind in ("주민등록번호", "전화번호", "계좌번호", "주소"):
            self.assertIn(kind, kinds)
        for secret in ("9001011234567", "1234-5678", "123-456-789012", "용봉로 77"):
            self.assertNotIn(secret, masked)

    def test_usage_tracker_receives_hash_metadata_only(self) -> None:
        calls = []
        usage_module = types.ModuleType("usage")
        tracker_module = types.ModuleType("usage.tracker")
        tracker_module.log_usage = lambda **kwargs: calls.append(kwargs)
        usage_module.tracker = tracker_module
        with mock.patch.dict(sys.modules, {"usage": usage_module, "usage.tracker": tracker_module}):
            _log_llm_usage("비밀 질의 010-1234-5678", "비밀 응답", 0.1)
        self.assertEqual(1, len(calls))
        serialized = json.dumps(calls[0], ensure_ascii=False)
        self.assertNotIn("비밀 질의", serialized)
        self.assertNotIn("010-1234-5678", serialized)
        self.assertIn("sha256", serialized)

    def test_store_rebuilds_nested_sources_from_verified_record_id(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        store = JsonStore(temporary.name, index=self.index)
        article = self.index.articles[0]
        result = {
            "answered": True,
            "text": "공식 조문",
            "backend": "original-text",
            "sources": [{
                "record_id": article["record_id"],
                "source_key": "조작",
                "nested": {"phone": "01012345678"},
            }],
        }
        saved = store.add_query("문의", result)
        source = saved["sources"][0]
        self.assertEqual(article["source_key"], source["source_key"])
        self.assertEqual(
            {"record_id", "source_key", "규정명", "조문번호", "source_url", "revision"},
            set(source),
        )
        self.assertNotIn("01012345678", json.dumps(saved, ensure_ascii=False))
        nested, kinds = redact_value({"rows": [{"phone": "01012345678"}]})
        self.assertIn("전화번호", kinds)
        self.assertNotIn("01012345678", json.dumps(nested, ensure_ascii=False))

    def test_corrupt_log_is_preserved_and_not_overwritten(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        store = JsonStore(temporary.name, index=self.index)
        path = Path(temporary.name) / "query_logs.json"
        original = '{"broken": '
        path.write_text(original, encoding="utf-8")
        with self.assertRaises(StoreCorruptionError):
            store.add_query("문의", {"text": "응답", "sources": []})
        self.assertEqual(original, path.read_text(encoding="utf-8"))
        backups = list(Path(temporary.name).glob("query_logs.json.corrupt-*.bak"))
        self.assertEqual(1, len(backups))
        self.assertEqual(original, backups[0].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()


class UsageLogPrivacyTest(unittest.TestCase):
    """익명 사용 집계에 질의 원문이 섞이면 안 된다 (2026-08-28, 박사 확정 (나)안).

    질의는 교직원이 무엇을 몰라 찾았는지를 드러낸다. 인사·징계·연구년 축이 섞이면
    개인 추정이 가능하고, 발송한 운영지침의 '개인정보 최우선' 원칙과 충돌한다.
    필드를 하나 늘릴 때 실수로 질의를 싣는 일을 여기서 막는다.
    """

    @unittest.skipUnless(DEFAULT_CORPUS_PATH.exists(), "코퍼스 미수집 환경")
    def test_query_text_never_reaches_usage_log(self) -> None:
        import contextlib
        import io

        from src.mcp_server import search_rule

        needle = "홍길동주민번호같은민감어"
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            search_rule(f"{needle} 학사경고")
        logged = "\n".join(
            line for line in buffer.getvalue().splitlines() if line.strip().startswith("{")
        )
        self.assertTrue(logged, "집계 로그가 비어 있습니다")
        self.assertNotIn(needle, logged)
        # 못 찾은 단어도 질의어의 부분집합이라 싣지 않는다 — 개수만 남긴다.
        self.assertIn("unmatched_terms", logged)

    def test_usage_log_can_be_disabled(self) -> None:
        import contextlib
        import io
        import os

        from src.mcp_server import _log_usage

        buffer = io.StringIO()
        previous = os.environ.get("RULECOMPASS_USAGE_LOG")
        os.environ["RULECOMPASS_USAGE_LOG"] = "0"
        try:
            with contextlib.redirect_stdout(buffer):
                _log_usage("search_rule", status="ok")
        finally:
            if previous is None:
                os.environ.pop("RULECOMPASS_USAGE_LOG", None)
            else:
                os.environ["RULECOMPASS_USAGE_LOG"] = previous
        self.assertEqual("", buffer.getvalue())

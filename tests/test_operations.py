"""2차 운영·확산 기능 테스트."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.dashboard import build_state, generate_html
from src.learn import capture
from src.mcp_server import TOOL_NAMES, get_article, search_rule
from src.pii import redact
from src.search import RuleSearchIndex
from src.store import JsonStore


class OperationsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = RuleSearchIndex()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = JsonStore(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_mcp_search_tool_structure(self) -> None:
        result = search_rule("교직원 이해충돌 방지", k=2)
        self.assertEqual("ok", result["status"])
        self.assertLessEqual(result["count"], 2)
        self.assertEqual(result["count"], len(result["results"]))
        for field in ("규정명", "조문번호", "본문", "source_url"):
            self.assertIn(field, result["results"][0])

    def test_mcp_get_article_returns_original(self) -> None:
        original = self.index.articles[0]
        result = get_article(original["규정명"], original["조문번호"])
        self.assertEqual("ok", result["status"])
        self.assertEqual(original["본문"], result["article"]["본문"])
        self.assertEqual(result["record_id"], result["article"]["record_id"])
        self.assertEqual(("search_rule", "get_article", "get_article_as_of"), TOOL_NAMES)

    def test_pii_redaction(self) -> None:
        masked, kinds = redact("학번 2024123456, 전화 010-1234-5678, a@jnu.ac.kr")
        self.assertIn("학번추정", kinds)
        self.assertIn("전화번호", kinds)
        self.assertIn("이메일", kinds)
        self.assertNotIn("2024123456", masked)
        self.assertNotIn("010-1234-5678", masked)
        self.assertNotIn("a@jnu.ac.kr", masked)

    def test_query_log_masks_before_atomic_save(self) -> None:
        raw = "겸직 문의 010-1234-5678 test@jnu.ac.kr"
        result = {"answered": False, "text": "해당 규정 미확인", "sources": [], "backend": "none"}
        saved = self.store.add_query(raw, result)
        file_text = (Path(self.temporary.name) / "query_logs.json").read_text(encoding="utf-8")
        self.assertNotIn("010-1234-5678", file_text)
        self.assertNotIn("test@jnu.ac.kr", file_text)
        self.assertIn("[전화번호 마스킹]", saved["question"])
        self.assertEqual(saved, json.loads(file_text)[0])

    def test_unanswered_query_becomes_deduplicated_candidate(self) -> None:
        result = {"answered": False, "text": "해당 규정 미확인", "sources": [], "backend": "none"}
        capture("플로비나크 절차 010-1234-5678", result, self.store)
        capture("플로비나크 절차 010-1234-5678", result, self.store)
        candidates = self.store.read("candidates")
        self.assertEqual(1, len(candidates))
        self.assertEqual(2, candidates[0]["asked_count"])
        serialized = json.dumps(candidates, ensure_ascii=False)
        self.assertNotIn("010-1234-5678", serialized)

    def test_answered_query_does_not_become_candidate(self) -> None:
        result = {"answered": True, "text": "공식 조문", "sources": [], "backend": "original-text"}
        capture("겸직 허가", result, self.store)
        self.assertEqual([], self.store.read("candidates"))

    def test_dashboard_state_and_html_generation(self) -> None:
        result = {"answered": False, "text": "해당 규정 미확인", "sources": [], "backend": "none"}
        capture("<script>alert(1)</script> 010-1234-5678", result, self.store)
        state = build_state(self.index, self.store)
        # 코퍼스 수는 가변 — 실제 인덱스 로드분과 정합하는지로 검증(하드코딩 금지).
        article_count = len(self.index.articles)
        self.assertEqual(article_count, state["corpus"]["article_count"])
        self.assertGreater(state["corpus"]["regulation_count"], 0)
        output = Path(self.temporary.name) / "dashboard.html"
        generated = generate_html(output, self.index, self.store)
        page = generated.read_text(encoding="utf-8")
        self.assertIn("CNU 규정 나침반", page)
        self.assertIn(str(article_count), page)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertNotIn("010-1234-5678", page)


if __name__ == "__main__":
    unittest.main()


class LedgerTest(unittest.TestCase):
    """적재 대장(역전된 승인) — 자동 검증 기본 + 이탈만 승인 큐."""

    def test_ingest_ledger_and_review_queue(self) -> None:
        import tempfile
        from pathlib import Path as P
        from src.ledger import record_ingest
        from src.search import RuleSearchIndex

        index = RuleSearchIndex()
        tmp = P(tempfile.mkdtemp())
        entry = record_ingest(index, ledger_path=tmp / "l.json", review_path=tmp / "r.json")
        self.assertEqual(len(index.articles), entry["accepted"])
        self.assertEqual(len(index.rejected_articles), entry["rejected"])
        self.assertTrue(entry["corpus_fingerprint"])
        self.assertEqual("v1", entry["rules_version"])
        # 같은 코퍼스 지문은 중복 기록하지 않는다.
        again = record_ingest(index, ledger_path=tmp / "l.json", review_path=tmp / "r.json")
        self.assertEqual(entry["recorded_at"], again["recorded_at"])
        # 승인 큐 파일이 생성되고 항목 수가 이탈 수와 일치한다.
        review = json.loads((tmp / "r.json").read_text(encoding="utf-8"))
        self.assertEqual(entry["rejected"], len(review["items"]))

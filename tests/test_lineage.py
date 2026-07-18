"""시점 질의(as-of) — 개정 계열 유효기간 판정 테스트."""

from __future__ import annotations

import unittest

from src.lineage import DEFAULT_LINEAGE_PATH, LineageStore


@unittest.skipUnless(DEFAULT_LINEAGE_PATH.exists(), "계열 코퍼스 미수집 환경")
class LineageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.store = LineageStore()

    def test_lineage_loaded(self) -> None:
        self.assertTrue(self.store.rule_names)
        for name in self.store.rule_names:
            versions = self.store.lineages[name]
            current = [v for v in versions if v.get("valid_until") is None]
            self.assertEqual(1, len(current), f"{name}: 현행본은 정확히 1개여야 한다")

    def test_as_of_past_returns_then_valid_version(self) -> None:
        result = self.store.articles_as_of(
            "전남대학교 연구비 중앙관리지침", "2015-06-01", keyword="인건비"
        )
        self.assertEqual("ok", result["status"])
        # 반환 판본의 유효기간이 질의 시점을 실제로 포함해야 한다.
        self.assertLessEqual(result["valid_from"], "2015-06-01")
        self.assertGreater(result["valid_until"], "2015-06-01")
        self.assertTrue(result["articles"])
        self.assertTrue(result["source_url"].startswith("https://www.jnu.ac.kr/"))

    def test_as_of_current_date_returns_current(self) -> None:
        result = self.store.articles_as_of("전남대학교 연구비 중앙관리지침", "2026-07-01")
        self.assertEqual("ok", result["status"])
        self.assertIsNone(result["valid_until"])

    def test_oldest_version_carries_uncertainty_notice(self) -> None:
        # 첫 판본은 제정일 미상 — 그 이전 날짜 질의에 확정 표현을 쓰지 않는다.
        result = self.store.articles_as_of("전남대학교 장학지침", "2005-01-01")
        self.assertEqual("ok", result["status"])
        self.assertIn("원문 이력 확인", result["notice"])

    def test_resolve_rule_token_match(self) -> None:
        self.assertEqual(
            "전남대학교 연구비 중앙관리지침", self.store.resolve_rule("연구비 인건비")
        )

    def test_invalid_date_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.store.version_as_of("전남대학교 장학지침", "2015/06/01")

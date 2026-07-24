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
        # 공개 샘플 계열에는 제정일 미상 판본이 없으므로 합성 계열로 로직을 검증한다.
        store = LineageStore("nonexistent_lineage.json")
        store.lineages = {
            "전남대학교 갑판본지침": [
                {
                    "label": "2010. 1. 1. 개정전",
                    "valid_from": None,
                    "valid_until": "2010-01-01",
                    "source_url": "https://www.jnu.ac.kr/?key=1",
                    "articles": [{"조문번호": "제1조", "조문제목": "목적", "본문": "본문"}],
                },
                {
                    "label": "현행",
                    "valid_from": "2010-01-01",
                    "valid_until": None,
                    "source_url": "https://www.jnu.ac.kr/?key=1",
                    "articles": [{"조문번호": "제1조", "조문제목": "목적", "본문": "본문"}],
                },
            ]
        }
        result = store.articles_as_of("전남대학교 갑판본지침", "2005-01-01")
        self.assertEqual("ok", result["status"])
        self.assertIn("원문 이력 확인", result["notice"])

    def test_resolve_rule_token_match(self) -> None:
        self.assertEqual(
            "전남대학교 연구비 중앙관리지침", self.store.resolve_rule("연구비 인건비")
        )

    def test_resolve_rule_rejects_generic_only_query(self) -> None:
        # '지침' 같은 행정 일반어 1개 일치만으로 규정을 확정하면
        # 다른 규정의 조문이 유효 판본으로 인용된다(H-1과 동일 원리).
        self.assertIsNone(self.store.resolve_rule("우주선 발사 지침"))
        self.assertIsNone(self.store.resolve_rule("학생 병역 지침"))
        self.assertIsNone(self.store.resolve_rule("운영 관리 지침"))

    def test_resolve_rule_ambiguous_candidates_return_none(self) -> None:
        # 후보가 2개 이상이면 길이 등으로 임의 선택하지 않는다.
        store = LineageStore("nonexistent_lineage.json")
        store.lineages = {
            "전남대학교 갑장학지침": [],
            "전남대학교 을장학지침": [],
        }
        self.assertIsNone(store.resolve_rule("장학"))
        self.assertIsNone(store.resolve_rule("갑장학 을장학"))
        self.assertEqual("전남대학교 갑장학지침", store.resolve_rule("갑장학"))

    def test_mcp_as_of_unresolved_returns_not_found_with_candidates(self) -> None:
        # 미해결 질의를 원문 그대로 규정명으로 밀어 넣지 않고 명시 반환한다.
        from src.mcp_server import get_article_as_of

        result = get_article_as_of("우주선 발사 지침", "2015-06-01")
        self.assertEqual("not_found", result["status"])
        self.assertIn("known_rules", result)

    def test_invalid_date_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.store.version_as_of("전남대학교 장학지침", "2015/06/01")

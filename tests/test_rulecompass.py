"""핵심 검색·답변·자기점검 회귀 테스트."""

from __future__ import annotations

import unittest

from src.answer import NOT_FOUND_TEXT, answer
from src.integrity import IntegrityChecker
from src.search import RuleSearchIndex


class RuleCompassTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = RuleSearchIndex()
        cls.checker = IntegrityChecker(index=cls.index)

    def test_corpus_count(self) -> None:
        # 코퍼스는 수집 범위에 따라 가변(공개 배포 시 규정 전량 확장). 하드코딩 대신
        # 충분한 규모 + 인덱스가 실제 로드분과 일치하는지 검증한다.
        self.assertGreater(len(self.index.articles), 100)
        self.assertEqual(len(self.index.articles), len(self.index._term_frequencies))

    def test_search_returns_relevant_article(self) -> None:
        # 픽스처는 공개 샘플 코퍼스에도 실재하는 규정으로 유지한다(신규 clone 재현성).
        results = self.index.search("교직원 이해충돌 방지", k=3)
        self.assertTrue(results)
        self.assertIn("이해충돌", results[0]["규정명"] + results[0]["본문"])

    def test_search_result_has_citation_fields(self) -> None:
        result = self.index.search("방사선 안전관리", k=1)[0]
        for field in ("규정명", "조문번호", "본문", "source_url"):
            self.assertTrue(result.get(field), field)

    def test_answer_quotes_original_and_source(self) -> None:
        result = answer("교직원 이해충돌 방지", index=self.index, top_k=1)
        self.assertTrue(result["answered"])
        article = result["articles"][0]
        self.assertIn(article["본문"], result["text"])
        self.assertIn(article["규정명"], result["text"])
        self.assertIn(article["조문번호"], result["text"])
        self.assertIn(article["source_url"], result["text"])

    def test_unknown_answer_does_not_fabricate(self) -> None:
        result = answer("플로비나크 제타웜홀 허가 기준 ZXQ-999", index=self.index)
        self.assertFalse(result["answered"])
        self.assertEqual(NOT_FOUND_TEXT, result["text"])
        self.assertEqual([], result["sources"])
        self.assertEqual([], result["articles"])

    def test_unknown_with_common_words_blocked(self) -> None:
        # 회귀: 실제 한국어 일반어(절차·허가)가 섞인 무관 질의는 2·3-gram이 방대한
        # 본문에 우연히 걸려 coverage 게이트를 넘던 결함이 있었다. 완전형 매칭만
        # coverage로 인정하도록 고친 뒤에는 '미확인'으로 차단되어야 한다.
        for query in ("우주선 발사 궤도 허가 절차 로켓", "양자컴퓨터 큐비트 초전도 냉각"):
            with self.subTest(query=query):
                self.assertEqual([], self.index.search(query, k=3))
                self.assertFalse(answer(query, index=self.index)["answered"])

    def test_two_stage_routing(self) -> None:
        # 2단 검색: 명확한 질의는 해당 규정으로 라우팅(rule-first)되고, 일반어만
        # 겹치는 질의는 규정을 좁히지 않고 전체 검색으로 폴백해야 한다(오배제 방지).
        routed = self.index.route_rules("교직원 이해충돌 방지")
        self.assertTrue(any("이해충돌" in name for name in routed))
        results = self.index.search("교직원 이해충돌 방지", k=2)
        self.assertEqual("rule-first", results[0]["routing"])
        # '사용·절차' 같은 일반어만으로는 라우팅이 확정되지 않는다.
        self.assertEqual([], self.index.route_rules("사용 절차 방법 기준"))

    def test_integrity_matches_contract_purchase(self) -> None:
        result = self.checker.check("학과에서 소액 기자재를 여러 번 나눠서 사려고 합니다.")
        self.assertTrue(result["matched"])
        self.assertEqual("INT-03", result["category"]["code"])
        self.assertEqual(3, len(result["selfcheck"]))
        self.assertIn("rule_anchor", result)

    def test_integrity_matches_external_lecture(self) -> None:
        result = self.checker.check("외부 특강 요청이 왔는데 따로 신고해야 하나요?")
        self.assertTrue(result["matched"])
        self.assertEqual("INT-08", result["category"]["code"])
        self.assertTrue(result["articles"])
        for field in ("규정명", "조문번호", "본문", "source_url"):
            self.assertTrue(result["articles"][0].get(field), field)

    def test_integrity_unknown_situation(self) -> None:
        result = self.checker.check("오늘 점심 메뉴를 추천해 주세요.")
        self.assertFalse(result["matched"])
        self.assertEqual("해당 지적유형 미확인", result["text"])


if __name__ == "__main__":
    unittest.main()

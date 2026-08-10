"""2026-08-10 실사용 핸드오프(T1~T7) 회귀 테스트.

배경: 소비자 스킬은 조문을 찾지 못하면 "규정에 근거가 없다"고 답한다. 따라서
거짓 음성은 검색 품질 문제가 아니라 잘못된 답변을 내보내는 사고다. 동시에
"없는 규정을 지어내지 않는다"(H-1 하드닝)는 이 도구의 핵심 계약이므로,
누락 복구와 무관 질의 차단을 **함께** 잠근다.
"""

from __future__ import annotations

import unittest

from src.search import DEFAULT_CORPUS_PATH, RuleSearchIndex
from src.mcp_server import (
    get_article,
    get_corpus_stats,
    get_related_articles,
    list_rules,
    search_rule,
)
from src.structure import SECTION_RE, split_structure_titles


@unittest.skipUnless(DEFAULT_CORPUS_PATH.exists(), "코퍼스 미수집 환경")
class TitleExactMatchTest(unittest.TestCase):
    """T1 — 조문제목 완전일치 레코드는 반드시 후보에 포함되어야 한다."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.index = RuleSearchIndex()

    def _assert_hit(self, query: str, rule: str, article_no: str, k: int = 10) -> None:
        results = self.index.search(query, k=k)
        found = [(r["규정명"], r["조문번호"]) for r in results]
        self.assertIn(
            (rule, article_no), found,
            f"{query!r} 검색 결과에 {rule} {article_no}이(가) 없다 — 반환: {found}",
        )

    def test_title_exact_match_returns_record(self) -> None:
        cases = [
            ("재입학", "전남대학교 학칙", "제30조"),
            ("복학", "전남대학교 학칙", "제35조"),
            ("제적", "전남대학교 학칙", "제37조"),
            ("퇴학", "전남대학교 학칙", "제36조"),
            ("성적 처리", "전남대학교 교학규정", "제46조"),
            ("파견", "전남대학교 교원 인사에 관한 규정", "제57조"),
        ]
        for query, rule, article_no in cases:
            with self.subTest(query=query):
                self._assert_hit(query, rule, article_no)

    def test_routing_keeps_tied_candidates(self) -> None:
        # 동점 후보를 상한으로 잘라내면 정답 규정이 조용히 사라진다(학칙 제30조 사고).
        routed = self.index.route_rules("재입학")
        self.assertIn("전남대학교 학칙", routed)


@unittest.skipUnless(DEFAULT_CORPUS_PATH.exists(), "코퍼스 미수집 환경")
class HallucinationGuardTest(unittest.TestCase):
    """골든 네거티브 — 어떤 완화 수정도 이 계약을 깨서는 안 된다."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.index = RuleSearchIndex()

    def test_unrelated_queries_return_nothing(self) -> None:
        for query in (
            "우주선 발사 궤도 허가 절차 로켓",
            "양자컴퓨터 큐비트 초전도 냉각",
            "국세청 종합소득세 신고",
        ):
            with self.subTest(query=query):
                self.assertEqual([], self.index.search(query, k=5))


@unittest.skipUnless(DEFAULT_CORPUS_PATH.exists(), "코퍼스 미수집 환경")
class StructureTitleTest(unittest.TestCase):
    """T4 — 편제 제목은 본문에서 분리되어 장/절 필드로 올라가야 한다."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.index = RuleSearchIndex()

    def test_no_section_title_left_in_body(self) -> None:
        residue = [
            (row["규정명"], row["조문번호"])
            for row in self.index.articles
            if any(SECTION_RE.match(line) for line in row["본문"].split("\n") if line.strip())
        ]
        self.assertEqual([], residue, f"본문에 장/절 제목 잔존: {residue[:5]}")

    def test_every_record_has_structure_fields(self) -> None:
        for row in self.index.articles:
            self.assertIn("장", row)
            self.assertIn("절", row)

    def test_absorbed_title_no_longer_causes_false_hit(self) -> None:
        # 교학규정 제10조(휴학)는 꼬리에 흡수된 '제4절 복학ㆍ재입학ㆍ퇴학' 때문에
        # '재입학' 질의에 잡혔다. 분리 후에는 잡히지 않아야 한다.
        hits = [
            (r["규정명"], r["조문번호"])
            for r in self.index.search("재입학 허가 신청", k=8)
        ]
        self.assertNotIn(("전남대학교 교학규정", "제10조"), hits)


@unittest.skipUnless(DEFAULT_CORPUS_PATH.exists(), "코퍼스 미수집 환경")
class MultiTermQueryTest(unittest.TestCase):
    """T2 — 질의어를 더한다고 정답이 사라져서는 안 된다."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.index = RuleSearchIndex()

    def _hits(self, query: str, k: int = 10) -> list[tuple[str, str]]:
        return [(r["규정명"], r["조문번호"]) for r in self.index.search(query, k=k)]

    def test_same_intent_queries_keep_the_answer(self) -> None:
        for query in ("재입학", "재입학 허가 신청", "재입학 어떻게 하나요"):
            with self.subTest(query=query):
                self.assertIn(("전남대학교 학칙", "제30조"), self._hits(query))
        for query in ("성적 처리", "성적 정정 기간"):
            with self.subTest(query=query):
                self.assertIn(("전남대학교 교학규정", "제46조"), self._hits(query))

    def test_known_limitation_synonym_gap(self) -> None:
        # 핸드오프 §T2 회귀 케이스 '성적 이의신청 언제까지' → 교학규정 제46조는
        # **동의어 확장 없이는 달성 불가**다. 코퍼스는 '이의신청'이 아니라 '정정'으로
        # 표현하고(제46조 본문에 '이의신청' 부재), '이의신청'을 실제로 가진 다른 규정들이
        # 상위를 차지한다. 게이트 문제가 아니라 어휘 불일치이므로 T2 범위 밖으로 둔다.
        # 여기서는 "빈손으로 돌려주지는 않는다"까지만 계약으로 잠근다.
        self.assertTrue(self._hits("성적 이의신청 언제까지"))

    def test_golden_case_2_and_3(self) -> None:
        hits = self._hits("재입학 허가 신청")
        self.assertIn(("전남대학교 학칙", "제30조"), hits)
        self.assertIn(("전남대학교 교학규정", "제11조"), hits)
        hits = self._hits("성적 정정 기간")
        self.assertIn(("전남대학교 교학규정", "제46조"), hits)
        self.assertNotIn(("전남대학교 총장임용후보자 선정에 관한 규정 시행 세칙", "제28조"), hits)


@unittest.skipUnless(DEFAULT_CORPUS_PATH.exists(), "코퍼스 미수집 환경")
class RelatedArticlesTest(unittest.TestCase):
    """T3 — 상호참조 추적. 소비자 스킬의 수동 참조 추출을 대체한다."""

    AGRI = "rule-2200000151665-81ff3bc2c51293a0"   # 농생명산업대학원 교학규정 제11조의2
    HAKCHIK_30 = "rule-2200000155895-6ef5c792ea6d7b66"  # 학칙 제30조

    def test_outbound_resolves_cross_rule_reference(self) -> None:
        result = get_related_articles(self.AGRI)
        self.assertEqual("ok", result["status"])
        self.assertTrue(
            any(e["target_article"] == "제30조" and e["resolved"] for e in result["outbound"]),
            result["outbound"],
        )

    def test_inbound_finds_citing_articles(self) -> None:
        result = get_related_articles(self.HAKCHIK_30, direction="inbound", resolve=False)
        self.assertGreaterEqual(len(result["inbound"]), 2)

    def test_unknown_record_id_is_not_found(self) -> None:
        self.assertEqual("not_found", get_related_articles("rule-없는-아이디")["status"])

    def test_invalid_direction_rejected(self) -> None:
        result = get_related_articles(self.AGRI, direction="sideways")
        self.assertEqual("invalid_argument", result["status"])


@unittest.skipUnless(DEFAULT_CORPUS_PATH.exists(), "코퍼스 미수집 환경")
class VersionMetadataTest(unittest.TestCase):
    """T5 — 구판본·삭제 조문을 살아 있는 근거로 인용하면 사고다."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.index = RuleSearchIndex()

    def test_all_records_carry_version_fields(self) -> None:
        for row in self.index.articles[:50]:
            for field in ("is_current", "superseded_by", "is_repealed", "repealed_date"):
                self.assertIn(field, row)

    def test_superseded_edition_excluded_by_default(self) -> None:
        # 골든 케이스 5 — '수업관리 지침(2012. 12. 26. 제정)' 계열은 기본 검색에서 빠진다.
        hits = self.index.search("수강신청 정정", k=8)
        self.assertFalse([r for r in hits if "(2012" in r["규정명"]], hits)

    def test_repealed_article_excluded_by_default(self) -> None:
        # 골든 케이스 4 — 교육대학원 교학규정 제11조는 본문이 '<삭제, 2020. 6. 3.>'다.
        hits = [(r["규정명"], r["조문번호"]) for r in self.index.search("재입학", k=10)]
        self.assertNotIn(("전남대학교 교육대학원 교학규정", "제11조"), hits)

    def test_opt_in_returns_superseded_with_flag(self) -> None:
        hits = self.index.search("수강신청 정정", k=8, include_superseded=True)
        old = [r for r in hits if "(2012" in r["규정명"]]
        self.assertTrue(old)
        self.assertFalse(old[0]["is_current"])


@unittest.skipUnless(DEFAULT_CORPUS_PATH.exists(), "코퍼스 미수집 환경")
class CorpusMapToolTest(unittest.TestCase):
    """T6·T7 — 정적 코퍼스 지도와 우회 판정 절차를 도구로 대체한다."""

    def test_list_rules_returns_current_rules(self) -> None:
        result = list_rules()
        self.assertEqual("ok", result["status"])
        self.assertGreater(result["count"], 300)
        row = result["rules"][0]
        for field in ("규정명", "편제", "source_key", "계층", "조문_수", "수집일시", "is_current"):
            self.assertIn(field, row)

    def test_list_rules_filters_by_division(self) -> None:
        filtered = list_rules(division="규정집/연구소")
        self.assertTrue(filtered["count"])
        self.assertTrue(all("연구소" in r["편제"] for r in filtered["rules"]))

    def test_corpus_stats_reports_index_consistency(self) -> None:
        stats = get_corpus_stats()
        self.assertEqual(stats["조문_수"], stats["색인_문서_수"])
        self.assertNotIn("warning", stats)

    def test_search_hints_flag_absent_vocabulary(self) -> None:
        result = search_rule("생성형 인공지능 챗봇", k=5)
        self.assertEqual(0, result["count"])
        self.assertIn("생성형", result["hints"]["query_terms_unmatched"])
        self.assertEqual("no_such_concept", result["hints"]["suggest"])

    def test_hints_absent_on_healthy_result(self) -> None:
        self.assertNotIn("hints", search_rule("재입학", k=5))


@unittest.skipUnless(DEFAULT_CORPUS_PATH.exists(), "코퍼스 미수집 환경")
class TextIntegrityTest(unittest.TestCase):
    """T8 — 원문 문자 손상은 고치지 않고 드러낸다.

    손상은 정본 제공처(law.go.kr) 응답 자체에 있다(2026-08-10 바이트 0x3F 확인).
    재수집으로 고쳐지지 않고 추정 복원은 날조이므로, 계약은 '조용히 통과하지 않는 것'이다.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.index = RuleSearchIndex()

    def test_damaged_article_is_flagged(self) -> None:
        row = next(
            r for r in self.index.articles
            if r["규정명"] == "전남대학교 학칙" and r["조문번호"] == "제25조"
        )
        self.assertIn("편?재입학", row["본문"], "원문 손상을 임의 복원하지 않는다")
        integrity = row["text_integrity"]
        self.assertGreaterEqual(integrity["suspect_marks"], 1)
        self.assertIn("가운뎃점_추정", integrity["kinds"])

    def test_clean_article_has_no_flag(self) -> None:
        row = next(
            r for r in self.index.articles
            if r["규정명"] == "전남대학교 학칙" and r["조문번호"] == "제30조"
        )
        self.assertIsNone(row["text_integrity"])

    def test_real_question_mark_is_not_flagged(self) -> None:
        rows = [r for r in self.index.articles if "숙지하였습니까" in r["본문"]]
        self.assertTrue(rows, "설문 문항 레코드가 있어야 이 계약을 검증할 수 있다")
        for row in rows:
            self.assertIsNone(row["text_integrity"], row["규정명"])

    def test_every_record_carries_the_field(self) -> None:
        for row in self.index.articles[:50]:
            self.assertIn("text_integrity", row)

    def test_stats_report_damage_count(self) -> None:
        stats = get_corpus_stats()
        self.assertEqual(
            stats["문자손상_조문_수"],
            sum(1 for r in self.index.articles if r.get("text_integrity")),
        )


class HtmlUnescapeOrderTest(unittest.TestCase):
    """이스케이프된 마크업이 태그 제거를 피해 본문에 되살아나면 안 된다(잠재 결함)."""

    def test_escaped_markup_does_not_survive(self) -> None:
        from collect_regulations import html_to_text

        self.assertNotIn("<td>", html_to_text("&lt;td&gt;예&lt;/td&gt;<p>정상</p>"))


@unittest.skipUnless(DEFAULT_CORPUS_PATH.exists(), "코퍼스 미수집 환경")
class GoldenCaseTest(unittest.TestCase):
    """T13 — v1.1.0~v1.2.0 수용 기준을 코드로 고정한다.

    골든 케이스가 테스트로 박혀 있지 않으면 다음 릴리스에서 조용히 회귀한다.
    특히 커버리지 게이트(T2)는 스코어링을 건드릴 때마다 되돌아가기 쉽다.
    """

    HAKCHIK_30 = "rule-2200000155895-6ef5c792ea6d7b66"

    @classmethod
    def setUpClass(cls) -> None:
        cls.index = RuleSearchIndex()

    def _hits(self, query: str, k: int = 10, **kwargs) -> list[tuple[str, str]]:
        return [(r["규정명"], r["조문번호"]) for r in self.index.search(query, k=k, **kwargs)]

    def test_01_index_matches_records(self) -> None:
        stats = get_corpus_stats()
        self.assertEqual(stats["조문_수"], stats["색인_문서_수"])

    def test_02_03_04_readmission_queries(self) -> None:
        for query in ("재입학", "재입학 어떻게 하나요", "재입학 허가 신청"):
            with self.subTest(query=query):
                self.assertIn(("전남대학교 학칙", "제30조"), self._hits(query))
        self.assertNotIn(("전남대학교 교학규정", "제10조"), self._hits("재입학 허가 신청", k=8))

    def test_05_repealed_excluded(self) -> None:
        self.assertNotIn(("전남대학교 교육대학원 교학규정", "제11조"), self._hits("재입학"))

    def test_06_superseded_excluded(self) -> None:
        self.assertFalse([n for n, _ in self._hits("수강신청 정정", k=8) if "(2012" in n])

    def test_07_grade_correction(self) -> None:
        hits = self._hits("성적 정정 기간")
        self.assertIn(("전남대학교 교학규정", "제46조"), hits)
        self.assertNotIn(("전남대학교 총장임용후보자 선정에 관한 규정 시행 세칙", "제28조"), hits)

    def test_08_inbound_references(self) -> None:
        result = get_related_articles(self.HAKCHIK_30, direction="inbound", resolve=False)
        self.assertGreaterEqual(len(result["inbound"]), 4)

    def test_09_section_title_split(self) -> None:
        article = get_article("전남대학교 교학규정", "제11조")["article"]
        self.assertNotIn("제5절", article["본문"])
        self.assertIsNotNone(article["절"])

    def test_10_ai_query_hint(self) -> None:
        # 핸드오프 골든 #10은 `생성형 인공지능` → count 0을 기대했으나, 규정 계층 확장으로
        # `인공지능` 어휘가 코퍼스에 실재하게 됐다(인공지능융합연구소 규정 등).
        # 어휘 부재 판정은 실제로 없는 어휘로 검증한다.
        result = search_rule("생성형 인공지능 챗봇 언어모델", k=5)
        self.assertEqual(0, result["count"])
        self.assertTrue(result["hints"]["query_terms_unmatched"])

    def test_10b_ai_vocabulary_exists_but_is_not_usage_rule(self) -> None:
        """⚠️ `인공지능`이 매칭된다고 'AI 활용 근거'가 아니다 — 가짜 근거 방지 계약.

        매칭되는 조문은 **연구소 설치·사무분장** 규정이다. AI 활용을 규율하지 않는다.
        소비자가 hints 부재만 보고 '근거 있음'으로 넘어가면 안 되므로 사실을 고정한다.
        """
        result = search_rule("인공지능", k=5)
        self.assertGreater(result["count"], 0)
        self.assertIsNone(result.get("hints"))
        names = {r["규정명"] for r in result["results"]}
        self.assertTrue(
            any("연구소" in n or "사무분장" in n for n in names),
            f"AI 어휘 매칭의 성격이 바뀌었다 — 스킬 §6-2 재검토 필요: {names}",
        )

    def test_11_12_no_repair_but_flagged(self) -> None:
        article = get_article("전남대학교 학칙", "제25조")["article"]
        self.assertIsNotNone(article["text_integrity"])  # 손상은 드러난다
        flagged = sum(1 for r in self.index.articles if r.get("text_integrity"))
        self.assertEqual(get_corpus_stats()["문자손상_조문_수"], flagged)

    def test_13_reference_classification(self) -> None:
        result = get_related_articles(self.HAKCHIK_30, direction="both", resolve=False)
        self.assertTrue(any(
            e["kind"] == "external_law" and "고등교육법시행령" in e["raw"]
            for e in result["unresolved"]
        ))
        self.assertFalse([e for e in result["unresolved"] if "제44조" in e["raw"]])
        self.assertFalse([
            e for e in result["unresolved"] if e["raw"].startswith(("경우", "다만", "에는"))
        ])
        self.assertFalse([e for e in result["unresolved"] if e["kind"] == "unknown"])

    def test_14_clause_level_repeal(self) -> None:
        article = get_article("전남대학교 학칙", "제44조")["article"]
        self.assertEqual(["③"], [c["clause"] for c in article["repealed_clauses"]])

    def test_15_stats_exclusion_breakdown(self) -> None:
        stats = get_corpus_stats()
        self.assertIn("적재제외_사유별", stats)
        self.assertEqual(stats["적재제외_레코드_수"], sum(stats["적재제외_사유별"].values()))
        self.assertNotIn("warning_excluded", stats, "의도치 않은 제외가 있으면 재색인이 필요하다")

    def test_attachment_reference_is_marked(self) -> None:
        record_id = get_article("전남대학교 학칙", "제44조")["record_id"]
        result = get_related_articles(record_id, direction="outbound", resolve=False)
        self.assertTrue(any(
            e["kind"] == "attachment_not_collected" and "별표" in e["raw"]
            for e in result["unresolved"]
        ))


class StructureIdempotencyTest(unittest.TestCase):
    """한 번 적용하면 본문에서 제목이 사라지므로, 재실행이 장/절을 지우면 안 된다."""

    def test_second_pass_is_a_no_op(self) -> None:
        articles = [
            {"규정명": "샘플", "조문번호": "제1조", "본문": "본문1\n제2장 다음장"},
            {"규정명": "샘플", "조문번호": "제2조", "본문": "본문2"},
        ]
        once = split_structure_titles(articles)
        twice = split_structure_titles(once)
        self.assertEqual(once, twice)
        self.assertEqual("제2장 다음장", once[1]["장"])
        self.assertIsNone(once[0]["장"])


if __name__ == "__main__":
    unittest.main()

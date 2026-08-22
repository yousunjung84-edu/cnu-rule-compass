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
    list_articles,
    list_rules,
    search_rule,
)
from src.structure import SECTION_RE, split_structure_titles


def reaches(index, query: str, rule: str, article_no: str, k: int = 10) -> bool:
    """검색 상위 k건에 있거나, 그 결과의 상호참조 1홉으로 닿는가.

    v1.8.0에서 항목식 지침을 편입하자 '재입학' 질의 상위를 「학부/대학원 재입학
    세부지침」이 채우고 학칙 제30조가 k=10에서도 밀려났다. 세부지침이 더 구체적인
    답이므로 이것을 오답으로 볼 수는 없다 — 다만 **허가 권한의 근거는 학칙**이라
    세부지침만 인용하면 근거를 빠뜨린 답이 된다.

    그래서 계약을 '검색 1순위'가 아니라 **'닿을 수 있는가'**로 쓴다.
    실측: 세부지침 `1. 목적`이 '학칙 제30조'를 인용하고 cross_rule로 해소된다.
    스킬 §5-C가 상호참조를 밟게 되어 있으므로 이 경로가 실제 답변 경로다.
    """
    hits = index.search(query, k=k)
    if any(r["규정명"] == rule and r["조문번호"] == article_no for r in hits):
        return True
    for row in hits:
        related = get_related_articles(row["record_id"], direction="outbound", resolve=False)
        if any(
            e.get("target_rule") == rule and e.get("target_article") == article_no
            and e.get("resolved")
            for e in related["outbound"]
        ):
            return True
    return False


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
            # '재입학'은 세부지침이 상위를 채우므로 참조 1홉까지 허용한다 (reaches 참고).
            ("복학", "전남대학교 학칙", "제35조"),
            ("제적", "전남대학교 학칙", "제37조"),
            ("퇴학", "전남대학교 학칙", "제36조"),
            ("성적 처리", "전남대학교 교학규정", "제46조"),
            ("파견", "전남대학교 교원 인사에 관한 규정", "제57조"),
        ]
        for query, rule, article_no in cases:
            with self.subTest(query=query):
                self._assert_hit(query, rule, article_no)
        self.assertTrue(reaches(self.index, "재입학", "전남대학교 학칙", "제30조"))

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
        # 별표·항목은 제외한다. 별표 본문의 '제1장 총칙'은 규정의 편제가 아니라
        # **첨부된 문서 자체의 구조**이고(포털 이용약관 전문), 항목식 지침의
        # '제2장. 교양교육과정'은 교과목번호 부여 표가 가리키는 **교육과정 편제**다.
        # 어느 쪽도 규정의 장/절이 아니므로 끌어올리면 원문을 훼손한다.
        residue = [
            (row["규정명"], row["조문번호"])
            for row in self.index.articles
            if row.get("record_type") not in {"별표", "항목"}
            and any(SECTION_RE.match(line) for line in row["본문"].split("\n") if line.strip())
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
                self.assertTrue(reaches(self.index, query, "전남대학교 학칙", "제30조"))
        for query in ("성적 처리", "성적 정정 기간"):
            with self.subTest(query=query):
                self.assertIn(("전남대학교 교학규정", "제46조"), self._hits(query))

    def test_known_limitation_gyohak_11_pushed_out(self) -> None:
        """v1.8.0 회귀 — 교학규정 제11조가 재입학 질의에서 밀려났다. **미해결로 박는다.**

        항목식 세부지침(학부/대학원 재입학)이 상위 k를 채우면서, 이전에 상위에 있던
        교학규정 제11조(복학·재입학·퇴학)가 검색으로도 참조 1홉으로도 닿지 않는다.
        학칙 제30조는 세부지침 `3. 재입학 대상 및 제외대상`의 참조로 여전히 닿지만,
        교학규정 제11조를 인용하는 `1. 목적`·`2. 근거`는 상위 10에 들지 못한다.

        완화 수정으로 덮지 않고 **현재 상태를 그대로 계약에 남긴다.** 이 테스트가
        실패로 바뀌면(=닿게 되면) 그때 계약을 되돌린다.
        """
        self.assertFalse(
            reaches(self.index, "재입학 허가 신청", "전남대학교 교학규정", "제11조"),
            "교학규정 제11조에 닿게 됐다면 golden_case_2로 되돌릴 것",
        )

    def test_known_limitation_synonym_gap(self) -> None:
        # 핸드오프 §T2 회귀 케이스 '성적 이의신청 언제까지' → 교학규정 제46조는
        # **동의어 확장 없이는 달성 불가**다. 코퍼스는 '이의신청'이 아니라 '정정'으로
        # 표현하고(제46조 본문에 '이의신청' 부재), '이의신청'을 실제로 가진 다른 규정들이
        # 상위를 차지한다. 게이트 문제가 아니라 어휘 불일치이므로 T2 범위 밖으로 둔다.
        # 여기서는 "빈손으로 돌려주지는 않는다"까지만 계약으로 잠근다.
        self.assertTrue(self._hits("성적 이의신청 언제까지"))

    def test_golden_case_2_and_3(self) -> None:
        self.assertTrue(reaches(self.index, "재입학 허가 신청", "전남대학교 학칙", "제30조"))
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
                self.assertTrue(reaches(self.index, query, "전남대학교 학칙", "제30조"))
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


@unittest.skipUnless(DEFAULT_CORPUS_PATH.exists(), "코퍼스 미수집 환경")
class GyeomjikDomainTest(unittest.TestCase):
    """T14~T17 — 겸직 지침(지침 계층·반각 낫표·교무과) 도메인 골든 케이스.

    재입학 케이스는 v1.1.0이 그것을 겨냥해 고쳐진 탓에 과적합이다. 계층·편제·낫표
    표기가 모두 다른 도메인을 별도로 잠가, 한쪽만 통과하는 상태를 막는다.
    """

    RULE = "전남대학교 전임교원 겸직에 관한 지침"

    @classmethod
    def setUpClass(cls) -> None:
        cls.index = RuleSearchIndex()
        cls.article_1 = get_article(cls.RULE, "제1조")

    def test_12_13_external_law_classified_with_name(self) -> None:
        result = get_related_articles(self.article_1["record_id"], direction="both", resolve=False)
        external = [e for e in result["unresolved"] if e["kind"] == "external_law"]
        self.assertEqual(4, len(external), result["unresolved"])
        raws = " ".join(e["raw"] for e in external)
        for law in ("국가공무원법", "교육공무원법", "교육공무원임용령", "국가공무원 복무규정"):
            self.assertIn(law, raws)
        self.assertFalse([e for e in result["unresolved"] if e["kind"] == "same_rule"])

    def test_14_mixed_clause_markers_recognised(self) -> None:
        from src.search import CLAUSE_NUMBER

        body = get_article(self.RULE, "제5조의2")["article"]["본문"]
        numbers = [CLAUSE_NUMBER[ch] for ch in body if ch in CLAUSE_NUMBER]
        self.assertEqual([1, 2, 3, 4], numbers, "마커 계열이 섞여도 항 4개로 인식되어야 한다")

    def test_15_16_item_level_repeal(self) -> None:
        five = get_article(self.RULE, "제5조")["article"]["repealed_items"]
        self.assertEqual(
            [{"clause": "①", "item": "1", "repealed_date": "2024-03-22"}], five
        )
        six = get_article(self.RULE, "제6조")["article"]["repealed_items"]
        self.assertEqual(
            [{"clause": "②", "item": "5", "repealed_date": "2024-03-22"}], six
        )

    def test_17_22_attachment_now_resolved_in_guideline_tier(self) -> None:
        # T22 이후 지침 계층 별표는 코퍼스에 있으므로 미수집이 아니라 **해소**된다.
        # 규정 계층(law.go.kr)은 여전히 이미지 정본이라 미수집으로 남는다.
        guide = get_related_articles(
            get_article(self.RULE, "제9조")["record_id"], direction="outbound", resolve=False
        )
        resolved = [e for e in guide["outbound"] if e["kind"] == "attachment"]
        self.assertTrue(resolved, guide)
        self.assertTrue(resolved[0]["resolved"])
        self.assertFalse(
            [e for e in guide["unresolved"] if e["kind"] == "attachment_not_collected"]
        )
        regulation = get_related_articles(
            get_article("전남대학교 학칙", "제44조")["record_id"],
            direction="outbound", resolve=False,
        )
        self.assertEqual(
            "image_only",
            [e for e in regulation["unresolved"] if e["kind"] == "attachment_not_collected"][0]["reason_code"],
        )

    def test_18_multiple_laws_in_one_article(self) -> None:
        result = get_related_articles(
            get_article(self.RULE, "제5조")["record_id"], direction="outbound", resolve=False
        )
        self.assertFalse(
            [e for e in result["unresolved"] if e["kind"] == "same_rule"],
            "법령 인용이 자기 규정 참조로 떨어지면 없는 조문을 지목하게 된다",
        )

    def test_11_gyeomjik_search_has_no_false_positive(self) -> None:
        hits = self.index.search("전임교원 겸직 허가 기준", k=8)
        self.assertTrue(hits)
        self.assertTrue(
            all(self.RULE == r["규정명"] for r in hits),
            [(r["규정명"], r["조문번호"]) for r in hits],
        )


@unittest.skipUnless(DEFAULT_CORPUS_PATH.exists(), "코퍼스 미수집 환경")
class V15GoldenCaseTest(unittest.TestCase):
    """T19~T24 — v1.5 신규 골든 케이스."""

    JINGGYE_4 = "rule-2200000137431-4ce6a5cd4a9fcd14"   # 학생 징계 규정 제4조
    JINGGYE_2 = "rule-2200000137431-7678ae43c1f395c3"   # 학생 징계 규정 제2조

    @classmethod
    def setUpClass(cls) -> None:
        cls.index = RuleSearchIndex()

    def test_12_13_rule_level_reference_not_dropped(self) -> None:
        for record_id in (self.JINGGYE_4, self.JINGGYE_2):
            with self.subTest(record_id=record_id):
                result = get_related_articles(record_id, direction="both", resolve=False)
                refs = result["outbound"] + result["unresolved"]
                self.assertTrue(any("인권센터" in e["raw"] for e in refs), refs)
                self.assertTrue(any(e.get("kind") == "rule_level" for e in refs), refs)

    def test_12_article_level_reference_still_resolves(self) -> None:
        result = get_related_articles(self.JINGGYE_4, direction="both", resolve=False)
        self.assertTrue(
            any(e["raw"] == "제2조" and e["resolved"] for e in result["outbound"])
        )

    def test_15_16_list_articles_is_exhaustive(self) -> None:
        result = list_articles("전남대학교 학생 징계 규정")
        self.assertEqual(14, result["조문_수"])
        self.assertIn("제14조", [a["조문번호"] for a in result["articles"]])
        guide = [a["조문번호"] for a in list_articles("전남대학교 전임교원 겸직에 관한 지침")["articles"]]
        self.assertLess(guide.index("제5조"), guide.index("제5조의2"))
        self.assertLess(guide.index("제5조의2"), guide.index("제6조"))

    def test_17_supplementary_split_from_main(self) -> None:
        article = get_article("연구소 평가 지침", "제1조")["article"]
        self.assertEqual("목적", article["조문제목"])
        self.assertEqual("본칙", article["record_type"])

    def test_18_no_untitled_main_article(self) -> None:
        # 삭제 조문은 제목이 없는 것이 정상이므로 제외한다.
        untitled = [
            (r["규정명"], r["조문번호"])
            for r in self.index.articles
            if r.get("record_type") == "본칙"
            and not str(r.get("조문제목", "")).strip()
            and not r.get("is_repealed")
        ]
        self.assertEqual([], untitled)

    def test_19_damaged_clause_marker_is_flagged(self) -> None:
        article = get_article("전남대학교 학술연구진흥에관한규정", "제48조")["article"]
        self.assertIsNotNone(article["text_integrity"])
        self.assertIn("원문자_추정", article["text_integrity"]["kinds"])
        self.assertTrue(article.get("clause_index_undetermined"))

    def test_22_attachment_collected_for_guideline_tier(self) -> None:
        attachments = [a for a in self.index.articles if a.get("record_type") == "별표"]
        self.assertGreaterEqual(len(attachments), 80)
        self.assertTrue(all(a.get("수집방법") == "auto" for a in attachments))
        # v1.6부터 별표는 검색 기본 제외다(T27) — 켜야 나온다.
        hits = self.index.search("겸직 허가절차 제출서류", k=5, include_attachments=True)
        self.assertTrue(any(r.get("record_type") == "별표" for r in hits), hits)
        self.assertFalse(
            any(r.get("record_type") == "별표"
                for r in self.index.search("겸직 허가절차 제출서류", k=5))
        )


@unittest.skipUnless(DEFAULT_CORPUS_PATH.exists(), "코퍼스 미수집 환경")
class V16GoldenCaseTest(unittest.TestCase):
    """T27~T31 — v1.6 신규 골든 케이스.

    이번 회차의 교훈은 '기능 추가가 다른 기능을 오염시킬 수 있다'였다. 별표 수집
    자체는 명세대로였는데, 검색 노출 방식을 함께 설계하지 않아 전 도메인의 응답
    품질을 떨어뜨렸다. 그래서 여기서는 **응답 규모**까지 회귀 대상으로 잠근다.
    """

    ATTACHMENT_QUERY = "계약 체결 수의계약 금액 기준"
    RESEARCH_FUND = "전남대학교 연구비 중앙관리지침"
    LIBRARY = "전남대학교 도서관 규정"

    @classmethod
    def setUpClass(cls) -> None:
        cls.index = RuleSearchIndex()

    def test_13_attachment_body_does_not_flood_search(self) -> None:
        result = search_rule(self.ATTACHMENT_QUERY, k=8)
        self.assertEqual([], [r for r in result["results"] if r.get("record_type") == "별표"])
        total = sum(len(str(r["본문"])) for r in result["results"])
        self.assertLess(total, 3_000, "별표 본문이 응답을 잠식하면 안 된다")

    def test_14_omitted_attachment_is_announced(self) -> None:
        result = search_rule(self.ATTACHMENT_QUERY, k=8)
        self.assertGreaterEqual(result["attachments_omitted"], 1)
        self.assertEqual(result["attachments_omitted"], len(result["attachments"]))
        for row in result["attachments"]:
            self.assertTrue(row["record_id"])
            self.assertTrue(str(row["조문제목"]).strip(), "제외 별표는 제목으로 식별 가능해야 한다")

    def test_13_attachment_body_truncated_when_included(self) -> None:
        result = search_rule(self.ATTACHMENT_QUERY, k=8, include_attachments=True)
        attachments = [r for r in result["results"] if r.get("record_type") == "별표"]
        self.assertTrue(attachments)
        for row in attachments:
            self.assertLessEqual(len(row["본문"]), 200)
            self.assertTrue(row["본문_절단"])
            self.assertGreater(row["본문_길이"], 200)

    def test_15_attachment_full_text_via_get_article(self) -> None:
        article = get_article(self.RESEARCH_FUND, "별표 3")["article"]
        self.assertIsNotNone(article)
        self.assertGreater(len(article["본문"]), 20_000)

    def test_16_attachment_has_title(self) -> None:
        article = get_article(self.RESEARCH_FUND, "별표 3")["article"]
        self.assertEqual("연구비 비목별 계상 및 집행기준", article["조문제목"])
        self.assertEqual({"조문번호": "제22조", "항": "③"}, article.get("위임조문"))

    def test_17_attachment_appears_in_inbound_of_delegating_article(self) -> None:
        record_id = get_article(self.RESEARCH_FUND, "제22조")["record_id"]
        inbound = get_related_articles(record_id, direction="inbound", resolve=False)["inbound"]
        self.assertIn("별표 3", [row["source_article"] for row in inbound])

    def test_18_no_untitled_record_except_source_side_blanks(self) -> None:
        # 제목 없는 레코드는 **삭제 조문과 부칙 시행일 조항뿐**이어야 한다.
        # 이 둘은 원문에 제목이 없다(원문 대조 확인) — 채우면 날조다.
        untitled = [
            (r["규정명"], r["조문번호"], r.get("record_type"))
            for r in self.index.articles
            if not str(r.get("조문제목", "")).strip()
            and not r.get("is_repealed")
            and r.get("record_type") != "부칙"
        ]
        self.assertEqual([], untitled)
        self.assertEqual(
            [],
            [r["규정명"] for r in self.index.articles
             if r.get("record_type") == "별표" and not str(r.get("조문제목", "")).strip()],
        )

    def test_19_unnamed_delegation_is_surfaced(self) -> None:
        record_id = get_article(self.LIBRARY, "제20조")["record_id"]
        result = get_related_articles(record_id, direction="both", resolve=False)
        delegations = [e for e in result["unresolved"] if e["kind"] == "unnamed_delegation"]
        self.assertEqual(3, len(delegations), delegations)
        self.assertEqual(["②", "③", "④"], [e["clause"] for e in delegations])

    def test_19_named_delegation_is_not_unnamed(self) -> None:
        # '별표와 같다'처럼 위임처가 명시된 조문은 무지정 위임이 아니다.
        record_id = get_article("전남대학교 전임교원 겸직에 관한 지침", "제9조")["record_id"]
        result = get_related_articles(record_id, direction="outbound", resolve=False)
        self.assertEqual(
            [], [e for e in result["unresolved"] if e["kind"] == "unnamed_delegation"]
        )

    def test_20_external_law_not_reported_as_missing_rule(self) -> None:
        record_id = get_article("전남대학교 행정감사규정", "제16조")["record_id"]
        result = get_related_articles(record_id, direction="both", resolve=False)
        law = [e for e in result["unresolved"] if "회계관계직원" in e["raw"]]
        self.assertTrue(law)
        self.assertEqual("external_law", law[0]["kind"])
        self.assertEqual(4, len(result["inbound"]))

    def test_21_coverage_is_reported_with_its_denominator(self) -> None:
        stats = get_corpus_stats()
        self.assertEqual(stats["조문_수"], stats["색인_문서_수"])
        self.assertTrue(stats["수집_범위_기준일"])
        self.assertGreater(stats["게시_규정_수"], stats["규정_수"] - 1)
        self.assertTrue(stats["수집_공백_편제"], "공백을 숨기지 않는다")

    def test_7_list_rules_still_reports_chongmugwa(self) -> None:
        # v1.8.0에서 '각종행사관련 총장우등상 …' 지침이 항목식으로 편입돼 10건이 됐다.
        self.assertEqual(10, list_rules(division="총무과")["count"])

    def test_11_12_domain_queries_have_no_false_positive(self) -> None:
        # 규정명 목록으로 잠그지 않는다. 코퍼스가 늘면 정답 규정도 늘기 때문이다
        # (지침 전량 확장 후 '정보보안기본지침 제8조(정보보안 감사)'가 합류했는데
        #  이것은 오탐이 아니라 정답이다). '오탐 0'의 실질은 **주제어가 걸렸는가**다.
        for query, topic in (
            ("도서관 자료 대출 연체 변상", ("도서관", "자료", "대출")),
            ("감사 실시 결과 처리", ("감사",)),
        ):
            with self.subTest(query=query):
                hits = self.index.search(query, k=8)
                self.assertTrue(hits)
                off_topic = [
                    (row["규정명"], row["조문번호"])
                    for row in hits
                    if not any(
                        word in row["규정명"] or word in str(row["조문제목"]) or word in row["본문"]
                        for word in topic
                    )
                ]
                self.assertEqual([], off_topic)


@unittest.skipUnless(DEFAULT_CORPUS_PATH.exists(), "코퍼스 미수집 환경")
class AdvisoryTest(unittest.TestCase):
    """v1.9.0 — 함정을 문서가 아니라 응답이 알린다.

    회차마다 발견한 함정을 소비자 스킬 문서에 적어 왔는데, 그 방식은 코퍼스가
    바뀌면 문서가 먼저 틀리고 문서를 읽지 않은 소비자에게는 전달되지 않는다.
    **결과에서 판정 가능한 함정은 응답이 알린다.**
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.index = RuleSearchIndex()

    def _codes(self, result: dict) -> set:
        return {a["code"] for a in result.get("advisories", [])}

    def test_item_style_result_points_to_upstream_norm(self) -> None:
        result = search_rule("재입학 허가 신청", k=8)
        self.assertIn("upstream_norm_check", self._codes(result))
        advisory = next(a for a in result["advisories"] if a["code"] == "upstream_norm_check")
        # 검색 상위에 없어도 상위 규범에 record_id로 닿아야 한다 — 이것이 계약이다.
        upstream = {(u["규정명"], u["조문번호"]) for u in advisory["upstream"]}
        self.assertIn(("전남대학교 학칙", "제30조"), upstream)
        self.assertTrue(all(u["record_id"] for u in advisory["upstream"]))

    def test_duplicate_titles_are_flagged(self) -> None:
        # 교육혁신본부 운영 지침은 제2~5조 제목이 모두 '업무'이고 센터 구분은 장에만 있다.
        result = search_rule("교육혁신본부 센터 업무", k=6)
        self.assertIn("duplicate_article_title", self._codes(result))

    def test_superseded_only_flagged_when_included(self) -> None:
        self.assertNotIn("superseded_included", self._codes(search_rule("수강신청 정정", k=6)))
        self.assertIn(
            "superseded_included",
            self._codes(search_rule("수강신청 정정", k=6, include_superseded=True)),
        )

    def test_clean_result_has_no_advisories(self) -> None:
        # 신호는 붙을 때만 붙는다. 정상 결과에 잡음을 얹지 않는다.
        self.assertNotIn("advisories", search_rule("도서관 자료 대출 연체 변상", k=5))


@unittest.skipUnless(DEFAULT_CORPUS_PATH.exists(), "코퍼스 미수집 환경")
class V191PatchTest(unittest.TestCase):
    """8/17 Codex findings F1~F3 — 수정 전 재현을 먼저 잠근다 (v1.9.1 게이트)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.index = RuleSearchIndex()

    @staticmethod
    def _fixture_refs(body: str):
        from src.references import ReferenceIndex

        article = {
            "규정명": "테스트 지침", "조문번호": "제1조", "조문제목": "테스트",
            "본문": body, "source_key": "9999", "record_id": "rule-9999-t",
        }
        index = ReferenceIndex([article])
        return index.outbound(article, resolve=False)

    def test_f1_common_noun_ending_beop_is_not_a_law(self) -> None:
        # '평가방법'은 법령이 아니다. 법령으로 분류하면 "코퍼스 밖 법령"이라는
        # 자신 있는 오답이 된다(실측 152회).
        _, unresolved = self._fixture_refs("성과 평가방법은 위원회의 심의를 거친다.")
        wrong = [e for e in unresolved if "평가방법" in e["raw"]
                 and e["kind"].startswith("external_law")]
        self.assertEqual([], wrong, unresolved)

    def test_f1_real_law_suffix_still_detected(self) -> None:
        _, unresolved = self._fixture_refs("「근로기준법」 제50조를 준용한다.")
        self.assertTrue(any(e["kind"] == "external_law" for e in unresolved), unresolved)

    def test_f2_delegation_not_killed_by_earlier_reference_in_paragraph(self) -> None:
        # 같은 문단 앞 문장의 낫표 인용이 뒷문장의 무지정 위임을 지우면 안 된다.
        # 실제 사례(조직 설치 규정 제3조의12): 두 문장이 한 줄에 붙어 있다.
        body = ("① 「소프트웨어 중심대학」사업의 원활한 추진을 위하여 "
                "소프트웨어교육원을 총장 직속기구로 둔다."
                "② 소프트웨어교육원의 조직 및 운영에 관한 사항은 따로 정한다.")
        _, unresolved = self._fixture_refs(body)
        found = [e for e in unresolved if e["kind"] == "unnamed_delegation"]
        self.assertEqual(1, len(found), unresolved)
        self.assertEqual("②", found[0]["clause"])

    def test_f2_named_delegation_in_same_sentence_still_suppressed(self) -> None:
        _, unresolved = self._fixture_refs("수당 지급은 제5조에서 따로 정한다.")
        self.assertEqual(
            [], [e for e in unresolved if e["kind"] == "unnamed_delegation"], unresolved
        )

    def test_f3_omitted_counts_every_attachment_above_returned_articles(self) -> None:
        # 반환된 마지막 조문보다 관련도가 높은 별표는 전부 보고되어야 한다.
        # 상위 k 창 안만 세면 뒤에서 보충된 조문 위의 별표가 조용히 사라진다.
        result = self.index.search_detailed("귀하", k=5)
        deep = self.index.search_detailed("귀하", k=20, include_attachments=True)["results"]
        articles = [r for r in deep if r.get("record_type") != "별표"][:5]
        self.assertTrue(articles)
        cut = deep.index(articles[-1])
        expected = len([r for r in deep[:cut] if r.get("record_type") == "별표"])
        self.assertEqual(expected, result["attachments_omitted"])
        self.assertEqual(result["attachments_omitted"], len(result["attachments"]))


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

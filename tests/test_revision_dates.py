"""W3 Phase A 「최근 개정일」 필드 — 파서와 부착 규칙. 네트워크/코퍼스 불필요."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import annotate_revision_dates as ard
from scripts import fetch_regulation_heads as frh

# 2026-11-10 법제처 연혁 응답 발췌(계절학기 운영 규정). 'N' 표기 행도 섞는다.
HIST_HTML = """<ul>
<li><a href="#AJAX" onclick="javascript:schlPubRulViewHst('Y','2200000146199','schlPubRul');return false;" class="on"> 1. 전남대학교 계절학기 운영 규정<br />
 <div class="subtit1_1">
   [시행 2025. 3. 20.] [전남대학교학교규정 제2095호, 2025. 3. 20., 타법개정]
 </div> </a></li>
<li><a href="#AJAX" onclick="javascript:schlPubRulViewHst('N','2200000134505','schlPubRul');return false;"> 2. 전남대학교 계절학기 운영 규정<br />
 <div class="subtit1_1">
   [시행 2024. 8. 31.] [전남대학교학교규정 제2046호, 2024. 8. 31., 일부개정]
 </div> </a></li>
</ul>"""


class HistoryParseTest(unittest.TestCase):
    def test_rows_pair_seq_with_own_head_including_n_rows(self):
        rows = frh.parse_history(HIST_HTML)
        self.assertEqual([r["seq"] for r in rows], ["2200000146199", "2200000134505"])
        self.assertEqual(rows[1]["head"],
                         "[시행 2024. 8. 31.] [전남대학교학교규정 제2046호, 2024. 8. 31., 일부개정]")

    def test_head_fields(self):
        self.assertEqual(
            ard.parse_head("[시행 2025. 3. 20.] [전남대학교학교규정 제2095호, 2025. 3. 20., 타법개정]"),
            {"시행일": "2025. 3. 20.", "공포일": "2025. 3. 20.", "구분": "타법개정"})
        # 시행일과 공포일이 다른 머리글 — 개정일은 공포일 쪽이다.
        self.assertEqual(
            ard.parse_head("[시행 2026. 3. 1.] [전남대학교학교규정 제2150호, 2026. 2. 10., 일부개정]")["공포일"],
            "2026. 2. 10.")
        self.assertIsNone(ard.parse_head("[시행 미상]"))


class GuidelineSourcesTest(unittest.TestCase):
    def test_old_version_labels(self):
        labels = ard.old_label_dates({
            "수업관리 지침 (2025. 10. 1. 개정전)", "수업관리 지침(2015. 7 14. 개정전)",
            "수업관리 지침(2012. 12. 26. 제정)",       # 제정 표기는 개정일이 아니다
            "어떤 지침 (2024. 1. 10. 개전전)",          # 실제 오탈자 표기
            "수업관리 지침"})
        self.assertEqual(sorted(labels["수업관리지침"], key=ard.key), ["2015. 7. 14.", "2025. 10. 1."])
        self.assertEqual(labels["어떤지침"], ["2024. 1. 10."])

    def test_addenda_headings(self):
        md = "### 부칙(2013. 3. 6.)\n이 지침은 …\n### 부  칙 (2024.7.15.)\n본문 중 부칙(2030. 1. 1.)은 표제가 아님"
        self.assertEqual(ard.addenda_dates(md), ["2013. 3. 6.", "2024. 7. 15."])

    def test_real_addenda_variants(self):
        # 2026-11-10 지침 원문에서 놓쳤던 표기들.
        md = "\n".join([
            "부칙(제정 2005. 5. 1)", "부칙(개정 2011. 1. 24)", "부칙 <제3호, 2011. 12. 27>",
            "**부 칙(2024. 10. 16.)**", "부 칙(시행일 2022. 10. 28.)",
            "부 칙(제OOOO호, 2022. 0. 0.)",  # 서식 자리표시 — 날짜 아님
        ])
        self.assertEqual(ard.addenda(md), [
            ("2005. 5. 1.", "제정"), ("2011. 1. 24.", ""), ("2011. 12. 27.", ""),
            ("2024. 10. 16.", "")])  # '(시행일 …)' 표제는 개정일이 아니다(코드 리뷰 #2)

    def test_history_lines_at_head(self):
        # 공익신고 운영지침: 부칙 최신 2020. 6. 1. — 이력 줄 2024. 7. 26.이 더 늦다(2026-11-10 Codex 대조).
        md = "제정 2014. 09. 29.\n\n개정 2024. 07. 26.\n| 제정 2024. 07. 16. |  |  |\n제정 2015. 11. 9 .\n" \
             "제3조 … <개정 2030. 1. 1.>\n### 부칙(2020. 6. 1.)\n"
        self.assertEqual(ard.history_lines(md), [
            ("2014. 9. 29.", "제정"), ("2024. 7. 26.", "개정"), ("2024. 7. 16.", "제정"), ("2015. 11. 9.", "제정")])
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "key_4892.md").write_text("제정 2014. 09. 29.\n개정 2024. 07. 26.\n### 부칙(2020. 6. 1.)\n", encoding="utf-8")
            with patch.object(ard, "MARKDOWN", Path(tmp)):
                f = ard.guideline_fields("공익신고 지침", "4892", {})
        self.assertEqual(f["규정_최근개정일"], "2024. 7. 26.")
        self.assertNotIn("규정_개정구분", f)

    def test_history_line_variants(self):
        md = "3차 개정 2024.07.11.\n제10차 개정 2025. 9. 18.\n일부개정 2025. 12. 08.\n전부개정 2022. 12. 16.\n" \
             "2014. 9. 1. 개정\n개정 2022. 09. .\n"  # 마지막 줄은 일 공란 — 날짜 아님
        self.assertEqual(ard.history_lines(md), [
            ("2024. 7. 11.", "개정"), ("2025. 9. 18.", "개정"), ("2025. 12. 8.", "개정"),
            ("2022. 12. 16.", "개정"), ("2014. 9. 1.", "개정")])

    def test_enactment_only_is_labelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "key_7.md").write_text("부칙(제정 2011. 9. 28)\n", encoding="utf-8")
            with patch.object(ard, "MARKDOWN", Path(tmp)):
                f = ard.guideline_fields("다 세칙", "7", {})
        self.assertEqual((f["규정_최근개정일"], f["규정_개정구분"]), ("2011. 9. 28.", "제정"))

    def test_later_source_wins_and_disagreement_is_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "key_9.md").write_text("### 부칙(2024. 7. 15.)\n", encoding="utf-8")
            with patch.object(ard, "MARKDOWN", Path(tmp)):
                f = ard.guideline_fields("가 지침", "9", {"가지침": ["2025. 10. 1."]})
                self.assertEqual(f["규정_최근개정일"], "2025. 10. 1.")
                self.assertIn("출처마다 날짜가 다름(2024. 7. 15. / 2025. 10. 1.)", f["규정_개정일_근거"])
                self.assertIsNone(ard.guideline_fields("나 지침", "10", {}))  # 출처 없으면 붙이지 않는다


class ReviewFixesTest(unittest.TestCase):
    """2026-11-10 Codex 코드 리뷰 #1·#2·#3·#7·#9 회귀."""

    def test_enforcement_date_headings_are_not_revision_dates(self):  # #2
        md = "개정 2024. 1. 1.\n부칙(시행일 2025. 1. 1.)\n부 칙(적용일 2026. 3. 1.)\n"
        self.assertEqual(ard.addenda(md), [])
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "key_1.md").write_text(md, encoding="utf-8")
            with patch.object(ard, "MARKDOWN", Path(tmp)):
                self.assertEqual(ard.guideline_fields("가", "1", {})["규정_최근개정일"], "2024. 1. 1.")

    def test_dates_below_attachments_or_cited_laws_are_ignored(self):  # #3
        md = "개정 2020. 1. 1.\n### 부칙(2020. 1. 1.)\n# 참고 법령\n### 부칙(2030. 1. 1.)\n" \
             "[별표 1] 서식\n개정 2031. 1. 1.\n"
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "key_2.md").write_text(md, encoding="utf-8")
            with patch.object(ard, "MARKDOWN", Path(tmp)):
                self.assertEqual(ard.guideline_fields("나", "2", {})["규정_최근개정일"], "2020. 1. 1.")
        # 본문의 '참고' 낱말로 시작하는 줄에서는 자르지 않는다.
        self.assertIn("부칙(2021. 1. 1.)", ard.own_text("참고 사항은 따로 정한다.\n부칙(2021. 1. 1.)\n"))

    def test_missing_regulation_head_stops_without_writing(self):  # #1
        with tempfile.TemporaryDirectory() as tmp:
            corpus = Path(tmp, "c.json"); heads = Path(tmp, "h.json")
            rows = [{"규정명": "가 규정", "source_key": "2200000000009", "본문": "x", "record_id": "r1"}]
            corpus.write_text(ard.json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            heads.write_text('{"heads": {}}', encoding="utf-8")
            before = corpus.read_text(encoding="utf-8")
            with patch.object(ard, "CORPUS", corpus), patch.object(ard, "HEADS", heads), \
                 patch.object(ard, "current_rules", lambda _p: {("가 규정", "2200000000009")}), \
                 patch.object(sys, "argv", ["annotate"]):
                self.assertEqual(ard.main(), 3)
            self.assertEqual(corpus.read_text(encoding="utf-8"), before)

    def test_failed_fetch_keeps_previous_good_head(self):  # #7
        with tempfile.TemporaryDirectory() as tmp:
            corpus = Path(tmp, "c.json"); out = Path(tmp, "h.json")
            corpus.write_text('[{"규정명": "가", "source_key": "2200000146199", '
                              '"source_url": "https://www.law.go.kr/x"}]', encoding="utf-8")
            good = {"head": "[시행 2025. 3. 20.] [전남대학교학교규정 제2095호, 2025. 3. 20., 타법개정]"}
            out.write_text(frh.json.dumps({"heads": {"2200000146199": good}}, ensure_ascii=False), encoding="utf-8")
            with patch.object(frh, "CORPUS", corpus), patch.object(frh, "OUT", out), \
                 patch.object(frh.cg, "fetch", lambda *a, **k: "<html>오류 페이지</html>"), \
                 patch.object(frh.time, "sleep", lambda _s: None), patch.object(sys, "argv", ["fetch"]):
                self.assertEqual(frh.main(), 1)
            self.assertEqual(frh.json.loads(out.read_text(encoding="utf-8"))["heads"]["2200000146199"], good)

    def test_date_only_change_is_reported_as_change(self):  # #9
        sys.path.insert(0, str(ROOT / "scripts"))
        import refresh_corpus as rc
        with tempfile.TemporaryDirectory() as tmp:
            corpus = Path(tmp, "c.json")
            row = {"규정명": "가", "record_id": "r1", "규정_최근개정일": "2024. 1. 1."}
            corpus.write_text(rc.json.dumps([row], ensure_ascii=False), encoding="utf-8")
            with patch.object(rc, "CORPUS", corpus):
                before = rc.corpus_summary()
                row["규정_최근개정일"] = "2025. 1. 1."
                corpus.write_text(rc.json.dumps([row], ensure_ascii=False), encoding="utf-8")
                self.assertNotEqual(before, rc.corpus_summary())


class AnnotateTest(unittest.TestCase):
    def test_current_only_and_idempotent(self):
        heads = {"2200000000001": {"head": "[시행 2020. 7. 1.] [전남대학교학교규정 제1781호, 2020. 7. 1., 일부개정]"}}
        corpus = [
            {"규정명": "가 규정", "source_key": "2200000000001", "본문": "x"},
            {"규정명": "가 규정 (2020. 7. 1. 개정전)", "source_key": "2200000000000", "본문": "x",
             "규정_최근개정일": "남은 값"},
        ]
        current = {("가 규정", "2200000000001")}
        for _ in range(2):
            ard.annotate(corpus, current, heads)
        self.assertEqual(corpus[0]["규정_최근개정일"], "2020. 7. 1.")
        self.assertEqual(corpus[0]["규정_개정구분"], "일부개정")
        self.assertNotIn("규정_최근개정일", corpus[1])  # 현행이 아니면 지운다


if __name__ == "__main__":
    unittest.main()

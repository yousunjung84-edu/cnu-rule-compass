"""월간 갱신 게이트 회귀: 모든 입출력은 임시 경로와 가짜 실행으로 격리한다."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from scripts import collect_guidelines_full as collector
from scripts import fetch_regulation_heads as heads
from scripts import annotate_revision_dates as dates

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import refresh_corpus as rc


class RefreshGatesTest(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.tmp = Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir=Path(__file__).parent)))
        for name in ("CORPUS", "REPORT", "CANDIDATE", "PENDING", "FAILURES", "KNOWN_FAILURES", "HEADS"):
            self.stack.enter_context(patch.object(rc, name, self.tmp / name))
        self.stack.enter_context(patch.object(rc, "log"))
        self.stack.enter_context(patch.object(rc.subprocess, "run"))
        self.stack.enter_context(patch.object(rc, "reconcile_names", return_value={"개명": 0}))
        self.stack.enter_context(patch.object(rc, "build_current_map", side_effect=lambda p, rows: {r["record_id"]: True for r in rows}))
        self.stack.enter_context(patch.object(dates, "current_rules", return_value={("규정", "1")}))
        self.stack.enter_context(patch.object(sys, "argv", ["refresh"]))
        self.rows = [{"record_id": "가", "규정명": "규정", "source_key": "1", "본문": "이전", "조문번호": "제1조"}]
        self.write(rc.CORPUS, self.rows)
        self.write(rc.HEADS, {"heads": {}})
        self.write(rc.KNOWN_FAILURES, {})
        self.calls = []

    def write(self, path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def execute(self, collect_code=0, head_code=0, other=None):
        def fake(args, timeout=0):
            self.calls.append(args)
            code = 0
            if "scripts/collect_guidelines_full.py" in args:
                self.rows[0]["본문"] = "변경"
                self.write(rc.CORPUS, self.rows)
                code = collect_code
            if "scripts/fetch_regulation_heads.py" in args:
                code = head_code
            if other and other in args:
                code = 3
            return subprocess.CompletedProcess(args, code, "완료", "실패" if code else "")
        with patch.object(rc, "run", side_effect=fake):
            code = rc.main()
        return code, json.loads(rc.REPORT.read_text(encoding="utf-8"))

    def test_부분_수집은_보존하고_배포를_막는다(self):
        self.write(rc.FAILURES, {"source_key": "누락", "error": "다운로드 실패"})
        code, report = self.execute(collect_code=3)
        self.assertEqual(code, 0)
        self.assertEqual(report["결과"], "부분 수집·배포 보류")
        self.assertEqual(report["수집"]["실패_건수"], 1)
        self.assertNotIn("배포_명령", report)
        self.assertEqual(json.loads(rc.CORPUS.read_text())[0]["본문"], "변경")
        self.assertTrue(any("unittest" in args for args in self.calls))

    def test_허용된_실패만_있으면_배포_대기(self):
        self.write(rc.FAILURES, {"source_key": "누락", "error": "본문 없음"})
        self.write(rc.KNOWN_FAILURES, {"누락": {"사유": "조문 없는 서식", "등록일": "2026-11-11"}})
        _, report = self.execute(collect_code=3)
        self.assertEqual(report["결과"], "성공·배포 대기")
        self.assertEqual(report["수집"]["허용된 실패_건수"], 1)
        self.assertIn("배포_명령", report)

    def test_허용과_미허용이_섞이면_미허용만_보류(self):
        rc.FAILURES.write_text('\n'.join(json.dumps({"source_key": key}) for key in ("허용", "누락")))
        self.write(rc.KNOWN_FAILURES, {"허용": {"사유": "조문 없는 서식", "등록일": "2026-11-11"}})
        _, report = self.execute(collect_code=3)
        self.assertEqual(report["수집"]["실패_건수"], 2)
        self.assertEqual(report["수집"]["보류_실패_건수"], 1)
        self.assertEqual(report["수집"]["허용된 실패_건수"], 1)
        self.assertNotIn("배포_명령", report)

    def test_부분수집이어도_테스트실패는_원복(self):
        self.write(rc.FAILURES, {"source_key": "누락"})
        before = rc.CORPUS.read_bytes()
        code, report = self.execute(collect_code=3, other="unittest")
        self.assertEqual(code, 1)
        self.assertEqual(rc.CORPUS.read_bytes(), before)
        self.assertEqual(report["수집"]["실패_건수"], 1)
        self.assertNotIn("배포_명령", report)

    def test_변경없어도_보류를_유지(self):
        self.write(rc.FAILURES, {"source_key": "누락"})
        self.rows[0]["본문"] = "변경"
        self.write(rc.CORPUS, self.rows)
        _, report = self.execute(collect_code=3)
        self.assertEqual(report["결과"], "부분 수집·배포 보류")
        self.assertEqual(report["이전"], report["이후"])

    def test_연혁_시간초과도_경고(self):
        def fake(args, timeout=0):
            if "scripts/fetch_regulation_heads.py" in args:
                raise subprocess.TimeoutExpired(args, 3600)
            return subprocess.CompletedProcess(args, 0, "완료", "")
        with patch.object(rc, "run", side_effect=fake):
            self.assertEqual(rc.main(), 0)
        report = json.loads(rc.REPORT.read_text())
        self.assertIn("시간 초과", report["경고"][0])
        self.assertEqual(report["결과"], "변경 없음")

    def test_다른_단계의_동일_종료코드는_원복(self):
        before = rc.CORPUS.read_bytes()
        code, report = self.execute(other="scripts/normalize_record_types.py")
        self.assertEqual(code, 1)
        self.assertEqual(rc.CORPUS.read_bytes(), before)
        self.assertTrue(report["결과"].startswith("실패·원복"))

    def test_본문만_변경해도_이전_이후_지문과_배포명령(self):
        _, report = self.execute()
        self.assertEqual(report["변경"], {"조문": 0, "규정": 0})
        self.assertNotEqual(report["이전"]["본문_지문"], report["이후"]["본문_지문"])
        self.assertEqual(len(report["이후"]["본문_지문"]), 64)
        self.assertIn("배포_명령", report)

    def test_순서와_키_순서는_지문에_영향_없음(self):
        rows = self.rows + [{**self.rows[0], "record_id": "나"}]
        self.write(rc.CORPUS, rows)
        before = rc.corpus_summary()
        self.write(rc.CORPUS, [dict(reversed(list(r.items()))) for r in reversed(rows)])
        self.assertEqual(before, rc.corpus_summary())

    def test_연혁_실패는_경고이며_개정일_앞에서_실행(self):
        code, report = self.execute(head_code=1)
        self.assertEqual(code, 0)
        self.assertIn("경고", report)
        self.assertEqual(report["결과"], "성공·배포 대기")
        scripts = [a[1] for a in self.calls]
        self.assertLess(scripts.index("scripts/fetch_regulation_heads.py"), scripts.index("scripts/annotate_revision_dates.py"))

    def test_현행_구판_의심은_실패시_이전_머리글로도_보류(self):
        self.write(rc.HEADS, {"heads": {"1": {"stale": True, "latest_seq": "2", "fetched_at": "이전"}, "3": {"stale": True}}})
        _, report = self.execute(head_code=1)
        self.assertEqual(report["결과"], "구판 의심·배포 보류")
        self.assertEqual([r["source_key"] for r in report["구판 의심"]], ["1"])
        self.assertNotIn("배포_명령", report)

    def test_구판_기록이_있어도_현행이_아니면_제외(self):
        self.write(rc.HEADS, {"heads": {"3": {"stale": True}}})
        _, report = self.execute()
        self.assertEqual(report["구판 의심"], [])
        self.assertIn("배포_명령", report)


class CollectorTest(unittest.TestCase):
    def test_성공분_보존_부분실패_종료와_이전실패_초기화(self):
        with contextlib.ExitStack() as stack:
            tmp = Path(stack.enter_context(tempfile.TemporaryDirectory(dir=Path(__file__).parent)))
            for name in ("CORPUS", "HWP_DIR", "MD_DIR", "FAILURES", "SNAPSHOT"):
                stack.enter_context(patch.object(collector, name, tmp / name))
            collector.CORPUS.write_text("[]", encoding="utf-8")
            stack.enter_context(patch.object(sys, "argv", ["collect"]))
            rules = [types.SimpleNamespace(key=k, name=k, division="부서", source_url=k) for k in ("1", "2")]
            listing = stack.enter_context(patch.object(collector, "fetch_listing", return_value=rules))
            stack.enter_context(patch.object(collector, "download_with_retry", side_effect=[None, OSError("다운로드 실패")]))
            stack.enter_context(patch.object(collector, "run_kordoc", return_value="본문"))
            stack.enter_context(patch.object(collector, "split_articles", return_value=[{"본문": "조문"}]))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            self.assertEqual(collector.main(), 3)
            self.assertEqual(len(json.loads(collector.CORPUS.read_text())), 1)
            self.assertEqual(json.loads(collector.FAILURES.read_text())["source_key"], "2")
            listing.return_value = rules[:1]
            self.assertEqual(collector.main(), 0)
            self.assertEqual(collector.FAILURES.read_text(), "")


class FetchHeadsTest(unittest.TestCase):
    def test_성공시각은_판본별이며_실패판본은_유지(self):
        with contextlib.ExitStack() as stack:
            tmp = Path(stack.enter_context(tempfile.TemporaryDirectory(dir=Path(__file__).parent)))
            corpus, out = tmp / "corpus", tmp / "heads"
            corpus.write_text(json.dumps([{"source_key": str(i), "규정명": "규정", "source_url": "https://law.go.kr"} for i in range(1, 5)]))
            old = {"head": "이전 머리글", "fetched_at": "2025-01-01T00:00:00+09:00"}
            out.write_text(json.dumps({"heads": {str(i): old for i in (1, 2, 3)}}))
            stack.enter_context(patch.object(heads, "CORPUS", corpus))
            stack.enter_context(patch.object(heads, "OUT", out))
            stack.enter_context(patch.object(sys, "argv", ["fetch"]))
            stack.enter_context(patch.object(heads.time, "sleep"))
            stack.enter_context(patch.object(heads.cg, "fetch", side_effect=["성공", OSError("연결 실패"), "누락", "누락"]))
            stack.enter_context(patch.object(heads, "parse_history", side_effect=[[{"seq": "1", "head": "새 머리글"}], [], []]))
            self.assertEqual(heads.main(), 1)
            result = json.loads(out.read_text())["heads"]
            self.assertNotEqual(result["1"]["fetched_at"], old["fetched_at"])
            self.assertEqual(result["2"], old)
            self.assertEqual(result["3"], old)
            self.assertNotIn("fetched_at", result["4"])

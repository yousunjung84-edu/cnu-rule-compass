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
        for name in ("CORPUS", "REPORT", "CANDIDATE", "PENDING", "FAILURES", "KNOWN_FAILURES", "HEADS", "SNAPSHOT"):
            self.stack.enter_context(patch.object(rc, name, self.tmp / name))
        self.stack.enter_context(patch.object(rc, "log"))
        self.stack.enter_context(patch.object(rc.subprocess, "run"))
        self.stack.enter_context(patch.object(rc, "reconcile_names", return_value={"개명": 0}))
        self.stack.enter_context(patch.object(rc, "check_core_version", return_value="0.7.1"))
        self.stack.enter_context(patch.object(rc, "build_current_map", side_effect=lambda p, rows: {r["record_id"]: True for r in rows}))
        self.stack.enter_context(patch.object(dates, "current_rules", return_value={("규정", "1")}))
        self.stack.enter_context(patch.object(sys, "argv", ["refresh"]))
        self.rows = [{"record_id": "가", "규정명": "규정", "source_key": "1", "본문": "이전", "조문번호": "제1조"}]
        self.write(rc.CORPUS, self.rows)
        self.write(rc.HEADS, {"heads": {}})
        self.write(rc.KNOWN_FAILURES, {})
        self.write(rc.SNAPSHOT, [])
        self.calls = []

    def write(self, path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def execute(self, collect_code=0, head_code=0, other=None, item_keys=()):
        def fake(args, timeout=0):
            self.calls.append(args)
            code = 0
            if "scripts/collect_guidelines_full.py" in args:
                self.rows[0]["본문"] = "변경"
                self.write(rc.CORPUS, self.rows)
                code = collect_code
            if "scripts/fetch_regulation_heads.py" in args:
                code = head_code
            if "scripts/collect_item_style.py" in args and item_keys:
                names = {str(f["source_key"]): f.get("규정명") for f in map(json.loads, rc.FAILURES.read_text().splitlines())}
                self.rows += [{"record_id": f"항목{k}", "규정명": names.get(k, "다른 이름"), "source_key": k, "본문": "가",
                               "조문번호": "1.", "record_type": "항목"} for k in item_keys]
                self.write(rc.CORPUS, self.rows)
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


    # 박사 결정 (가), 2026-11-11 — 현행 실패만 배포를 막고 구판 실패는 경고.
    def test_구판_실패만_있으면_배포_대기_경고는_남긴다(self):
        rc.FAILURES.write_text("\n".join(json.dumps({"source_key": k, "규정명": n}) for k, n in (
            ("1797", "교과과정 편성 및 운영지침(2008. 6. 25 개정전)"),
            ("1795", "교육공무원 국외여행 업무처리지침(2006. 9. 26. 개정전)"))), encoding="utf-8")
        _, report = self.execute(collect_code=3)
        self.assertEqual(report["결과"], "성공·배포 대기")
        self.assertEqual(report["수집"]["구판 실패_건수"], 2)
        self.assertEqual(report["수집"]["보류_실패_건수"], 0)
        self.assertIn("배포_명령", report)

    def test_현행_실패가_섞이면_보류(self):
        rc.FAILURES.write_text("\n".join(json.dumps({"source_key": k, "규정명": n}) for k, n in (
            ("1797", "교과과정 편성 및 운영지침(2008. 6. 25 개정전)"),
            ("9001", "학사 운영 지침"))), encoding="utf-8")
        _, report = self.execute(collect_code=3)
        self.assertEqual(report["결과"], "부분 수집·배포 보류")
        self.assertEqual([f["source_key"] for f in report["수집"]["보류_실패"]], ["9001"])
        self.assertNotIn("배포_명령", report)

    def _no_article(self, *keys, stage="parse", error=rc.NO_ARTICLE_ERROR):
        rc.FAILURES.write_text("\n".join(json.dumps({"source_key": k, "규정명": f"{k} 지침", "stage": stage, "error": error},
                                                    ensure_ascii=False) for k in keys))

    def test_다운로드_실패는_옛_마크다운으로_편입돼도_보류(self):
        self._no_article("편입", stage="download", error="HTTP 503")
        _, report = self.execute(collect_code=3, item_keys=("편입",))
        self.assertEqual(report["수집"]["항목식 편입_건수"], 0)
        self.assertEqual(report["수집"]["보류_실패_건수"], 1)

    def test_이번_항목식이_넣지_않은_판본은_복구가_아니다(self):
        self._no_article("편입")
        self.rows.append({"record_id": "옛", "규정명": "편입 지침", "source_key": "편입", "본문": "가",
                          "조문번호": "1.", "record_type": "항목"})
        self.write(rc.CORPUS, self.rows)
        _, report = self.execute(collect_code=3)
        self.assertEqual(report["수집"]["보류_실패_건수"], 1)
        rc.FAILURES.write_text(json.dumps({"source_key": "편입", "규정명": "편입 지침", "stage": "parse",
                                           "error": rc.NO_ARTICLE_ERROR}, ensure_ascii=False))
        self.rows = [r for r in self.rows if r["record_id"] != "옛"]
        self.rows.append({"record_id": "별표", "규정명": "편입 지침", "source_key": "편입", "본문": "가",
                          "조문번호": "별표 1", "record_type": "별표"})
        _, report = self.execute(collect_code=3)  # 같은 키라도 항목식 판본이 아니면 복구가 아니다
        self.assertEqual(report["수집"]["보류_실패_건수"], 1)

    def test_항목식으로_편입된_현행_실패는_보류하지_않는다(self):
        self._no_article("편입", "누락")
        _, report = self.execute(collect_code=3, item_keys=("편입",))
        self.assertEqual(report["수집"]["항목식 편입_건수"], 1)
        self.assertEqual([f["source_key"] for f in report["수집"]["보류_실패"]], ["누락"])
        self.assertNotIn("배포_명령", report)
        self.rows = self.rows[:1]  # 앞 실행이 넣은 항목 판본을 걷어 낸다
        self.write(rc.CORPUS, self.rows)
        _, report = self.execute(collect_code=3, item_keys=("편입", "누락"))
        self.assertEqual(report["수집"]["보류_실패_건수"], 0)
        self.assertIn("배포_명령", report)

    def test_구판_판정은_코어_전남대_기준을_따른다(self):
        """코어 tail_superseded_names + year_prefix_demoted — 판이 하나뿐인 예외는 프로필 명단만."""
        editions = {n: {k} for n, k in {
            "수업관리 지침": "1", "수업관리 지침(2012. 12. 26. 제정)": "2",
            "전남대학교 대학원생 권리장전 (현행, 2016. 8. 26.(제정))": "3",
            "융합인재교육원 취업전담조교 운용 지침(2013. 9. 1. 제정)": "4167",       # 명단
            "전남대학교 대학원 학과간 이동(전과) 지침 (2013. 7. 17.)": "3862",      # 명단
            "다른 유일판 지침(2013. 9. 1. 제정)": "8",                               # 명단 밖
            "전남대학교 교육 및 연구 조교 선발과 운영에 관한 지침(2012)": "9",
            "2007학년도 강사료 지급지침": "10", "2025학년도 강사료 지급지침": "11",
            "옛 규정(2010. 1. 1. 폐지(고시))": "12", "옛 지침(2024. 1. 10. 개전전)": "13",
            "전남대학교 일반대학원 학과 신설·폐지 및 학사구조 개편 지침": "14"}.items()}
        past = rc.past_names(editions)
        self.assertEqual({"수업관리 지침(2012. 12. 26. 제정)", "다른 유일판 지침(2013. 9. 1. 제정)",
                          "전남대학교 교육 및 연구 조교 선발과 운영에 관한 지침(2012)", "2007학년도 강사료 지급지침",
                          "옛 규정(2010. 1. 1. 폐지(고시))", "옛 지침(2024. 1. 10. 개전전)"}, past)

    def test_명단의_유일판_실패는_보류_명단_밖은_경고(self):
        single = "융합인재교육원 취업전담조교 운용 지침(2013. 9. 1. 제정)"
        self.write(rc.SNAPSHOT, [{"name": single, "key": "4167"}, {"name": "다른 지침(2012. 1. 1. 제정)", "key": "2"}])
        rc.FAILURES.write_text("\n".join(json.dumps({"source_key": k, "규정명": n}, ensure_ascii=False) for k, n in (
            ("4167", single), ("2", "다른 지침(2012. 1. 1. 제정)"))), encoding="utf-8")
        _, report = self.execute(collect_code=3)
        self.assertEqual([f["source_key"] for f in report["수집"]["보류_실패"]], ["4167"])
        self.assertEqual([f["source_key"] for f in report["수집"]["구판 실패(경고)"]], ["2"])


class CoreVersionGateTest(unittest.TestCase):
    """수집 환경 코어 == 배포 고정 코어 >= 0.7.1 (2026-11-11)."""

    def check(self, pinned, installed, code=0, line=None, child_file=None):
        import core
        child_file = child_file or core.__file__  # 기본: 부모와 같은 코어 파일
        with tempfile.TemporaryDirectory() as d:
            req = Path(d) / "requirements-core.txt"
            line = line or f"rule-compass-core=={pinned} \\"
            req.write_text(f"# 주석\n{line}\n    --hash=sha256:00\n", encoding="utf-8")
            done = subprocess.CompletedProcess([], code, f"{installed}\n{child_file}\n", "")
            with patch.object(rc, "REQUIREMENTS_CORE", req), patch.object(rc.subprocess, "run", return_value=done):
                return rc.check_core_version()

    def test_같은_0_7_1이면_통과(self):
        self.assertEqual("0.7.1", self.check("0.7.1", "0.7.1"))

    def test_수집_환경과_배포_고정이_다르면_중단(self):
        with self.assertRaises(RuntimeError):
            self.check("0.7.1", "0.6.2")

    def test_0_7_1보다_낮으면_중단(self):
        with self.assertRaises(RuntimeError):
            self.check("0.7.0", "0.7.0")

    def test_코어를_못_읽으면_중단(self):
        with self.assertRaises(RuntimeError):
            self.check("0.7.1", "", code=1)

    def test_시험판은_정식판으로_보지_않는다(self):
        for version in ("0.7.1rc1", "0.7.1.dev1", "0.7.1a1", "0.7.1.post1"):
            with self.subTest(version), self.assertRaises(RuntimeError):
                self.check(version, version)

    def test_버전_고정_줄의_다른_표기도_읽는다(self):
        for line in ("  rule-compass-core==0.7.1 \\", "rule-compass-core == 0.7.1 \\", "rule-compass-core==0.7.1\\",
                     "rule-compass-core==0.7.1  # 주석"):
            with self.subTest(line):
                self.assertEqual("0.7.1", self.check("0.7.1", "0.7.1", line=line))

    def test_부모와_수집_환경의_코어_파일이_다르면_중단(self):
        with self.assertRaises(RuntimeError):
            self.check("0.7.1", "0.7.1", child_file="/elsewhere/site-packages/core/__init__.py")

    def test_승인_경로도_버전이_틀리면_멈춘다(self):
        with tempfile.TemporaryDirectory() as d:
            candidate, report = Path(d) / "c.candidate", Path(d) / "r.json"
            candidate.write_text("[]", encoding="utf-8")
            with patch.object(rc, "CANDIDATE", candidate), patch.object(rc, "REPORT", report), patch.object(rc, "log"), \
                    patch.object(rc, "check_core_version", side_effect=RuntimeError("불일치")), \
                    patch.object(rc, "_adopt_candidate") as adopt, patch.object(sys, "argv", ["refresh", "--approve", "ab"]):
                self.assertEqual(1, rc.main())
            adopt.assert_not_called()

    def test_중단되면_코퍼스를_건드리지_않는다(self):
        with tempfile.TemporaryDirectory() as d:
            corpus, report = Path(d) / "c.json", Path(d) / "r.json"
            corpus.write_text("[]", encoding="utf-8")
            with patch.object(rc, "CORPUS", corpus), patch.object(rc, "REPORT", report), patch.object(rc, "log"), \
                    patch.object(rc, "check_core_version", side_effect=RuntimeError("불일치")), \
                    patch.object(rc, "reconcile_names") as reconcile, patch.object(sys, "argv", ["refresh"]):
                self.assertEqual(1, rc.main())
            reconcile.assert_not_called()
            self.assertFalse(corpus.with_suffix(".json.refresh-backup").exists())
            self.assertIn("코어 버전", json.loads(report.read_text(encoding="utf-8"))["결과"])


class StaleImportPathTest(unittest.TestCase):
    def test_스크립트_실행_경로에서도_구판_점검을_불러온다(self):
        root = Path(__file__).resolve().parents[1]
        code = ("import sys, json, tempfile, pathlib;"
                f"sys.path[:] = [{str(root / 'scripts')!r}] + [p for p in sys.path[1:] if p not in ('', {str(root)!r})];"
                "import refresh_corpus as rc;"
                "d = pathlib.Path(tempfile.mkdtemp());"
                "(d/'h.json').write_text(json.dumps({'heads': {}}));"
                "(d/'c.json').write_text('[]');"
                "rc.HEADS = d/'h.json'; rc.CORPUS = d/'c.json';"
                "\ntry:\n    rc.stale_regulations()\nexcept ValueError:\n    pass\nprint('불러옴')")
        # 빈 코퍼스라 현행 판정에서 ValueError가 나는 것까지는 괜찮다 — 확인할 것은
        # scripts. 패키지 import가 ModuleNotFoundError 없이 되는가다.
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=root / "scripts")
        self.assertEqual(done.returncode, 0, done.stderr[-500:])
        self.assertEqual(done.stdout.strip(), "불러옴")


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

"""refresh_corpus --approve — 후보 채택은 재수집 없이 (2026-09-02 code-review #2).

승인 필요 시 새 코퍼스를 .candidate로 보존하고 --approve <fp>로 재실행하라고
안내했지만, --approve는 수집 전체를 다시 돌렸다. 그 사이 게시가 하나만 바뀌어도
지문이 달라져 승인이 거부됐고, 무인 경로(plist 인자 없음)는 승인 자체가 불가능했다.
"""

import json
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import change_gate  # noqa: E402
import refresh_corpus as rc  # noqa: E402


def _row(rid, key="K", name="R", art="제1조", cur=True):
    return {"record_id": rid, "source_key": key, "규정명": name, "조문번호": art, "_cur": cur}


def _cur_map(path, raw):
    return {r["record_id"]: bool(r["_cur"]) for r in raw}


class AdoptCandidateTest(unittest.TestCase):
    PATCHED = ("CORPUS", "CANDIDATE", "PENDING", "REPORT", "run",
               "build_current_map", "corpus_summary", "log")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = {k: getattr(rc, k) for k in self.PATCHED}
        rc.CORPUS = self.tmp / "rules_corpus.json"
        rc.CANDIDATE = rc.CORPUS.with_suffix(".json.candidate")
        rc.PENDING = self.tmp / "pending_change_report.json"
        rc.REPORT = self.tmp / "corpus_refresh_report.json"
        self.gate_calls = []
        rc.run = lambda args, timeout=0: (self.gate_calls.append(args) or
                                          types.SimpleNamespace(returncode=0, stdout="", stderr="Ran 1 test\n\nOK"))
        rc.build_current_map = _cur_map
        rc.corpus_summary = lambda: {"조문": 0, "규정": 0}
        rc.log = lambda msg: None
        # prev: 규정 R 현행 1건 → new: 강등, 후속 없음 = current_zero → 승인 필요
        self.prev = [_row("a")]
        self.new = [_row("a", cur=False)]
        rc.CORPUS.write_text(json.dumps(self.prev, ensure_ascii=False), encoding="utf-8")
        rc.CANDIDATE.write_text(json.dumps(self.new, ensure_ascii=False), encoding="utf-8")
        rc.PENDING.write_text("{}", encoding="utf-8")
        self.fp = change_gate.change_report(
            self.prev, self.new, _cur_map(None, self.prev), _cur_map(None, self.new))["fingerprint"]
        self.assertIsNotNone(self.fp, "전제: 이 change-set은 승인이 필요해야 한다")

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(rc, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_지문이_맞으면_후보를_재수집_없이_채택한다(self):
        rc_code = rc._adopt_candidate(self.fp)
        self.assertEqual(0, rc_code)
        self.assertEqual(self.new, json.loads(rc.CORPUS.read_text(encoding="utf-8")))
        self.assertFalse(rc.CANDIDATE.exists(), "채택된 후보는 지워야 한다")
        self.assertFalse(rc.PENDING.exists(), "채택됐으면 승인 대기 보고서도 지워야 한다")
        report = json.loads(rc.REPORT.read_text(encoding="utf-8"))
        self.assertEqual("candidate_adopt", report["모드"])
        self.assertTrue(report["결과"].startswith("성공"), report["결과"])
        # 재수집 없음 — 게이트(unittest) 1회만 돌았고 collect_* 는 부르지 않았다
        self.assertEqual(1, len(self.gate_calls))
        self.assertIn("unittest", " ".join(self.gate_calls[0]))

    def test_지문이_다르면_코퍼스도_후보도_그대로다(self):
        self.assertEqual(1, rc._adopt_candidate("000000000000"))
        self.assertEqual(self.prev, json.loads(rc.CORPUS.read_text(encoding="utf-8")))
        self.assertTrue(rc.CANDIDATE.exists(), "거부된 후보는 보존해야 검토 내용이 남는다")
        self.assertEqual([], self.gate_calls, "지문이 안 맞으면 코퍼스를 건드리기 전에 멈춘다")
        self.assertIn("지문 불일치", json.loads(rc.REPORT.read_text(encoding="utf-8"))["결과"])

    def test_테스트_게이트_실패면_원복하고_후보는_남긴다(self):
        rc.run = lambda args, timeout=0: types.SimpleNamespace(returncode=1, stdout="", stderr="FAILED")
        self.assertEqual(1, rc._adopt_candidate(self.fp))
        self.assertEqual(self.prev, json.loads(rc.CORPUS.read_text(encoding="utf-8")))
        self.assertTrue(rc.CANDIDATE.exists())


if __name__ == "__main__":
    unittest.main()

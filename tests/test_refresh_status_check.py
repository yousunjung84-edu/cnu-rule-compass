"""refresh_status_check — 월간 갱신 실패·누락·배포 대기를 일일 점검이 잡는지 (2026-11-02).

11/1 갱신 실패는 맥미니 화면 알림 하나로만 알려졌고, 10/1 갱신분은 한 달간 배포 대기였다.
"""

import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import refresh_status_check as rs  # noqa: E402

KST = dt.timezone(dt.timedelta(hours=9))


def _at(m, d, h=9, mi=10):
    return dt.datetime(2026, m, d, h, mi, tzinfo=KST)


def _report(started, result):
    return {"시작": started.isoformat(), "결과": result}


def _no_call():
    raise AssertionError("서비스 조회가 필요 없는 경우다")


class ExpectedRunTest(unittest.TestCase):
    def test_after_grace_is_this_month(self):
        self.assertEqual(rs.expected_run(_at(11, 1, 9, 10)), _at(11, 1, 4, 30))

    def test_within_grace_is_previous_month(self):
        self.assertEqual(rs.expected_run(_at(11, 1, 5, 0)), _at(10, 1, 4, 30))

    def test_january_rolls_back_year(self):
        now = dt.datetime(2027, 1, 1, 6, 0, tzinfo=KST)
        self.assertEqual(rs.expected_run(now), _at(12, 1, 4, 30))


class JudgeTest(unittest.TestCase):
    def test_failure_report_is_problem(self):
        rep = _report(_at(11, 1, 4, 30), "실패·원복: 게시 목록 조회 실패: timed out")
        out = rs.judge(rep, _at(11, 1), asof_fn=_no_call)
        self.assertEqual(len(out), 1)
        self.assertIn("timed out", out[0])

    def test_approval_needed_is_problem(self):
        rep = _report(_at(11, 1, 4, 30), "실패·원복: change-set 승인 필요 (fingerprint ab12, 위험 3건)")
        self.assertTrue(rs.judge(rep, _at(11, 1), asof_fn=_no_call))

    def test_rejected_approval_is_problem(self):
        rep = _report(_at(11, 1, 4, 30), "승인 거부·지문 불일치 (a ≠ b)")
        self.assertTrue(rs.judge(rep, _at(11, 2), asof_fn=_no_call))

    def test_missing_run_is_problem(self):
        rep = _report(_at(10, 1, 4, 30), "변경 없음")
        out = rs.judge(rep, _at(11, 1), asof_fn=_no_call)
        self.assertEqual(len(out), 1)
        self.assertIn("기록 없음", out[0])

    def test_no_change_is_clean(self):
        self.assertEqual(rs.judge(_report(_at(11, 1, 4, 30), "변경 없음"), _at(11, 2), asof_fn=_no_call), [])

    def test_deploy_pending_within_grace_is_clean(self):
        rep = _report(_at(11, 1, 4, 30), "성공·배포 대기")
        self.assertEqual(rs.judge(rep, _at(11, 2), asof_fn=_no_call), [])

    def test_deploy_pending_too_long_is_problem(self):
        rep = _report(_at(10, 1, 4, 30), "성공·배포 대기")
        out = rs.judge(rep, _at(10, 20), asof_fn=lambda: "2026-08-30T21:52:53+09:00")
        self.assertEqual(len(out), 1)
        self.assertIn("배포 대기", out[0])

    def test_deployed_is_clean(self):
        rep = _report(dt.datetime(2026, 11, 2, 10, 1, 38, tzinfo=KST), "성공·배포 대기")
        self.assertEqual(rs.judge(rep, _at(11, 10), asof_fn=lambda: "2026-11-02T10:01:49"), [])

    def test_unknown_result_raises(self):
        with self.assertRaises(ValueError):
            rs.judge(_report(_at(11, 1, 4, 30), ""), _at(11, 2), asof_fn=_no_call)

    def test_empty_served_asof_raises(self):
        rep = _report(_at(10, 1, 4, 30), "성공·배포 대기")
        with self.assertRaises(ValueError):
            rs.judge(rep, _at(10, 20), asof_fn=lambda: None)


if __name__ == "__main__":
    unittest.main()

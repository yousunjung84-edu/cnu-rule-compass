"""change_gate — 현행성 위험 판정 (2026-09-01 교차검증 #5·#6 대응).

이 게이트는 2026-09-01 04:30 갱신에서 규정 4건의 현행 조문이 0건이 된 사건을
통과시켰다. 원인이 둘이었고 둘 다 여기서 고정한다.

  1) 현행성이 코퍼스에 없다(is_current 키 0/17,585). 현행성은 적재 시점에
     규정명의 「(… 개정전)」 표기로 계산된다. 그래서 맵 없이 부르면 현행 관련
     검사가 통째로 꺼진다 — 그 상태가 「위험 없음」으로 나오면 안 된다.
  2) 위험 목록에 현행 소실이 없었다.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import change_gate  # noqa: E402


def _row(rid, key="K", name="R", art="제1조"):
    return {"record_id": rid, "source_key": key, "규정명": name, "조문번호": art}


class ChangeGateCurrentTest(unittest.TestCase):
    def test_현행_0건이_되면_위험이고_승인_없이는_채택_못한다(self):
        """실제 사건의 축약 — 개명으로 구판만 남고 신판이 안 들어온 경우."""
        prev = [_row("a"), _row("b", art="제2조")]
        new = [_row("a", name="R (2026. 8. 19. 개정전)"),
               _row("b", name="R (2026. 8. 19. 개정전)", art="제2조")]
        ok, report = change_gate.guard(
            prev, new, prev_current={"a": True, "b": True},
            new_current={"a": False, "b": False})
        self.assertFalse(ok)
        risks = [r for r in report["risks"] if r["type"] == "current_article_loss"]
        self.assertEqual(len(risks), 1)
        self.assertEqual((risks[0]["before"], risks[0]["after"]), (2, 0))
        self.assertEqual(risks[0]["severity"], "current_zero")
        # 개명은 위험이 아니라 참고 변화로만 남는다.
        self.assertEqual(len(report["renames"]), 1)

    def test_지문_일치_승인만_통과시킨다(self):
        prev = [_row("a")]
        new = [_row("a", name="R (2026. 8. 19. 개정전)")]
        maps = {"prev_current": {"a": True}, "new_current": {"a": False}}
        _, report = change_gate.guard(prev, new, **maps)
        self.assertFalse(change_gate.guard(prev, new, approve="deadbeef", **maps)[0])
        self.assertTrue(
            change_gate.guard(prev, new, approve=report["fingerprint"], **maps)[0])

    def test_맵_없이_부르면_fail_closed(self):
        """현행 검사가 꺼진 상태를 통과로 읽지 않는다 — 교차검증 #5.

        코퍼스에 is_current가 없으므로 맵이 없으면 현행 수·승격이 전부 0으로
        계산된다. 그 상태에서 '위험 없음'이 나오면 게이트가 아니다.
        """
        prev = [_row("a")]
        new = [_row("a", name="R (2026. 8. 19. 개정전)")]
        ok, report = change_gate.guard(prev, new)
        self.assertFalse(ok)
        self.assertEqual(report["current_source"], "row_field")
        self.assertIn("current_source_missing", {r["type"] for r in report["risks"]})

    def test_조문_통합은_current_drop으로_구분된다(self):
        """구판 2조문을 신판 1개 통합 조문이 대체하는 정상 개정 — 교차검증 #6.

        위험으로 남기되(행정 근거라 사람이 한 번 본다) severity로 사고와
        구별한다. 「정상 개정에서는 현행 수가 유지된다」는 일반적으로 거짓이다.
        """
        prev = [_row("a"), _row("b", art="제2조")]
        new = [_row("a", name="R (2026. 1. 1. 개정전)"),
               _row("b", name="R (2026. 1. 1. 개정전)", art="제2조"),
               _row("c")]
        _, report = change_gate.guard(
            prev, new, prev_current={"a": True, "b": True},
            new_current={"a": False, "b": False, "c": True})
        risks = [r for r in report["risks"] if r["type"] == "current_article_loss"]
        self.assertEqual(len(risks), 1)
        self.assertEqual((risks[0]["before"], risks[0]["after"]), (2, 1))
        self.assertEqual(risks[0]["severity"], "current_drop")

    def test_변화가_없으면_승인이_필요없다(self):
        rows = [_row("a"), _row("b", art="제2조")]
        cur = {"a": True, "b": True}
        ok, report = change_gate.guard(rows, list(rows),
                                       prev_current=cur, new_current=dict(cur))
        self.assertTrue(ok)
        self.assertFalse(report["requires_approval"])
        self.assertIsNone(report["fingerprint"])
        self.assertEqual(report["current_source"], "engine")

    def test_조문_소실은_기존대로_위험이다(self):
        prev = [_row("a"), _row("b", art="제2조")]
        new = [_row("a")]
        ok, report = change_gate.guard(prev, new, prev_current={"a": True, "b": True},
                                       new_current={"a": True})
        self.assertFalse(ok)
        self.assertIn("article_loss", {r["type"] for r in report["risks"]})


if __name__ == "__main__":
    unittest.main()

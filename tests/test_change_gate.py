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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
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

    def test_맵_없이_부르면_차단되고_지문도_없다(self):
        """현행 검사가 꺼진 상태는 승인 대상이 아니라 차단 대상이다 — 3회차.

        처음에는 이 상태를 위험 목록에 넣어 「승인하면 통과」로 만들었다.
        그 위험은 고정 문자열이라 서로 다른 변경이 같은 지문을 만들고, 한 번
        받은 승인이 다음 변경에도 통했다. 이제 지문 자체를 발급하지 않는다.
        """
        prev = [_row("a")]
        new = [_row("a", name="R (2026. 8. 19. 개정전)")]
        ok, report = change_gate.guard(prev, new)
        self.assertFalse(ok)
        self.assertTrue(report["blocked"])
        self.assertIsNone(report["fingerprint"])
        self.assertFalse(report["requires_approval"])
        self.assertEqual({b["type"] for b in report["blockers"]},
                         {"current_map_missing"})

    def test_빈_맵이나_부분_맵으로_검사를_끌_수_없다(self):
        """맵의 **존재**만 보면 빈 맵({})이 검사를 끄는 스위치가 된다 — 3회차."""
        prev = [_row("a"), _row("b", art="제2조")]
        new = [_row("a", name="R (2026. 8. 19. 개정전)"),
               _row("b", name="R (2026. 8. 19. 개정전)", art="제2조")]
        for label, pm, nm in (("빈 맵", {}, {}),
                              ("부분 맵", {"a": True}, {"a": False, "b": False}),
                              ("타입 오류", {"a": 1, "b": 1}, {"a": 0, "b": 0})):
            with self.subTest(label):
                ok, report = change_gate.guard(prev, new, prev_current=pm,
                                               new_current=nm)
                self.assertFalse(ok)
                self.assertTrue(report["blocked"])
                self.assertIsNone(report["fingerprint"])

    def test_차단_상태는_어떤_승인으로도_통과하지_못한다(self):
        prev = [_row("a")]
        new = [_row("a", name="R (2026. 8. 19. 개정전)")]
        for approve in (None, "", "4566489c4418", "deadbeefcafe"):
            with self.subTest(approve=approve):
                self.assertFalse(change_gate.guard(prev, new, approve=approve)[0])

    def test_지문은_행_순서에_흔들리지_않는다(self):
        """promotions가 new_rows 순서를 그대로 담아 순서만 뒤집혀도 지문이 변했다."""
        prev = [_row("a"), _row("b", art="제2조")]
        new = [_row("a", name="R (2026. 8. 19. 개정전)"),
               _row("b", name="R (2026. 8. 19. 개정전)", art="제2조")]
        maps = {"prev_current": {"a": True, "b": True},
                "new_current": {"a": False, "b": False}}
        first = change_gate.change_report(prev, new, **maps)["fingerprint"]
        second = change_gate.change_report(prev, list(reversed(new)), **maps)["fingerprint"]
        self.assertIsNotNone(first)
        self.assertEqual(first, second)

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


class RefreshWiringTest(unittest.TestCase):
    """라이브 배선 회귀 — change_gate 단위 테스트만으로는 못 잡는 자리.

    3회차 교차검증: `current_map()`을 `{}` 반환으로 망가뜨려도 위 테스트는
    전부 통과한다. refresh_corpus.py를 실행하지 않기 때문이다. 배선이 실제로
    엔진 맵을 만들어 넘기는지는 여기서 본다.
    """

    def test_refresh가_엔진_기반_current_map을_넘긴다(self):
        source = (Path(__file__).resolve().parents[1]
                  / "scripts" / "refresh_corpus.py").read_text(encoding="utf-8")
        self.assertIn("def current_map(", source,
                      "현행 맵 생성 함수가 사라졌다 — 배선이 끊기면 게이트가 차단된다")
        self.assertIn("RuleSearchIndex", source,
                      "현행성을 엔진으로 계산하지 않고 있다")
        self.assertIn("prev_current=current_map(", source)
        self.assertIn("new_current=current_map(", source)
        # 맵은 원본 행 전체를 덮어야 한다 — 적재분만 담으면 매 실행이 차단된다.
        self.assertIn("current_map(backup, prev_rows)", source)
        self.assertIn("current_map(CORPUS, new_rows)", source)
        self.assertIn('change["blocked"]', source,
                      "차단 상태를 배포 흐름이 확인하지 않는다")

    def test_실제_배선_조합에서_차단되지_않는다(self):
        """★ 배선이 넘기는 것과 같은 조합으로 본다 — 실 코퍼스 전 행 + 실 맵.

        처음 이 테스트는 양쪽에 **적재분**을 넣어 통과했다. 실제 배선은
        change_gate에 **원본 행**(17,585)을 넘기고 맵은 적재분(17,287)만
        담았으므로 221건 미커버로 매 실행이 차단됐을 것이다(실측). 통과하지만
        회귀를 못 막는 테스트의 표본이라, 조합 자체를 배선과 일치시킨다.
        """
        import json as _json

        from src.search import RuleSearchIndex

        corpus = Path(__file__).resolve().parents[1] / "data" / "rules_corpus.json"
        raw = _json.loads(corpus.read_text(encoding="utf-8"))
        raw = raw["rows"] if isinstance(raw, dict) else raw
        idx = RuleSearchIndex(corpus)
        loaded = idx.articles
        loaded = list(loaded.values()) if isinstance(loaded, dict) else list(loaded)
        by_id = {str(r.get("record_id")): bool(r.get("is_current", True))
                 for r in loaded if r.get("record_id")}
        # refresh_corpus.current_map과 같은 규칙: 적재되지 않은 레코드는 False.
        cur = {str(r.get("record_id")): by_id.get(str(r.get("record_id")), False)
               for r in raw if r.get("record_id")}
        self.assertEqual(change_gate._blockers(raw, raw, cur, cur), [],
                         "실제 배선 조합에서 차단이 발생한다 — 매 실행이 막힌다")
        self.assertGreater(len(raw), len(loaded), "적재 제외분이 있어야 이 검사가 의미 있다")

    def test_적재분만_담은_맵은_차단된다(self):
        """위 규칙을 빼면 어떻게 되는지 고정한다 — 음성 방향."""
        import json as _json

        from src.search import RuleSearchIndex

        corpus = Path(__file__).resolve().parents[1] / "data" / "rules_corpus.json"
        raw = _json.loads(corpus.read_text(encoding="utf-8"))
        raw = raw["rows"] if isinstance(raw, dict) else raw
        idx = RuleSearchIndex(corpus)
        loaded = idx.articles
        loaded = list(loaded.values()) if isinstance(loaded, dict) else list(loaded)
        partial = {str(r.get("record_id")): bool(r.get("is_current", True))
                   for r in loaded if r.get("record_id")}
        blockers = change_gate._blockers(raw, raw, partial, partial)
        self.assertTrue(blockers)
        self.assertEqual({b["type"] for b in blockers}, {"current_map_incomplete"})


if __name__ == "__main__":
    unittest.main()

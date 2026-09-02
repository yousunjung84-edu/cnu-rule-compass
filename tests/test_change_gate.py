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


_OLD = "R (2026. 8. 19. 개정전)"      # 구판 표기 — 계열은 그대로 "R"이다


class ChangeGateCurrentTest(unittest.TestCase):
    def test_현행_0건이_되면_위험이고_승인_없이는_채택_못한다(self):
        """실제 사건의 축약 — 개명으로 구판만 남고 신판이 안 들어온 경우."""
        prev = [_row("a"), _row("b", art="제2조")]
        new = [_row("a", name=_OLD), _row("b", name=_OLD, art="제2조")]
        ok, report = change_gate.guard(
            prev, new, prev_current={"a": True, "b": True},
            new_current={"a": False, "b": False})
        self.assertFalse(ok)
        risks = [r for r in report["risks"] if r["type"] == "current_article_loss"]
        self.assertEqual(len(risks), 1)
        self.assertEqual((risks[0]["before"], risks[0]["after"]), (2, 0))
        self.assertEqual(risks[0]["severity"], "current_zero")
        self.assertEqual(risks[0]["계열"], "R", "계열 단위로 보고해야 한다")
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

    def test_판정_불능_경계는_모두_차단된다(self):
        """무ID·중복ID·여분키·비-dict 맵·빈 코퍼스 — 4회차 교차검증.

        전부 「무엇이 변했는지 판정할 수 없는」 상태다. 건너뛰거나 예외로
        끝내면 그 행의 현행성은 아무도 판정하지 않은 채 지나간다.
        """
        a = _row("a")
        cases = {
            "record_id_missing": ([a, {"source_key": "K", "규정명": "R",
                                       "조문번호": "제2조"}], {"a": True}),
            # 같은 ID·다른 내용 — 어느 쪽 현행성인지 정할 수 없다.
            # (내용까지 같은 중복은 정상이다: record_id가 내용 해시라 실 코퍼스에
            #  52종 77행 존재하며 필드 불일치 0. 차단하면 매 실행이 막힌다.)
            "record_id_conflict": ([a, _row("a", art="제2조")], {"a": True}),
            "current_map_extra": ([a], {"a": True, "ghost": False}),
            "current_map_type": ([a], ["a"]),
            "empty_corpus": ([], {}),
        }
        for expected, (rows, cur) in cases.items():
            with self.subTest(expected):
                ok, report = change_gate.guard(rows, rows, prev_current=cur,
                                               new_current=cur)
                self.assertFalse(ok)
                self.assertTrue(report["blocked"])
                self.assertIsNone(report["fingerprint"])
                self.assertIn(expected, {b["type"] for b in report["blockers"]})

    def test_내용이_같은_중복ID는_차단하지_않는다(self):
        """실 코퍼스에 52종 77행 존재한다 — 차단하면 매 실행이 막힌다."""
        rows = [_row("a"), _row("a")]
        ok, report = change_gate.guard(rows, rows, prev_current={"a": True},
                                       new_current={"a": True})
        self.assertTrue(ok)
        self.assertFalse(report["blocked"])

    def test_위치_메타만_다른_중복ID는_차단하지_않는다(self):
        """code-review #8 (2026-09-02). 장/절·수집일시·source_url은 record_id가
        덮지 않는 필드다 — 그것만 다른 쌍둥이로 매 재수집이 막히면 안 된다."""
        a = {**_row("a"), "장": "제1장", "절": None, "수집일시": "2026-09-01", "source_url": "u1"}
        b = {**_row("a"), "장": "제2장", "절": "제1절", "수집일시": "2026-09-02", "source_url": "u2"}
        ok, report = change_gate.guard([a], [a, b], prev_current={"a": True},
                                       new_current={"a": True})
        self.assertFalse(report["blocked"], report["blockers"])
        self.assertTrue(ok)

    def test_내용이_다른_중복ID는_여전히_차단한다(self):
        a = _row("a"); b = {**_row("a"), "본문": "다른 본문"}
        _, report = change_gate.guard([a], [a, b], prev_current={"a": True},
                                      new_current={"a": True})
        self.assertTrue(report["blocked"])
        self.assertIn("record_id_conflict", [x["type"] for x in report["blockers"]])
        # 규정명이 갈리면 현행성을 정할 수 없다 — 내용으로 본다
        c = {**_row("a"), "규정명": "다른 규정"}
        _, report = change_gate.guard([a], [a, c], prev_current={"a": True},
                                      new_current={"a": True})
        self.assertTrue(report["blocked"])

    def test_차단_상태는_어떤_승인으로도_통과하지_못한다(self):
        prev = [_row("a")]
        new = [_row("a", name="R (2026. 8. 19. 개정전)")]
        for approve in (None, "", "4566489c4418", "deadbeefcafe"):
            with self.subTest(approve=approve):
                self.assertFalse(change_gate.guard(prev, new, approve=approve)[0])

    def test_지문은_행_순서에_흔들리지_않는다(self):
        """★ promotions **두 건 이상**을 만들어야 이 회귀가 고정된다 — 4회차.

        처음 이 테스트는 모두 True→False로 두어 생성되는 위험이 이미 정렬된
        current_article_loss 한 건뿐이었다. canonical sorted()를 제거해도
        통과했다 — 고친 회귀를 고정하지 못한 테스트였다. promotions는
        new_rows 순서를 그대로 담으므로 두 건을 서로 다른 순서로 넣어야
        정렬 제거가 드러난다.
        """
        prev = [_row("a"), _row("b", art="제2조")]
        new = [_row("a"), _row("b", art="제2조")]
        maps = {"prev_current": {"a": False, "b": False},
                "new_current": {"a": True, "b": True}}
        first = change_gate.change_report(prev, new, **maps)
        second = change_gate.change_report(prev, list(reversed(new)), **maps)
        promos = [r for r in first["risks"] if r["type"] == "current_promotion"]
        self.assertEqual(len(promos), 2, "순서 의존을 드러내려면 승격이 2건 이상이어야 한다")
        # 순서를 뒤집으면 risks 배열의 원소 순서가 실제로 달라진다.
        self.assertNotEqual([r["record_id"] for r in first["risks"]],
                            [r["record_id"] for r in second["risks"]])
        self.assertIsNotNone(first["fingerprint"])
        self.assertEqual(first["fingerprint"], second["fingerprint"])

    def test_지문은_내용에_결속된다(self):
        """서로 다른 change-set은 서로 다른 지문을 가져야 한다 — 4회차.

        기존 승인 테스트는 「보고서가 준 지문으로 승인된다」만 봐서, 모든
        change-set에 같은 고정 지문을 발급하도록 망가뜨려도 통과했다.
        """
        prev = [_row("a"), _row("b", art="제2조")]
        maps = {"prev_current": {"a": True, "b": True}}
        one = change_gate.change_report(
            prev, [_row("a", name="R (2026. 8. 19. 개정전)"), _row("b", art="제2조")],
            new_current={"a": False, "b": True}, **maps)["fingerprint"]
        two = change_gate.change_report(
            prev, [_row("a"), _row("b", name="R2 (2026. 8. 19. 개정전)", art="제2조")],
            new_current={"a": True, "b": False}, **maps)["fingerprint"]
        self.assertIsNotNone(one)
        self.assertIsNotNone(two)
        self.assertNotEqual(one, two, "다른 변경인데 같은 지문 — 승인이 재사용된다")

    def test_지문은_상태_전이_전체에_결속된다(self):
        """★ 5회차 교차검증 반례 4종 — 지문을 risks에서 뽑으면 전부 충돌했다.

        항목마다 필드를 덧붙이는 방식으로는 계속 샜다. 「무엇을 위험으로
        부르는가」와 「무엇이 바뀌었는가」는 다른 질문이고, 승인은 후자에
        결속돼야 한다. 여기서 각 축이 실제로 지문을 가르는지 고정한다.
        """
        def fp(prev, new, pm, nm):
            return change_gate.change_report(prev, new, pm, nm)["fingerprint"]

        base = [_row("a"), _row("b", art="제2조")]
        both = {"a": True, "b": True}
        cases = {
            # 중복 정리 — lost_ids·demoted_ids가 둘 다 비어 같은 지문이었다.
            "중복정리": (fp([_row("a"), _row("a")], [_row("a")], {"a": True}, {"a": True}),
                       fp([_row("b"), _row("b")], [_row("b")], {"b": True}, {"b": True})),
            # 개명 — 규정명이 {**new, **prev}라 늘 이전 이름이었다.
            "개명 대상": (fp(base, [_row("a", name="NEW-1"), _row("b", art="제2조")],
                          both, {"a": False, "b": True}),
                       fp(base, [_row("a", name="NEW-2"), _row("b", art="제2조")],
                          both, {"a": False, "b": True})),
            # 개명 유무 자체 — 생존 변이(규정명만 지문에서 빼기)가 여기서 죽는다.
            "개명 유무": (fp(base, [_row("a", name="NEW-1"), _row("b", art="제2조")],
                          both, {"a": False, "b": True}),
                       fp(base, [_row("a"), _row("b", art="제2조")],
                          both, {"a": False, "b": True})),
            # 등장 횟수 — 위험은 같고 **중복 개수만** 다른 두 change-set.
            # (risks가 담지 않는 축이라 상태 요약에만 실린다)
            "중복 개수": (fp([_row("a"), _row("a"), _row("x", key="K2")],
                          [_row("a"), _row("a")],
                          {"a": True, "x": True}, {"a": True}),
                       fp([_row("a"), _row("x", key="K2")], [_row("a")],
                          {"a": True, "x": True}, {"a": True})),
            # 현행성 — 위험은 같고 **변화 없는 레코드의 현행성만** 다른 두 경우.
            "무변화 현행성": (fp([_row("a"), _row("c", key="K3")],
                             [_row("a"), _row("c", key="K3")],
                             {"a": False, "c": True}, {"a": True, "c": True}),
                          fp([_row("a"), _row("c", key="K3")],
                             [_row("a"), _row("c", key="K3")],
                             {"a": False, "c": False}, {"a": True, "c": False})),
            # 승격 — current_promotion에 source_key가 없었다.
            "승격 규정": (fp([_row("a", key="K1")], [_row("a", key="K1")],
                          {"a": False}, {"a": True}),
                       fp([_row("a", key="K2")], [_row("a", key="K2")],
                          {"a": False}, {"a": True})),
        }
        for label, (one, two) in cases.items():
            with self.subTest(label):
                self.assertIsNotNone(one)
                self.assertNotEqual(one, two,
                                    f"{label}이 다른데 같은 지문 — 승인이 재사용된다")

    def test_정상_개정은_위험이_아니다(self):
        """★ 구판 강등 + 신판 유입 — 계열에 현행이 남으므로 통과 (2026-09-02).

        이 학교는 개정 시 신판에 **새 source_key**를 준다. source_key로 현행
        소실을 보면 정상 아카이브가 전부 위험이 된다 — 실측으로 809 key 중
        391개(48%)가 현행 0이었고 그중 355개가 정상 아카이브였다.
        """
        prev = [_row("a", key="5043")]
        new = [_row("a", key="5043", name=_OLD), _row("b", key="5063")]
        ok, report = change_gate.guard(
            prev, new, prev_current={"a": True},
            new_current={"a": False, "b": True})
        self.assertTrue(ok, "정상 개정이 위험으로 잡혔다")
        self.assertEqual([], [r for r in report["risks"]
                              if r["type"] == "current_article_loss"])
        self.assertEqual(1, len(report["renames"]), "개명은 참고 변화로 남아야 한다")

    def test_계열_접미사_규칙(self):
        """실측으로 확정한 규칙 — 「제정」은 제목의 일부일 수 있다."""
        cases = {
            "전남대학교 마이크로디그리 운영 지침 (2026. 8. 19. 개정전)": "전남대학교 마이크로디그리 운영 지침",
            "전남대학교 대학원 협동과정 운영지침(2015. 6. 이전)": "전남대학교 대학원 협동과정 운영지침",
            "전남대학교 대학원 지도교수 운영지침 (2019. 6월 개정전)": "전남대학교 대학원 지도교수 운영지침",
            # 아래 둘은 벗기면 안 된다.
            "전남대학교 전신 학교 졸업자 등에 대한 명예졸업증서 수여 규정 제정":
                "전남대학교 전신 학교 졸업자 등에 대한 명예졸업증서 수여 규정 제정",
            "전남대학교 대학원생 권리장전 (현행, 2016. 8. 26.(제정))":
                "전남대학교 대학원생 권리장전 (현행, 2016. 8. 26.(제정))",
        }
        for name, want in cases.items():
            with self.subTest(name[:24]):
                self.assertEqual(want, change_gate.lineage_of(name))

    def test_계열_규칙은_search_py_강등_규칙과_같은_집합을_벗긴다(self):
        """code-review #4 (2026-09-02). search.py가 구판으로 강등하는 이름을
        lineage_of가 안 벗기면 단독 계열이 되어 정상 개정이 current_zero가 된다."""
        cases = {
            "2013~2014학년도 강사료 지급지침": "강사료 지급지침",
            "2014~2015학년도 강사료 지급지침": "강사료 지급지침",
            "2007년도 강사료 지급지침": "강사료 지급지침",
            "전남대학교 병역 면제지침 (2020. 6. 9.)": "전남대학교 병역 면제지침",
            "전남대학교 대학원생 권리장전(2016. 8. 26. 제정)": "전남대학교 대학원생 권리장전",
            "전남대학교 X 규정 (2012)": "전남대학교 X 규정",
            "전남대학교 Y 지침(2024. 1. 10. 개전전)": "전남대학교 Y 지침",   # 오탈자도 연도 꼬리
            "2026": "2026",                                             # 이름 전체가 연도
        }
        for name, want in cases.items():
            with self.subTest(name[:24]):
                self.assertEqual(want, change_gate.lineage_of(name))

    def test_연도판_교체는_위험이_아니다(self):
        """실코퍼스 시뮬레이션(code-review #4): 다음 학년도판이 들어오며 구판이
        강등 — 계열이 같으므로 current_article_loss가 아니어야 한다."""
        old, new_ed = "2013~2014학년도 강사료 지급지침", "2014~2015학년도 강사료 지급지침"
        prev = [_row("a", key="1", name=old)]
        new = [_row("a", key="1", name=old), _row("b", key="2", name=new_ed)]
        ok, report = change_gate.guard(prev, new, prev_current={"a": True},
                                       new_current={"a": False, "b": True})
        self.assertTrue(ok)
        self.assertEqual([], [r for r in report["risks"]
                              if r["type"] == "current_article_loss"])

    def test_실코퍼스에서_search_py가_강등하는_이름은_전부_계열이_벗겨진다(self):
        """두 모듈의 규칙 집합이 어긋나면 여기서 잡힌다 — 검토자가 쓴 방법 그대로."""
        import json
        corpus = Path(__file__).resolve().parents[1] / "data" / "rules_corpus.json"
        if not corpus.exists():
            self.skipTest("코퍼스 미수집 환경")
        from src.search import _SUPERSEDED_NAME_RE, _YEAR_PREFIX_RE
        names = {str(r.get("규정명", "")) for r in json.loads(corpus.read_text(encoding="utf-8"))}
        stuck = []
        for name in names:
            m = _YEAR_PREFIX_RE.match(name)
            year_prefixed = bool(m and name[m.end():].strip())
            if (_SUPERSEDED_NAME_RE.match(name) or year_prefixed) \
                    and change_gate.lineage_of(name) == name:
                stuck.append(name)
        self.assertEqual([], sorted(stuck)[:10], f"{len(stuck)}건이 단독 계열로 남는다")

    def test_형제_key의_상쇄로_한_key의_현행_전멸이_가려지지_않는다(self):
        """code-review #5 (2026-09-02). 같은 규정명의 key A·B — A 전멸, B는 새 현행
        유입. 계열 합계 4→4라 종전엔 위험 0건·지문 없음으로 채택됐다."""
        N = "전남대학교 공동지도교수제 시행 지침"
        prev = [_row("a1", key="2826", name=N), _row("a2", key="2826", name=N, art="제2조"),
                _row("b1", key="3670", name=N), _row("b2", key="3670", name=N, art="제2조")]
        new = prev + [_row("b3", key="3670", name=N, art="제3조"),
                      _row("b4", key="3670", name=N, art="제4조")]
        ok, report = change_gate.guard(
            prev, new,
            prev_current={"a1": True, "a2": True, "b1": True, "b2": True},
            new_current={"a1": False, "a2": False, "b1": True, "b2": True,
                         "b3": True, "b4": True})
        self.assertFalse(ok, "규정 2826이 현행 0건인데 채택됐다")
        hits = [r for r in report["risks"] if r["type"] == "current_article_loss"
                and r.get("source_key") == "2826"]
        self.assertEqual(1, len(hits), report["risks"])
        self.assertEqual("current_zero", hits[0]["severity"])
        self.assertEqual(["a1", "a2"], hits[0]["demoted_ids"])
        self.assertIsNotNone(report["fingerprint"])

    def test_신판이_새_key로_오면_구판_key_전멸은_정상이다(self):
        """#5 가드가 정상 개정(355건 실측)을 다시 잡으면 안 된다 — 후속 key 판정."""
        prev = [_row("a", key="5043")]
        new = [_row("a", key="5043", name=_OLD), _row("b", key="5063")]
        ok, report = change_gate.guard(prev, new, prev_current={"a": True},
                                       new_current={"a": False, "b": True})
        self.assertTrue(ok)
        self.assertEqual([], [r for r in report["risks"] if r["type"] == "current_article_loss"])

    def test_조문_통합은_current_drop으로_구분된다(self):
        """구판 2조문을 신판 1개 통합 조문이 대체하는 정상 개정 — 교차검증 #6.

        위험으로 남기되(행정 근거라 사람이 한 번 본다) severity로 사고와
        구별한다. 「정상 개정에서는 현행 수가 유지된다」는 일반적으로 거짓이다.
        """
        prev = [_row("a"), _row("b", art="제2조")]
        new = [_row("a", name="R (2026. 1. 1. 개정전)"),
               _row("b", name="R (2026. 1. 1. 개정전)", art="제2조"),
               _row("c", key="K2")]
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

    def test_배선이_쓰는_함수를_직접_호출해_검증한다(self):
        """★ 문자열 검사가 아니라 **실제 함수**를 부른다 — 4회차 교차검증.

        이전 판은 소스에 특정 문자열이 있는지만 봤다. 그래서 current_map의
        반환을 적재분으로 되돌려도(=매 실행 차단되는 그 결함) 배선 테스트
        3건이 전부 통과했다. 중첩 함수라 호출할 수 없었던 것이 원인이라
        build_current_map을 모듈 레벨로 올리고 여기서 직접 부른다.
        """
        import json as _json

        from refresh_corpus import build_current_map

        corpus = Path(__file__).resolve().parents[1] / "data" / "rules_corpus.json"
        raw = _json.loads(corpus.read_text(encoding="utf-8"))
        raw = raw["rows"] if isinstance(raw, dict) else raw
        cur = build_current_map(corpus, raw)
        # 배선이 넘기는 그 맵으로 차단이 없어야 한다.
        self.assertEqual(change_gate._blockers(raw, raw, cur, cur), [],
                         "배선이 만든 맵이 차단된다 — 매 실행이 막힌다")
        self.assertEqual(len(cur), len({str(r.get("record_id")) for r in raw
                                        if r.get("record_id")}),
                         "맵이 원본 행 전체를 덮지 않는다")
        self.assertTrue(all(isinstance(v, bool) for v in cur.values()))
        self.assertTrue(any(cur.values()), "현행이 하나도 없다 — 계산이 깨졌다")
        self.assertFalse(all(cur.values()), "전부 현행이다 — 계열 계산이 꺼졌다")

    def test_배포_흐름이_차단_상태를_확인한다(self):
        source = (Path(__file__).resolve().parents[1]
                  / "scripts" / "refresh_corpus.py").read_text(encoding="utf-8")
        self.assertIn('change["blocked"]', source,
                      "차단 상태를 배포 흐름이 확인하지 않는다")
        self.assertIn("build_current_map(backup, prev_rows)", source)
        self.assertIn("build_current_map(CORPUS, new_rows)", source)

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

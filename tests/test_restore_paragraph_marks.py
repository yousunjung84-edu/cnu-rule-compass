"""빠진 항 번호만 끼우고 기존 본문은 한 글자도 바꾸지 않는지 (2026-11-02)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import restore_paragraph_marks as m  # noqa: E402


class InsertMarksTest(unittest.TestCase):
    def test_line_head_mark_with_space(self):
        old = "① 총장은 대책을 마련한다.\n총장은 인력을 둔다."
        new, added = m.insert_marks(old, "① 총장은 대책을 마련한다.\n\n② 총장은 인력을 둔다.")
        self.assertEqual(new, "① 총장은 대책을 마련한다.\n② 총장은 인력을 둔다.")
        self.assertEqual(added, ["②"])
        self.assertTrue(m.only_mark_inserts(old, new))

    def test_mid_sentence_mark_without_space(self):
        old = "운영 지침 제9조 항에 근거하여"
        new, _ = m.insert_marks(old, "운영 지침 제9조 ④항에 근거하여")
        self.assertEqual(new, "운영 지침 제9조 ④항에 근거하여")

    def test_other_differences_are_ignored(self):
        # 새 파싱 꼬리의 장 제목·굵게 표시는 들이지 않는다 — 후처리 결과를 지킨다
        old = "보고해야 한다.\n제2항 후 조치한다."
        fresh = "보고해야 한다.\n\n③ **제2항** 후 조치한다.\n\n제4장 건전한 공직풍토의 조성"
        new, _ = m.insert_marks(old, fresh)
        self.assertEqual(new, "보고해야 한다.\n③ 제2항 후 조치한다.")

    def test_replacement_is_not_an_insert(self):
        # 깨진 글자(U+F082) 자리의 ② 는 「교체」다 — 이 도구의 범위 밖
        old = "유지한다.\n 연구 중점 교수는"
        new, added = m.insert_marks(old, "유지한다.\n② 연구 중점 교수는")
        self.assertEqual((new, added), (old, []))

    def test_invariant_rejects_text_change(self):
        self.assertFalse(m.only_mark_inserts("총장은 둔다.", "② 총장은 두어야 한다."))


if __name__ == "__main__":
    unittest.main()

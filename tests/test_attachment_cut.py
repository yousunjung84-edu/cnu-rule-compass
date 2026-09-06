"""attachment_cut — 별지·붙임 뒤 서식 조문 차단 (2026-09-02 code-review #6)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import collect_rules as cr  # noqa: E402

FORM = "\n".join(f"제{i}조(항목{i}) 서식 조문 {i}." for i in range(1, 9))


class AttachmentCutTest(unittest.TestCase):
    def test_첫머리_표식은_경계가_아니다(self):
        md = "[붙임 1]\n계약심의회 운영 지침\n제1조(목적) 본칙.\n제2조(정의) 본칙."
        self.assertEqual(2, len(cr.split_articles(md)))

    def test_항목식_본칙_뒤_서식_조문은_수확하지_않는다(self):
        # 종전 조건(앞에 제N조가 있어야 절단)에서는 8건이 규정 조문으로 들어갔다.
        md = "전남대학교 X 지침\n\n1. 목적\n이 지침은 …\n\n2. 적용범위\n…\n\n[별지 1호 서식]\n복무협약서\n" + FORM
        self.assertEqual([], cr.split_articles(md))

    def test_조문식_본칙_뒤_서식_조문도_자른다(self):
        md = "X 규정\n제1조(목적) 본칙.\n제2조(정의) 본칙.\n\n[별지 1]\n서식\n" + FORM
        got = cr.split_articles(md)
        self.assertEqual(["제1조", "제2조"], [a["조문번호"] for a in got])

    def test_공문_꼬리_붙임도_경계다(self):
        md = "X 규정\n제1조(목적) 본칙.\n붙임: 서식 1부. 끝.\n" + FORM
        self.assertEqual(["제1조"], [a["조문번호"] for a in cr.split_articles(md)])


if __name__ == "__main__":
    unittest.main()

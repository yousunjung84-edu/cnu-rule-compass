"""kordoc 4.12.0 <u> 밑줄 태그 — 세 수집 경로가 같은 세대여야 한다 (2026-09-02 code-review #7).

<u> 제거가 clean_markdown_text(조문 경로)에만 있었다. 별표·항목식 수집기는
원시 마크다운을 그대로 본문으로 잘라, 다음 재수집에서 별표 1,189행·항목 89행의
본문에 <u>가 붙고 record_id(본문 해시)가 재발급될 자리였다 — 커밋 3c5e992가
막는다고 한 「표기 세대 분리」 그 자체.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import collect_attachments  # noqa: E402
import collect_item_style  # noqa: E402
from collect_rules import clean_markdown_text  # noqa: E402


class UnderlineParityTest(unittest.TestCase):
    def test_조문_경로(self):
        self.assertNotIn("<u>", clean_markdown_text("제1조(목적) 이 <u>지침</u>은"))

    def test_별표_경로(self):
        text = "[별표 1] 수당 기준\n\n| 구분 | <u>금액</u> |\n|---|---|\n| A | <u>10</u> |\n"
        blocks = collect_attachments.extract(text)
        self.assertTrue(blocks, "별표 블록이 추출돼야 전제가 성립한다")
        for _, _, body in blocks:
            self.assertNotIn("<u>", body); self.assertNotIn("</u>", body)

    def test_항목식_경로(self):
        text = ("1. 목적\n이 지침은 <u>연구</u>를 위한 것이다.\n\n"
                "2. 적용범위\n<u>전 부서</u>에 적용한다.\n\n"
                "3. 정의\n용어는 다음과 같다.\n\n"
                "4. 절차\n절차는 <u>별도</u>로 정한다.\n")
        items = collect_item_style.extract(text)
        self.assertTrue(items, "항목 헤더가 추출돼야 전제가 성립한다")
        for _, _, body in items:
            self.assertNotIn("<u>", body); self.assertNotIn("</u>", body)

    def test_밑줄_없는_본문은_그대로다(self):
        # 기존 1,189+89행(밑줄 0건)의 record_id가 흔들리면 안 된다.
        text = "[별표 1] 기준\n\n| 구분 | 금액 |\n|---|---|\n| A | 10 |\n"
        body = collect_attachments.extract(text)[0][2]
        self.assertEqual("| 구분 | 금액 |\n|---|---|\n| A | 10 |", body)


if __name__ == "__main__":
    unittest.main()

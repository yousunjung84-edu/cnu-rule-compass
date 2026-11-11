"""항목식 편입의 현행 판정 — 코어 명단의 유일 판본 「(… 제정)」은 구판으로 버리지 않는다(2026-11-11, 4167).

코어 식은 이름 속 「(2013」만 보고 구판으로 쳐서, 항목 6개가 뽑히는 현행 지침이
코퍼스에서 빠졌다. 편입 여부는 이름이 아니라 판정식이 정하므로 main()을 그대로 돌려 본다.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import collect_item_style as cis  # noqa: E402

DOC = "1. 정의\n가\n\n2. 소속\n나\n\n3. 직무\n다\n"


class ItemStyleCurrentTest(unittest.TestCase):
    def _run(self, failures):
        d = Path(tempfile.mkdtemp())
        (d / "md").mkdir()
        (d / "c.json").write_text("[]", encoding="utf-8")
        (d / "f.jsonl").write_text("\n".join(json.dumps({"stage": "parse", "error": "제N조 패턴을 찾지 못함", **f},
                                                         ensure_ascii=False) for f in failures), encoding="utf-8")
        for f in failures:
            (d / "md" / f"key_{f['source_key']}.md").write_text(DOC, encoding="utf-8")
        with mock.patch.object(cis, "CORPUS", d / "c.json"), mock.patch.object(cis, "MARKDOWN", d / "md"), \
                mock.patch.object(cis, "FAILURES", d / "f.jsonl"), mock.patch.object(sys, "argv", ["x"]), \
                mock.patch.object(cis, "listed_editions", return_value={"수업 지침": {"90"}}), \
                mock.patch("builtins.print"):
            cis.main()
        return {r["source_key"] for r in json.loads((d / "c.json").read_text(encoding="utf-8"))}

    def test_명단의_유일판은_편입하고_구판은_뺀다(self):
        keys = self._run([
            {"source_key": "4167", "규정명": "융합인재교육원 취업전담조교 운용 지침(2013. 9. 1. 제정)", "편제": "가"},
            {"source_key": "6", "규정명": "취업지원 운용 지침", "편제": "가"},
            {"source_key": "1", "규정명": "다른 운용 지침(2013. 9. 1. 제정)", "편제": "가"},  # 명단 밖
            {"source_key": "3", "규정명": "수업관리 지침 (2020. 12. 1. 개정전)", "편제": "가"},
            {"source_key": "4", "규정명": "교과과정 이수 지침(2007년 10월 29일 이전)", "편제": "가"},
            {"source_key": "5", "규정명": "수업 지침(2012. 12. 26. 제정)", "편제": "가"},  # 새 판이 따로 있는 첫 판
        ])
        self.assertEqual({"4167", "6"}, keys)

    def test_다운로드_실패는_남은_마크다운으로_메우지_않는다(self):
        keys = self._run([{"source_key": "1", "규정명": "운용 지침", "편제": "가", "stage": "download", "error": "HTTP 503"}])
        self.assertEqual(set(), keys)


if __name__ == "__main__":
    unittest.main()

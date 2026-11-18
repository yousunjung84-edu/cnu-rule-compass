"""같은 key 본문 변경 감지 회귀 (2026-11-19). 네트워크·kordoc 없이 가짜로 격리한다."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_same_key_changes as sk
import refresh_corpus as rc


class SameKeyCheckTest(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.tmp = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        (self.tmp / "hwp").mkdir()
        for name, value in (("HWP_DIR", self.tmp / "hwp"), ("OUT_DIR", self.tmp),
                            ("CORPUS", self.tmp / "corpus.json"), ("SNAPSHOT", self.tmp / "snap.json")):
            self.stack.enter_context(patch.object(sk, name, value))
        self.work = self.tmp / "work"
        self.work.mkdir()

    def stored(self, key, payload=b"HWP-1"):
        (self.tmp / "hwp" / f"key_{key}.hwp").write_bytes(payload)

    def fake_download(self, payload=None, error=None):
        def download(url, dest):
            if error:
                raise RuntimeError(error)
            dest.write_bytes(payload)
        return download

    def target(self, key="1"):
        return {"key": key, "name": "지침", "url": "u"}

    def test_바이트가_같으면_변환하지_않는다(self):
        self.stored("1")
        def never(*_):
            raise AssertionError("같은 파일을 변환했다")
        r = sk.classify(self.target(), self.work, self.fake_download(b"HWP-1"), never)
        self.assertEqual(r["판정"], "같음")

    def test_다시_저장만_됨과_본문_바뀜을_가른다(self):
        self.stored("1")
        same = sk.classify(self.target(), self.work, self.fake_download(b"HWP-2"), lambda h, m: "제1조 목적")
        self.assertEqual(same["판정"], "다시 저장만 됨")
        texts = iter(["제1조 목적", "제1조 목적 개정"])
        changed = sk.classify(self.target(), self.work, self.fake_download(b"HWP-2"), lambda h, m: next(texts))
        self.assertEqual(changed["판정"], "본문 바뀜")
        self.assertEqual((changed["이전_크기"], changed["새_크기"]), (5, 5))

    def test_받기_실패는_변경이_아니다_변환_실패는_검토(self):
        self.stored("1")
        failed = sk.classify(self.target(), self.work, self.fake_download(error="HTTP 503"))
        self.assertEqual(failed["판정"], "받기 실패")
        def broken(*_):
            raise RuntimeError("kordoc 종료 코드 1")
        unconv = sk.classify(self.target(), self.work, self.fake_download(b"HWP-2"), broken)
        self.assertEqual(unconv["판정"], "변환 실패")
        self.assertIn("변환 실패", sk.REVIEW)
        self.assertEqual((self.tmp / "hwp" / "key_1.hwp").read_bytes(), b"HWP-1")  # 원본 불변

    def test_대상은_현행_게시_코퍼스_저장원본_교집합(self):
        for key in ("1", "2", "4"):
            self.stored(key)
        snapshot = [{"name": "현행", "key": "1", "source_url": "u1"},
                    {"name": "현행", "key": "1", "source_url": "u1"},       # 중복 행
                    {"name": "구판(개정전)", "key": "2", "source_url": "u2"},  # 구판
                    {"name": "원본없음", "key": "3", "source_url": "u3"},     # 저장 원본 없음
                    {"name": "코퍼스밖", "key": "4", "source_url": "u4"}]
        rows = sk.targets(snapshot, {"1", "2", "3"}, {"구판(개정전)"})
        self.assertEqual([r["key"] for r in rows], ["1"])

    def run_main(self, results):
        (self.tmp / "snap.json").write_text("[]")
        (self.tmp / "corpus.json").write_text("[]")
        fake_targets = [self.target(str(i)) for i in range(len(results))]
        out = io.StringIO()
        with patch.object(sk, "targets", return_value=fake_targets), \
             patch.object(sk, "classify", side_effect=[{"key": t["key"], "판정": j} for t, j in zip(fake_targets, results)]), \
             patch.object(rc, "listed_editions", return_value={}), \
             patch.object(rc, "past_names", return_value=set()), \
             contextlib.redirect_stdout(out):
            code = sk.main([])
        return code, json.loads(out.getvalue().strip().splitlines()[-1])

    def test_검토_필요면_exit_2_요약과_보고서(self):
        code, summary = self.run_main(["같음", "본문 바뀜", "받기 실패"])
        self.assertEqual(code, 2)
        self.assertEqual(summary["검토_필요"], 1)
        report = json.loads((self.tmp / summary["보고서"]).read_text(encoding="utf-8"))
        self.assertEqual(report["대상"], 3)
        self.assertEqual([r["key"] for r in report["본문 바뀜"]], ["1"])
        self.assertNotIn("같음", report)  # 같은 것은 목록으로 남기지 않는다

    def test_이상_없으면_exit_0(self):
        code, summary = self.run_main(["같음", "다시 저장만 됨", "받기 실패"])
        self.assertEqual((code, summary["결과"]), (0, "이상 없음"))


class RefreshWiringTest(unittest.TestCase):
    def call(self, stdout, code=0):
        result = subprocess.CompletedProcess([], code, stdout, "")
        with patch.object(rc, "run", return_value=result), patch.object(rc, "log"):
            return rc.same_key_check()

    def test_요약을_그대로_싣는다(self):
        summary = {"결과": "검토 필요", "검토_필요": 2, "집계": {"본문 바뀜": 2}}
        self.assertEqual(self.call("진행\n" + json.dumps(summary, ensure_ascii=False), 2), summary)

    def test_요약이_없거나_시간초과면_실패로만_적고_멈추지_않는다(self):
        self.assertEqual(self.call("완료")["결과"], "실행 실패")
        with patch.object(rc, "run", side_effect=subprocess.TimeoutExpired([], 3600)), patch.object(rc, "log"):
            self.assertIn("시간 초과", rc.same_key_check()["결과"])


if __name__ == "__main__":
    unittest.main()

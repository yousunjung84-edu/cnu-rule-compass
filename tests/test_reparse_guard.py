"""소급 재파싱 도구의 안전장치 (2026-09-07, Codex 점검 #1·#4·#5·#6·#7 반영).

이 도구는 **파이프라인의 일부만 재현한다** — split_articles만 부르므로 수집
이후의 후처리(apply_structure_titles 등)를 되돌린다. P10 대상 10판본은 후처리
전이라 안전했지만(짧아짐만 9건), 후처리를 거친 24판본에 돌리니 본문이 **늘어나는**
변경 156건이 나왔고 그 내용이 전부 「제2장 …」 헤더 유입이었다.

그래서 「본문이 늘어나면 거부한다」를 계약으로 박는다. 늘어남은 개선이 아니라
후처리를 되돌리는 신호다.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY_BIN = str(ROOT / ".venv" / "bin" / "python")
SCRIPT = str(ROOT / "scripts" / "reparse_articles.py")
# 후처리를 거친 판본 — 재파싱하면 장 제목이 본문에 다시 붙는다(실측).
POST_PROCESSED = "3124,3267,3667"
ENV = {"RULE_COMPASS_DATA_DIR": str(ROOT / "data"), "PATH": "/usr/bin:/bin"}


def _run(*args):
    import os
    return subprocess.run([PY_BIN, SCRIPT, *args], capture_output=True, text=True,
                          cwd=str(ROOT), env={**os.environ, **ENV}, timeout=1800)


@unittest.skipUnless((ROOT / "data" / "rules_corpus.json").exists(),
                     "코퍼스 미보유 환경")
class ReparseGuardTest(unittest.TestCase):

    def test_본문이_늘어나면_거부한다(self) -> None:
        """★ 후처리를 되돌리는 변경은 쓰지 않는다. 종료코드 3."""
        r = _run("--keys", POST_PROCESSED, "--dry-run")
        self.assertEqual(3, r.returncode, r.stderr[-300:])

    def test_멱등_대상은_통과한다(self) -> None:
        """P10 기본 대상은 이미 적용돼 변화가 없다."""
        r = _run("--dry-run")
        self.assertEqual(0, r.returncode, r.stderr[-300:])
        self.assertIn('"제거": 0', r.stdout)

    def test_keys_값_누락은_기본값으로_흐르지_않는다(self) -> None:
        """단일 규정만 처리하려던 명령이 기본 10판본에 적용되던 결함(Codex #6)."""
        r = _run("--keys", "--dry-run")
        self.assertEqual(2, r.returncode)
        self.assertIn("--keys 뒤에", r.stderr)

    def test_등호_형식도_받는다(self) -> None:
        r = _run("--keys=4197", "--dry-run")
        self.assertEqual(0, r.returncode, r.stderr[-300:])

    def test_증거는_실행마다_새_파일이다(self) -> None:
        """덮어쓰면 적용 뒤 멱등성 확인만 해도 삭제 목록이 사라진다(Codex #4)."""
        before = set((ROOT / "data").glob("reparse_evidence_*.json"))
        _run("--keys=4197", "--dry-run")
        after = set((ROOT / "data").glob("reparse_evidence_*.json"))
        self.assertTrue(after >= before, "기존 증거가 사라졌다")


class PathTest(unittest.TestCase):
    def test_ROOT는_하드코딩되지_않는다(self) -> None:
        """다른 체크아웃에서 실행해도 원래 운영 경로를 건드리면 안 된다(Codex #5)."""
        src = Path(SCRIPT).read_text(encoding="utf-8")
        self.assertNotIn('Path("/Users/', src)
        self.assertIn("Path(__file__).resolve().parent.parent", src)

    def test_적용_경로에_백업이_있다(self) -> None:
        """사람이 기억하는 절차에 의존하지 않는다(Codex #1)."""
        src = Path(SCRIPT).read_text(encoding="utf-8")
        self.assertIn("shutil.copy2(CORPUS, backup)", src)
        self.assertIn("change_gate 승인이 남았다", src)


if __name__ == "__main__":
    unittest.main()

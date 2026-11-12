"""scripts/usage_log.py — 보존본을 지키는 규칙 (2026-11-12 Codex 리뷰 #1~#3)."""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("usage_log", ROOT / "scripts" / "usage_log.py")
usage_log = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(usage_log)


def _row(i: str, day: str) -> dict:
    return {"id": i, "ts": f"{day}T01:00:00Z", "usage": "search_rule", "status": "ok"}


class PullKeepsArchiveTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.patches = [
            mock.patch.object(usage_log, "OUT_DIR", self.tmp),
            mock.patch.object(usage_log, "EVENTS", self.tmp / "usage_events.jsonl"),
            mock.patch.object(usage_log, "HTTP_DAILY", self.tmp / "http_daily.json"),
        ]
        for p in self.patches:
            p.start()
        usage_log.EVENTS.write_text(
            "".join(json.dumps(_row(f"a{i}", "2026-10-01")) + "\n" for i in range(3)), encoding="utf-8")
        usage_log.HTTP_DAILY.write_text(json.dumps(
            {"2026-10-01": {"cnu-rule-compass\tundici\t200": 5,
                            "cnu-rule-compass\tnode\t200": 2}}), encoding="utf-8")

    def tearDown(self) -> None:
        for p in self.patches:
            p.stop()

    def _pull(self, rows, http):
        args = mock.Mock(since="2026-10-01", until="2026-10-01")
        with mock.patch.object(usage_log, "pull_usage_day", return_value=rows), \
                mock.patch.object(usage_log, "pull_http_day", return_value=http):
            return usage_log.cmd_pull(args)

    def _saved(self):
        events = [json.loads(x) for x in usage_log.EVENTS.read_text(encoding="utf-8").splitlines()]
        return events, json.loads(usage_log.HTTP_DAILY.read_text(encoding="utf-8"))

    def test_만료된_날은_빈_결과여도_보존본을_지킨다(self) -> None:
        self._pull([], {})
        events, http = self._saved()
        self.assertEqual(3, len(events))
        self.assertEqual(7, sum(http["2026-10-01"].values()))

    def test_일부만_오면_보존본을_지킨다(self) -> None:
        self._pull([_row("a0", "2026-10-01")], {"cnu-rule-compass\tundici\t200": 1})
        events, http = self._saved()
        self.assertEqual(3, len(events))
        self.assertEqual(7, sum(http["2026-10-01"].values()))

    def test_같거나_많으면_갈아_끼운다(self) -> None:
        rows = [_row(f"b{i}", "2026-10-01") for i in range(4)]
        self._pull(rows, {"cnu-rule-compass\tundici\t200": 9})
        events, http = self._saved()
        self.assertEqual({f"b{i}" for i in range(4)}, {r["id"] for r in events})
        self.assertEqual({"cnu-rule-compass\tundici\t200": 9}, http["2026-10-01"])

    def test_예전_키의_모르는_이름은_기타로_다시_묶는다(self) -> None:
        self._pull([], {})
        _, http = self._saved()
        self.assertEqual({"cnu-rule-compass\tundici\t200": 5, "cnu-rule-compass\t기타\t200": 2},
                         http["2026-10-01"])

    def test_상한에_닿으면_멈추고_파일을_건드리지_않는다(self) -> None:
        before = usage_log.EVENTS.read_bytes()
        with mock.patch.object(usage_log, "LIMIT", 2), \
                mock.patch.object(usage_log, "_gcloud_read",
                                  return_value=json.dumps([{"insertId": "x", "timestamp": "t",
                                                            "jsonPayload": {}}] * 2)):
            with self.assertRaises(RuntimeError):
                usage_log.pull_usage_day(dt.date(2026, 10, 1))
            self.assertEqual(1, usage_log.main(["pull", "--since", "2026-10-01", "--until", "2026-10-01"]))
        self.assertEqual(before, usage_log.EVENTS.read_bytes())


class UaTokenTest(unittest.TestCase):
    def test_허용_목록_밖은_기타(self) -> None:
        for agent, expected in {"undici": "undici", "python-httpx/0.28.1": "python-httpx",
                                "192.0.2.1": "기타", "person@example.test/1.0": "기타",
                                "": "(없음)", None: "(없음)"}.items():
            with self.subTest(agent=agent):
                self.assertEqual(expected, usage_log.ua_token(agent))


if __name__ == "__main__":
    unittest.main()

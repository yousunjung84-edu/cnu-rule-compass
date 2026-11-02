"""게시 목록 요청 재시도 (2026-11-01 월간 갱신이 시간 초과 1회로 통째 실패)."""

from __future__ import annotations

import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

import collect_rules


class HttpGetRetryTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(collect_rules.time, "sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def test_timeout_then_success(self):
        ok = ("<html>목록</html>".encode(), "text/html")
        with mock.patch.object(collect_rules, "http_get",
                               side_effect=[URLError(TimeoutError("timed out")), ok]) as get:
            self.assertEqual(collect_rules.http_get_retry("https://x"), ok)
        self.assertEqual(get.call_count, 2)
        self.sleep.assert_called_once()

    def test_last_error_is_raised(self):
        err = URLError(TimeoutError("timed out"))
        with mock.patch.object(collect_rules, "http_get", side_effect=[err, err, err]) as get:
            with self.assertRaises(URLError):
                collect_rules.http_get_retry("https://x", retries=2)
        self.assertEqual(get.call_count, 3)

    def test_4xx_is_not_retried(self):
        err = HTTPError("https://x", 404, "Not Found", {}, None)
        with mock.patch.object(collect_rules, "http_get", side_effect=[err]) as get:
            with self.assertRaises(HTTPError):
                collect_rules.http_get_retry("https://x")
        self.assertEqual(get.call_count, 1)
        self.sleep.assert_not_called()

    def test_5xx_is_retried(self):
        err = HTTPError("https://x", 503, "Unavailable", {}, None)
        ok = (b"ok", "text/html")
        with mock.patch.object(collect_rules, "http_get", side_effect=[err, ok]) as get:
            self.assertEqual(collect_rules.http_get_retry("https://x"), ok)
        self.assertEqual(get.call_count, 2)


if __name__ == "__main__":
    unittest.main()

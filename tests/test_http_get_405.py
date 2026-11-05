"""GET /mcp 대기 스트림 거절 (2026-11-05 AIONI 커넥터가 Cloud Run 뒤 빈 SSE에 멈춤)."""

from __future__ import annotations

import asyncio
import unittest

from src.http_server import reject_standalone_get


def _call(method, path):
    sent, called = [], []

    async def app(scope, receive, send):
        called.append(scope["method"])

    async def send(msg):
        sent.append(msg)

    async def receive():
        return {"type": "http.request"}

    asyncio.run(reject_standalone_get(app)({"type": "http", "method": method, "path": path}, receive, send))
    return sent, called


class RejectStandaloneGetTest(unittest.TestCase):
    def test_get_mcp_is_405(self):
        for path in ("/mcp", "/mcp/"):
            sent, called = _call("GET", path)
            self.assertEqual(sent[0]["status"], 405)
            self.assertEqual(called, [])

    def test_post_mcp_passes_through(self):
        sent, called = _call("POST", "/mcp")
        self.assertEqual(called, ["POST"])

    def test_get_health_passes_through(self):
        sent, called = _call("GET", "/health")
        self.assertEqual(called, ["GET"])


if __name__ == "__main__":
    unittest.main()

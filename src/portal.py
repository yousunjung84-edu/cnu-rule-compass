"""구성원용 규정 질의 웹 포털(표준 라이브러리, localhost 기본)."""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from src.answer import answer
from src.learn import capture


_HTML = """<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>CNU 규정 나침반</title>
<style>body{font-family:sans-serif;max-width:820px;margin:40px auto;padding:0 20px;color:#172b4d}
textarea{width:100%;min-height:100px;padding:10px;box-sizing:border-box}button{margin-top:10px;padding:10px 18px}
pre{white-space:pre-wrap;background:#f4f7fa;padding:16px;border-radius:8px;line-height:1.6}</style></head>
<body><h1>🧭 CNU 규정 나침반</h1><p>전남대학교 공식 규정 조문을 찾아 원문 그대로 안내합니다.</p>
<textarea id="q" placeholder="예: 전임교원 겸직 허가 절차"></textarea><br><button onclick="ask()">규정 찾기</button>
<pre id="out">질문을 입력하세요.</pre><script>
function esc(s){return String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
async function ask(){const q=document.getElementById('q').value.trim();if(!q)return;
const r=await fetch('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:q})});
const j=await r.json();document.getElementById('out').innerHTML=esc(j.response||j.error||'오류');}</script></body></html>"""


def handle_question(question: str) -> dict:
    """1차 답변 엔진을 호출하고 운영 로그·지식 후보를 갱신한다."""
    result = answer(question, prefer_llm=False)
    learned = capture(question, result)
    return {"response": result["text"], "answer": result, "learning": learned}


class _Handler(BaseHTTPRequestHandler):
    def _send_json(self, value: dict, status: int = 200) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path not in ("/", "/index.html"):
            self.send_error(404)
            return
        payload = _HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:
        if self.path != "/api/ask":
            self._send_json({"error": "알 수 없는 요청"}, 404)
            return
        length = int(self.headers.get("Content-Length", 0))
        if length > 20_000:
            self._send_json({"error": "질문이 너무 깁니다"}, 413)
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send_json({"error": "잘못된 JSON 요청"}, 400)
            return
        question = str(body.get("question", "")).strip()
        if not question:
            self._send_json({"error": "질문을 입력하세요"}, 400)
            return
        self._send_json(handle_question(question))

    def log_message(self, format_string: str, *args) -> None:
        return


def main(port: int = 8797) -> int:
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    print(f"[portal] http://127.0.0.1:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 8797))

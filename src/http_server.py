"""CNU 규정 나침반 — 원격 MCP 진입점 (Streamable HTTP, Cloud Run 배포용).

stdio 진입점(src/mcp_server.py)과 같은 도구 3종을 HTTP로 노출한다.
claude.ai 커스텀 커넥터 등 원격 클라이언트가 `/mcp` 경로로 접속한다.

- 공개 프로브는 `/health` 사용 — run.app 도메인은 Google Frontend가
  정확히 `/healthz` 경로를 가로채므로 쓰지 않는다(academyinfo 실측).
- `ALLOWED_HOSTS` 환경변수(콤마 구분)가 설정되면 DNS rebinding 보호를 켠다.
"""

from __future__ import annotations

import os
import sys

from src.mcp_server import SERVER_VERSION, create_server
from src.profile import active_profile
from src.search import get_default_index

DEFAULT_PORT = 8080


def read_allowed_hosts(value: str | None = None) -> list[str]:
    raw = os.environ.get("ALLOWED_HOSTS", "") if value is None else value
    return [host.strip() for host in raw.split(",") if host.strip()]


def read_port(value: str | None = None) -> int:
    raw = (os.environ.get("PORT", "") if value is None else value).strip()
    if not raw:
        return DEFAULT_PORT
    if not raw.isdigit() or not 1 <= int(raw) <= 65535:
        raise ValueError(f"PORT는 1~65535 정수여야 합니다: {raw}")
    return int(raw)


def create_http_server():
    allowed_hosts = read_allowed_hosts()
    settings: dict = {
        "host": "0.0.0.0",
        "port": read_port(),
        # Cloud Run은 인스턴스 재기동이 잦으므로 세션 상태를 서버에 남기지 않는다.
        "stateless_http": True,
    }
    if allowed_hosts:
        from mcp.server.transport_security import TransportSecuritySettings

        settings["transport_security"] = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
            allowed_origins=[f"https://{host}" for host in allowed_hosts],
        )
    server = create_server(**settings)

    from starlette.requests import Request
    from starlette.responses import JSONResponse

    @server.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> JSONResponse:
        index = get_default_index()
        return JSONResponse(
            {
                "status": "ok",
                "name": active_profile().display_name,
                "version": SERVER_VERSION,
                "articles": len(index.articles),
                "rules": len({row.get("규정명") for row in index.articles}),
            }
        )

    return server


def main() -> int:
    try:
        get_default_index()  # 서버를 열기 전에 코퍼스 게이트를 통과시킨다.
        server = create_http_server()
    except Exception as exc:
        print(f"[오류] {exc}", file=sys.stderr)
        return 1
    try:
        server.run(transport="streamable-http")
    except Exception as exc:
        print(f"[오류] HTTP MCP 서버 실행 실패: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

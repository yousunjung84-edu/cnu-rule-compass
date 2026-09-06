"""CNU 규정 나침반 — 원격 MCP 진입점 (Streamable HTTP, Cloud Run 배포용).

★ 이 레포는 2026-09-07부터 **adapter**다 (코어 전환 P04).

검색·조문·참조·정본대조 엔진은 전부 비공개 코어 패키지(rule-compass-core)가
소유하고, 여기 남는 것은 전남대 배포본만의 관심사 셋뿐이다.

  ① 사용집계        src/usage.py — 코어에 없다(2026-09-06 박사 결정 B).
                    옮기면 확산 9개교까지 대상이 되는데 로그를 읽는 코드가
                    아직 없어 Cloud Logging 비용만 는다.
  ② 서비스 버전      RULE_COMPASS_SERVER_VERSION 환경변수(코어가 읽는다).
  ③ 진입점·데이터    이 파일 + RULE_COMPASS_DATA_DIR(Dockerfile이 /app/data 주입).

엔진 회귀 검사도 코어가 소유한다(369건). 여기 tests/는 adapter만 검사한다.

- 공개 프로브는 `/health` 사용 — run.app 도메인은 Google Frontend가
  정확히 `/healthz` 경로를 가로채므로 쓰지 않는다(academyinfo 실측).
- `ALLOWED_HOSTS` 환경변수(콤마 구분)가 설정되면 DNS rebinding 보호를 켠다.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 설치본 코어는 코퍼스를 RULE_COMPASS_DATA_DIR 에서 찾는다 — core import 전에 둔다(P04).
os.environ.setdefault("RULE_COMPASS_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data"))

from core.mcp_server import SERVER_VERSION, create_server
from core.profile import active_profile
from core.search import get_default_index

from src.usage import use_stdout_for_usage
from src.usage import wrap as usage_wrapper

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
    # 이 전송에서는 stdout이 로그 채널이다(Cloud Logging 자동 수집).
    # stdio에서는 JSON-RPC 전용이라 집계 기본값이 stderr다.
    use_stdout_for_usage()
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
    # 집계 훅을 코어 도구에 얹는다. 도구 **목록**은 코어가 소유하므로
    # 코어에 도구가 늘어도 여기를 고치지 않아도 따라온다 (코어 0.6.1 _TOOLS).
    server = create_server(tool_wrapper=usage_wrapper, **settings)

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

"""CNU 규정 나침반 — stdio MCP 진입점 (adapter).

2026-09-07 코어 전환(P04)에서 엔진과 함께 지웠다가, 두 리뷰가 같은 지적을 해서
되살렸다 — pyproject `[project.scripts] cnu-rule-compass-mcp`와 로컬 Claude
설정(~/.claude.json)이 여전히 `src.mcp_server:main`을 가리킨다.

HTTP 진입점(src/http_server.py)과 같은 훅으로 사용집계를 얹되, 채널은 **stderr**
그대로다 — stdio에서 stdout은 JSON-RPC 전용이다(src/usage.py 참고).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 설치본 코어는 코퍼스를 RULE_COMPASS_DATA_DIR 에서 찾는다. core import 전에 둔다.
os.environ.setdefault("RULE_COMPASS_DATA_DIR",
                      str(Path(__file__).resolve().parent.parent / "data"))

from core.mcp_server import create_server  # noqa: E402
from core.search import get_default_index  # noqa: E402

from src.usage import wrap as usage_wrapper  # noqa: E402


def main() -> int:
    try:
        get_default_index()  # 서버를 열기 전에 코퍼스 게이트를 통과시킨다.
        server = create_server(tool_wrapper=usage_wrapper)
    except Exception as exc:
        print(f"[오류] {exc}", file=sys.stderr)
        return 1
    try:
        server.run()
    except Exception as exc:
        print(f"[오류] MCP 서버 실행 실패: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

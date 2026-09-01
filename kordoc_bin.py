"""kordoc 실행 환경 해석 — 무인 실행에서도 잡히게 한다.

★ 왜 필요한가 (2026-09-01 실측)

kordoc(HWP 파서)은 nvm이 관리하는 node 아래에 있고, 그 경로는 nvm을 source한
**대화형 셸에서만** PATH에 잡힌다. launchd는 최소 PATH로 실행하고 plist에
PATH 선언이 없어, 무인 수집이 매번 죽었다:

    [Errno 2] No such file or directory: 'kordoc'

  전남대 게시 목록 637건 중 **113건이 전부 이 실패로 미수집**이었다.
  8/14·8/19 개정된 규정 4건의 신판(key 5061~5064)도 여기서 막혀, 구판만
  「…(개정전)」으로 남고 **현행 조문이 0건**이 됐다. 「기다리면 들어온다」가
  아니라 고쳐야 들어오는 문제였다.

★ 경로만 풀어서는 안 된다

kordoc은 `#!/usr/bin/env node` 스크립트다. 절대경로로 불러도 **동반 node가
PATH에 없으면** `env: node: No such file or directory`로 죽는다. 처음 이
모듈은 경로만 돌려주고 통과한 줄 알았는데, 최소 환경에서 `--version` 출력이
비어 드러났다. 그래서 실행 파일과 **환경을 함께** 돌려준다.

★ 최신 node를 고르면 틀린다

실측: node v24.14.1에는 kordoc **4.2.0**, v22.23.1에는 **4.7.3**이 있다.
node 버전은 kordoc 버전과 무관하다. 그래서 후보마다 실제로 `--version`을
물어 **동작하는 것 중 가장 높은 kordoc**을 고른다. 추정으로 정렬하지 않는다.

해석 순서:
  1. `KORDOC_BIN` 환경변수 (명시 지정이 언제나 이긴다)
  2. PATH의 kordoc
  3. nvm 설치본 전수 — 각각 실행해 보고 버전으로 선택
  4. 못 찾으면 **조치 가능한 메시지로** 실패한다. [Errno 2]는 원인을 말해
     주지 않아 113건이 두 주 넘게 조용히 쌓였다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

_CACHED: tuple[str, dict[str, str]] | None = None


def _probe(binary: Path) -> tuple[int, ...] | None:
    """이 kordoc이 **실제로 도는지** 묻고 버전을 돌려준다. 안 돌면 None."""
    env = {**os.environ, "PATH": _path_with(binary.parent)}
    try:
        done = subprocess.run([str(binary), "--version"], capture_output=True,
                              text=True, timeout=30, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    raw = (done.stdout or "").strip().splitlines()
    if done.returncode != 0 or not raw:
        return None
    token = raw[0].strip()
    try:
        return tuple(int(x) for x in token.split("."))
    except ValueError:
        # 버전 형식이 달라도 「돌기는 한다」는 사실은 남긴다.
        return (0,)


def _path_with(bin_dir: Path) -> str:
    """동반 node를 찾도록 그 bin 디렉터리를 PATH 맨 앞에 붙인다."""
    current = os.environ.get("PATH", "")
    return f"{bin_dir}{os.pathsep}{current}" if current else str(bin_dir)


def _candidates() -> list[Path]:
    found = shutil.which("kordoc")
    out = [Path(found)] if found else []
    base = Path.home() / ".nvm" / "versions" / "node"
    if base.is_dir():
        out += [d / "bin" / "kordoc" for d in sorted(base.iterdir()) if d.is_dir()]
    return [p for p in out if p.exists() and os.access(p, os.X_OK)]


def resolve_kordoc() -> tuple[str, dict[str, str]]:
    """(실행 파일 경로, 실행에 쓸 환경)을 돌려준다. 결과는 캐시한다."""
    global _CACHED
    if _CACHED is not None:
        return _CACHED
    explicit = os.environ.get("KORDOC_BIN", "").strip()
    if explicit:
        path = Path(explicit)
        if not path.exists():
            raise RuntimeError(f"KORDOC_BIN이 가리키는 파일이 없습니다: {explicit}")
        _CACHED = (str(path), {**os.environ, "PATH": _path_with(path.parent)})
        return _CACHED

    best: tuple[tuple[int, ...], Path] | None = None
    for candidate in _candidates():
        version = _probe(candidate)
        if version is None:
            continue
        if best is None or version > best[0]:
            best = (version, candidate)
    if best is None:
        raise RuntimeError(
            "동작하는 kordoc을 찾지 못했습니다. HWP 파싱이 전부 실패합니다.\n"
            "  · PATH에도, ~/.nvm/versions/node/*/bin/ 에도 실행 가능한 것이 없습니다.\n"
            "  · kordoc은 #!/usr/bin/env node 스크립트라 동반 node도 필요합니다.\n"
            "  · 무인 실행(launchd)이라면 nvm 경로가 PATH에 없는 것이 원인입니다.\n"
            "  · 조치: KORDOC_BIN 환경변수로 절대경로를 주거나 kordoc을 설치하세요.")
    version, binary = best
    _CACHED = (str(binary), {**os.environ, "PATH": _path_with(binary.parent)})
    return _CACHED


def kordoc_binary() -> str:
    return resolve_kordoc()[0]


def kordoc_env() -> dict[str, str]:
    """subprocess에 넘길 환경. 동반 node를 찾도록 PATH가 조정돼 있다."""
    return resolve_kordoc()[1]


def kordoc_command(src: Path, out: Path) -> list[str]:
    """`kordoc <src> -o <out>` 명령. 호출부는 env=kordoc_env()도 함께 넘긴다."""
    return [kordoc_binary(), str(src), "-o", str(out)]


def kordoc_version() -> str:
    binary = Path(kordoc_binary())
    version = _probe(binary)
    return ".".join(str(x) for x in version) if version else "unknown"


if __name__ == "__main__":  # 진단용: 어느 것을 쓸지 눈으로 확인한다.
    b, env = resolve_kordoc()
    print(f"kordoc {kordoc_version()}  {b}")
    print(f"PATH 앞머리: {env['PATH'].split(os.pathsep)[0]}")

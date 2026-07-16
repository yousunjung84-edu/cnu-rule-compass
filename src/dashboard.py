"""규정 코퍼스와 운영 로그를 보여 주는 읽기 전용 대시보드."""

from __future__ import annotations

import argparse
import html
from collections import Counter
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from src.learn import list_candidates
from src.search import RuleSearchIndex, get_default_index
from src.store import JsonStore, get_default_store


_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_PATH = _ROOT / "dashboard.html"


def build_state(
    index: RuleSearchIndex | None = None,
    data_store: JsonStore | None = None,
    recent: int = 20,
) -> dict:
    """서버 없이 테스트 가능한 운영 상태 스냅샷을 만든다."""
    search_index = index or get_default_index()
    target = data_store or get_default_store()
    articles = search_index.articles
    distribution = Counter(str(item.get("편제", "미분류")) for item in articles)
    regulations = {str(item.get("규정명", "")) for item in articles if item.get("규정명")}
    logs = list(reversed(target.read("queries")[-max(recent, 0):]))
    return {
        "corpus": {
            "regulation_count": len(regulations),
            "article_count": len(articles),
            "division_distribution": dict(sorted(distribution.items())),
        },
        "recent_queries": logs,
        "candidates": list_candidates(target),
    }


def _table_rows(rows: list[list[object]]) -> str:
    return "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(cell))}</td>" for cell in row) + "</tr>"
        for row in rows
    )


def _division_bars(distribution: dict) -> str:
    """편제별 조문 수를 내림차순 가로 막대 차트로 렌더링한다."""
    if not distribution:
        return '<div class="muted">편제 정보 없음</div>'
    ordered = sorted(distribution.items(), key=lambda item: -item[1])
    peak = max(distribution.values()) or 1
    rows = []
    for name, count in ordered:
        width = count / peak * 100
        rows.append(
            f'<div class="bar-row"><span class="bar-label">{html.escape(str(name))}</span>'
            f'<span class="bar-track"><span class="bar-fill" style="width:{width:.1f}%"></span></span>'
            f'<span class="bar-val">{count}</span></div>'
        )
    return "".join(rows)


def render_html(state: dict) -> str:
    """운영 상태를 자체완결 단일 HTML로 렌더링한다."""
    corpus = state["corpus"]
    div_bars = _division_bars(corpus["division_distribution"])
    div_count = len(corpus["division_distribution"])
    recent = _table_rows([
        [row.get("created_at", ""), row.get("question", ""), "확인" if row.get("answered") else "미확인"]
        for row in state["recent_queries"]
    ]) or '<tr><td colspan="3" class="empty">질의 기록 없음 — 이용이 쌓이면 여기에 표시됩니다</td></tr>'
    candidates = _table_rows([
        [row.get("id", ""), row.get("asked_count", 1), row.get("question", "")]
        for row in state["candidates"]
    ]) or '<tr><td colspan="3" class="empty">대기 중인 지식 후보 없음 — 답 못 한 질의가 여기 쌓입니다</td></tr>'
    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CNU 규정 나침반 — 운영 대시보드</title>
<style>
:root{{--navy:#16294d;--blue:#3949ab;--gold:#c19a3e;--pale:#eef2f8;--line:#c8d4df;--ink:#182431;--gray:#657585}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--pale);color:var(--ink);font-family:"Pretendard","Apple SD Gothic Neo",sans-serif}}
header{{padding:26px 30px;background:linear-gradient(135deg,var(--navy),var(--blue));color:white}}
header h1{{margin:0;font-size:24px}} .wrap{{max-width:1080px;margin:auto;padding:24px 24px 48px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px}}
.card,.panel{{background:white;border:1px solid var(--line);border-radius:12px}}
.card{{padding:18px;border-top:3px solid var(--gold)}}
.number{{font-size:32px;font-weight:800;color:var(--navy);line-height:1.1}}
.card .lbl{{font-size:13.5px;color:var(--gray);margin-top:4px}}
h2{{margin:30px 0 12px;color:var(--navy);font-size:18px;border-left:4px solid var(--gold);padding-left:10px}}
.panel{{padding:18px 20px}}
.bar-row{{display:flex;align-items:center;gap:10px;margin:7px 0;font-size:14px}}
.bar-label{{flex:0 0 130px;text-align:right;color:var(--ink)}}
.bar-track{{flex:1;background:#e6ecf4;border-radius:6px;height:16px;overflow:hidden}}
.bar-fill{{display:block;height:100%;background:linear-gradient(90deg,var(--blue),var(--navy));border-radius:6px}}
.bar-val{{flex:0 0 52px;text-align:right;font-weight:700;color:var(--navy)}}
table{{border-collapse:collapse;width:100%;background:white;border:1px solid var(--line);border-radius:12px;overflow:hidden}}
th,td{{border:1px solid var(--line);padding:8px 10px;text-align:left;font-size:14px}}
th{{background:var(--navy);color:white}} .muted{{color:var(--gray);font-size:13px}}
.empty{{color:var(--gray);text-align:center;padding:16px}}
.note{{margin-top:8px;font-size:12.5px;color:var(--gray)}}
</style></head><body>
<header><h1>🧭 CNU 규정 나침반 — 운영 대시보드</h1><div class="muted" style="color:#dbe7f2">공식 규정 코퍼스와 비식별 운영 로그 · 조문 원문 인용·미확인 응답 원칙</div></header>
<main class="wrap"><section class="cards">
<div class="card"><div class="number">{corpus['regulation_count']}</div><div class="lbl">규정 수</div></div>
<div class="card"><div class="number">{corpus['article_count']}</div><div class="lbl">조문 수 (무결성 검증 적재)</div></div>
<div class="card"><div class="number">{div_count}</div><div class="lbl">편제</div></div>
<div class="card"><div class="number">{len(state['candidates'])}</div><div class="lbl">미확인 지식 후보</div></div>
</section>
<h2>편제별 조문 분포</h2><div class="panel">{div_bars}</div>
<p class="note">빈 조문·중복·비정상 본문은 적재 시점에 걸러낸 뒤 인덱싱한 결과입니다.</p>
<h2>최근 질의 로그</h2><table><thead><tr><th>시간(UTC)</th><th>마스킹 질의</th><th>상태</th></tr></thead><tbody>{recent}</tbody></table>
<h2>미확인 질의 — 지식 후보</h2><table><thead><tr><th>ID</th><th>질의 횟수</th><th>마스킹 질의</th></tr></thead><tbody>{candidates}</tbody></table>
</main></body></html>"""


def generate_html(
    output_path: str | Path = DEFAULT_OUTPUT_PATH,
    index: RuleSearchIndex | None = None,
    data_store: JsonStore | None = None,
) -> Path:
    """대시보드 HTML을 UTF-8로 저장하고 경로를 반환한다."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(build_state(index, data_store)), encoding="utf-8")
    return path.resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description="CNU 규정 나침반 운영 대시보드")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8792)
    args = parser.parse_args()
    path = generate_html(args.output)
    print(f"[dashboard] {path}")
    if args.serve:
        handler = lambda *handler_args, **kwargs: SimpleHTTPRequestHandler(  # noqa: E731
            *handler_args, directory=str(path.parent), **kwargs
        )
        server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
        print(f"[dashboard] http://127.0.0.1:{args.port}/{path.name}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""사용 기록 보존·집계 — Cloud Logging은 30일 뒤 지우므로 로컬에 옮겨 둔다.

  pull    운영 로그에서 두 가지를 data/usage/ 로 옮긴다 (로컬 전용, 공개 레포 제외).
          ① 사용 집계 줄(src/usage.py 가 남기는 {"usage": ...}) — 전남대만, 한 줄씩
          ② 10개교 운영 서비스(<대학>-rule-compass)의 POST /mcp 요청 수 —
             날짜·서비스·접속 프로그램·HTTP 상태별 개수만 (IP·경로·질의는 옮기지 않는다)
  report  보존본으로 기간 집계를 낸다 (도구·접속 프로그램·규정 상위·못 찾음 비율).

같은 날짜를 다시 받으면 그날 몫을 갈아 끼운다 — 몇 번 돌려도 결과가 같다.
단, 새로 받은 몫이 보존본보다 적으면 갈아 끼우지 않는다. Cloud Logging 보존기간
(30일)이 지난 날은 빈 결과가 오고, 경계에 걸린 날은 일부만 오기 때문이다.
조회가 상한(LIMIT)에 닿으면 그날은 빠진 시간대가 있을 수 있어 오류로 멈춘다.

날짜는 UTC 기준이다 — 한국시간 09:00부터 다음 날 09:00까지가 하루다.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.usage import KNOWN_CLIENTS  # noqa: E402 — 서버와 같은 허용 목록을 쓴다

OUT_DIR = ROOT / "data" / "usage"
EVENTS = OUT_DIR / "usage_events.jsonl"
HTTP_DAILY = OUT_DIR / "http_daily.json"
PROJECT = "academyinfo-mcp-2026"
USAGE_SERVICE = "cnu-rule-compass"
# 운영 10개교만 — rule-compass-gateway 같은 다른 서비스는 뺀다.
SERVICE_RE = "^[a-z]+-rule-compass$"
MCP_URL_RE = "^https?://[^/]+/mcp/?([?].*)?$"
LIMIT = 1_000_000

# 접속 프로그램 이름 → 보고서 묶음. 이름은 요청 머리글 그대로라 추정이 섞인다.
# 스크립트·CLI 도구는 외부 사용자도 쓰므로 우리 시험으로 확정하지 않는다.
SCRIPT_GROUP = "스크립트·CLI(우리 시험 포함)"
CLIENT_GROUPS = {
    "undici": "AIONI(추정)",
    "Claude-User": "claude.ai 커넥터",
    "claude-code": SCRIPT_GROUP,
    "python-httpx": SCRIPT_GROUP,
    "Python-urllib": SCRIPT_GROUP,
    "curl": SCRIPT_GROUP,
}


def ua_token(user_agent: str | None) -> str:
    """서버(src/usage.py)와 같은 규칙: 알려진 프로그램 이름만 남기고 나머지는 "기타".

    머리글은 요청자가 정하는 글자라 IP·이메일이 들어올 수 있다.
    """
    token = (user_agent or "").split("/", 1)[0].split(" ", 1)[0]
    if not token:
        return "(없음)"
    return token if token in KNOWN_CLIENTS else "기타"


def client_group(token: str) -> str:
    return CLIENT_GROUPS.get(token, "기타")


def _day_range(since: dt.date, until: dt.date):
    day = since
    while day <= until:
        yield day
        day += dt.timedelta(days=1)


def _gcloud_read(log_filter: str, fmt: str) -> str:
    cmd = ["gcloud", "logging", "read", log_filter, "--project", PROJECT,
           "--limit", str(LIMIT), "--format", fmt]
    done = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"gcloud logging read 실패: {done.stderr.strip()[:300]}")
    return done.stdout


def _window(day: dt.date) -> str:
    start = f"{day.isoformat()}T00:00:00Z"
    end = f"{(day + dt.timedelta(days=1)).isoformat()}T00:00:00Z"
    return f'timestamp>="{start}" AND timestamp<"{end}"'


def pull_usage_day(day: dt.date) -> list[dict]:
    raw = _gcloud_read(
        'resource.type="cloud_run_revision" '
        f'AND resource.labels.service_name="{USAGE_SERVICE}" '
        f"AND jsonPayload.usage:* AND {_window(day)}", "json")
    entries = json.loads(raw or "[]")
    _check_limit(len(entries), day, "사용 집계")
    rows = []
    for entry in entries:
        rows.append({
            "id": entry["insertId"],
            "ts": entry["timestamp"],
            "revision": entry.get("resource", {}).get("labels", {}).get("revision_name"),
            **entry["jsonPayload"],
        })
    return rows


def pull_http_day(day: dt.date) -> dict:
    raw = _gcloud_read(
        'resource.type="cloud_run_revision" '
        f'AND resource.labels.service_name=~"{SERVICE_RE}" '
        f'AND httpRequest.requestMethod="POST" AND httpRequest.requestUrl=~"{MCP_URL_RE}" '
        f"AND {_window(day)}",
        "value(resource.labels.service_name,httpRequest.userAgent,httpRequest.status)")
    lines = raw.splitlines()
    _check_limit(len(lines), day, "요청 수")
    counts: collections.Counter = collections.Counter()
    for line in lines:
        service, _, rest = line.partition("\t")
        agent, _, status = rest.partition("\t")
        counts[f"{service}\t{ua_token(agent)}\t{status or '?'}"] += 1
    return dict(sorted(counts.items()))


def _check_limit(n: int, day: dt.date, what: str) -> None:
    # gcloud --limit 은 전체 반환 상한이고 최신순이다 — 닿으면 이른 시간대가 빠졌다.
    if n >= LIMIT:
        raise RuntimeError(f"{day} {what} 조회가 상한 {LIMIT}건에 닿음 — 그날은 갈아 끼우지 않고 멈춘다")


def _renormalize_http(counts: dict) -> dict:
    """이전 규칙(첫 낱말 그대로)으로 쌓인 키를 지금 허용 목록 규칙으로 다시 묶는다."""
    merged: collections.Counter = collections.Counter()
    for key, n in counts.items():
        service, token, status = key.split("\t")
        known = token if token in KNOWN_CLIENTS or token in ("(없음)", "기타") else "기타"
        merged[f"{service}\t{known}\t{status}"] += n
    return dict(sorted(merged.items()))


def _load_events() -> dict[str, dict]:
    if not EVENTS.exists():
        return {}
    rows = (json.loads(line) for line in EVENTS.read_text(encoding="utf-8").splitlines() if line)
    return {row["id"]: row for row in rows}


def cmd_pull(args) -> int:
    since = dt.date.fromisoformat(args.since)
    until = dt.date.fromisoformat(args.until) if args.until else dt.datetime.now(dt.timezone.utc).date()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    events = _load_events()
    http = json.loads(HTTP_DAILY.read_text(encoding="utf-8")) if HTTP_DAILY.exists() else {}
    http = {d: _renormalize_http(c) for d, c in http.items()}
    for day in _day_range(since, until):
        key = day.isoformat()
        kept_rows = {i: r for i, r in events.items() if r["ts"].startswith(key)}
        day_rows = pull_usage_day(day)
        day_http = pull_http_day(day)
        # 새로 받은 몫이 보존본보다 적으면(보존기간 만료·경계) 보존본을 지킨다.
        if len(day_rows) >= len(kept_rows):
            events = {i: r for i, r in events.items() if i not in kept_rows}
            events.update({row["id"]: row for row in day_rows})
            usage_note = f"사용 집계 {len(day_rows)}줄"
        else:
            usage_note = f"사용 집계 보존본 유지({len(kept_rows)}줄 > 새로 받은 {len(day_rows)}줄)"
        if sum(day_http.values()) >= sum(http.get(key, {}).values()):
            http[key] = day_http
            http_note = f"요청 {sum(day_http.values())}건"
        else:
            http_note = (f"요청 수 보존본 유지({sum(http[key].values())}건 "
                         f"> 새로 받은 {sum(day_http.values())}건)")
        print(f"{key} {usage_note} · {http_note}", flush=True)
    ordered = sorted(events.values(), key=lambda r: (r["ts"], r["id"]))
    tmp = EVENTS.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ordered), encoding="utf-8")
    tmp.replace(EVENTS)
    tmp = HTTP_DAILY.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(sorted(http.items())), ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(HTTP_DAILY)
    print(f"보존본: {EVENTS} ({len(ordered)}줄) · {HTTP_DAILY} ({len(http)}일)")
    return 0


def _in_range(day: str, since: str | None, until: str | None) -> bool:
    return (since is None or day >= since) and (until is None or day <= until)


def cmd_report(args) -> int:
    events = [r for r in _load_events().values() if _in_range(r["ts"][:10], args.since, args.until)]
    http = json.loads(HTTP_DAILY.read_text(encoding="utf-8")) if HTTP_DAILY.exists() else {}
    http = {d: c for d, c in http.items() if _in_range(d, args.since, args.until)}
    exclude = set(args.exclude_day or [])

    print(f"# 규정 나침반 사용 집계 ({args.since or '처음'} ~ {args.until or '끝'})")
    if exclude:
        print(f"제외한 날짜(시험 등): {', '.join(sorted(exclude))}")

    print("\n## 10개교 운영 서비스 POST /mcp 요청 수 — 접속 프로그램 묶음별")
    by_group: collections.Counter = collections.Counter()
    by_service_group: collections.Counter = collections.Counter()
    for day, counts in http.items():
        if day in exclude:
            continue
        for key, n in counts.items():
            service, token, _status = key.split("\t")
            group = client_group(token)
            by_group[group] += n
            by_service_group[(service.removesuffix("-rule-compass"), group)] += n
    for group, n in by_group.most_common():
        print(f"- {group}: {n}")
    print("\n| 대학 | " + " | ".join(g for g, _ in by_group.most_common()) + " |")
    print("|---|" + "---|" * len(by_group))
    for service in sorted({s for s, _ in by_service_group}):
        cells = [str(by_service_group.get((service, g), 0)) for g, _ in by_group.most_common()]
        print(f"| {service} | " + " | ".join(cells) + " |")

    kept = [r for r in events if r["ts"][:10] not in exclude]
    has_client = [r for r in kept if "client" in r]
    print(f"\n## 전남대 도구 호출 (사용 집계 줄 {len(kept)}건)")
    if kept and len(has_client) < len(kept):
        print(f"※ 접속 프로그램이 적힌 줄은 {len(has_client)}건 — 그 전 줄은 출처를 구별할 수 없다.")
    tools = collections.Counter(r["usage"] for r in kept)
    for tool, n in tools.most_common():
        miss = sum(1 for r in kept if r["usage"] == tool and r.get("status") != "ok")
        print(f"- {tool}: {n}건 (못 찾음 {miss}건, {miss / n:.1%})")
    if has_client:
        print("\n접속 프로그램별:")
        for group, n in collections.Counter(client_group(r["client"]) for r in has_client).most_common():
            print(f"- {group}: {n}")

    arts = collections.Counter(r["rule"] for r in kept if r["usage"] == "get_article" and r.get("rule"))
    print(f"\n## 조문을 직접 연 규정 상위 {args.top} (get_article)")
    for rule, n in arts.most_common(args.top):
        print(f"- {rule}: {n}")
    hits = collections.Counter(name for r in kept if r["usage"] == "search_rule" for name in r.get("rules") or [])
    print(f"\n## 검색 결과에 오른 규정 상위 {args.top} "
          "(search_rule — 한 검색당 결과 규정명을 이름순으로 최대 5개 기록, 검색 순위 아님)")
    for rule, n in hits.most_common(args.top):
        print(f"- {rule}: {n}")
    unmatched = sum(1 for r in kept if r["usage"] == "search_rule" and r.get("unmatched_terms"))
    searches = tools.get("search_rule", 0)
    if searches:
        print(f"\n못 찾은 낱말이 하나 이상 있던 검색: {unmatched}/{searches} ({unmatched / searches:.1%})")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    pull = sub.add_parser("pull", help="운영 로그 → data/usage/")
    pull.add_argument("--since", required=True, help="YYYY-MM-DD (UTC, 한국시간 09:00 시작)")
    pull.add_argument("--until", help="YYYY-MM-DD (UTC, 기본 오늘)")
    pull.set_defaults(func=cmd_pull)
    report = sub.add_parser("report", help="보존본 집계")
    report.add_argument("--since")
    report.add_argument("--until")
    report.add_argument("--exclude-day", action="append", help="집계에서 뺄 날짜(시험일). 여러 번 가능")
    report.add_argument("--top", type=int, default=15)
    report.set_defaults(func=cmd_report)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except RuntimeError as exc:
        print(f"[오류] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

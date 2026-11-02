#!/usr/bin/env python3
"""월간 코퍼스 갱신(refresh_corpus.py)이 실패·누락·배포 대기로 멈춰 있지 않은지 본다.

왜 따로 보나 (2026-11-02):
  11/1 갱신은 게시 목록 시간 초과로 실패·원복됐지만, 알림은 맥미니 화면 알림
  (osascript) 하나뿐이었고 일일 점검은 보고서를 읽지 않았다. 10/1 갱신분은
  '배포 대기'로 한 달 동안 운영에 안 올라갔다. 둘 다 AIONI 에서 옛 규정 답이 나간다.

판정:
  - 결과가 '실패'·'승인 거부'로 시작 → 문제 (승인 필요 change-set 도 '실패·원복: change-set 승인 필요'로 남는다)
  - 이번 달 예정 실행(1일 04:30) 뒤 보고서가 없다 → 문제 (job 이 안 돌았거나 보고서 전에 죽었다)
  - '성공·배포 대기'인데 운영 서비스가 그 수집분을 서빙하지 않은 채 DEPLOY_GRACE_DAYS 일이 지났다 → 문제

종료코드: 0 = 정상 / 1 = 문제 있음 / 2 = 검사 자체 실패 (조용한 실패 금지 — service_drift_check.py 와 같은 규약)
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "data" / "corpus_refresh_report.json"
MCP_URL = "https://cnu-rule-compass-433006350023.asia-northeast3.run.app/mcp"
SCHEDULE_DAY, SCHEDULE_HOUR, SCHEDULE_MINUTE = 1, 4, 30  # com.yuseon.rulecompass-corpus-refresh.plist
RUN_GRACE = dt.timedelta(hours=4)  # 예정 시각 뒤 이만큼은 '아직 도는 중'으로 본다
DEPLOY_GRACE_DAYS = 3
CLEAN_TOKEN = "갱신 이상 0건"


def expected_run(now: dt.datetime) -> dt.datetime:
    """now 시점에 이미 끝났어야 하는 가장 최근 예정 실행 시각."""
    this = now.replace(day=SCHEDULE_DAY, hour=SCHEDULE_HOUR, minute=SCHEDULE_MINUTE,
                       second=0, microsecond=0)
    if now >= this + RUN_GRACE:
        return this
    prev_month_last = this - dt.timedelta(days=1)
    return prev_month_last.replace(day=SCHEDULE_DAY)


def served_asof(timeout: int = 60) -> str | None:
    """운영 서비스가 서빙 중인 코퍼스의 최신 수집일시(get_corpus_stats)."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "get_corpus_stats", "arguments": {}}}).encode()
    req = urllib.request.Request(MCP_URL, data=body, headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    raw = urllib.request.urlopen(req, timeout=timeout).read().decode()
    for line in raw.splitlines():
        if line.startswith("data: "):
            content = json.loads(line[6:]).get("result", {}).get("content", [])
            if content:
                return json.loads(content[0]["text"]).get("최신_수집일시")
    return None


def judge(report: dict, now: dt.datetime, asof_fn=served_asof) -> list[str]:
    """문제 문장 목록. 빈 목록이면 정상. 판단 불가(보고서 형식 깨짐)는 ValueError."""
    problems: list[str] = []
    started = dt.datetime.fromisoformat(report["시작"])
    result = str(report.get("결과", ""))
    due = expected_run(now)
    if started < due:
        problems.append(f"이번 예정 실행({due:%m-%d %H:%M}) 기록 없음 — 마지막 실행 {started:%m-%d %H:%M}"
                        f" '{result[:60]}'. launchd job 이 안 돌았거나 보고서 전에 죽었다")
    if result.startswith(("실패", "승인 거부")):
        problems.append(f"{started:%m-%d %H:%M} 갱신 {result[:300]}")
    elif result == "성공·배포 대기":
        waited = (now - started).days
        if waited >= DEPLOY_GRACE_DAYS:
            asof = asof_fn()
            if asof is None:
                raise ValueError("운영 서비스 수집일시 조회 결과가 비었다")
            served = dt.datetime.fromisoformat(asof)
            if served.tzinfo is None:  # get_corpus_stats 는 시간대 없이 준다(KST)
                served = served.replace(tzinfo=started.tzinfo)
            if served < started:
                problems.append(f"{started:%m-%d} 갱신분이 {waited}일째 배포 대기 — 운영은 {asof[:10]} 수집분을 서빙 중."
                                f" data/corpus_refresh_report.json 의 배포_명령 참고(배포는 박사 승인 뒤)")
    elif result != "변경 없음":
        raise ValueError(f"알 수 없는 결과 값: {result[:80]!r}")
    return problems


def main() -> int:
    now = dt.datetime.now().astimezone()
    try:
        report = json.loads(REPORT.read_text(encoding="utf-8"))
        problems = judge(report, now)
    except Exception as exc:  # 검사기가 깨져도 '정상'을 내지 않는다
        print(f"갱신 상태 검사 실패: {type(exc).__name__}: {exc}")
        return 2
    if not problems:
        print(CLEAN_TOKEN)
        return 0
    print("\n".join(f"- {p}" for p in problems))
    return 1


if __name__ == "__main__":
    sys.exit(main())

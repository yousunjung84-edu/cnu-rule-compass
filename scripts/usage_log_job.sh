#!/bin/bash
# 일일 사용 기록 보존 — Cloud Logging 은 30일 뒤 지우므로 매일 로컬로 옮긴다.
# 03:20 일일 작업(com.rulecompass.daily) 뒤 03:50 에 돈다.
#
# 최근 7일(UTC)을 매번 다시 받는다. 맥미니가 며칠 꺼져 있어도 7일 안이면 메워지고,
# 덜 찬 날은 다음 회차에 더 많은 몫으로 갈아 끼워진다(usage_log.py 의 보존본 규칙).
#
# ⚠️ 조용한 실패 금지: 실패하면 화면 알림 + 디스코드 운영 경보 채널로 알린다.
#    webhook 은 ops-alert(봇 경보)의 plist 에서 읽는다 — 비밀값을 두 곳에 복사하지 않는다.
set -uo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$ROOT/data/usage/pull.log"
mkdir -p "$(dirname "$LOG")"
ts=$(date '+%Y-%m-%d %H:%M:%S')
since=$(date -u -v-7d '+%Y-%m-%d')

out=$("$ROOT/.venv/bin/python" "$ROOT/scripts/usage_log.py" pull --since "$since" 2>&1); rc=$?
{
  echo "[$ts] pull --since $since rc=$rc"
  echo "$out"
  echo "---"
} >> "$LOG"
[ "$rc" -eq 0 ] && exit 0

osascript -e 'display notification "사용 기록 보존(pull)이 실패했습니다. data/usage/pull.log 확인" with title "규정 나침반 사용 기록"' 2>/dev/null
webhook=$(plutil -extract EnvironmentVariables.OPS_ALERT_WEBHOOK_URL raw \
  "$HOME/Library/LaunchAgents/com.allagent.ops-alert.plist" 2>/dev/null)
if [ -z "$webhook" ]; then
  echo "[$ts] ⚠️ 디스코드 전송 못 함 — ops-alert plist 에 webhook 없음" >> "$LOG"
  exit 1
fi
msg=$(printf '🔴 **규정 나침반 사용 기록 보존 실패 (rc=%s)**\n%s\n(맥미니 ~/rulecompass-build/data/usage/pull.log)' \
  "$rc" "$(echo "$out" | tail -15)")
body=$(MSG="$msg" python3 -c 'import json,os; print(json.dumps({"content": os.environ["MSG"][:1900]}))')
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' \
  -H 'User-Agent: rulecompass-usage-pull/1.0' --data "$body" "$webhook")
echo "[$ts] 디스코드 전송 HTTP $code" >> "$LOG"
exit 1

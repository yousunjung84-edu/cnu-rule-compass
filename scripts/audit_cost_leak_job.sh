#!/bin/bash
# 주간 Cloud Run 과금 누수 감사 — revision_tag_guard.sh audit 를 돌려
# 누수가 있을 때만 알린다(없으면 로그만 남기고 조용히 끝난다).
#
# 왜 스케줄인가: 태그+min-instances 누수는 배포 순간엔 티가 안 나고 월말 청구서에서야
# 드러난다. 사람이 기억해서 돌리는 방식은 신뢰할 수 없다.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$HOME/rulecompass-build/data/cost_leak_audit.log"
mkdir -p "$(dirname "$LOG")"

out=$("$DIR/revision_tag_guard.sh" audit 2>&1)
ts=$(date '+%Y-%m-%d %H:%M:%S')

if echo "$out" | grep -q "누수 0건"; then
  echo "[$ts] 정상 — 누수 0건" >> "$LOG"
  exit 0
fi

# 누수 검출 (혹은 감사기 자체가 깨진 경우) — 둘 다 사람이 봐야 한다
echo "[$ts] ⚠️ 감사 결과 확인 필요" >> "$LOG"
echo "$out" >> "$LOG"
echo "---" >> "$LOG"
osascript -e 'display notification "Cloud Run 과금 누수 감사에서 이상이 잡혔습니다. data/cost_leak_audit.log 확인" with title "규정 나침반 비용 감사"' 2>/dev/null
exit 0

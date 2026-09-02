#!/bin/bash
# 일일 Cloud Run 점검 — 두 가지를 본다.
#   (1) 과금 누수: 트래픽 0% 태그 + minScale>0 리비전 (revision_tag_guard.sh)
#   (2) 설정 드리프트: ops/protected_services.json 선언 대비 실제 (service_drift_check.py)
# 이상이 있을 때만 알린다. 정상이면 로그 한 줄.
#
# 왜 스케줄인가: 둘 다 배포 순간엔 티가 안 나고 나중에야 드러난다.
# 사람이 기억해서 돌리는 방식은 신뢰할 수 없다.
#
# ⚠️ 조용한 실패 금지: 점검기 자체가 깨진 경우도 알림 대상이다.
#    (2026-09-02: 감사기 내부가 죽고도 "누수 0건"을 출력한 사고가 있었다.)
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$HOME/rulecompass-build/data/cost_leak_audit.log"
mkdir -p "$(dirname "$LOG")"
ts=$(date '+%Y-%m-%d %H:%M:%S')
problems=()

# (1) 과금 누수 감사 — 항상 exit 0 이므로 출력 문자열로 판정한다
leak_out=$("$DIR/revision_tag_guard.sh" audit 2>&1)
if ! echo "$leak_out" | grep -q "누수 0건"; then
  problems+=( "[과금 누수]"$'\n'"$leak_out" )
fi

# (2) 설정 드리프트 — 종료코드로 판정 (0 정상 / 1 드리프트 / 2 검사 실패)
drift_out=$(python3 "$DIR/service_drift_check.py" 2>&1); drift_rc=$?
if [ "$drift_rc" -ne 0 ]; then
  problems+=( "[설정 드리프트 rc=$drift_rc]"$'\n'"$drift_out" )
fi

if [ ${#problems[@]} -eq 0 ]; then
  echo "[$ts] 정상 — 누수 0건 / 드리프트 0건" >> "$LOG"
  exit 0
fi

{
  echo "[$ts] ⚠️ 점검에서 이상 검출 (${#problems[@]}건)"
  printf '%s\n' "${problems[@]}"
  echo "---"
} >> "$LOG"
osascript -e 'display notification "Cloud Run 일일 점검에서 이상이 잡혔습니다. data/cost_leak_audit.log 확인" with title "규정 나침반 점검"' 2>/dev/null
exit 0

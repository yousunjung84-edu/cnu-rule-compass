#!/bin/bash
# 8/18 시상식이 끝난 뒤 Cloud Run 상시 가동을 해제하는 1회성 잡 (8/19 09:00).
#
# 왜 필요한가: 8/17 재점등 잡(enable_min_instances.sh)은 min-instances=1을 켜기만 하고
# 되돌리지 않는다. cnu-rule-compass는 코퍼스 확장(17,271조문)으로 메모리를 2Gi로
# 올렸으므로, 켜둔 채 잊으면 2Gi 인스턴스가 상시 과금된다.
#
# **메모리는 되돌리지 않는다.** 인덱스가 1.4GB라 512Mi로 낮추면 서버가 기동하지 않는다.
# 이 잡이 만지는 것은 min-instances뿐이다.
#
# 8/17 잡이 실행되지 않았다면 이미 0이므로 이 잡은 무해한 no-op이다.

set -u

GCLOUD=/opt/homebrew/bin/gcloud
PROJECT=academyinfo-mcp-2026
REGION=asia-northeast3
SERVICES=(cnu-rule-compass academyinfo-mcp)
LABEL=com.yuseon.rulecompass-minscale-revert
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/rulecompass-build/data/min_instances_job.log"
DRY_RUN="${DRY_RUN:-0}"

mkdir -p "$(dirname "$LOG")"
echo "=== $(date '+%Y-%m-%d %H:%M:%S') 원복 잡 시작 (DRY_RUN=$DRY_RUN)" >> "$LOG"

for svc in "${SERVICES[@]}"; do
  current=$("$GCLOUD" run services describe "$svc" --project "$PROJECT" --region "$REGION" \
    --format='value(spec.template.metadata.annotations["autoscaling.knative.dev/minScale"])' 2>&1)
  if [ "$DRY_RUN" = "1" ]; then
    echo "[dry-run] $svc 현재 minScale='${current}' → 0으로 되돌릴 대상" >> "$LOG"
    continue
  fi
  if [ -z "$current" ] || [ "$current" = "0" ]; then
    echo "[생략] $svc 이미 minScale=0 (8/17 잡 미실행이거나 수동 원복됨)" >> "$LOG"
    continue
  fi
  if "$GCLOUD" run services update "$svc" --project "$PROJECT" --region "$REGION" \
      --min-instances 0 --quiet >> "$LOG" 2>&1; then
    echo "[성공] $svc min-instances=0 (메모리는 그대로 둔다)" >> "$LOG"
  else
    echo "[실패] $svc — 수동 확인 필요: gcloud run services update $svc --min-instances 0" >> "$LOG"
  fi
done

if [ "$DRY_RUN" = "1" ]; then
  echo "=== dry-run 종료 (plist 유지)" >> "$LOG"
  exit 0
fi

osascript -e 'display notification "Cloud Run 상시 가동 해제 완료 — 유휴 과금 종료" with title "규정 나침반"' 2>/dev/null

# 1회성 잡 — 스스로 해제(이듬해 8/19 재발화 방지)
echo "=== 잡 자체 해제" >> "$LOG"
rm -f "$PLIST"
/bin/launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null

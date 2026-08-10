#!/bin/bash
# 8/18 시상식 시연 대비 — Cloud Run 상시 가동(min-instances=1) 재점등 1회성 잡.
# 미적용 시 유휴 후 첫 질의가 약 7초 지연된다(2026-07-26 실측).
# 실행 후 스스로 plist를 제거해 이듬해 재발화를 막는다.
# 원복: gcloud run services update <svc> ... --min-instances 0

set -u

GCLOUD=/opt/homebrew/bin/gcloud
PROJECT=academyinfo-mcp-2026
REGION=asia-northeast3
SERVICES=(cnu-rule-compass academyinfo-mcp)
LABEL=com.yuseon.rulecompass-minscale
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/rulecompass-build/data/min_instances_job.log"
DRY_RUN="${DRY_RUN:-0}"

mkdir -p "$(dirname "$LOG")"
echo "=== $(date '+%Y-%m-%d %H:%M:%S') 재점등 잡 시작 (DRY_RUN=$DRY_RUN)" >> "$LOG"

for svc in "${SERVICES[@]}"; do
  if [ "$DRY_RUN" = "1" ]; then
    # 상태만 읽고 변경하지 않는다(설치 시점 점검용).
    current=$("$GCLOUD" run services describe "$svc" --project "$PROJECT" --region "$REGION" \
      --format='value(spec.template.metadata.annotations["autoscaling.knative.dev/minScale"])' 2>&1)
    echo "[dry-run] $svc 현재 minScale='$current'" >> "$LOG"
    continue
  fi
  if "$GCLOUD" run services update "$svc" --project "$PROJECT" --region "$REGION" \
      --min-instances 1 --quiet >> "$LOG" 2>&1; then
    echo "[성공] $svc min-instances=1" >> "$LOG"
  else
    echo "[실패] $svc — 수동 확인 필요" >> "$LOG"
  fi
done

if [ "$DRY_RUN" = "1" ]; then
  echo "=== dry-run 종료 (plist 유지)" >> "$LOG"
  exit 0
fi

# 확인용 프로브 (콜드면 수 초, 웜이면 0.1초대)
probe=$(curl -s -o /dev/null -w '%{http_code} %{time_total}s' \
  https://cnu-rule-compass-433006350023.asia-northeast3.run.app/health 2>&1)
echo "[확인] health $probe" >> "$LOG"

osascript -e 'display notification "Cloud Run 상시 가동 재점등 완료 — 내일 14:00 용봉홀 발표" with title "규정 나침반"' 2>/dev/null

# 1회성 잡 — 스스로 해제(이듬해 8/17 재발화 방지)
echo "=== 잡 자체 해제" >> "$LOG"
rm -f "$PLIST"
/bin/launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null

#!/bin/bash
# Cloud Run 상시 가동(min-instances=1) 재점등 잡 — 원래 8/18 시상식 시연 대비 1회성.
# 실행 후 스스로 plist를 제거해 재발화를 막는다.
#
# ⚠️ 2026-09-02 박사 확정으로 **대상이 cnu-rule-compass 하나로 축소**됐다.
#    academyinfo-mcp 는 min-instances=0 이 정책이다 — 아래에 넣지 말 것.
#
#    근거(2026-09-02 실측): 콜드스타트 p50 이 academyinfo 1.1초 / cnu 61.4초로 갈린다.
#    academyinfo 는 1.1초면 상시 가동이 필요 없다. cnu 는 AIONI 커넥터 대면 서비스라
#    61초 콜드스타트가 서비스 불가 수준이므로 유지한다.
#
# 원복: gcloud run services update cnu-rule-compass ... --min-instances 0

set -u

GCLOUD=/opt/homebrew/bin/gcloud
PROJECT=academyinfo-mcp-2026
REGION=asia-northeast3
SERVICES=(cnu-rule-compass)   # academyinfo-mcp 제외 — min=0 정책(2026-09-02 박사 확정)
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

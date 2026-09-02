#!/bin/bash
# revision_tag_guard.sh — Cloud Run 검증용 리비전의 "태그 + min-instances" 비용 누수 방지.
#
# ── 왜 있는가 (2026-09-02 사고) ────────────────────────────────────────────
# 검증용 리비전을 다음처럼 띄웠다:
#     gcloud run deploy cnu-rule-compass --min-instances=1 --no-traffic --tag=v195 ...
#
# --no-traffic 과 --tag 는 옳다(사용자에게 안 보내고 URL로 먼저 확인).
# 문제는 --min-instances=1 이다. min-instances 는 **리비전에 저장**되고,
# **태그가 붙은 리비전은 트래픽 0%여도 "참조 중"이라 죽지 않는다.**
# 결과: 검증 1회당 상시 인스턴스 1개가 영구 적립된다. 반복하면 그만큼 쌓인다.
#
# 단가는 기억하지 말고 Billing Catalog API 에서 뽑을 것 (리전 티어를 틀리기 쉽다):
#   GET cloudbilling.googleapis.com/v1/services/152E-C115-5142/skus?currencyCode=KRW
#   asia-northeast3 는 Tier 2 — Min Instance CPU·Memory 각 ₩0.004841479/s
#   = 1 vCPU-일 또는 1 GiB-일 약 ₩418. (2 vCPU/2 GiB 상시면 월 약 5만원)
#
# 규칙 두 개:
#   1) 검증용 리비전은 --min-instances=0 으로 띄운다 (운영 리비전 설정과 무관하다)
#   2) 검증이 끝나면 태그를 뗀다 — 안 떼면 영구 과금
#
# 사용법:
#   ./revision_tag_guard.sh audit                       # 누수 감사 (읽기 전용)
#   ./revision_tag_guard.sh deploy <svc> <image> <tag>  # 안전한 검증 배포
#   ./revision_tag_guard.sh cleanup <svc>               # 휴면 태그 제거 명령 출력
set -uo pipefail

GCLOUD="${GCLOUD:-/opt/homebrew/bin/gcloud}"
PROJECT="${PROJECT:-academyinfo-mcp-2026}"
REGION="${REGION:-asia-northeast3}"

die(){ echo "오류: $*" >&2; exit 1; }

audit() {
  echo "=== Cloud Run 상시 과금 리비전 감사 ($PROJECT / $REGION) ==="
  echo "판정 기준: 트래픽 0% 인데 태그가 붙어 있고 minScale>0  → 영구 과금 누수"
  echo
  local leak=0
  for svc in $("$GCLOUD" run services list --project "$PROJECT" --region "$REGION" \
                 --format='value(metadata.name)' 2>/dev/null); do
    # status.traffic 을 JSON 으로 받아 태그 달린 0% 항목만 뽑는다
    local tagged
    tagged=$("$GCLOUD" run services describe "$svc" --project "$PROJECT" --region "$REGION" \
      --format=json 2>/dev/null | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: sys.exit()
for t in d.get("status",{}).get("traffic",[]):
    if t.get("tag") and not t.get("percent"):
        print(t["revisionName"], t["tag"], sep="\t")
')
    [ -z "$tagged" ] && continue
    while IFS=$'\t' read -r rev tag; do
      [ -z "$rev" ] && continue
      local ms
      ms=$("$GCLOUD" run revisions describe "$rev" --project "$PROJECT" --region "$REGION" \
        --format='value(metadata.annotations["autoscaling.knative.dev/minScale"])' 2>/dev/null)
      if [ -n "$ms" ] && [ "$ms" != "0" ]; then
        local cpu mem
        cpu=$("$GCLOUD" run revisions describe "$rev" --project "$PROJECT" --region "$REGION" \
          --format='value(spec.containers[0].resources.limits.cpu)' 2>/dev/null)
        mem=$("$GCLOUD" run revisions describe "$rev" --project "$PROJECT" --region "$REGION" \
          --format='value(spec.containers[0].resources.limits.memory)' 2>/dev/null)
        echo "  ⚠️ 누수  $svc / $rev  (태그 $tag, minScale=$ms, $cpu vCPU / $mem)"
        leak=$((leak+1))
      fi
    done <<< "$tagged"
  done
  echo
  if [ "$leak" -eq 0 ]; then
    echo "  누수 0건."
  else
    echo "  누수 $leak 건 — 태그가 아직 쓰이는지 먼저 확인하고(요청 수 계측) 제거하십시오:"
    echo "    ./revision_tag_guard.sh cleanup <service>"
  fi
  return 0
}

deploy_verify() {
  local svc="${1:-}" image="${2:-}" tag="${3:-}"
  [ -n "$svc" ] && [ -n "$image" ] && [ -n "$tag" ] || die "사용법: deploy <service> <image> <tag>"
  echo "=== 검증용 리비전 배포 (min-instances=0 강제) ==="
  "$GCLOUD" run deploy "$svc" --project "$PROJECT" --region "$REGION" \
    --image "$image" --no-traffic --tag "$tag" --min-instances=0 --quiet || die "배포 실패"
  echo
  echo "검증 URL: https://${tag}---$("$GCLOUD" run services describe "$svc" --project "$PROJECT" \
      --region "$REGION" --format='value(status.url)' 2>/dev/null | sed 's#https://##')"
  echo
  echo "⚠️ 검증이 끝나면 반드시 태그를 떼십시오 (안 떼면 리비전이 남습니다):"
  echo "    $GCLOUD run services update-traffic $svc --project $PROJECT --region $REGION --remove-tags $tag"
  echo
  echo "⚠️ deploy 성공 메시지의 「100 percent of traffic」을 믿지 마십시오 —"
  echo "   트래픽이 고정된 서비스에서는 *현재 서빙 중인* 리비전을 가리킵니다(2026-09-02 실측)."
  echo "   /health 로 실제 응답을 확인하십시오."
}

cleanup() {
  local svc="${1:-}"
  [ -n "$svc" ] || die "사용법: cleanup <service>"
  local tags
  tags=$("$GCLOUD" run services describe "$svc" --project "$PROJECT" --region "$REGION" \
    --format=json 2>/dev/null | python3 -c '
import json,sys
d=json.load(sys.stdin)
print(",".join(t["tag"] for t in d.get("status",{}).get("traffic",[]) if t.get("tag") and not t.get("percent")))
')
  if [ -z "$tags" ]; then echo "$svc: 0% 트래픽 태그 없음."; return 0; fi
  echo "$svc 의 0% 트래픽 태그: $tags"
  echo
  echo "⚠️ 제거 전 확인 — 이 태그 URL을 쓰는 클라이언트가 있으면 즉시 깨집니다."
  echo "   최근 요청이 0인지 Monitoring 으로 먼저 확인하십시오."
  echo
  echo "제거 명령:"
  echo "    $GCLOUD run services update-traffic $svc --project $PROJECT --region $REGION --remove-tags $tags"
}

case "${1:-audit}" in
  audit)   audit ;;
  deploy)  shift; deploy_verify "$@" ;;
  cleanup) shift; cleanup "$@" ;;
  *)       die "알 수 없는 명령: $1  (audit | deploy | cleanup)" ;;
esac

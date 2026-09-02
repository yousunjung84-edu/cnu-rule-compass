#!/bin/bash
# ⛔ 이 스크립트는 무력화돼 있다 — 어떤 인자로 실행해도 아무것도 바꾸지 않는다.
#
# ── 서비스별 min-instances 정책 (2026-09-02 박사 확정) ──────────────────────
#   cnu-rule-compass : min-instances=1  (상시 가동 유지)
#       AIONI(전남대 AI 플랫폼) 정식 커넥터. 콜드스타트 **p50 61.4초** 실측(30일).
#       요청 간격 분석상 영업일당 7.6회 콜드스타트 조건 — 교무과 대면 서비스라 불가.
#
#   academyinfo-mcp  : min-instances=0  (2026-09-02 변경, 이전 8/19 결정을 대체)
#       콜드스타트 **p50 1.1초 / p99 3.5초** 실측 — 상시 가동이 필요 없다.
#       8/19 결정은 콜드스타트가 더 길다는 전제 위에 있었다. 재실측 결과
#       1.1초로 확인돼 상시 가동 근거가 없어졌고, 2026-09-02 재확정으로 min=0 이 됐다.
#
# ── 왜 이 스크립트가 무력화돼 있나 ──────────────────────────────────────────
# 2026-08-18 16:05, 다른 세션이 구 런시트의 "발표 종료 후 원복" 지침을 따라
# 두 서비스를 한꺼번에 내렸고 감사 로그로 추적해 복구했다. 일괄 내림은 금지다.
# min-instances 변경은 **서비스별로, 박사 확인 후, 직접** 한다.
#
#   cnu 를 내려야 한다면(서비스 종료 등):
#     gcloud run services update cnu-rule-compass --project academyinfo-mcp-2026 \
#       --region asia-northeast3 --min-instances 0
#   academyinfo 를 다시 올려야 한다면:
#     gcloud run services update academyinfo-mcp --project academyinfo-mcp-2026 \
#       --region asia-northeast3 --min-instances 1
#
# ── 관련 ───────────────────────────────────────────────────────────────────
# min-instances 는 **리비전 속성**이고 **태그가 붙은 리비전은 트래픽 0%여도 살아남는다.**
# 검증용 리비전에 --min-instances=1 을 딸려 보내면 태그를 뗄 때까지 영구 과금된다.
# 검증 배포는 scripts/revision_tag_guard.sh 를 쓸 것(min-instances=0 강제 + 누수 감사).
echo "[중단] min-instances 일괄 변경은 폐지된 절차입니다."
echo "       정책: cnu-rule-compass=1 / academyinfo-mcp=0 (2026-09-02 박사 확정)"
echo "       변경이 필요하면 스크립트 주석의 서비스별 명령을 박사 확인 후 직접 실행하십시오."
exit 0

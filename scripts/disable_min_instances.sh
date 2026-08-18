#!/bin/bash
# ⛔ 이 스크립트는 무력화됐다 (2026-08-19, 박사 확정).
#
# 두 서비스(cnu-rule-compass·academyinfo-mcp)는 **상시 가동(min-instances=1)이 정책**이다.
# - cnu-rule-compass: AIONI(전남대 AI 플랫폼) 정식 커넥터 — 콜드 35초는 서비스 불가 수준
# - academyinfo-mcp: 박사 결정 "몇천원 서버비는 감당" (8/18·8/19 재확인)
#
# 실제로 8/18 16:05, 다른 세션이 구 런시트의 "발표 종료 후 원복" 지침을 따라
# 두 서비스를 내렸고 감사 로그로 추적해 복구했다. 같은 사고를 막기 위해
# 이 스크립트는 어떤 인자로 실행해도 아무것도 바꾸지 않는다.
#
# 정말 내려야 한다면(서비스 종료 등) 박사 확인 후 직접:
#   gcloud run services update <svc> --project academyinfo-mcp-2026 \
#     --region asia-northeast3 --min-instances 0
echo "[중단] min-instances 원복은 폐지된 절차입니다 — 두 서비스는 상시 가동이 정책입니다(2026-08-19 박사 확정). 스크립트 주석 참고."
exit 0

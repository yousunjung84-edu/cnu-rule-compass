# CNU 규정 나침반 — Streamable HTTP MCP 진입점 컨테이너 (Cloud Run).
# pip install을 프로젝트에 걸지 않고 /app 상대 실행을 유지한다:
# src/*의 데이터 경로가 Path(__file__).parent.parent/data 로 풀리기 때문.
FROM python:3.12-slim

WORKDIR /app

# 상한을 반드시 둔다: mcp 2.0.0은 mcp.server.fastmcp를 없애 서버가 기동하지 않는다
# (2026-08-10 배포 실패로 확인). 검증 버전은 1.28.1.
RUN pip install --no-cache-dir "mcp>=1.20,<2"

COPY src ./src
# 대학 프로필(정체성·허용 호스트·URL 키 파라미터·계층 임계). 없으면 기동 시 즉시
# 실패한다 — 빈 값이 호스트 검증에 스며드는 것보다 낫다(src/profile.py 참조).
COPY profiles ./profiles
# coverage_report.json은 get_corpus_stats가 '분모'(게시 대비 수집률)를 보고할 때 읽는다.
# 없으면 그 필드만 빠지고 나머지는 그대로 동작한다 (T30).
COPY data/rules_corpus.json data/lineage_corpus.json data/integrity_selfcheck_samples.json data/coverage_report.json ./data/

ENV PORT=8080
EXPOSE 8080

CMD ["python", "-m", "src.http_server"]

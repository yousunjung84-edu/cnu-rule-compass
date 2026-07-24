# CNU 규정 나침반 — Streamable HTTP MCP 진입점 컨테이너 (Cloud Run).
# pip install을 프로젝트에 걸지 않고 /app 상대 실행을 유지한다:
# src/*의 데이터 경로가 Path(__file__).parent.parent/data 로 풀리기 때문.
FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir "mcp>=1.20"

COPY src ./src
COPY data/rules_corpus.json data/lineage_corpus.json data/integrity_selfcheck_samples.json ./data/

ENV PORT=8080
EXPOSE 8080

CMD ["python", "-m", "src.http_server"]

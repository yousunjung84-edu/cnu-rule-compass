# CNU 규정 나침반 — Streamable HTTP MCP 진입점 컨테이너 (Cloud Run).
#
# ★ 2026-09-07(코어 전환 P03+P04)부터 이 이미지는 **adapter**를 담는다.
# 엔진은 비공개 코어 패키지(rule-compass-core)에서 오고, 레포에 남은 src/는
# 진입점과 사용집계뿐이다.
#
# ★ 2026-09-21(인덱스 DB 전환)부터 **다단 빌드**다. 기동 때 인덱스를 짓던 것이
# 콜드스타트 p50 61.4초·상주 1.4GB의 원인이었다. builder 가 코퍼스에서 인덱스
# DB를 **파생**시키고 runtime 은 그 DB만 싣는다 — 코퍼스 JSON 은 이미지에 없다.
# 파생이지 복사가 아닌 이유: 만들어 둔 DB 를 COPY 하면 코퍼스와 어긋난 채로도
# 빌드가 통과한다. 파생하면 어긋날 수가 없다(core/index_db.py 머리말 참고).

# ---------- 공통 베이스: SDK + 코어 wheel ----------
FROM python:3.12-slim AS base
WORKDIR /app
# 상한을 반드시 둔다: mcp 2.0.0은 mcp.server.fastmcp를 없애 서버가 기동하지 않는다
# (2026-08-10 배포 실패로 확인). ★ 정확히 pin한다(2026-09-02, 드리프트감사 §E4).
RUN pip install --no-cache-dir "mcp==1.28.1"
# 비공개 코어 (Artifact Registry PYTHON 저장소). 인증은 keyring 백엔드가 ADC로
# 처리한다. 별도 토큰을 빌드 인자로 넘기지 않는다 — 넘기면 레이어에 남는다.
RUN pip install --no-cache-dir keyrings.google-artifactregistry-auth
COPY requirements-core.txt ./
RUN pip install --no-cache-dir --require-hashes \
      --index-url https://asia-northeast3-python.pkg.dev/academyinfo-mcp-2026/rule-compass-python/simple/ \
      -r requirements-core.txt

# ---------- builder: 코퍼스 → 인덱스 DB ----------
FROM base AS builder
# 코퍼스는 여기서만 쓰인다. legacy 인덱스를 한 번 지어(≈1.4GB, 수 초) 통계를 DB 로
# 굳힌다. 프로필은 wheel 에 동봉돼 있고 전남대는 정본 대장이 없다(jnu=legacy_policy).
COPY data/rules_corpus.json ./data/
ENV RULE_COMPASS_DATA_DIR=/app/data
RUN python -m core.index_db /app/data/rules_corpus.json /app/data/rules_index.db \
 && python -c "from core.index_db import verify; verify('/app/data/rules_index.db', '/app/data/rules_corpus.json', require_corpus=True)" \
 && rm /app/data/rules_corpus.json

# ---------- runtime ----------
FROM base AS runtime
# adapter만 담는다: 진입점(http_server) + 사용집계(usage).
COPY src ./src
# 프로필은 코어 패키지에 동봉돼 있다(core/profiles/jnu.json). 기본 프로필 id가
# 'jnu'라 환경변수도 필요 없다 — 예전처럼 profiles/를 COPY하면 진본이 둘이 된다.
#
# 인덱스 DB(builder 가 파생) + 계보·자기점검 표본·coverage_report. 코퍼스 JSON 은
# 싣지 않는다 — DB 의 payload 가 조문 전문을 들고 있고(articles 전건 동일 확인),
# 코퍼스가 없으면 index_db.verify 는 스키마·프로필·대장 해시만 대조한다.
COPY --from=builder /app/data/rules_index.db ./data/
COPY data/lineage_corpus.json data/integrity_selfcheck_samples.json data/coverage_report.json ./data/
# ★ 설치본에서는 이 주입이 필수다. 코어는 wheel 안에 있어 패키지 상대 경로가
# site-packages를 가리키므로, 이 값이 없으면 데이터를 못 찾는다.
ENV RULE_COMPASS_DATA_DIR=/app/data
# ★ 이 값이 있으면 코어가 기동 때 _build() 를 돌리지 않고 DB 를 연다.
ENV RULE_COMPASS_INDEX_DB=/app/data/rules_index.db
ENV PORT=8080
EXPOSE 8080
CMD ["python", "-m", "src.http_server"]

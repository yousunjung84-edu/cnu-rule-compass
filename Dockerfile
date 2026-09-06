# CNU 규정 나침반 — Streamable HTTP MCP 진입점 컨테이너 (Cloud Run).
#
# ★ 2026-09-07(코어 전환 P03+P04)부터 이 이미지는 **adapter**를 담는다.
# 엔진은 비공개 코어 패키지(rule-compass-core)에서 오고, 레포에 남은 src/는
# 진입점과 사용집계뿐이다.
FROM python:3.12-slim

WORKDIR /app

# 상한을 반드시 둔다: mcp 2.0.0은 mcp.server.fastmcp를 없애 서버가 기동하지 않는다
# (2026-08-10 배포 실패로 확인). 검증 버전은 1.28.1.
# ★ 정확히 pin한다(2026-09-02, 드리프트감사 §E4). 범위 설치는 매 빌드가 다른 SDK를
# 집어 「구조 전환」과 「SDK 변경」이 같은 배포에 섞인다 — 무엇이 깨졌는지 갈라낼 수
# 없다. 1.29+ 호환 자체는 전송 게이트 wheel 축이 별도 venv로 확인한다.
RUN pip install --no-cache-dir "mcp==1.28.1"

# --- 비공개 코어 (Artifact Registry PYTHON 저장소) ---
# 인증은 keyring 백엔드가 ADC(Cloud Build 서비스 계정)로 처리한다. 별도 토큰을
# 빌드 인자로 넘기지 않는다 — 넘기면 레이어에 남는다.
# 이 설치만 --index-url을 코어 저장소로 돌린다. 코어는 dependencies=[]라서
# PyPI를 볼 일이 없고, 위 mcp는 이미 설치가 끝났다.
RUN pip install --no-cache-dir keyrings.google-artifactregistry-auth
COPY requirements-core.txt ./
RUN pip install --no-cache-dir --require-hashes \
      --index-url https://asia-northeast3-python.pkg.dev/academyinfo-mcp-2026/rule-compass-python/simple/ \
      -r requirements-core.txt

# adapter만 담는다: 진입점(http_server) + 사용집계(usage).
COPY src ./src

# 프로필은 코어 패키지에 동봉돼 있다(core/profiles/jnu.json). 기본 프로필 id가
# 'jnu'라 환경변수도 필요 없다 — 예전처럼 profiles/를 COPY하면 진본이 둘이 된다.

# 코퍼스·계보·자기점검 표본·coverage_report. 코어는 이 넷을 전부
# RULE_COMPASS_DATA_DIR 기준으로 찾는다(core/paths.py).
# coverage_report.json은 get_corpus_stats가 '분모'(게시 대비 수집률)를 보고할 때
# 읽는다. 없으면 그 필드만 빠지고 나머지는 그대로 동작한다 (T30).
COPY data/rules_corpus.json data/lineage_corpus.json data/integrity_selfcheck_samples.json data/coverage_report.json ./data/

# ★ 설치본에서는 이 주입이 필수다. 코어는 wheel 안에 있어 패키지 상대 경로가
# site-packages를 가리키므로, 이 값이 없으면 코퍼스를 못 찾는다.
ENV RULE_COMPASS_DATA_DIR=/app/data

ENV PORT=8080
EXPOSE 8080

CMD ["python", "-m", "src.http_server"]

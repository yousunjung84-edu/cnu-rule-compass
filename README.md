# CNU 규정 나침반

전남대학교 공개 규정·지침을 조문 단위로 검색하고, 근거 원문과 공식 URL을 함께
제시하는 표준 라이브러리 기반 데모다. 선택 기능으로 청렴 자기점검, 운영 로그,
대시보드와 MCP 도구를 제공한다.

> **데이터 범위 안내** — 이 공개 저장소에는 민감 서식(선거·서약서·개인정보 필드
> 등)을 제외한 **데모 샘플 코퍼스**(`data/rules_corpus.sample.json`)만 포함한다.
> clone하면 이 샘플로 검색·조문 인용·미확인 응답·청렴 점검이 즉시 동작한다.
> 전량 코퍼스는 각 기관이 `collect_rules.py`로 자기 규정을 직접 수집해 만든다
> (`data/rules_corpus.json`이 있으면 그것을, 없으면 샘플을 자동 사용).

## 실행

Python 3.10 이상에서 외부 패키지 없이 핵심 기능과 테스트를 실행할 수 있다.

```bash
python3 app.py
python3 -m unittest discover tests/ -v
python3 -m unittest discover -v
```

MCP 서버만 `mcp` 패키지를 실행 시점에 지연 임포트한다.

```bash
python3 -m src.mcp_server
```

## 안전 경계

- 검색 로더는 빈 본문, 30,000자 초과 본문, 동일 레코드 중복, 비공식 URL을 제외한다.
- 검색 결과는 `record_id`로 판본을 식별하며 `get_article`은 한 레코드만 반환한다.
- LLM 재서술은 기본적으로 꺼져 있다. 켜더라도 문장별 근거와 신규 수치·조문명을
  검증하지 못하면 공식 원문 안내로 되돌아간다.
- 운영 로그는 Unicode 정규화 후 PII를 재귀 마스킹하며, source 필드는 코퍼스의
  검증된 `record_id`로 재구성한다.
- 로그 손상이 감지되면 `.corrupt-*.bak`을 남기고 예외를 발생시켜 자동 덮어쓰기를
  중단한다.
- 저장소는 POSIX `flock`과 Windows `msvcrt` lockfile을 사용한다. 모든 기록자는
  동일한 `runtime/.store.lock` 프로토콜을 사용해야 다중 프로세스 안전성이 유지된다.

데이터 출처와 재배포 조건은 [DATA_PROVENANCE.md](DATA_PROVENANCE.md)를 확인한다.
코드는 [MIT License](LICENSE)로 배포한다.

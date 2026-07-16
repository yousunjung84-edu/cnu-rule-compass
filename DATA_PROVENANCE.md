# 데이터 출처 및 재배포 조건

**공개 배포 범위**: 이 저장소에는 민감 서식(선거·서약서·개인정보 필드 포함 조문)을
제외한 데모 샘플(`data/rules_corpus.sample.json`)만 포함한다. 전량 코퍼스
(`data/rules_corpus.json`)는 로컬 전용이며 배포하지 않는다(`.gitignore`). 기관 규정
원문의 대량 재배포를 피하고, 각 이용자가 `collect_rules.py`로 자기 규정을 수집한다.

`data/rules_corpus.json`은 전남대학교 규정·지침 공개 페이지의 현행 문서를
`collect_rules.py`로 수집하고 조문 단위로 변환한 검색용 파생 데이터다.
각 레코드의 `source_url`과 `source_key`가 원문 위치를 나타내며, 로더는 HTTPS,
`jnu.ac.kr` 허용 호스트, URL `key`와 `source_key` 일치를 검증한다.

2026-07-14 정제에서는 원본 1,746건을
`data/rules_corpus.pre_cleanup.json`에 보존하고, 빈 본문 25건, 30,000자 초과
파싱 이상 8건, 내용이 같은 중복 26건을 제외해 1,687건을 만들었다. 상세 내역은
`data/rules_corpus_cleanup_report.json`에 있다. 본칙과 부칙은 `record_type`으로
구분하고, 콘텐츠 기반 `record_id`와 `revision`을 부여했다.

`data/integrity_selfcheck_samples.json`은 실제 감사 사건·개인정보를 포함하지 않는
합성 데모 데이터이며 파일 내부 `data_manifest`가 그 성격을 선언한다.

코드의 MIT 라이선스는 규정 원문과 파생 코퍼스의 권리까지 허가하지 않는다.
규정 원문의 저작권·공공데이터 이용조건·최신성은 전남대학교 공개 페이지에서
별도로 확인해야 한다. 코퍼스를 외부에 재배포하기 전에는 원문 제공기관의 이용조건과
관련 법령에 따라 재배포 가능 범위를 확인하고, 출처 URL과 변경·수집 시점을 유지한다.

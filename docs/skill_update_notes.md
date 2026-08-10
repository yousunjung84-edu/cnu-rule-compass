# 소비자 스킬 갱신 지시서 — 서버 v1.1.0 기준

> 대상: `cnu-rule-answer` 스킬 (전남대 규정 근거 조문 답변)
> 근거: 2026-08-10 핸드오프 명세 T1~T7 반영 완료
> 서버 버전: **1.1.0** (이전 1.0.0). 스킬 본문에 이 버전을 기준으로 명시할 것.

서버가 고쳐졌으므로 스킬에 적힌 **우회 지침 다수가 이제 거짓**이다. 방치하면
불필요한 우회를 계속하거나 새로 색인된 결과를 의심하게 된다.

## 1. 삭제·수정 대상 (핸드오프 §완료 후 표에 대응)

| 서버 작업 | 스킬에서 할 일 | 이유 (실측) |
|---|---|---|
| T1 | `SKILL.md` §5-E 전체 삭제, `corpus-map.md` §4-1 삭제 | 색인 누락이 아니라 라우팅 상한 문제였고 수정됨. 조문제목 완전일치 6케이스 회귀 테스트로 고정 |
| T2 | `SKILL.md` §5-0("핵심 명사 하나로 먼저 치라") 전체 삭제 | 다중어 질의로도 정답이 유지된다. 커버리지 분모를 핵심어만으로 바꿈 |
| T3 | `SKILL.md` §5-C(수동 참조 추출)를 `get_related_articles` 호출 한 줄로 축약 | 도구가 outbound/inbound/unresolved를 구조화해 반환 |
| T4 | `SKILL.md` §5-D 오탐 대응 축소, `corpus-map.md` §2-1 수정 | 본문에 흡수된 절·장 제목 583건 분리 완료(잔존 0), `장`/`절` 필드 신설 |
| T5 | `SKILL.md` §5-A 괄호 휴리스틱 → `is_current`/`is_repealed` 필드 판정으로 교체 | 규정명 문자열 추정이 불필요해짐 |
| T6 | `corpus-map.md` §2 표 삭제 → `list_rules` / `get_corpus_stats` 호출 | 정적 문서 갱신 부담 소멸 |
| T7 | `SKILL.md` §6-2 판정 로직 단순화 | `hints.query_terms_unmatched`로 "코퍼스에 개념 부재"를 직접 판정 |

## 2. 도구 계약 변경 (하위 호환)

기존 3개 도구는 시그니처·필드 유지. **추가만** 있었다.

### 파라미터 추가
```
search_rule(query, k=5, include_superseded=False, include_repealed=False)
```
⚠️ **기본 동작이 바뀐다**: 구판본·삭제 조문이 기본 결과에서 빠진다(그게 목적).
감사 대응처럼 당시 판본이 필요하면 `include_superseded=True`로 켠다.

### 응답 필드 추가
- `장`, `절` — 해당 조문이 속한 편제 구조 (없으면 `null`). "같은 절의 다른 조문" 탐색에 쓸 수 있다
- `is_current`, `superseded_by`, `is_repealed`, `repealed_date`
- `hints` — 결과가 0~1건일 때만 붙는다 (`query_terms_unmatched`, `suggest`, `note`)

### 신규 도구
```
get_related_articles(record_id, direction="outbound"|"inbound"|"both", resolve=True)
list_rules(division=None, include_superseded=False)
get_corpus_stats()
```

`record_id` 형식·값은 보존했다. 본문 수정 전에 기존 파생 ID를 코퍼스에 동결했으므로
이미 발급된 답변의 인용(`rule-2200000155895-6ef5c792ea6d7b66` 등)은 그대로 유효하다.

## 3. 남은 한계 — 스킬에 그대로 유지할 것

**동의어 확장은 하지 않았다.** 핸드오프 §T2 회귀 케이스 중
`"성적 이의신청 언제까지" → 교학규정 제46조`는 달성하지 못했다. 코퍼스가 해당 개념을
`정정`으로 표현하고 제46조 본문에 `이의신청`이 없어서, 검색 게이트가 아니라 어휘의 문제다.
사용자 표현과 규정 용어가 다를 수 있다는 안내는 스킬에 남겨 두는 편이 좋다.

## 4. 검증 방법

```python
search_rule("재입학", k=10)          # 학칙 제30조 포함
search_rule("재입학 허가 신청")        # 학칙 제30조 + 교학규정 제11조, 교학규정 제10조 없음
search_rule("생성형 인공지능 챗봇")     # count 0 + hints.query_terms_unmatched
get_related_articles("rule-2200000155895-6ef5c792ea6d7b66", direction="inbound")  # 4건
get_corpus_stats()                   # 조문_수 == 색인_문서_수, warning 없음
```

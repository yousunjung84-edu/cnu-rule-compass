"""조문 본문에 흡수된 장·절 제목을 분리하고 편제 구조(장/절)를 필드로 승격한다.

원문 파싱은 '제N조' 헤더로만 조문을 나누므로, 조문 사이에 놓인 '제4절 …' 같은
편제 제목이 **직전 조문 본문 끝에 흡수**된다(실측 572/6,110건). 이 꼬리는
검색 오탐의 직접 원인이다 — 교학규정 제10조(휴학)가 꼬리의 '제4절 복학ㆍ재입학ㆍ퇴학'
때문에 '재입학' 질의에 잡혔다.

제목은 버리지 않고 `장`·`절` 필드로 올린다. 문서 순서상 꼬리 제목은 **다음 조문**의
소속이므로, 조문 목록을 문서 순서대로 넘겨야 한다. `record_id`는 건드리지 않는다
(이미 발급된 답변이 인용하고 있다).
"""

from __future__ import annotations

import re

# '제 8 장'처럼 공백이 섞인 표기도 잡는다.
SECTION_RE = re.compile(r"^\s*제\s*(\d+)\s*(장|절)(?:\s|$|[^가-힣])")
# 편제 제목에 딸린 개정 주석('<개정 2013.05.24.>', '<장 이름 변경 …>').
# 제목과 함께 있을 때만 떼어낸다 — 단독으로는 조문 자신의 주석이다.
ANNOTATION_RE = re.compile(r"^\s*<[^>]*>\s*$")


def _apply(state: dict[str, str | None], line: str) -> None:
    kind = SECTION_RE.match(line).group(2)
    state[kind] = line.strip()
    if kind == "장":
        state["절"] = None  # 장이 바뀌면 절은 초기화된다


def split_structure_titles(articles: list[dict]) -> list[dict]:
    """한 규정의 조문 목록(문서 순서)에 장/절을 부여하고 흡수된 제목을 본문에서 뗀다."""
    state: dict[str, str | None] = {"장": None, "절": None}
    result: list[dict] = []
    for article in articles:
        lines = str(article.get("본문", "")).split("\n")

        # 0) 이미 부여된 장/절이 있으면 상태를 그것으로 되살린다. 한 번 적용하면
        #    본문에서 제목이 사라지므로, 이 복원이 없으면 재실행 시 전부 None이 된다(멱등성).
        if article.get("장") is not None or article.get("절") is not None:
            state["장"] = article.get("장")
            state["절"] = article.get("절")

        # 1) 선행 제목 — 이 조문 자신의 소속을 바꾼다
        start = 0
        while start < len(lines):
            line = lines[start]
            if not line.strip():
                start += 1
                continue
            if SECTION_RE.match(line):
                _apply(state, line)
                start += 1
                continue
            break

        # 2) 후행 제목 — 다음 조문의 소속이므로 지금은 떼어만 둔다.
        #    제목·주석·빈 줄로만 이루어진 꼬리 블록을 통째로 보고, 제목이 하나라도
        #    있을 때만 떼어낸다(주석만 있는 꼬리는 조문 자신의 것이므로 보존).
        cursor = len(lines)
        trailing: list[str] = []
        while cursor > start:
            line = lines[cursor - 1]
            if not line.strip() or ANNOTATION_RE.match(line):
                cursor -= 1
                continue
            if SECTION_RE.match(line):
                trailing.append(line)
                cursor -= 1
                continue
            break
        end = cursor if trailing else len(lines)

        updated = dict(article)
        updated["본문"] = "\n".join(lines[start:end]).strip()
        updated["장"] = state["장"]
        updated["절"] = state["절"]
        result.append(updated)

        for line in reversed(trailing):  # 문서 등장 순서대로 반영
            _apply(state, line)
    return result


def apply_to_corpus(corpus: list[dict]) -> tuple[list[dict], dict[str, int]]:
    """코퍼스 전체에 적용한다. 규정 단위로 묶되 원래 순서를 보존한다."""
    order: list[str] = []
    grouped: dict[str, list[dict]] = {}
    for article in corpus:
        name = str(article.get("규정명", ""))
        if name not in grouped:
            grouped[name] = []
            order.append(name)
        grouped[name].append(article)

    processed: dict[str, list[dict]] = {}
    stats = {"조문": 0, "본문_변경": 0, "장_부여": 0, "절_부여": 0}
    for name in order:
        rows = split_structure_titles(grouped[name])
        processed[name] = rows
        for before, after in zip(grouped[name], rows):
            stats["조문"] += 1
            if before.get("본문") != after["본문"]:
                stats["본문_변경"] += 1
            if after.get("장"):
                stats["장_부여"] += 1
            if after.get("절"):
                stats["절_부여"] += 1

    cursor = {name: iter(rows) for name, rows in processed.items()}
    return [next(cursor[str(a.get("규정명", ""))]) for a in corpus], stats

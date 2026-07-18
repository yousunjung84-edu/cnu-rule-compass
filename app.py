"""CNU 규정 나침반 1차 CLI 데모."""

from __future__ import annotations

import re

from src.answer import answer
from src.integrity import IntegrityChecker
from src.lineage import get_default_lineage
from src.search import RuleSearchIndex


EXIT_COMMANDS = {"종료", "끝", "exit", "quit", "q"}
_ASOF_RE = re.compile(r"@\s*(\d{4}-\d{2}-\d{2})\s*$")


def answer_as_of(request: str) -> str:
    """'시점: <규정/질의> @YYYY-MM-DD' — 그 날짜에 유효했던 판본의 조문을 답한다."""
    match = _ASOF_RE.search(request)
    if not match:
        return "[형식] 시점: <규정명 또는 질의> @YYYY-MM-DD (예: 시점: 연구비 인건비 @2015-06-01)"
    date = match.group(1)
    query = _ASOF_RE.sub("", request).strip()
    lineage = get_default_lineage()
    rule_name = lineage.resolve_rule(query)
    if rule_name is None:
        names = ", ".join(lineage.rule_names) or "(수집된 계열 없음)"
        return f"개정 계열이 수집된 규정이 아닙니다. 현재 계열: {names}"
    result = lineage.articles_as_of(rule_name, date, keyword=query)
    if result["status"] != "ok":
        return f"[{rule_name}] {result['reason']}"
    lines = [
        f"📅 {date} 시점 유효 판본: {result['rule']} ({result['version_label']})",
        f"   유효기간: {result['valid_from'] or '수집 범위 시작'} ~ {result['valid_until'] or '현행'}",
    ]
    for article in result["articles"][:3]:
        title = f" ({article['조문제목']})" if article.get("조문제목") else ""
        lines.append(f"\n[{result['rule']} {article['조문번호']}{title} — 당시 판본]")
        body = article.get("본문", "")
        lines.append(f"원문: {body[:300]}{'…' if len(body) > 300 else ''}")
    lines.append(f"출처(당시 판본): {result['source_url']}")
    lines.append(f"※ {result['notice']}")
    return "\n".join(lines)


def main() -> None:
    """대화형 규정 질의·청렴 자기점검을 실행한다."""
    index = RuleSearchIndex()
    checker = IntegrityChecker(index=index)
    print("CNU 규정 나침반 — 전남대 규정 검색·질의응답 데모")
    print(f"공식 코퍼스 {len(index.articles)}개 조문을 불러왔습니다.")
    print("사용법: 규정: <질의> | 점검: <업무 상황> | 시점: <규정> @YYYY-MM-DD | 종료")

    while True:
        try:
            raw = input("\n나침반> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n종료합니다.")
            break
        if not raw:
            continue
        if raw.lower() in EXIT_COMMANDS:
            print("종료합니다.")
            break
        if raw.startswith("규정:"):
            question = raw.split(":", 1)[1].strip()
            print(answer(question, index=index)["text"])
        elif raw.startswith("점검:"):
            situation = raw.split(":", 1)[1].strip()
            print(checker.check(situation)["text"])
        elif raw.startswith("시점:"):
            print(answer_as_of(raw.split(":", 1)[1].strip()))
        else:
            print("[건너뜀] '규정: <질의>' 또는 '점검: <상황>' 형식으로 입력해 주세요.")


if __name__ == "__main__":
    main()


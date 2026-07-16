"""CNU 규정 나침반 1차 CLI 데모."""

from __future__ import annotations

from src.answer import answer
from src.integrity import IntegrityChecker
from src.search import RuleSearchIndex


EXIT_COMMANDS = {"종료", "끝", "exit", "quit", "q"}


def main() -> None:
    """대화형 규정 질의·청렴 자기점검을 실행한다."""
    index = RuleSearchIndex()
    checker = IntegrityChecker(index=index)
    print("CNU 규정 나침반 — 전남대 규정 검색·질의응답 데모")
    print(f"공식 코퍼스 {len(index.articles)}개 조문을 불러왔습니다.")
    print("사용법: 규정: <질의> | 점검: <업무 상황> | 종료")

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
        else:
            print("[건너뜀] '규정: <질의>' 또는 '점검: <상황>' 형식으로 입력해 주세요.")


if __name__ == "__main__":
    main()


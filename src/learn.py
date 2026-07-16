"""미확인 규정 질의를 지식 후보로 연결하는 자기성장 루프."""

from __future__ import annotations

from src.store import JsonStore, get_default_store


def capture(question: str, answer_result: dict, data_store: JsonStore | None = None) -> dict:
    """모든 질의를 기록하고 미답 질의는 검토 후보로 축적한다."""
    target = data_store or get_default_store()
    query_log = target.add_query(question, answer_result)
    candidate = None
    if not answer_result.get("answered"):
        candidate = target.register_candidate(question)
    return {
        "query_log": query_log,
        "candidate": candidate,
        "status": "candidate" if candidate else "recorded",
    }


def list_candidates(data_store: JsonStore | None = None) -> list[dict]:
    """대기 후보를 빈도 높은 순서로 반환한다."""
    target = data_store or get_default_store()
    candidates = [
        item for item in target.read("candidates") if item.get("status") == "pending"
    ]
    return sorted(
        candidates,
        key=lambda item: (-int(item.get("asked_count", 0)), item.get("created_at", "")),
    )

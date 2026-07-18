"""시점 질의(as-of) — "그 날짜에 유효했던 조문"을 찾는다.

감사 대응의 실제 질문은 "지금 규정"이 아니라 "그 업무를 했던 당시 규정"이다.
개정 계열 코퍼스(collect_lineage.py 산출)에서 주어진 날짜에 유효했던 버전을 찾아
그 버전의 조문을 반환한다. 각 버전의 유효기간은 [valid_from, valid_until)이며,
valid_until이 None이면 현행본이다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
# 전량 계열(lineage_corpus.json)은 로컬 전용(공개 배포 제외) — clone 환경은
# 축약 데모 샘플로 자동 폴백한다(rules_corpus와 동일한 공개 원칙).
_FULL_LINEAGE = _ROOT / "data" / "lineage_corpus.json"
_SAMPLE_LINEAGE = _ROOT / "data" / "lineage_corpus.sample.json"
DEFAULT_LINEAGE_PATH = _FULL_LINEAGE if _FULL_LINEAGE.exists() else _SAMPLE_LINEAGE
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class LineageStore:
    """개정 계열 코퍼스 로더 + 시점 검색."""

    def __init__(self, lineage_path: str | Path = DEFAULT_LINEAGE_PATH) -> None:
        self.lineage_path = Path(lineage_path)
        try:
            with self.lineage_path.open(encoding="utf-8") as file:
                self.lineages: dict[str, list[dict]] = json.load(file)
        except (OSError, json.JSONDecodeError):
            self.lineages = {}

    @property
    def rule_names(self) -> list[str]:
        return sorted(self.lineages)

    def resolve_rule(self, query: str) -> str | None:
        """질의 문자열에서 계열 규정명을 찾는다(포함 → 토큰 매칭 순, 최다 일치 우선)."""
        hits = [name for name in self.lineages if name in query or query in name]
        if not hits:
            tokens = [t for t in re.split(r"\s+", query) if len(t) >= 2]
            scored = [
                (sum(1 for t in tokens if t in name), name)
                for name in self.lineages
            ]
            best = max(scored, default=(0, None))
            if best[0] >= 1:
                hits = [name for count, name in scored if count == best[0]]
        return max(hits, key=len) if hits else None

    def version_as_of(self, rule_name: str, date: str) -> dict | None:
        """rule_name 계열에서 date(YYYY-MM-DD) 시점에 유효했던 버전을 반환한다."""
        if not _DATE_RE.match(date):
            raise ValueError("날짜는 YYYY-MM-DD 형식이어야 합니다.")
        for version in self.lineages.get(rule_name, []):
            start = version.get("valid_from")
            end = version.get("valid_until")
            if (start is None or start <= date) and (end is None or date < end):
                return version
        return None

    def articles_as_of(
        self, rule_name: str, date: str, keyword: str | None = None
    ) -> dict:
        """시점 버전의 조문(선택: 키워드 필터)과 유효기간 메타를 반환한다."""
        version = self.version_as_of(rule_name, date)
        if version is None:
            known = self.lineages.get(rule_name)
            reason = (
                "해당 시점의 개정 이력이 수집 범위 밖입니다."
                if known
                else "이 규정의 개정 계열이 아직 수집되지 않았습니다."
            )
            return {"status": "not_found", "reason": reason, "rule": rule_name, "date": date}
        articles = version["articles"]
        if keyword:
            tokens = [t for t in keyword.split() if len(t) >= 2]
            if tokens:
                filtered = [
                    a for a in articles
                    if any(t in (a.get("조문제목", "") + a.get("본문", "")) for t in tokens)
                ]
                articles = filtered or articles[:3]
        notice = "해당 시점 유효 판본 기준이며, 현행 규정 확인이 함께 필요합니다."
        if version.get("valid_from") is None and version.get("valid_until"):
            # 계열의 첫 판본은 제정·이전 개정일이 수집 범위 밖이라 시작 시점이 미상이다.
            # 그 이전 날짜 질의에 이 판본을 확정 근거로 쓰지 않도록 불확실성을 명시한다.
            notice = (
                "수집된 가장 오래된 판본입니다. 제정·이전 개정일이 수집 범위 밖이라 "
                "이 날짜에 실제 유효했는지는 원문 이력 확인이 필요합니다."
            )
        return {
            "status": "ok",
            "rule": rule_name,
            "date": date,
            "version_label": version["label"],
            "valid_from": version.get("valid_from"),
            "valid_until": version.get("valid_until"),
            "source_url": version["source_url"],
            "articles": articles[:5],
            "notice": notice,
        }


_DEFAULT: LineageStore | None = None


def get_default_lineage() -> LineageStore:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = LineageStore()
    return _DEFAULT

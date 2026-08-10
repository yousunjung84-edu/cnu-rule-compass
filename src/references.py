"""조문 상호참조 추출·해소 (T3).

조문 본문은 다른 조문을 자주 인용한다("「전남대학교 교학규정」 제32조에 의하여",
"학칙 제30조제1항 각 호"). 이 참조를 코퍼스 안에서 해소하면 검색으로 닿기 어려운
중심 조문에 도달할 수 있고, 역방향(누가 이 조문을 인용하는가)은 그 조문이
얼마나 중심적인지를 알려준다.

세 종류를 구분한다.
- ``cross_rule``   : 코퍼스 안의 다른 규정을 인용
- ``same_rule``    : 같은 규정 안의 다른 조문을 인용
- ``external_law`` : 국가 법령 등 코퍼스 범위 밖 (해소 불가를 명시)

해소하지 못한 참조는 버리지 않고 사유와 함께 돌려준다. 조용히 사라지면 소비자가
누락을 인지하지 못한다.
"""

from __future__ import annotations

import re

# 「규정명」 또는 맨앞 수식 없는 규정명 + 제N조(의M)(제K항)(각 호)
_REFERENCE_RE = re.compile(
    r"(?:「\s*(?P<quoted>[^」]{2,40}?)\s*」\s*)?"
    r"(?P<plain>[가-힣A-Za-z·]{2,30})?\s*"
    r"제\s*(?P<article>\d+)\s*조(?:\s*의\s*(?P<sub>\d+))?"
    r"(?:\s*제\s*(?P<clause>\d+)\s*항)?"
    r"(?P<each>\s*각\s*호)?"
)
_EXTERNAL_SUFFIX = ("법", "법률", "시행령", "시행규칙", "령", "규칙")
# 규정명 자리에 올 수 있으나 규정을 특정하지 않는 말
_VAGUE_PREFIX = {"이", "본", "동", "같은", "그", "위", "해당", "규정", "지침", "이상", "각"}


def _norm_article(article: str, sub: str | None) -> str:
    return f"제{int(article)}조의{int(sub)}" if sub else f"제{int(article)}조"


class ReferenceIndex:
    """코퍼스 전체의 참조 그래프. 규정명 별칭 해소와 역인덱스를 담당한다."""

    def __init__(self, articles: list[dict]) -> None:
        self._articles = articles
        self._by_id = {row["record_id"]: row for row in articles}
        self._by_key: dict[tuple[str, str], dict] = {}
        for row in articles:
            self._by_key.setdefault((row["규정명"], str(row["조문번호"])), row)

        # 별칭: 정식 규정명 + '전남대학교' 접두 제거형 (본문은 '학칙 제30조'처럼 줄여 쓴다)
        self._alias: dict[str, str] = {}
        for name in {row["규정명"] for row in articles}:
            self._alias.setdefault(name, name)
            short = name.replace("전남대학교", "").strip()
            if short:
                self._alias.setdefault(short, name)
        self._inbound: dict[str, list[dict]] | None = None

    def _resolve_rule(self, raw: str | None, current_rule: str) -> tuple[str | None, str]:
        """참조에 적힌 규정명을 코퍼스 규정명으로 해소하고 종류를 판정한다."""
        if not raw or raw in _VAGUE_PREFIX:
            return current_rule, "same_rule"
        name = raw.strip()
        if name in self._alias:
            resolved = self._alias[name]
            kind = "same_rule" if resolved == current_rule else "cross_rule"
            return resolved, kind
        if name.endswith(_EXTERNAL_SUFFIX):
            return None, "external_law"
        return None, "unknown"

    def outbound(self, record: dict, resolve: bool = True) -> tuple[list[dict], list[dict]]:
        """이 조문이 인용하는 참조 목록과, 해소하지 못한 참조 목록을 반환한다."""
        body = str(record.get("본문", ""))
        current_rule = str(record.get("규정명", ""))
        found: list[dict] = []
        unresolved: list[dict] = []
        seen: set[tuple] = set()

        for match in _REFERENCE_RE.finditer(body):
            raw_name = match.group("quoted") or match.group("plain")
            target_article = _norm_article(match.group("article"), match.group("sub"))
            clause = f"제{int(match.group('clause'))}항" if match.group("clause") else None
            if match.group("each"):
                clause = f"{clause} 각 호" if clause else "각 호"
            rule, kind = self._resolve_rule(raw_name, current_rule)
            raw_text = " ".join(match.group(0).split())

            if kind in {"external_law", "unknown"}:
                key = ("unresolved", raw_text)
                if key in seen:
                    continue
                seen.add(key)
                unresolved.append({
                    "raw": raw_text,
                    "kind": kind,
                    "reason": "코퍼스 범위 밖" if kind == "external_law" else "규정명을 특정할 수 없음",
                })
                continue

            # 자기 자신 참조는 정보가 없다
            if rule == current_rule and target_article == str(record.get("조문번호")):
                continue
            key = (rule, target_article, clause)
            if key in seen:
                continue
            seen.add(key)

            target = self._by_key.get((rule, target_article))
            entry = {
                "raw": raw_text,
                "target_rule": rule,
                "target_article": target_article,
                "target_clause": clause,
                "kind": kind,
                "resolved": target is not None,
                "record_id": target["record_id"] if target else None,
            }
            if target is None:
                unresolved.append({
                    "raw": raw_text,
                    "kind": kind,
                    "reason": f"{rule} {target_article} 레코드가 코퍼스에 없음",
                })
                continue
            if resolve:
                entry["article"] = target
            found.append(entry)
        return found, unresolved

    def _build_inbound(self) -> dict[str, list[dict]]:
        inbound: dict[str, list[dict]] = {}
        for row in self._articles:
            targets, _ = self.outbound(row, resolve=False)
            for entry in targets:
                if not entry["resolved"]:
                    continue
                inbound.setdefault(entry["record_id"], []).append({
                    "raw": entry["raw"],
                    "source_rule": row["규정명"],
                    "source_article": str(row["조문번호"]),
                    "record_id": row["record_id"],
                    "kind": entry["kind"],
                })
        return inbound

    def inbound(self, record_id: str, resolve: bool = True) -> list[dict]:
        """이 조문을 인용하는 조문 목록(역인덱스). 최초 호출 시 그래프를 만든다."""
        if self._inbound is None:
            self._inbound = self._build_inbound()
        rows = self._inbound.get(record_id, [])
        if not resolve:
            return rows
        enriched = []
        for row in rows:
            item = dict(row)
            source = self._by_id.get(row["record_id"])
            if source is not None:
                item["article"] = source
            enriched.append(item)
        return enriched

    def get(self, record_id: str) -> dict | None:
        return self._by_id.get(record_id)

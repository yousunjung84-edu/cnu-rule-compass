"""청렴 취약업무 자기점검과 규정 조문 연결."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from src import pii
from src.search import RuleSearchIndex, get_default_index, tokenize


_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SAMPLES_PATH = _ROOT / "data" / "integrity_selfcheck_samples.json"

# 데이터의 의미를 바꾸지 않고 일상 표현을 9개 지적유형의 용어에 연결한다.
_CATEGORY_ALIASES = {
    "INT-01": ("근태", "출퇴근", "병가", "시간외"),
    "INT-02": ("출장비", "교통비", "숙박비", "여비"),
    "INT-03": ("분할", "나눠서 구매", "쪼개서 구매", "소액 구매", "견적", "발주"),
    "INT-04": ("연구과제", "인건비", "참여율", "연구비"),
    "INT-05": ("교부금", "국비", "보조사업", "목적 외 사용"),
    "INT-06": ("기자재", "비품", "자산대장", "불용", "실물"),
    "INT-07": ("개인 정보", "접근 권한", "보안", "외부 전송", "파기"),
    "INT-08": ("외부 특강", "외부 강의", "겸업", "사적 이해", "금품", "향응"),
    "INT-09": ("법인 카드", "클린카드", "영수증", "회계", "예산 집행"),
}

_GENERIC = {"절차", "업무", "규정", "사용", "처리", "승인", "관련", "유형", "실제"}


def _plain_words(text: str) -> set[str]:
    return {
        word for word in re.findall(r"[0-9A-Za-z가-힣]+", text.lower())
        if len(word) >= 2 and word not in _GENERIC
    }


class IntegrityChecker:
    """9개 지적유형을 상황과 매칭하고 코퍼스 조문을 연결한다."""

    def __init__(
        self,
        index: RuleSearchIndex | None = None,
        samples_path: str | Path = DEFAULT_SAMPLES_PATH,
    ) -> None:
        self.index = index or get_default_index()
        self.samples_path = Path(samples_path)
        self.categories, self.demo_scenarios = self._load_samples()
        self._category_documents = self._build_category_documents()

    def _load_samples(self) -> tuple[list[dict], list[dict]]:
        try:
            with self.samples_path.open(encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"자기점검 데이터를 읽을 수 없습니다: {self.samples_path}") from exc
        categories = data.get("categories") if isinstance(data, dict) else None
        scenarios = data.get("demo_scenarios", []) if isinstance(data, dict) else []
        manifest = data.get("data_manifest") if isinstance(data, dict) else None
        if not isinstance(manifest, dict) or manifest.get("classification") != "synthetic":
            raise ValueError("자기점검 데이터에는 합성데이터 manifest 선언이 필요합니다.")
        if not isinstance(categories, list) or len(categories) != 9:
            raise ValueError("자기점검 데이터에는 9개 categories가 필요합니다.")
        if not isinstance(scenarios, list):
            raise ValueError("demo_scenarios는 배열이어야 합니다.")
        expected_codes = {f"INT-{number:02d}" for number in range(1, 10)}
        actual_codes: set[str] = set()
        for number, category in enumerate(categories, start=1):
            if not isinstance(category, dict):
                raise ValueError(f"{number}번째 category는 객체여야 합니다.")
            code = category.get("code")
            anchor = category.get("rule_anchor")
            required_strings = ("code", "name", "typical_issue", "risk")
            if any(not isinstance(category.get(field), str) or not category[field].strip() for field in required_strings):
                raise ValueError(f"{number}번째 category 문자열 필드가 올바르지 않습니다.")
            if not isinstance(category.get("selfcheck"), list) or not all(
                isinstance(value, str) and value.strip() for value in category["selfcheck"]
            ):
                raise ValueError(f"{code} selfcheck는 비어 있지 않은 문자열 배열이어야 합니다.")
            if not isinstance(anchor, dict) or any(
                not isinstance(anchor.get(field), list)
                or not all(isinstance(value, str) and value.strip() for value in anchor[field])
                for field in ("편제", "keywords")
            ):
                raise ValueError(f"{code} rule_anchor 스키마가 올바르지 않습니다.")
            actual_codes.add(code)
        if actual_codes != expected_codes:
            raise ValueError("자기점검 category 코드는 INT-01부터 INT-09까지 유일해야 합니다.")
        for number, scenario in enumerate(scenarios, start=1):
            if not isinstance(scenario, dict) or scenario.get("matched_category") not in expected_codes:
                raise ValueError(f"{number}번째 demo_scenario 스키마가 올바르지 않습니다.")
            if not isinstance(scenario.get("user_input_example"), str):
                raise ValueError(f"{number}번째 demo_scenario 입력 예시가 문자열이 아닙니다.")
        detected = pii.scan(json.dumps(data, ensure_ascii=False))
        if detected:
            raise ValueError(f"자기점검 데이터에 PII 추정 패턴이 있습니다: {', '.join(detected)}")
        return categories, scenarios

    def _build_category_documents(self) -> dict[str, str]:
        scenarios_by_code: dict[str, list[str]] = {}
        for scenario in self.demo_scenarios:
            code = scenario.get("matched_category")
            text = scenario.get("user_input_example", "")
            scenarios_by_code.setdefault(code, []).append(text)

        documents = {}
        for category in self.categories:
            anchor = category.get("rule_anchor", {})
            pieces = [
                category.get("name", ""),
                category.get("typical_issue", ""),
                " ".join(category.get("selfcheck", [])),
                " ".join(anchor.get("편제", [])),
                " ".join(anchor.get("keywords", [])),
                " ".join(_CATEGORY_ALIASES.get(category.get("code"), ())),
                " ".join(scenarios_by_code.get(category.get("code"), [])),
            ]
            documents[category["code"]] = " ".join(pieces)
        return documents

    def _category_score(self, situation: str, category: dict) -> tuple[float, list[str], int]:
        document = self._category_documents[category["code"]]
        situation_words = _plain_words(situation)
        document_words = _plain_words(document)
        exact = situation_words & document_words

        query_tokens = Counter(tokenize(situation))
        document_tokens = Counter(tokenize(document))
        grams = sum(
            1 for token in query_tokens
            if token.startswith("#") and token in document_tokens
        )
        phrase_bonus = 0.0
        lowered = situation.lower()
        anchor = category.get("rule_anchor", {})
        phrases = (
            [category.get("name", "")]
            + anchor.get("keywords", [])
            + list(_CATEGORY_ALIASES.get(category["code"], ()))
        )
        for phrase in phrases:
            if phrase and phrase.lower() in lowered:
                phrase_bonus += 3.0
        score = len(exact) * 2.0 + min(grams, 8) * 0.25 + phrase_bonus
        return score, sorted(exact), int(phrase_bonus / 3.0)

    def check(self, situation: str, top_k: int = 3) -> dict:
        """업무 상황의 최우선 지적유형·자기점검 문항·연결 조문을 반환한다."""
        if not isinstance(situation, str) or not situation.strip():
            return {
                "matched": False,
                "text": "해당 지적유형 미확인",
                "category": None,
                "selfcheck": [],
                "rule_anchor": None,
                "articles": [],
            }
        ranked = []
        for order, category in enumerate(self.categories):
            score, matched_terms, phrase_hits = self._category_score(situation, category)
            ranked.append((score, -order, category, matched_terms, phrase_hits))
        ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
        score, _, category, matched_terms, phrase_hits = ranked[0]
        runner_up_score = ranked[1][0]
        independent_concepts = len(matched_terms)
        high_confidence = score >= 10.0 and phrase_hits >= 2 and score - runner_up_score >= 3.0
        if independent_concepts < 2 and not high_confidence:
            return {
                "matched": False,
                "text": "해당 지적유형 미확인",
                "category": None,
                "selfcheck": [],
                "rule_anchor": None,
                "articles": [],
            }

        anchor = category["rule_anchor"]
        # 앵커 하나씩 검색해 코퍼스에 실제 존재하는 근거만 합친다. 여러 앵커를 한 질의로
        # 묶으면 코퍼스에 없는 키워드가 관련성 비율을 낮춰 실제 조문까지 누락할 수 있다.
        articles_by_key: dict[tuple[str, str], dict] = {}
        for anchor_term in anchor.get("keywords", []) + anchor.get("편제", []):
            for article in self.index.search(anchor_term, k=top_k):
                key = (article["source_key"], article["조문번호"])
                previous = articles_by_key.get(key)
                if previous is None or article["score"] > previous["score"]:
                    articles_by_key[key] = article
        articles = sorted(
            articles_by_key.values(), key=lambda article: article["score"], reverse=True
        )[:top_k]
        category_summary = {
            key: category.get(key)
            for key in ("code", "name", "typical_issue", "risk")
        }
        text = self._format(category_summary, category["selfcheck"], articles)
        return {
            "matched": True,
            "text": text,
            "category": category_summary,
            "selfcheck": list(category["selfcheck"]),
            "rule_anchor": dict(anchor),
            "articles": articles,
            "matched_terms": matched_terms,
            "score": round(score, 2),
            "confidence": "high" if high_confidence else "medium",
        }

    @staticmethod
    def _format(category: dict, questions: list[str], articles: list[dict]) -> str:
        lines = [
            f"🧭 지적유형: {category['code']} {category['name']} (위험도 {category['risk']})",
            f"유형 설명: {category['typical_issue']}",
            "",
            "자기점검:",
        ]
        lines.extend(f"{number}. {question}" for number, question in enumerate(questions, start=1))
        lines.append("")
        if articles:
            lines.append("연결된 규정 조문:")
            for article in articles:
                title = f" ({article['조문제목']})" if article.get("조문제목") else ""
                lines.extend(
                    [
                        f"- [{article['규정명']} {article['조문번호']}{title}]",
                        f"  원문: {article['본문']}",
                        f"  출처: {article['source_url']}",
                    ]
                )
        else:
            lines.append("연결 규정: 해당 규정 미확인")
        lines.append("※ 자기점검은 감사·법률 판단을 대신하지 않습니다.")
        return "\n".join(lines)


_DEFAULT_CHECKER: IntegrityChecker | None = None


def check(situation: str, top_k: int = 3) -> dict:
    """기본 자기점검기 편의 함수."""
    global _DEFAULT_CHECKER
    if _DEFAULT_CHECKER is None:
        _DEFAULT_CHECKER = IntegrityChecker()
    return _DEFAULT_CHECKER.check(situation, top_k=top_k)

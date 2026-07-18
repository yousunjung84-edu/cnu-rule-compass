"""전남대학교 규정 조문 검색기.

외부 검색엔진 없이 조문 코퍼스를 메모리에 올리고 BM25 근사 점수로 검색한다.
규정명·편제·조문 제목은 본문보다 높은 가중치를 주어 짧은 규정 질의도 찾는다.
"""

from __future__ import annotations

import json
import hashlib
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import parse_qs, urlparse


_ROOT = Path(__file__).resolve().parent.parent
# 전량 코퍼스(rules_corpus.json)는 로컬 전용(공개 배포 시 .gitignore). 공개 레포를
# clone하면 전량이 없으므로, 민감 서식을 제외한 데모 샘플로 자동 폴백해 즉시 동작한다.
_FULL_CORPUS = _ROOT / "data" / "rules_corpus.json"
_SAMPLE_CORPUS = _ROOT / "data" / "rules_corpus.sample.json"
DEFAULT_CORPUS_PATH = _FULL_CORPUS if _FULL_CORPUS.exists() else _SAMPLE_CORPUS
MAX_ARTICLE_LENGTH = 30_000
ALLOWED_SOURCE_HOST = "jnu.ac.kr"

_STOPWORDS = {
    "규정", "규칙", "지침", "관련", "문의", "알려줘", "알려주세요", "어떻게",
    "무엇", "뭐야", "되나요", "있나요", "대한", "관한", "경우", "사항", "전남대학교",
    "어떤", "다른", "필요한가요", "하려면", "절차가", "인가요",
}
_PARTICLES = (
    "으로부터", "에서부터", "에게서", "까지는", "에서는", "으로는", "에게는",
    "으로", "에서", "에게", "한테", "부터", "까지", "처럼", "보다", "이나", "거나",
    "은", "는", "이", "가", "을", "를", "의", "에", "와", "과", "도", "만", "로",
)

# 행정 문서에 광범위하게 등장하는 일반어. 이들만 겹쳐서는 관련성을 인정하지 않는다.
# (H-1: '승인·신청' 등 일반어 2개만 걸려 무관 질의가 coverage 0.4를 넘던 우회 차단.
#  검색 점수 기여는 그대로 두되, 관련성 게이트의 '핵심어 매칭' 집계에서만 제외한다.)
_GENERIC_TERMS = {
    "승인", "신청", "처리", "업무", "관리", "사용", "계약", "구매", "기준", "운영",
    "규정", "지침", "방법", "대상", "적용", "보고", "작성", "제출", "등록", "실시",
    "절차", "허가", "운용", "지원", "수행", "요청", "확인", "검토", "결정", "시행",
}


def _word_forms(word: str) -> set[str]:
    """조사 차이를 완화한 단어형과 한국어 부분 일치용 2·3-gram을 만든다."""
    forms = {word}
    for suffix in _PARTICLES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 2:
            forms.add(word[:-len(suffix)])
            break
    if re.fullmatch(r"[가-힣]+", word):
        for size in (2, 3):
            if len(word) >= size:
                forms.update(f"#{word[i:i + size]}" for i in range(len(word) - size + 1))
    return forms


def tokenize(text: str) -> list[str]:
    """한국어·영문·숫자를 검색 토큰으로 정규화한다."""
    tokens: list[str] = []
    for word in re.findall(r"[0-9A-Za-z가-힣]+", str(text).lower()):
        if len(word) < 2 or word in _STOPWORDS:
            continue
        tokens.extend(_word_forms(word))
    return tokens


def _query_words(text: str) -> list[str]:
    """검색 관련성 비율을 계산할 때 사용할 원 단어 목록."""
    return list(dict.fromkeys(
        word
        for word in re.findall(r"[0-9A-Za-z가-힣]+", str(text).lower())
        if len(word) >= 2 and word not in _STOPWORDS
    ))


def validate_source_url(source_url: object, source_key: object) -> bool:
    """공식 HTTPS 호스트이고 URL의 key가 레코드 source_key와 같은지 확인한다."""
    try:
        parsed = urlparse(str(source_url).strip())
    except ValueError:
        return False
    hostname = (parsed.hostname or "").lower().rstrip(".")
    allowed_host = hostname == ALLOWED_SOURCE_HOST or hostname.endswith("." + ALLOWED_SOURCE_HOST)
    keys = parse_qs(parsed.query).get("key", [])
    return parsed.scheme == "https" and allowed_host and keys == [str(source_key).strip()]


def _record_type(article: dict) -> str:
    title = str(article.get("조문제목", "")).strip()
    body = str(article.get("본문", "")).lstrip()
    if title in {"시행일", "경과조치", "종전지침 폐지", "재검토기한"} or body.startswith("부칙"):
        return "부칙"
    return "본칙"


def prepare_article(article: dict) -> dict:
    """코퍼스 레코드에 안정적인 레코드 ID와 개정 식별자를 부여한다."""
    prepared = dict(article)
    prepared["record_type"] = str(article.get("record_type") or _record_type(article))
    identity = "\x1f".join(
        str(prepared.get(field, "")).strip()
        for field in ("source_key", "조문번호", "record_type", "조문제목", "본문")
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    prepared["revision"] = str(
        article.get("revision") or f"{prepared['record_type']}-{digest[:12]}"
    )
    prepared["record_id"] = str(article.get("record_id") or f"rule-{prepared['source_key']}-{digest}")
    return prepared


class RuleSearchIndex:
    """866개 조문 규모에 맞춘 메모리 BM25 근사 인덱스."""

    REQUIRED_FIELDS = (
        "규정명", "편제", "조문번호", "조문제목", "본문", "source_key", "source_url",
    )

    def __init__(self, corpus_path: str | Path = DEFAULT_CORPUS_PATH) -> None:
        self.corpus_path = Path(corpus_path)
        self.articles = self._load_corpus()
        self._term_frequencies: list[Counter[str]] = []
        self._document_frequencies: Counter[str] = Counter()
        self._field_tokens: list[dict[str, set[str]]] = []
        self._lengths: list[int] = []
        self._postings: dict[str, set[int]] = defaultdict(set)
        self._build()

    def _load_corpus(self) -> list[dict]:
        try:
            with self.corpus_path.open(encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"규정 코퍼스를 읽을 수 없습니다: {self.corpus_path}") from exc
        if not isinstance(data, list):
            raise ValueError("규정 코퍼스는 JSON 배열이어야 합니다.")
        accepted: list[dict] = []
        seen_record_ids: set[str] = set()
        self.rejected_articles: list[dict] = []
        for number, article in enumerate(data, start=1):
            if not isinstance(article, dict) or any(field not in article for field in self.REQUIRED_FIELDS):
                raise ValueError(f"{number}번째 조문의 필수 필드가 누락되었습니다.")
            reason = None
            body = str(article.get("본문", "")).strip()
            if not body:
                reason = "empty_body"
            elif len(body) > MAX_ARTICLE_LENGTH:
                reason = "oversized_body"
            elif not validate_source_url(article.get("source_url"), article.get("source_key")):
                reason = "invalid_source_url"
            prepared = prepare_article(article)
            if prepared["record_id"] in seen_record_ids:
                reason = reason or "duplicate_record"
            if reason:
                self.rejected_articles.append({"row": number, "reason": reason, "article": prepared})
                continue
            seen_record_ids.add(prepared["record_id"])
            accepted.append(prepared)
        if not accepted:
            raise ValueError("유효한 규정 조문이 없습니다.")
        return accepted

    def _build(self) -> None:
        for doc_id, article in enumerate(self.articles):
            fields = {
                "규정명": set(tokenize(article["규정명"])),
                "편제": set(tokenize(article["편제"])),
                "조문제목": set(tokenize(article["조문제목"])),
                "본문": set(tokenize(article["본문"])),
            }
            full_text = " ".join(
                str(article.get(field, ""))
                for field in ("규정명", "편제", "조문번호", "조문제목", "본문")
            )
            frequency = Counter(tokenize(full_text))
            self._term_frequencies.append(frequency)
            self._field_tokens.append(fields)
            self._lengths.append(sum(frequency.values()))
            for term in frequency:
                self._document_frequencies[term] += 1
                self._postings[term].add(doc_id)
        self._average_length = (
            sum(self._lengths) / len(self._lengths) if self._lengths else 1.0
        )
        # 2단 검색용 규정 프로필: 규정명·편제·조문제목 토큰을 규정 단위로 집계한다.
        # 1단(규정 라우팅)에서 질의를 후보 규정으로 좁힌 뒤 2단(조문 검색)을 돌리면,
        # 본문 우연 매칭으로 엉뚱한 규정의 조문이 상위에 오는 오매칭이 줄어든다.
        self._rule_docs: dict[str, set[int]] = defaultdict(set)
        self._rule_profiles: dict[str, Counter[str]] = defaultdict(Counter)
        for doc_id, article in enumerate(self.articles):
            name = str(article.get("규정명", ""))
            self._rule_docs[name].add(doc_id)
            profile = self._rule_profiles[name]
            for term in tokenize(name):
                profile[term] += 3  # 규정명 가중
            for term in tokenize(article.get("편제", "")):
                profile[term] += 1
            for term in tokenize(article.get("조문제목", "")):
                profile[term] += 2

    def _idf(self, term: str) -> float:
        count = self._document_frequencies.get(term, 0)
        total = len(self.articles)
        return math.log(1.0 + (total - count + 0.5) / (count + 0.5))

    def route_rules(self, query: str, top_r: int = 5) -> list[str]:
        """1단 라우팅 — 질의와 프로필(규정명·편제·조문제목)이 겹치는 후보 규정을 좁힌다.

        보수적으로 동작한다: 완전형(비 gram) 토큰이 하나도 안 겹치는 규정은 후보에서
        제외하고, 아무 규정도 못 좁히면 빈 목록을 반환해 호출부가 전체 검색으로
        폴백하게 한다(정답 규정을 잘못 배제하는 것이 오매칭보다 나쁘기 때문).
        """
        query_terms = Counter(tokenize(query))
        if not query_terms:
            return []
        scored: list[tuple[float, str]] = []
        for name, profile in self._rule_profiles.items():
            core_hit = False
            score = 0.0
            for term, qf in query_terms.items():
                pf = profile.get(term, 0)
                if not pf:
                    continue
                score += self._idf(term) * min(pf, 6) * qf
                # 라우팅 확정은 비일반 핵심어 완전 매칭만 인정한다. '사용·절차' 같은
                # 일반 행정어만 겹친 규정으로 좁히면 오히려 오매칭이 생긴다(H-1과 동일 원리).
                if not term.startswith("#") and term not in _GENERIC_TERMS:
                    core_hit = True
            if core_hit and score > 0:
                scored.append((score, name))
        scored.sort(key=lambda row: (-row[0], row[1]))
        return [name for _, name in scored[:top_r]]

    def search(self, query: str, k: int = 5) -> list[dict]:
        """관련 조문 상위 k개를 인용 필드와 점수와 함께 반환한다 (2단 검색).

        1단에서 후보 규정을 좁히고(route_rules) 2단에서 그 규정들의 조문만 검색한다.
        1단이 아무 규정도 못 좁히거나 2단 결과가 비면 전체 조문 검색으로 폴백한다.
        검색어와 실질 토큰이 하나도 겹치지 않으면 빈 목록을 반환한다. 따라서 호출부는
        낮은 관련성 결과를 근거로 오인하지 않고 '해당 규정 미확인'으로 처리할 수 있다.
        """
        if not isinstance(query, str) or not query.strip() or k <= 0:
            return []
        routed = self.route_rules(query)
        if routed:
            routed_ids: set[int] = set()
            for name in routed:
                routed_ids.update(self._rule_docs.get(name, ()))
            results = self._search_articles(query, k, restrict_ids=routed_ids)
            if results:
                for row in results:
                    row["routing"] = "rule-first"
                return results
        results = self._search_articles(query, k, restrict_ids=None)
        for row in results:
            row["routing"] = "full-scan"
        return results

    def _search_articles(
        self, query: str, k: int, restrict_ids: set[int] | None
    ) -> list[dict]:
        """2단 조문 검색 — 기존 BM25 + 관련성 게이트. restrict_ids로 후보를 제한한다."""
        query_terms = Counter(tokenize(query))
        query_words = _query_words(query)
        if not query_terms or not query_words:
            return []
        candidate_ids: set[int] = set()
        for term in query_terms:
            candidate_ids.update(self._postings.get(term, ()))
        if restrict_ids is not None:
            candidate_ids &= restrict_ids
        if not candidate_ids:
            return []

        scores: list[tuple[float, int, list[str]]] = []
        k1, b = 1.5, 0.75
        for doc_id in candidate_ids:
            frequencies = self._term_frequencies[doc_id]
            score = 0.0
            matched_words: list[str] = []
            for term, query_frequency in query_terms.items():
                frequency = frequencies.get(term, 0)
                if not frequency:
                    continue
                denominator = frequency + k1 * (
                    1.0 - b + b * self._lengths[doc_id] / self._average_length
                )
                score += self._idf(term) * (frequency * (k1 + 1.0) / denominator) * query_frequency
                if not term.startswith("#"):
                    matched_words.append(term)

                fields = self._field_tokens[doc_id]
                if term in fields["규정명"]:
                    score += 2.8 * self._idf(term)
                if term in fields["조문제목"]:
                    score += 2.0 * self._idf(term)
                if term in fields["편제"]:
                    score += 1.2 * self._idf(term)

            # 질의의 핵심 개념 중 40% 이상이 조문과 겹칠 때만 근거로 인정한다.
            # '허가·기준' 같은 일반어만 우연히 맞은 미확인 질의의 오답 서빙을 막는다.
            # coverage 판정은 완전형(원형·조사제거형) 매칭만 인정한다. 2·3-gram(#xx)
            # 부분매칭은 방대한 본문에 우연히 걸려 coverage를 부풀리므로 점수 기여만
            # 하고 관련성 게이트에는 반영하지 않는다.
            # matched_concepts: 완전형이 겹친 질의어 수(비율용)
            # matched_core: 그중 일반 행정어가 아닌 '핵심어'가 겹친 수
            # 무관 질의가 일반어만으로 비율을 넘기던 우회(H-1)를 막기 위해, 관련성은
            # 40% 비율 AND 핵심어 최소 1개 완전매칭을 함께 요구한다.
            matched_concepts = 0
            matched_core = 0
            for word in query_words:
                forms = {form for form in _word_forms(word) if not form.startswith("#")}
                if any(frequencies.get(form) for form in forms):
                    matched_concepts += 1
                    if not (forms & _GENERIC_TERMS):
                        matched_core += 1
            coverage = matched_concepts / len(query_words)
            if score > 0 and coverage >= 0.4 and matched_core >= 1:
                scores.append((score, doc_id, sorted(set(matched_words))))

        scores.sort(key=lambda row: (-row[0], row[1]))
        results: list[dict] = []
        for score, doc_id, matched_words in scores[:k]:
            result = dict(self.articles[doc_id])
            result["score"] = round(score, 4)
            result["matched_terms"] = matched_words
            results.append(result)
        return results


_DEFAULT_INDEX: RuleSearchIndex | None = None


def get_default_index() -> RuleSearchIndex:
    """기본 코퍼스 인덱스를 프로세스에서 한 번만 만든다."""
    global _DEFAULT_INDEX
    if _DEFAULT_INDEX is None:
        _DEFAULT_INDEX = RuleSearchIndex()
    return _DEFAULT_INDEX


def search(query: str, k: int = 5) -> list[dict]:
    """기본 인덱스 검색 편의 함수."""
    return get_default_index().search(query, k=k)

"""질의 로그 저장 전 개인정보 탐지·마스킹.

호환 문자와 전각 문자를 NFKC로 정규화한 뒤 국내 식별정보의 흔한 표기 변형을
보수적으로 가린다. 중첩 컨테이너에는 ``redact_value``를 사용한다.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any


_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "주민등록번호",
        re.compile(r"(?<!\d)\d{6}\s*[-–—]?\s*[1-8]\d{6}(?!\d)"),
    ),
    (
        "전화번호",
        re.compile(
            r"(?<![\d+])(?:\+\s*82[-.\s]?(?:\(0\)[-.\s]?)?|0082[-.\s]?)\(?(?:10|1[016789]|[2-6]\d?)\)?[-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)"
            r"|(?<!\d)\(?0(?:1[016789]|2|[3-6]\d)\)?[-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)"
        ),
    ),
    (
        "이메일",
        re.compile(r"[0-9A-Za-z._%+-]+@[0-9A-Za-z.-]+\.[A-Za-z]{2,}(?![A-Za-z0-9])"),
    ),
    (
        "계좌번호",
        re.compile(
            r"(?:(?:계좌(?:번호)?|입금|예금주)\s*[:：]?\s*)(?:\d[-.\s]?){8,16}\d"
            r"|(?<!\d)(?:\d{3,6}-\d{2,6}-\d{4,6}|\d{3}-\d{6}-\d{2})(?!\d)"
        ),
    ),
    (
        "주소",
        re.compile(
            r"(?:서울특별시|부산광역시|대구광역시|인천광역시|광주광역시|대전광역시|울산광역시|세종특별자치시|"
            r"경기도|강원(?:특별자치)?도|충청[남북]도|전라[남북]도|경상[남북]도|제주특별자치도)"
            r"\s+[가-힣0-9·]+(?:시|군|구)\s+[가-힣0-9·]+(?:로|길|동|읍|면|리)\s*\d+(?:-\d+)?"
        ),
    ),
    ("학번추정", re.compile(r"(?<!\d)20\d{6,8}(?!\d)")),
)


def normalize(text: object) -> str:
    """PII 검사 전에 Unicode 호환 문자를 표준 형태로 접는다."""
    return unicodedata.normalize("NFKC", str(text))


def scan(text: object) -> list[str]:
    """감지된 개인정보 종류를 정의 순서대로 반환한다."""
    value = normalize(text)
    return [kind for kind, pattern in _PATTERNS if pattern.search(value)]


def redact(text: object) -> tuple[str, list[str]]:
    """문자열을 마스킹하고 감지 종류를 반환한다."""
    masked = normalize(text)
    kinds: list[str] = []
    for kind, pattern in _PATTERNS:
        if pattern.search(masked):
            kinds.append(kind)
            masked = pattern.sub(f"[{kind} 마스킹]", masked)
    return masked, kinds


def redact_value(value: Any) -> tuple[Any, list[str]]:
    """문자열·목록·딕셔너리의 모든 문자열 값을 재귀적으로 마스킹한다."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        output = []
        kinds: list[str] = []
        for item in value:
            masked, found = redact_value(item)
            output.append(masked)
            kinds.extend(found)
        return output, list(dict.fromkeys(kinds))
    if isinstance(value, tuple):
        masked, kinds = redact_value(list(value))
        return tuple(masked), kinds
    if isinstance(value, dict):
        output = {}
        kinds = []
        for key, item in value.items():
            masked, found = redact_value(item)
            output[str(key)] = masked
            kinds.extend(found)
        return output, list(dict.fromkeys(kinds))
    return value, []


def notice(kinds: list[str]) -> str:
    """사용자에게 보여 줄 개인정보 처리 안내를 만든다."""
    if not kinds:
        return ""
    joined = "·".join(kinds)
    return f"개인 식별정보 추정 패턴({joined})을 마스킹했으며 원문은 저장하지 않았습니다."

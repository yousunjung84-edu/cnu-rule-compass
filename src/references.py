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
    # 낫표는 전각(「」 U+300C/D)만이 아니라 **반각(｢｣ U+FF62/3)**도 쓰인다.
    # 반각은 jnu.ac.kr 지침 계층에서 광범위하다 — 미처리 시 법령명이 통째로 유실된다.
    r"(?:[「｢『《‘“]\s*(?P<quoted>[^」｣』》’”]{2,50}?)\s*[」｣』》’”]\s*)?"
    r"(?P<plain>[가-힣A-Za-z·]{2,30})?\s*"
    # '고등교육법시행령 부칙 제19조'처럼 법령명과 조문 사이에 '부칙'이 끼어든다.
    # 이걸 흡수하지 않으면 규정명 자리에 '부칙'이 잡혀 법령명을 잃는다.
    r"(?P<addendum>부칙)?\s*"
    r"제\s*(?P<article>\d+)\s*조(?:\s*의\s*(?P<sub>\d+))?"
    r"(?:\s*제\s*(?P<clause>\d+)\s*항)?"
    r"(?P<each>\s*각\s*호)?"
)
_EXTERNAL_SUFFIX = ("법", "법률", "시행령", "시행규칙", "령", "규칙", "조례", "특별법", "정관")
# 알려진 외부 법령. **사전은 완결될 수 없다** — 미등재는 버리지 않고
# external_law_unmatched로 남긴다(사전 확장은 정확도 수단이지 정확성의 전제가 아니다).
_EXTERNAL_LAWS = (
    "고등교육법시행령", "고등교육법", "교육공무원법", "교육공무원임용령", "사립학교법",
    "개인정보보호법", "개인정보 보호법", "국가공무원법", "국가공무원 복무규정", "지방공무원법",
    "산업교육진흥 및 산학연협력촉진에 관한 법률", "학교보건법", "평생교육법",
    "초·중등교육법시행령", "초·중등교육법", "병역법", "국가연구개발혁신법",
    "공공기관의 운영에 관한 법률", "상법", "근로기준법", "국립대학병원 설치법",
    "금융산업의 구조개선에 관한 법률", "벤처기업육성에 관한 특별 조치법",
    "중소기업창업 지원법", "중소기업 인력지원 특별법", "지능정보화 기본법",
    "소재ㆍ부품ㆍ장비산업 경쟁력 강화 및 공급망 안정화를 위한 특별조치법",
    "국가첨단전략산업법", "연구실 안전환경 조성에 관한 법률", "원자력안전법",
    "대학교원 자격기준 등에 관한 규정",
    # v1.6 보강 (T31). 사전은 완결될 수 없다 — 미등재는 external_law_unmatched로 남는다.
    "회계관계직원 등의 책임에 관한 법률",
    "국가를 당사자로 하는 계약에 관한 법률",
    "부정청탁 및 금품등 수수의 금지에 관한 법률",
    "공무원 여비 규정", "공무원 여비규정", "고용보험법", "국민건강보험법",
    "공공감사에 관한 법률", "부패방지 및 국민권익위원회의 설치와 운영에 관한 법률",
    "공직자의 이해충돌 방지법", "공익신고자 보호법", "행정절차법", "민법", "형법",
)
# 규정명 자리에 올 수 있으나 규정을 특정하지 않는 말. 조사·접속어가 규정명으로
# 오인되면 같은 참조가 해소본과 미해소본으로 중복 계상된다('경우에는 제44조' 사고).
_VAGUE_PREFIX = {
    "이", "본", "동", "같은", "그", "위", "해당", "규정", "지침", "이상", "각",
    "경우", "경우에는", "다만", "때에는", "따라", "의하여", "의한", "따른", "관한",
    "및", "또는", "제외하고는", "포함한", "준용한다", "정한", "정하는", "위하여",
}
# 규정명 후보 끝에 붙는 조사·어미 — 벗겨서 다시 판정한다.
_TRAILING_PARTICLE_RE = re.compile(
    r"(에는|에서|에게|으로|이나|이라도|에|은|는|이|가|을|를|의|와|과|도|만|로)$"
)


# 조문번호 없이 규정 전체를 지목하는 참조 (T19).
# '「전남대학교 인권센터 규정」에 의한 조사 결과는 …' 처럼 흔한 형태인데,
# 조문 패턴을 필수로 요구하던 추출기가 통째로 흘려보냈다 — 놓친 줄도 모르는 누락이다.
_RULE_LEVEL_RE = re.compile(
    # ① 낫표로 감싼 이름
    r"(?:[「｢『《]\s*(?P<quoted_name>[^」｣』》]{2,50}?)\s*[」｣』》]"
    # ② 홑·겹따옴표로 감싼 이름 — **규정 접미사로 끝날 때만** 인정한다.
    #    한국 규정문에서 ‘’ “”는 대부분 용어 정의다(“위원회”라 한다). 낫표와 같이
    #    취급했더니 미해소 참조가 278 → 970으로 부풀어, 정작 수집 공백 신호(T30)를
    #    묽혔다. 규정을 지목하는 인용(‘대학 등록금에 관한 규칙’)만 통과시킨다.
    r"|[‘“]\s*(?P<soft_name>[^’”]{2,50}?(?:규정|지침|학칙|규칙|세칙|요령|기준))\s*[’”]"
    # ③ 감싸지 않았으나 규정·법령 접미사로 끝나는 이름
    r"|(?P<plain_name>[가-힣A-Za-z0-9·ㆍ]{3,30}(?:규정|지침|학칙|규칙|세칙|법률|법|시행령|시행규칙|조례|정관)))"
    # 뒤에 조문이 오면 조문 단위 참조이므로 여기서 잡지 않는다.
    # 닫는 인용부호를 넘겨봐야 한다 — ｢국가공무원법｣ 제64조에서 낫표 안쪽만 걸리면
    # 조문 단위 참조가 규정 단위로 중복 계상된다.
    r"(?!\s*[」｣』》’”]?\s*(?:부칙\s*)?제\s*\d+\s*조)"
    # '「업무 정보화」란 …' 같은 용어 정의는 규정 참조가 아니다
    r"(?!\s*(?:이)?란|\s*이라\s*함은|\s*이라\s*한다)"
)
# 접미사만 보면 잡히지만 규정명이 아닌 말
_NOT_A_RULE_NAME = {
    "관련 규정", "관계 규정", "이 규정", "본 규정", "해당 규정", "관련 지침", "이 지침",
    "관계 법령", "관련 법령", "이 학칙", "본 학칙", "제 규정", "각종 규정", "타 규정",
}
# 무지정 위임 (T29) — 제3의 공백 유형.
# 별표 위임은 이름(별표 1)과 위치(원문 링크)가 있고, 외부 법령은 어느 법인지 안다.
# `따로 정한다`는 **무엇에 위임했는지조차 명시하지 않는다.** 하위 지침인지 내규인지
# 조문만으로는 알 수 없어, 소비자에게는 '규정 없음'과 전혀 다른 안내가 필요하다.
# ("규정 미비" → 소관 부서 / "위임되었으나 수임 규범 미특정" → 하위 기준 문의)
_UNNAMED_DELEGATION_RE = re.compile(
    r"(?:(?P<delegate>[가-힣]{2,12}(?:총장|장|위원회))\s*(?:이|가)\s*)?"
    r"(?:따로|별도로|달리)\s*정(?:한다|하는\s*바에\s*(?:따른다|의한다)|하여야\s*한다)"
    r"|(?P<delegate2>[가-힣]{2,12}(?:총장|장|위원회))\s*(?:이|가)\s*정(?:한다|하는\s*바에\s*(?:따른다|의한다))"
)
# 위임 문장 안에 조문·별표가 지목돼 있으면 무지정이 아니다('제5조에서 따로 정한다').
_NAMED_TARGET_RE = re.compile(
    r"제\s*\d+\s*조|별표|별지|[「｢『《]|[‘“][^’”]{2,50}(?:규정|지침|학칙|규칙|세칙)[’”]"
)

# 별표·별지서식 위임 표기
_ATTACHMENT_RE = re.compile(
    r"별표\s*\d+(?:의\s*\d+)?|별지\s*서식\s*제\s*\d+\s*호|별지\s*제\s*\d+\s*호\s*서식|별표(?=\s*와\s*같)"
)
# '제3항' → '③' 대조용
_CLAUSE_MARKS = {f"제{i}항": mark for i, mark in enumerate("①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮", start=1)}


def _looks_like_external_law(name: str) -> bool:
    """법령 사전 또는 법령 접미사로 외부 규범임이 확인되는가 (T31).

    조문번호 유무와 무관하게 판정한다. 사전은 완결될 수 없으므로 접미사도 함께 본다.
    """
    text = name.strip()
    if any(text.endswith(law) or law in text for law in _EXTERNAL_LAWS):
        return True
    return text.endswith(("법", "법률", "시행령", "시행규칙", "조례", "특별법"))


def _sentence_at(text: str, position: int) -> str:
    """해당 위치가 속한 문장(줄 단위)을 돌려준다 — 위임 문언을 그대로 보여주기 위함."""
    start = max(text.rfind("\n", 0, position), text.rfind(". ", 0, position)) + 1
    end = text.find("\n", position)
    end = len(text) if end == -1 else end
    return " ".join(text[start:end].split())


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
        # 본문은 띄어쓰기를 자주 생략한다('전남대학교학칙', '전남대보안규정').
        # 공백 제거형도 별칭으로 넣어 같은 규정으로 해소한다.
        for alias, target in list(self._alias.items()):
            self._alias.setdefault(alias.replace(" ", ""), target)
        # 별표 대조용 공백 제거 색인 ('별표1' ↔ '별표 1')
        self._attachment_by_key: dict[tuple[str, str], dict] = {}
        for row in articles:
            if row.get("record_type") == "별표":
                self._attachment_by_key.setdefault(
                    (row["규정명"], str(row["조문번호"]).replace(" ", "")), row
                )
        self._rule_source_key = {
            row["규정명"]: row.get("source_key") for row in articles
        }
        self._inbound: dict[str, list[dict]] | None = None

    def _resolve_rule(
        self, raw: str | None, current_rule: str, quoted: bool = False
    ) -> tuple[str | None, str]:
        """참조에 적힌 규정명을 해소하고 종류를 판정한다 (T14 정정 규칙).

        세 상태를 구별한다. 이전 판(v1.2 T9)은 규정명 추출 실패를 자기 규정 참조로
        흡수해, 존재하지도 않는 조문을 '코퍼스에 없다'고 보고했다 — 자신 있게 틀린 답이다.

        1. 앞에 고유명사가 없음 ('제44조에 의한')        → same_rule
        2. 법령명이고 사전/접미사로 확인됨              → external_law
        3. 고유명사인데 코퍼스에도 사전에도 없음        → external_law_unmatched
        """
        name = (raw or "").strip()
        if not name or name in _VAGUE_PREFIX:
            return current_rule, "same_rule"
        if name in self._alias:  # 낫표 안에는 교내 규정도 들어간다(「전남대학교 학칙」)
            resolved = self._alias[name]
            return resolved, "same_rule" if resolved == current_rule else "cross_rule"
        if any(name.endswith(law) or law in name for law in _EXTERNAL_LAWS):
            return None, "external_law"
        # 조사가 붙어 있으면 벗기고 한 번 더 본다('교학규정에' → '교학규정')
        stripped = _TRAILING_PARTICLE_RE.sub("", name)
        if stripped and stripped != name:
            if stripped in _VAGUE_PREFIX:
                return current_rule, "same_rule"
            if stripped in self._alias:
                resolved = self._alias[stripped]
                return resolved, "same_rule" if resolved == current_rule else "cross_rule"
            if any(stripped.endswith(law) or law in stripped for law in _EXTERNAL_LAWS):
                return None, "external_law"
            name = stripped
        if name.endswith(_EXTERNAL_SUFFIX):
            return None, "external_law"
        if quoted:
            # 낫표로 감싼 고유명사인데 코퍼스에도 사전에도 없다. 자기 규정 참조로
            # 떨어뜨리면 없는 조문을 지목하게 되므로 미등재 상태를 그대로 남긴다.
            return None, "external_law_unmatched"
        # 감싸지 않은 앞말은 조사·접속어일 뿐인 경우가 대부분이다.
        return current_rule, "same_rule"

    def outbound(self, record: dict, resolve: bool = True) -> tuple[list[dict], list[dict]]:
        """이 조문이 인용하는 참조 목록과, 해소하지 못한 참조 목록을 반환한다."""
        body = str(record.get("본문", ""))
        current_rule = str(record.get("규정명", ""))
        found: list[dict] = []
        unresolved: list[dict] = []
        seen: set[tuple] = set()

        # 별표·별지서식 위임 (T11). 정본(law.go.kr)이 별표를 이미지로 제공해
        # 텍스트 수집이 불가하다(2026-08-10 확인: <img src="/LSW/flDownload.do?flSeq=...">).
        # 수집하지 못했다는 사실 자체를 남겨, 소비자가 '규정에 없음'과 구별하게 한다.
        # 미수집 사유는 계층마다 다르다 (T17). 규정 계층(law.go.kr)은 별표를 이미지로
        # 제공해 텍스트가 없고, 지침 계층(jnu.ac.kr HWP)은 텍스트가 있으나 조문 파서가
        # '제N조' 헤더만 잡아 코퍼스에 넣지 못했다. 원인이 다르면 대응도 다르다.
        is_regulation_tier = len(str(record.get("source_key", ""))) > 6
        reason_code = "image_only" if is_regulation_tier else "parser_scope"
        reason_text = (
            "정본(law.go.kr)이 별표를 이미지로 제공해 텍스트 수집이 불가합니다. 원문 링크에서 확인하세요."
            if is_regulation_tier
            else "원문(HWP)에는 별표 텍스트가 있으나 조문 단위 수집 범위 밖이라 코퍼스에 없습니다. 원문 링크에서 확인하세요."
        )
        for match in _ATTACHMENT_RE.finditer(body):
            raw_text = " ".join(match.group(0).split())
            key = ("attachment", raw_text)
            if key in seen:
                continue
            seen.add(key)
            # T22 이후 지침 계층 별표는 코퍼스에 있다. 있으면 해소해서 돌려준다.
            # 본문은 '별표1', 코퍼스는 '별표 1'처럼 띄어쓰기가 어긋난다 — 공백을 지운
            # 형태로도 대조한다(어긋남 하나로 해소된 별표가 미수집으로 보고되던 문제).
            target = (
                self._by_key.get((current_rule, raw_text))
                or self._attachment_by_key.get((current_rule, raw_text.replace(" ", "")))
                or (self._by_key.get((current_rule, "별표")) if raw_text.startswith("별표") else None)
            )
            if target is not None:
                entry = {
                    "raw": raw_text,
                    "target_rule": current_rule,
                    "target_article": target["조문번호"],
                    "target_clause": None,
                    "kind": "attachment",
                    "resolved": True,
                    "record_id": target["record_id"],
                }
                if resolve:
                    entry["article"] = target
                found.append(entry)
                continue
            unresolved.append({
                "raw": raw_text,
                "kind": "attachment_not_collected",
                "reason_code": reason_code,
                "reason": reason_text,
            })

        # 무지정 위임 (T29). 조문마다 여러 번 나오므로 항 단위로 구분해 각각 남긴다
        # — 세 항이 각각 위임한 것을 하나로 합치면 무엇이 비었는지 알 수 없다.
        for match in _UNNAMED_DELEGATION_RE.finditer(body):
            sentence = _sentence_at(body, match.start())
            if _NAMED_TARGET_RE.search(sentence):
                continue  # 위임 대상이 명시돼 있으면 무지정이 아니다
            clause = None
            for mark in reversed(body[: match.start()]):
                if mark in _CLAUSE_MARKS.values():
                    clause = mark
                    break
            key = ("unnamed_delegation", clause, match.start())
            if key in seen:
                continue
            seen.add(key)
            delegate = match.group("delegate") or match.group("delegate2")
            entry = {
                "raw": sentence,
                "kind": "unnamed_delegation",
                "clause": clause,
                "delegate": delegate,
                "reason": (
                    "하위 규범에 위임되었으나 수임 규범이 코퍼스에서 특정되지 않습니다. "
                    "'규정 없음'이 아니라 '위임처 미특정'이며, 담당 부서에 하위 기준을 확인해야 합니다."
                ),
            }
            unresolved.append(entry)

        # 규정명만 지목하는 참조 (T19). 조문 단위 해소는 불가하므로 resolved=False로
        # 두되, 참조가 있었다는 사실은 반드시 남긴다.
        for match in _RULE_LEVEL_RE.finditer(body):
            name = (match.group("quoted_name") or match.group("soft_name")
                    or match.group("plain_name") or "").strip()
            if not name or name in _NOT_A_RULE_NAME or name in _VAGUE_PREFIX:
                continue
            raw_text = " ".join(match.group(0).split())
            resolved_name = self._alias.get(name) or self._alias.get(name.replace(" ", ""))
            if resolved_name is None:
                stripped = _TRAILING_PARTICLE_RE.sub("", name)
                resolved_name = self._alias.get(stripped) or self._alias.get(stripped.replace(" ", ""))
            if resolved_name == current_rule:
                continue  # 자기 규정을 이름으로 부른 것뿐이다
            key = ("rule_level", name)
            if key in seen:
                continue
            seen.add(key)
            if resolved_name:
                entry = {
                    "raw": raw_text,
                    "target_rule": resolved_name,
                    "target_article": None,
                    "target_clause": None,
                    "kind": "rule_level",
                    "resolved": False,  # 규정 전체 지목이라 조문 단위 해소는 없다
                    "record_id": None,
                    "source_key": self._rule_source_key.get(resolved_name),
                }
                if resolve:
                    entry["article"] = None
                found.append(entry)
            else:
                # 국가 법령은 애초에 이 코퍼스에 있을 수 없다 (T31). 이걸 '코퍼스에
                # 없다'로 쓰면 총무과 계약 규정 같은 **실제 수집 공백**과 구별되지 않는다.
                # external_rule_unmatched는 '교내 규정으로 보이는데 없는 것'만 남긴다.
                if _looks_like_external_law(name):
                    unresolved.append({
                        "raw": raw_text,
                        "kind": "external_law",
                        "reason": "코퍼스 범위 밖(국가 법령 등 외부 규범)입니다. 수집 누락이 아닙니다.",
                    })
                else:
                    unresolved.append({
                        "raw": raw_text,
                        "kind": "external_rule_unmatched",
                        "reason": (
                            "교내 규정으로 보이나 코퍼스에 해당 규정이 없습니다. "
                            "수집 범위 공백일 수 있습니다."
                        ),
                    })

        for match in _REFERENCE_RE.finditer(body):
            raw_name = match.group("quoted") or match.group("plain")
            addendum = bool(match.group("addendum"))
            target_article = _norm_article(match.group("article"), match.group("sub"))
            clause = f"제{int(match.group('clause'))}항" if match.group("clause") else None
            if match.group("each"):
                clause = f"{clause} 각 호" if clause else "각 호"
            rule, kind = self._resolve_rule(
                raw_name, current_rule, quoted=bool(match.group("quoted"))
            )
            raw_text = " ".join(match.group(0).split())
            # 규정명으로 인정되지 않은 앞말(조사·접속어)은 표시 문자열에서도 덜어낸다.
            if raw_name and kind == "same_rule" and raw_name not in (rule or ""):
                cut = raw_text.find("제")
                if cut > 0:
                    raw_text = raw_text[cut:]
            if addendum and kind == "same_rule" and not raw_name:
                # 규정명 없는 '부칙 제N조'는 자기 규정의 부칙이다.
                target_article = f"부칙 {target_article}"

            if kind in {"external_law", "external_law_unmatched", "unknown"}:
                key = ("unresolved", raw_text)
                if key in seen:
                    continue
                seen.add(key)
                unresolved.append({
                    "raw": raw_text,
                    "kind": kind,
                    "reason": {
                        "external_law": "외부 법령이라 이 코퍼스의 수집 범위 밖입니다.",
                        "external_law_unmatched": "법령·외부 규정으로 보이나 사전에 없습니다. 코퍼스 밖일 수 있습니다.",
                    }.get(kind, "규정명을 특정할 수 없습니다."),
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
            if target is not None and clause:
                # 삭제된 항을 가리키는 참조는 무효다. 조용히 해소하면 소비자가
                # 없는 항을 근거로 인용하게 된다(T10).
                gone = {row["clause"] for row in target.get("repealed_clauses") or []}
                head = clause.split()[0]
                circled = _CLAUSE_MARKS.get(head)
                if circled and circled in gone:
                    unresolved.append({
                        "raw": raw_text,
                        "kind": "repealed_clause",
                        "reason": f"{rule} {target_article} {head}은(는) 삭제된 항입니다.",
                    })
                    continue
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

#!/usr/bin/env python3
"""전남대학교 지침 HWP를 내려받아 조문 단위 JSON 코퍼스로 변환한다."""

from __future__ import annotations

import argparse
import html
import json
import logging
import random
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import Request, urlopen

from kordoc_bin import kordoc_command, kordoc_env


LIST_URL = "https://www.jnu.ac.kr/WebApp/web/HOM/COM/Rule/AdminRule400.aspx"
USER_AGENT = "Mozilla/5.0"
DEFAULT_LIMIT = 40
PAST_VERSION_RE = re.compile(r"개정\s*전|이전|폐지")
CURRENT_MARK_RE = re.compile(r"[\s(（]*(?:현행|현행본)[)）]?[\s]*$")
LEADING_NUMBER_RE = re.compile(r"^\s*\d+\s*[.)．]\s*")
KEY_RE = re.compile(r"(?:[?&]|&amp;)key=(\d+)", re.IGNORECASE)

# 편제명과 규정명 양쪽에 적용한다. 앞쪽 단어일수록 동점 정렬 때 우선한다.
CORE_KEYWORDS = (
    "인사", "교원", "직원", "임용", "복무", "학사", "교무", "교육과정", "수업",
    "성적", "학적", "연구", "산학", "행정", "총무", "학생", "장학", "시설", "안전",
    "정보화", "정보보안", "데이터", "개인정보", "재무", "회계", "예산", "계약", "구매",
)
CORE_DOMAINS = {
    "인사": ("인사", "교원", "직원", "임용", "승진", "복무", "성과급", "겸직"),
    "학사": ("학사", "교무", "교육과정", "교과과정", "수업", "성적", "학적", "학점"),
    "연구": ("연구", "산학", "실험", "방사선"),
    "행정·총무": ("행정", "총무", "공무", "출장", "위원회", "업무처리"),
    "학생": ("학생", "장학", "동아리", "생활관"),
    "시설": ("시설", "안전", "교통", "관사", "환경"),
    "정보화": ("정보화", "정보보안", "정보시스템", "데이터", "개인정보", "전산"),
    "재무·회계": ("재무", "회계", "예산", "계약", "구매", "등록금", "상품권"),
}

ARTICLE_RE = re.compile(
    r"(?m)^[ \t]*(?:#{1,6}[ \t]*)?(?:[-*+>][ \t]*)?(?:\*\*|__)?"
    r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?"
    r"\s*(?:[（(]\s*([^\n)）]+?)\s*[)）]|【\s*([^\n】]+?)\s*】)?"
    r"(?:\*\*|__)?[ \t]*(.*)$"
)


@dataclass(frozen=True)
class Rule:
    name: str
    key: str
    division: str
    source_url: str
    score: int = 0


class RuleListParser(HTMLParser):
    """treeData의 폴더 계층을 보존하며 다운로드 링크를 수집한다."""

    def __init__(self, base_url: str = LIST_URL) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.in_tree = False
        self.ul_depth = 0
        self.folder_by_depth: dict[int, str] = {}
        self.current_anchor: dict[str, object] | None = None
        self.rules: list[Rule] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "ul" and attrs_dict.get("id") == "treeData":
            self.in_tree = True
            self.ul_depth = 1
            return
        if self.in_tree and tag == "ul":
            self.ul_depth += 1
            return
        if self.in_tree and tag == "a":
            self.current_anchor = {
                "href": attrs_dict.get("href") or "",
                "onclick": attrs_dict.get("onclick") or "",
                "depth": self.ul_depth,
                "text": [],
            }

    def handle_data(self, data: str) -> None:
        if self.current_anchor is not None:
            text_parts = self.current_anchor["text"]
            assert isinstance(text_parts, list)
            text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self.in_tree:
            return
        if tag == "a" and self.current_anchor is not None:
            self._finish_anchor()
            self.current_anchor = None
        elif tag == "ul":
            self.folder_by_depth.pop(self.ul_depth, None)
            self.ul_depth -= 1
            if self.ul_depth == 0:
                self.in_tree = False

    def _finish_anchor(self) -> None:
        assert self.current_anchor is not None
        raw_text = "".join(self.current_anchor["text"])
        name = normalize_space(html.unescape(raw_text))
        href = html.unescape(str(self.current_anchor["href"]))
        onclick = html.unescape(str(self.current_anchor["onclick"]))
        depth = int(self.current_anchor["depth"])

        key = extract_key(href) or extract_key(onclick)
        if key:
            division = self.folder_by_depth.get(1, "미분류")
            raw_url = extract_file_url(href) or extract_file_url(onclick)
            source_url = urljoin(self.base_url, raw_url) if raw_url else make_source_url(key)
            self.rules.append(Rule(clean_rule_name(name), key, division, source_url))
        elif href.strip().lower() in {"#none", "#"} and name:
            self.folder_by_depth[depth] = clean_rule_name(name)


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def clean_rule_name(name: str) -> str:
    name = LEADING_NUMBER_RE.sub("", normalize_space(name))
    return CURRENT_MARK_RE.sub("", name).strip()


def extract_key(value: str) -> str | None:
    match = KEY_RE.search(value)
    if match:
        return match.group(1)
    # onclick 인자 안에 URL 인코딩되지 않은 쿼리가 들어오는 변형을 보완한다.
    for candidate in re.findall(r"['\"]([^'\"]+)['\"]", value):
        query = parse_qs(urlparse(html.unescape(candidate)).query)
        if query.get("mode", [""])[0].lower() == "file" and query.get("key"):
            return query["key"][0]
    return None


def extract_file_url(value: str) -> str | None:
    candidates = [value, *re.findall(r"['\"]([^'\"]+)['\"]", value)]
    for candidate in candidates:
        candidate = html.unescape(candidate).strip()
        if extract_key(candidate) and re.search(r"(?:[?&])mode=file(?:&|$)", candidate, re.I):
            return candidate
    return None


def make_source_url(key: str) -> str:
    return f"{LIST_URL}?group=&type=&mode=file&key={key}"


def parse_rule_list(document: str) -> list[Rule]:
    parser = RuleListParser()
    parser.feed(document)
    # 동일 key가 href/onclick 양쪽에서 잡혀도 한 번만 수집한다.
    unique: dict[str, Rule] = {}
    for rule in parser.rules:
        unique.setdefault(rule.key, rule)
    return list(unique.values())


def relevance_score(rule: Rule) -> int:
    division = rule.division.replace(" ", "")
    name = rule.name.replace(" ", "")
    score = 0
    for index, keyword in enumerate(CORE_KEYWORDS):
        weight = max(1, len(CORE_KEYWORDS) - index)
        if keyword in division:
            score += 100 + weight
        if keyword in name:
            score += 20 + weight
    if "현행" in rule.name:
        score += 5
    return score


def matches_domain(rule: Rule, keywords: tuple[str, ...]) -> bool:
    haystack = (rule.division + rule.name).replace(" ", "")
    return any(keyword in haystack for keyword in keywords)


def select_core_rules(rules: Iterable[Rule], limit: int) -> list[Rule]:
    candidates: list[Rule] = []
    for rule in rules:
        if PAST_VERSION_RE.search(rule.name):
            continue
        score = relevance_score(rule)
        if score > 0:
            candidates.append(Rule(rule.name, rule.key, rule.division, rule.source_url, score))
    candidates.sort(key=lambda item: (-item.score, item.division, item.name, int(item.key)))
    # 한 분야의 다수 문서가 표본을 독점하지 않도록 8개 핵심 영역을 먼저 배정한다.
    quota = max(1, min(3, limit // len(CORE_DOMAINS)))
    selected: list[Rule] = []
    selected_keys: set[str] = set()
    for keywords in CORE_DOMAINS.values():
        domain_rules = [rule for rule in candidates if matches_domain(rule, keywords)]
        added = 0
        for rule in domain_rules:
            if rule.key in selected_keys:
                continue
            selected.append(rule)
            selected_keys.add(rule.key)
            added += 1
            if added >= quota or len(selected) >= limit:
                break
    for rule in candidates:
        if len(selected) >= limit:
            break
        if rule.key not in selected_keys:
            selected.append(rule)
            selected_keys.add(rule.key)
    return selected


def http_get(url: str, timeout: float = 60.0) -> tuple[bytes, str]:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return response.read(), response.headers.get("Content-Type", "")


def download_with_retry(
    url: str,
    destination: Path,
    sleep_min: float,
    sleep_max: float,
    retries: int = 2,
    timeout: float = 60.0,
) -> None:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        time.sleep(random.uniform(sleep_min, sleep_max))
        try:
            payload, content_type = http_get(url, timeout=timeout)
            if not payload or payload.lstrip().startswith((b"<!DOCTYPE html", b"<html")):
                raise ValueError(f"HWP가 아닌 응답: {content_type or 'unknown'}, {len(payload)} bytes")
            destination.write_bytes(payload)
            return
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            last_error = exc
            logging.warning("[재시도] %s (%d/%d): %s", url, attempt + 1, retries + 1, exc)
    assert last_error is not None
    raise RuntimeError(f"{retries + 1}회 요청 실패: {last_error}")


def run_kordoc(hwp_path: Path, md_path: Path, timeout: int = 120) -> str:
    command = kordoc_command(hwp_path, md_path)
    completed = subprocess.run(
        command,
        env=kordoc_env(),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if completed.returncode != 0:
        detail = normalize_space(completed.stderr or completed.stdout)
        raise RuntimeError(f"kordoc 종료 코드 {completed.returncode}: {detail}")
    if not md_path.exists():
        raise RuntimeError("kordoc 출력 파일이 생성되지 않음")
    return md_path.read_text(encoding="utf-8", errors="replace")


# kordoc 4.12.0이 새로 넣는 밑줄 표기. **태그만 벗기고 텍스트는 남긴다.**
#
# 2026-09-01 실측: 기존 코퍼스 17,585건에 <u>가 0건인데 4.12.0은 이를 넣는다.
# 그대로 두면 신규 수집분만 표기 세대가 달라지고, 전건 재수집하면 밑줄이 있는
# 조문의 본문이 바뀌어 record_id가 재발급된다(ID는 본문 해시다).
#
# 밑줄은 **서식이지 조문 내용이 아니다** — 인용에 필요 없다. 반면 표 구조를
# 나르는 <table>·<tr>·<td>·<th>·<br>은 그대로 둔다(코퍼스에 이미 1,269건).
# 지우는 것은 의미를 나르지 않는 태그 하나뿐이다.
_UNDERLINE_TAG = re.compile(r"</?u>", re.IGNORECASE)


def clean_markdown_text(value: str) -> str:
    value = _UNDERLINE_TAG.sub("", value)
    value = re.sub(r"(?m)^#{1,6}\s*", "", value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


# 순수 별지·붙임 표식. 대괄호 단독 줄과 공문 꼬리 「붙임: … 1부. 끝.」 두 형태다.
# (rule-compass-core `2403b07`·`a41399e`의 attachment_cut 개념 이식, backport #2)
_ATTACHMENT_MARK = re.compile(r"(?m)^\[\s*(?:별지|붙임)[^\]]*\]\s*$|^붙임\s*[::]")


def attachment_cut(markdown: str) -> int:
    """본문이 끝나는 오프셋. 별지·붙임 뒤의 조문은 규정의 조문이 아니다.

    2026-08-31 실측: 「연구조교 및 교육조교 선발과 운영에 관한 지침」 5개 판본에서
    별지 서식의 **복무협약서 제1~8조가 본칙으로 적재**돼 있었다(합계 109조문,
    제2조·제8조는 판본당 3~4회 중복). 서식 견본의 조문을 규정으로 인용하면
    소스가 주지 않은 규범적 지위를 부여하는 것이다.

    문서 **첫머리**의 표식은 경계가 아니다 — 그 문서 자신이 다른 문서의 붙임으로
    배포됐다는 표지다(코어에서 충남대 「계약심의회 운영 지침」으로 확인).
    앞에 조문이 하나도 없으면 자르지 않는다.
    """
    for match in _ATTACHMENT_MARK.finditer(markdown):
        if ARTICLE_RE.search(markdown[:match.start()]):
            return match.start()
    return len(markdown)


def split_articles(markdown: str) -> list[dict[str, str]]:
    markdown = markdown[:attachment_cut(markdown)]
    matches = list(ARTICLE_RE.finditer(markdown))
    articles: list[dict[str, str]] = []
    for index, match in enumerate(matches):
        number = match.group(1)
        if match.group(2):
            article_number = f"제{number}조의{match.group(2)}"
        else:
            article_number = f"제{number}조"
        title = normalize_space(match.group(3) or match.group(4) or "")
        inline_body = (match.group(5) or "").strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        trailing_body = markdown[start:end].strip()
        body = "\n".join(part for part in (inline_body, trailing_body) if part)
        articles.append(
            {
                "조문번호": article_number,
                "조문제목": title,
                "본문": clean_markdown_text(body),
            }
        )
    return articles


def append_failure(path: Path, rule: Rule, stage: str, error: Exception) -> None:
    record = {
        "수집일시": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_key": rule.key,
        "규정명": rule.name,
        "편제": rule.division,
        "stage": stage,
        "error": str(error),
    }
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_json_atomic(path: Path, data: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="첫 실행 수집 상한 (기본 40)")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path.home() / "rulecompass-build" / "data",
        help="출력 data 디렉터리",
    )
    parser.add_argument("--list-html", type=Path, help="네트워크 목록 GET 대신 저장된 HTML 사용")
    parser.add_argument("--sleep-min", type=float, default=0.5)
    parser.add_argument("--sleep-max", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--refresh", action="store_true", help="기존 HWP도 다시 다운로드")
    parser.add_argument("--dry-run", action="store_true", help="목록 파싱과 선택만 수행")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    if args.limit < 1:
        raise SystemExit("--limit은 1 이상이어야 합니다.")
    if not 0 <= args.sleep_min <= args.sleep_max:
        raise SystemExit("sleep 범위가 올바르지 않습니다.")

    output_dir: Path = args.output_dir.expanduser().resolve()
    hwp_dir = output_dir / "hwp"
    md_dir = output_dir / "markdown"
    output_dir.mkdir(parents=True, exist_ok=True)
    hwp_dir.mkdir(exist_ok=True)
    md_dir.mkdir(exist_ok=True)
    log_path = output_dir / "collect_rules.log"
    failure_path = output_dir / "failures.jsonl"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler()],
    )

    if args.list_html:
        list_document = args.list_html.read_text(encoding="utf-8", errors="replace")
    else:
        payload, _ = http_get(LIST_URL, timeout=args.timeout)
        list_document = payload.decode("utf-8", errors="replace")

    all_rules = parse_rule_list(list_document)
    selected = select_core_rules(all_rules, args.limit)
    logging.info("[목록] 전체 %d건, 핵심 현행 후보 중 %d건 선택", len(all_rules), len(selected))

    selection_path = output_dir / "selected_rules.json"
    write_json_atomic(selection_path, [asdict(rule) for rule in selected])
    if args.dry_run:
        print(json.dumps([asdict(rule) for rule in selected], ensure_ascii=False, indent=2))
        return 0

    corpus: list[dict[str, str]] = []
    successful_rules = 0
    failures = 0
    for position, rule in enumerate(selected, start=1):
        logging.info("[수집] %d/%d key=%s %s", position, len(selected), rule.key, rule.name)
        hwp_path = hwp_dir / f"key_{rule.key}.hwp"
        md_path = md_dir / f"key_{rule.key}.md"
        try:
            if args.refresh or not hwp_path.exists():
                download_with_retry(
                    rule.source_url,
                    hwp_path,
                    args.sleep_min,
                    args.sleep_max,
                    timeout=args.timeout,
                )
        except Exception as exc:  # 한 규정 실패가 전체 수집을 중단하지 않게 한다.
            failures += 1
            logging.error("[오류] 다운로드 key=%s: %s", rule.key, exc)
            append_failure(failure_path, rule, "download", exc)
            continue

        try:
            markdown = run_kordoc(hwp_path, md_path)
            articles = split_articles(markdown)
            if not articles:
                raise ValueError("제N조 패턴을 찾지 못함")
        except Exception as exc:  # kordoc/조문 파싱 실패도 기록 후 계속한다.
            failures += 1
            logging.error("[오류] 파싱 key=%s: %s", rule.key, exc)
            append_failure(failure_path, rule, "parse", exc)
            continue

        collected_at = datetime.now().astimezone().isoformat(timespec="seconds")
        for article in articles:
            corpus.append(
                {
                    "규정명": rule.name,
                    "편제": rule.division,
                    **article,
                    "source_key": rule.key,
                    "source_url": rule.source_url,
                    "수집일시": collected_at,
                }
            )
        successful_rules += 1
        write_json_atomic(output_dir / "rules_corpus.json", corpus)

    corpus_path = output_dir / "rules_corpus.json"
    write_json_atomic(corpus_path, corpus)
    report = {
        "수집_규정_수": successful_rules,
        "총_조문_수": len(corpus),
        "실패_건수": failures,
        "선택_규정_수": len(selected),
        "rules_corpus": str(corpus_path),
        "샘플_3건": corpus[:3],
    }
    write_json_atomic(output_dir / "first_run_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if successful_rules else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[중단] 사용자 요청으로 수집을 중단했습니다.", file=sys.stderr)
        raise SystemExit(130)

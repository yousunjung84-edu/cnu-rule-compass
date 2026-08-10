#!/usr/bin/env python3
"""전남대학교 규정집(rule.jnu.ac.kr → law.go.kr 학칙공포)에서 '규정' 계층을 수집한다.

AdminRule400(지침·세칙 게시판)과 달리 규정·학칙 계층은 국가법령정보센터
학칙공포 서비스가 정본이다. 경로: DRF 셸(admRulSeq 추출) → schlPubRulInfoR.do
POST(본문 HTML) → 태그 제거 → 제N조 분할. 산출은 raw 파일로만 쓰고
rules_corpus.json 병합은 별도 단계에서 게이트를 거친다.
"""

from __future__ import annotations

import html
import json
import logging
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
from collect_rules import split_articles, write_json_atomic  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent / "data"
LISTING_PATH = DATA_DIR / "rule_site_listing_260724.json"
OUTPUT_PATH = DATA_DIR / "regulations_corpus_raw.json"
FAILURE_PATH = DATA_DIR / "regulations_failures.jsonl"
LEFTMENU_URL = "http://rule.jnu.ac.kr/Rule/include/LeftMenu.aspx?search=&sm=2&sub={sub}&query="
BODY_URL = "https://www.law.go.kr/LSW/schlPubRulInfoR.do"
VIEW_URL = "https://www.law.go.kr/LSW/schlPubRulInfoP.do?schlPubRulSeq={seq}&chrClsCd={chr_cls}&urlMode=schlPubRulLsInfoP"
USER_AGENT = "Mozilla/5.0"
SEQ_RE = re.compile(r'id="admRulSeq"\s+value\s*=\s*"(\d+)"')
CHR_RE = re.compile(r"chrClsCd=(\d+)")
HEADING_RE = re.compile(r'(?:<h\d[^>]*>|class="[^"]*tit[^"]*"[^>]*>)\s*([^<+][^<]{1,29})')
# law.go.kr 뷰어 UI 잔재 — 본문이 아닌 줄을 걸러낸다.
NOISE_RE = re.compile(r"AJAX|조문목록|체크박스|return false|\.gif|\.png|javascript:")
ADDENDUM_RE = re.compile(r"(?m)^부\s{0,6}칙\b.*$")
# 문서 수준 절단용 — 부칙 헤더 단독 줄(공포번호 동반 포함)부터는 본칙이 아니다.
# 부칙·개정이력 블록의 자체 '제N조'가 본칙과 중복 번호로 적재되는 것을 차단한다
# (진단 실측: 미절단 시 281규정 중 122규정에서 잔여 1,528조문 발생).
DOC_ADDENDUM_RE = re.compile(r"(?m)^부\s{0,6}칙\s*(?:$|[<(（])")


def fetch(url: str, data: dict | None = None, referer: str | None = None, timeout: float = 60.0) -> str:
    headers = {"User-Agent": USER_AGENT}
    if referer:
        headers["Referer"] = referer
    request = Request(url, data=urlencode(data).encode() if data else None, headers=headers)
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def html_to_text(document: str) -> str:
    document = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "\n", document)
    # 엔티티를 먼저 푼 뒤 태그를 지운다. 순서를 뒤집으면 '&lt;td&gt;'처럼 이스케이프된
    # 마크업이 태그 제거를 피해 갔다가 본문에 되살아난다(현재 소스에서는 미발현이나
    # 잠재 결함이라 순서를 고정한다).
    text = html.unescape(document)
    text = re.sub(r"<[^>]+>", "\n", text)
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line or NOISE_RE.search(line):
            continue
        if len(line) == 1 and not line.isalnum():  # 태그 제거 잔재('<' 등)
            continue
        lines.append(line)
    return "\n".join(lines)


# '제25조의2(제목)'이 줄바꿈으로 '제25조' + '2(제목)…'로 쪼개진 경우
SPLIT_UI_RE = re.compile(r"^(\d+)\(([^)\n]{1,40})\)\s*(.*)", re.DOTALL)
# 본문 속 줄머리 조문 인용이 헤더로 오인된 조각의 본문 시작 패턴
FRAGMENT_START = (",", ")", "의 ", "에 ", "와 ", "과 ", "및 ", "내지 ", "부터 ", "까지 ")
FRAGMENT_REF_RE = re.compile(r"^제\d+[항호]")


def normalize_articles(articles: list[dict]) -> list[dict]:
    """분할 사고(조의N 헤더 분리·인용 조각)를 재조립한다.

    제목 없는 항목만 대상으로 한다 — ① 본문이 'N(제목)…'이면 직전 매치가
    '제M조의N' 헤더의 앞부분이었던 것이므로 조문번호·제목을 복원하고,
    ② 본문이 접속 표현('의 …', '제2항…')으로 시작하면 앞 조문 본문에서
    뜯겨 나온 인용 조각이므로 되붙인다(버리면 뒷 본문이 유실된다).
    """
    result: list[dict] = []
    for article in articles:
        if not article["조문제목"]:
            body = article["본문"].lstrip()
            split_ui = SPLIT_UI_RE.match(body)
            if split_ui and "의" not in article["조문번호"][article["조문번호"].index("조"):]:
                article = {
                    **article,
                    "조문번호": f"{article['조문번호']}의{split_ui.group(1)}",
                    "조문제목": split_ui.group(2).strip(),
                    "본문": split_ui.group(3).strip(),
                }
            elif body.startswith(FRAGMENT_START) or FRAGMENT_REF_RE.match(body):
                if result:
                    result[-1]["본문"] = (
                        result[-1]["본문"].rstrip() + "\n" + article["조문번호"] + article["본문"]
                    )
                continue
        result.append(article)
    return result


def category_names() -> dict[int, str]:
    names: dict[int, str] = {}
    for sub in range(1, 9):
        try:
            document = fetch(LEFTMENU_URL.format(sub=sub), timeout=30)
            match = HEADING_RE.search(document)
            names[sub] = match.group(1).strip() if match else f"분류{sub}"
        except Exception:
            names[sub] = f"분류{sub}"
    return names


def append_failure(item: dict, stage: str, error: Exception) -> None:
    record = {
        "수집일시": datetime.now().astimezone().isoformat(timespec="seconds"),
        "title": item["title"],
        "sub": item["sub"],
        "stage": stage,
        "error": str(error),
    }
    with FAILURE_PATH.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    items = json.loads(LISTING_PATH.read_text(encoding="utf-8"))
    categories = category_names()
    logging.info("[분류] %s", categories)

    corpus: list[dict] = []
    done_rules = 0
    failures = 0
    for position, item in enumerate(items, start=1):
        logging.info("[수집] %d/%d %s", position, len(items), item["title"])
        try:
            time.sleep(random.uniform(0.4, 0.8))
            shell = fetch(item["url"], referer="http://rule.jnu.ac.kr/Rule/")
            seq_match = SEQ_RE.search(shell)
            chr_match = CHR_RE.search(shell)
            if not seq_match:
                raise ValueError("admRulSeq를 찾지 못함")
            seq = seq_match.group(1)
            chr_cls = chr_match.group(1) if chr_match else "010202"
            time.sleep(random.uniform(0.4, 0.8))
            body_html = fetch(
                BODY_URL,
                data={"schlPubRulSeq": seq, "chrClsCd": chr_cls},
                referer="https://www.law.go.kr/LSW/schlPubRulInfoP.do",
            )
            text = html_to_text(body_html)
            doc_cut = DOC_ADDENDUM_RE.search(text)
            if doc_cut:
                text = text[: doc_cut.start()]
            articles = split_articles(text)
            if not articles:
                raise ValueError("제N조 패턴을 찾지 못함")
        except Exception as exc:  # 한 규정 실패가 전체 수집을 중단하지 않게 한다.
            failures += 1
            logging.error("[오류] %s: %s", item["title"], exc)
            append_failure(item, "collect", exc)
            continue

        articles = normalize_articles(articles)
        if not articles:
            failures += 1
            append_failure(item, "parse", ValueError("조각 필터 후 조문 없음"))
            continue
        collected_at = datetime.now().astimezone().isoformat(timespec="seconds")
        for article in articles:
            # 다음 '제N조'가 없어 직전 조문 꼬리에 붙는 부칙 헤더 이후를 잘라낸다
            # (부칙의 자체 조문들은 별도 항목으로 이미 분할됨).
            marker = ADDENDUM_RE.search(article["본문"])
            if marker:
                article["본문"] = article["본문"][: marker.start()].rstrip()
            corpus.append(
                {
                    "규정명": item["title"],
                    "편제": f"규정집/{categories.get(item['sub'], item['sub'])}",
                    **article,
                    "source_key": seq,
                    "source_url": VIEW_URL.format(seq=seq, chr_cls=chr_cls),
                    "수집일시": collected_at,
                }
            )
        done_rules += 1
        if position % 20 == 0:
            write_json_atomic(OUTPUT_PATH, corpus)

    write_json_atomic(OUTPUT_PATH, corpus)
    report = {
        "수집_규정_수": done_rules,
        "총_조문_수": len(corpus),
        "실패_건수": failures,
        "출력": str(OUTPUT_PATH),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if done_rules else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""규정 개정 계열(현행+개정전) 수집 — 시점 질의(as_of)용 lineage 코퍼스를 만든다.

규정집 목록의 같은 규정 계열(예: "연구비 중앙관리지침 (2015. 7. 1. 개정전)")을 모아
각 버전에 유효기간 [valid_from, valid_until)을 부여한다. 제목의 "YYYY. M. D. 개정전"은
그 날짜에 개정되었다는 뜻이므로 해당 버전의 유효 종료일이 되고, 계열을 날짜순으로
정렬하면 이전 버전의 종료일이 다음 버전의 시작일이 된다. 현행본은 종료일이 없다.
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import logging
import re
import sys
from pathlib import Path

from collect_rules import (
    LIST_URL,
    download_with_retry,
    http_get,
    make_source_url,
    run_kordoc,
    split_articles,
    write_json_atomic,
)

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
PAIR_RE = re.compile(r"mode=file&key=(\d+)'>([^<]{2,90})</a>")
VERSION_RE = re.compile(
    r"^(.*?)\s*[\(（]\s*(현행본?|[0-9]{4}[.\s]*[0-9]{1,2}[.\s]*[0-9]{1,2}[.]?\s*개정전)\s*[\)）]$"
)
DATE_RE = re.compile(r"([0-9]{4})[.\s]+([0-9]{1,2})[.\s]*([0-9]{1,2})")


def parse_revision_date(label: str) -> str | None:
    """'2015. 7. 1. 개정전' → '2015-07-01'. 현행본이면 None."""
    match = DATE_RE.search(label)
    if not match:
        return None
    year, month, day = (int(g) for g in match.groups())
    return f"{year:04d}-{month:02d}-{day:02d}"


def find_lineage(document: str, rule_name: str) -> list[dict]:
    """목록 HTML에서 rule_name 계열의 (버전 라벨, key) 목록을 뽑는다."""
    versions: list[dict] = []
    for key, raw_title in PAIR_RE.findall(document):
        title = re.sub(r"^\d+\s*[.)]\s*", "", html_mod.unescape(raw_title).strip())
        match = VERSION_RE.match(title)
        if not match or match.group(1).strip() != rule_name:
            continue
        label = match.group(2)
        versions.append({
            "key": key,
            "label": label,
            "revised_on": parse_revision_date(label),  # 개정전 버전의 유효 종료일
        })
    return versions


def assign_validity(versions: list[dict]) -> list[dict]:
    """개정일 순으로 정렬해 각 버전에 [valid_from, valid_until)을 부여한다."""
    dated = sorted(
        (v for v in versions if v["revised_on"]), key=lambda v: v["revised_on"]
    )
    current = [v for v in versions if not v["revised_on"]]
    previous_end: str | None = None
    for version in dated:
        version["valid_from"] = previous_end  # 첫 버전은 시작 미상(None)
        version["valid_until"] = version["revised_on"]
        previous_end = version["revised_on"]
    for version in current:
        version["valid_from"] = previous_end
        version["valid_until"] = None  # 현행
    return dated + current


def collect_lineage(rule_names: list[str], sleep_min: float = 0.5) -> dict:
    raw, _ = http_get(LIST_URL)
    document = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    hwp_dir = DATA_DIR / "hwp_lineage"
    md_dir = DATA_DIR / "markdown_lineage"
    hwp_dir.mkdir(parents=True, exist_ok=True)
    md_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, list[dict]] = {}
    for rule_name in rule_names:
        versions = assign_validity(find_lineage(document, rule_name))
        logging.info("[계열] %s — %d버전", rule_name, len(versions))
        kept: list[dict] = []
        for version in versions:
            key = version["key"]
            hwp_path = hwp_dir / f"key_{key}.hwp"
            md_path = md_dir / f"key_{key}.md"
            try:
                if not hwp_path.exists():
                    download_with_retry(
                        make_source_url(key), hwp_path, sleep_min, sleep_min + 0.5
                    )
                markdown = run_kordoc(hwp_path, md_path)
                articles = split_articles(markdown)
            except Exception as error:  # noqa: BLE001 — 실패 버전은 건너뛰고 계속
                logging.warning("  key=%s 실패: %s", key, error)
                continue
            if not articles:
                logging.warning("  key=%s 조문 0건 — 제외", key)
                continue
            kept.append({
                **{k: version[k] for k in ("key", "label", "valid_from", "valid_until")},
                "source_url": make_source_url(key),
                "articles": articles,
            })
        result[rule_name] = kept
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rules", nargs="+", help="수집할 규정명(정확 일치)")
    parser.add_argument("--output", default=str(DATA_DIR / "lineage_corpus.json"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    lineage = collect_lineage(args.rules)
    write_json_atomic(Path(args.output), lineage)
    total = sum(len(v) for v in lineage.values())
    print(f"계열 {len(lineage)}개 / 버전 {total}개 → {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

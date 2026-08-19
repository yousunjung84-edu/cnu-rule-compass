#!/usr/bin/env python3
"""지침 계층을 게시 목록 전량으로 확장 수집한다 (T30 후속).

배경: 지침 계층은 경진대회 준비기의 `collect_rules.py --limit`로 **관련도 선별
수집**됐다(relevance_score > 0 인 것 중 상한까지). 그래서 게시 179건 중 104건만
코퍼스에 있었고, 진로취업본부·교육혁신본부 등 8개 편제는 0건이었다.
'검색이 못 찾은 것'과 '애초에 수집하지 않은 것'은 사용자에게 전혀 다른 상황이므로,
분모를 좁힌 채 품질을 논하는 것은 의미가 없다.

`collect_rules.py`의 main()은 rules_corpus.json을 **통째로 덮어쓴다** — 규정 계층
281건과 별표 118건이 날아간다. 그래서 여기서는 다운로드·변환·조문분리 함수만
재사용하고, **누락분만 수집해 기존 코퍼스에 병합**한다.

기존 레코드는 건드리지 않는다. record_id는 저장값이 우선하므로 불변이다.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from collect_rules import (  # noqa: E402
    LIST_URL,
    download_with_retry,
    http_get,
    parse_rule_list,
    run_kordoc,
    split_articles,
)

CORPUS = ROOT / "data" / "rules_corpus.json"
HWP_DIR = ROOT / "data" / "hwp"
MD_DIR = ROOT / "data" / "markdown"
SNAPSHOT = ROOT / "data" / "listing_snapshot_latest.json"
FAILURES = ROOT / "data" / "failures_full.jsonl"


def fetch_listing() -> list:
    payload, _ = http_get(LIST_URL, timeout=60.0)
    rules = parse_rule_list(payload.decode("utf-8", errors="replace"))
    SNAPSHOT.write_text(
        json.dumps(
            [
                {"name": r.name, "key": r.key, "division": r.division, "source_url": r.source_url}
                for r in rules
            ],
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    return rules


def _save(corpus: list, added: list) -> None:
    backup = CORPUS.with_suffix(".json.pre_full_guidelines")
    if not backup.exists():
        shutil.copy2(CORPUS, backup)
    CORPUS.write_text(json.dumps(corpus + added, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    have = {str(row.get("source_key")) for row in corpus}

    rules = fetch_listing()
    missing = [rule for rule in rules if rule.key not in have]
    # 현행을 먼저 수집한다 — 중간에 멈춰도 기능적 공백(오늘 오답이 나는 쪽)이 먼저 메워진다.
    # 구판본은 is_current=false로 기본 검색에서 빠지므로 급하지 않다.
    past = re.compile(r"개정\s*전|이전|폐지|\(\s*\d{4}")
    missing.sort(key=lambda rule: (bool(past.search(rule.name)), rule.division, rule.name))
    print(f"게시 {len(rules)}건 / 코퍼스 보유 {len(have & {r.key for r in rules})}건 "
          f"/ 미수집 {len(missing)}건")
    if dry_run:
        for rule in missing:
            print(f"  - [{rule.division}] {rule.name} (key {rule.key})")
        return 0
    if not missing:
        print("추가할 규정이 없습니다(멱등).")
        return 0

    HWP_DIR.mkdir(parents=True, exist_ok=True)
    MD_DIR.mkdir(parents=True, exist_ok=True)
    added: list[dict] = []
    ok_rules = 0
    failures: list[dict] = []
    for position, rule in enumerate(missing, start=1):
        hwp_path = HWP_DIR / f"key_{rule.key}.hwp"
        md_path = MD_DIR / f"key_{rule.key}.md"
        stage = "download"
        try:
            if not hwp_path.exists():
                download_with_retry(rule.source_url, hwp_path, 0.5, 1.0, timeout=60.0)
            stage = "parse"
            markdown = run_kordoc(hwp_path, md_path)
            articles = split_articles(markdown)
            if not articles:
                raise ValueError("제N조 패턴을 찾지 못함")
        except Exception as exc:  # 한 건 실패가 전체를 멈추지 않는다
            failures.append({
                "source_key": rule.key, "규정명": rule.name, "편제": rule.division,
                "stage": stage, "error": str(exc),
            })
            print(f"  [{position}/{len(missing)}] ✗ {rule.name}: {exc}")
            continue

        collected_at = datetime.now().astimezone().isoformat(timespec="seconds")
        for article in articles:
            added.append({
                "규정명": rule.name,
                "편제": rule.division,
                **article,
                "source_key": rule.key,
                "source_url": rule.source_url,
                "수집일시": collected_at,
            })
        ok_rules += 1
        print(f"  [{position}/{len(missing)}] ✓ [{rule.division}] {rule.name} — {len(articles)}조문",
              flush=True)
        if ok_rules % 20 == 0:  # 장시간 수집이므로 중간 저장 (중단해도 진척 보존)
            _save(corpus, added)

    FAILURES.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in failures), encoding="utf-8"
    )
    if added:
        _save(corpus, added)
    print(json.dumps({
        "추가_규정": ok_rules,
        "추가_조문": len(added),
        "실패": len(failures),
        "실패_사유별": {
            reason: sum(1 for row in failures if row["error"].startswith(reason[:12]))
            for reason in {row["error"][:40] for row in failures}
        },
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

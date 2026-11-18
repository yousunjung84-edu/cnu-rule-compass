#!/usr/bin/env python3
"""같은 key 본문 변경 감지 — 월간 갱신의 감지 전용 단계 (2026-11-19 박사 승인).

월간 수집(collect_guidelines_full.py)은 코퍼스에 이미 있는 key를 건너뛴다. 전남대는
개정 때 대개 새 key를 쓰므로 대부분 잡히지만, **같은 key에 첨부만 갈아 끼우면**
daily 목록 비교(이름·날짜 칸)도 월간 수집도 보지 못한다.

현행으로 게시된 key 중 저장 원본(data/hwp/key_<k>.hwp)이 있는 것을 다시 받아
바이트를 비교한다. 2026-11-18 실측: 그대로인 파일은 7월 수집분까지 바이트가
같았다(무작위 5/5). 바이트가 다를 때만 kordoc으로 둘 다 풀어 본문 글자를
비교한다 — 다시 저장만 된 파일을 변경으로 오인하지 않기 위해서다.

코퍼스·저장 원본은 건드리지 않는다. 반영하면 record_id가 다시 매겨지므로 승인
단계로 둔다. 결과는 data/same_key_check_<YYMMDD>.json, 요약은 stdout 마지막 줄 JSON.
exit 0 = 검토할 것 없음, 2 = 검토 필요(본문 바뀜·변환 실패), 1 = 실행 실패.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

CORPUS = ROOT / "data" / "rules_corpus.json"
SNAPSHOT = ROOT / "data" / "listing_snapshot_latest.json"
HWP_DIR = ROOT / "data" / "hwp"
OUT_DIR = ROOT / "data"
REVIEW = ("본문 바뀜", "변환 실패")


def targets(snapshot: list[dict], corpus_keys: set[str], past: set[str]) -> list[dict]:
    """현행 게시 key 중 코퍼스에 있고 저장 원본이 있는 것."""
    out, seen = [], set()
    for row in snapshot:
        key = str(row["key"])
        if (key in seen or key not in corpus_keys or row["name"] in past
                or not (HWP_DIR / f"key_{key}.hwp").exists()):
            continue
        seen.add(key)
        out.append({"key": key, "name": row["name"], "url": row["source_url"]})
    return out


def _download(url: str, dest: Path) -> None:
    from collect_rules import download_with_retry
    download_with_retry(url, dest, 0.5, 1.0, timeout=60.0)


def _text(hwp: Path, md: Path) -> str:
    from collect_rules import clean_markdown_text, run_kordoc
    return " ".join(clean_markdown_text(run_kordoc(hwp, md)).split())


def classify(target: dict, tmp: Path, download=_download, to_text=_text) -> dict:
    key = target["key"]
    old, new = HWP_DIR / f"key_{key}.hwp", tmp / f"key_{key}.hwp"
    result = {"key": key, "규정명": target["name"]}
    try:
        download(target["url"], new)
    except Exception as exc:  # 받기 실패는 변경으로 치지 않는다 — 숫자만 센다
        return {**result, "판정": "받기 실패", "오류": str(exc)[:200]}
    before, after = old.read_bytes(), new.read_bytes()
    if before == after:
        return {**result, "판정": "같음"}
    sizes = {"이전_크기": len(before), "새_크기": len(after)}
    try:
        same = to_text(old, tmp / f"old_{key}.md") == to_text(new, tmp / f"new_{key}.md")
    except Exception as exc:
        return {**result, **sizes, "판정": "변환 실패", "오류": str(exc)[:200]}
    return {**result, **sizes, "판정": "다시 저장만 됨" if same else "본문 바뀜"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="같은 key 본문 변경 감지 (감지 전용)")
    ap.add_argument("--limit", type=int, default=None, help="앞에서 N개만 (시험용)")
    args = ap.parse_args(argv)
    try:
        from refresh_corpus import listed_editions, past_names
        snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        corpus_keys = {str(r.get("source_key")) for r in
                       json.loads(CORPUS.read_text(encoding="utf-8"))}
        rows = targets(snapshot, corpus_keys, past_names(listed_editions()))
    except Exception as exc:
        print(json.dumps({"결과": "실행 실패", "오류": str(exc)[:300]}, ensure_ascii=False))
        return 1
    rows = rows[: args.limit] if args.limit else rows
    with tempfile.TemporaryDirectory() as tmp:
        results = [classify(t, Path(tmp)) for t in rows]
    counts: dict[str, int] = {}
    for r in results:
        counts[r["판정"]] = counts.get(r["판정"], 0) + 1
    out = OUT_DIR / f"same_key_check_{datetime.date.today():%y%m%d}.json"
    detail = {k: [r for r in results if r["판정"] == k]
              for k in ("본문 바뀜", "변환 실패", "다시 저장만 됨", "받기 실패")}
    out.write_text(json.dumps({
        "일시": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "대상": len(rows), "집계": counts, **detail,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    review = sum(counts.get(k, 0) for k in REVIEW)
    print(json.dumps({"결과": "검토 필요" if review else "이상 없음", "대상": len(rows),
                      "집계": counts, "검토_필요": review, "보고서": out.name},
                     ensure_ascii=False))
    return 2 if review else 0


if __name__ == "__main__":
    raise SystemExit(main())

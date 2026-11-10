#!/usr/bin/env python3
"""법제처 학교규정 연혁 머리글 수집 — 「최근 개정일」 필드와 구판 점검의 출처.

규정 계층(law.go.kr 출처)은 월간 자동 재수집 대상이 아니다. 그래서 학교 규정집이
옛 판 ID에 링크돼 있으면 구판이 현행으로 남는다(2026-11-10 학사 24건 검증에서
특별학기 운영 규정 등 25건). 이 스크립트는 코퍼스의 규정 판본 번호마다 연혁 목록
(`schlPubRulHstListR.do`)을 받아 다음을 남긴다.

- 그 판본 자신의 머리글 `[시행 …] [전남대학교학교규정 제n호, 날짜, 개정구분]`
- 연혁상 최신 판본 번호와 머리글(우리 판이 최신이 아니면 stale)

연혁 링크 표기는 'Y'와 'N' 두 가지가 있다(2026-11-10 Codex 지적 — 'Y'만 보면
판정 불능 7건이 「일치」로 셌다).

출력: data/regulation_heads.json (로컬 전용, 다시 받을 수 있음)
사용: python3 scripts/fetch_regulation_heads.py [--only SEQ ...]
"""
import argparse
import datetime
import json
import random
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import collect_regulations as cg  # noqa: E402

CORPUS = ROOT / "data" / "rules_corpus.json"
OUT = ROOT / "data" / "regulation_heads.json"
HIST_URL = "https://www.law.go.kr/LSW/schlPubRulHstListR.do"
REFERER = "https://www.law.go.kr/LSW/schlPubRulInfoP.do"
ROW_RE = re.compile(
    r"schlPubRulViewHst\('\w*'\s*,\s*'(\d+)'.*?(\[시행[^\]]*\]\s*\[[^\]]*\])", re.S)


def parse_history(html: str) -> list[dict]:
    """연혁 목록 → [{seq, head}] (최신이 먼저)."""
    text = re.sub(r"<(?!/?a\b)[^>]+>", " ", html)  # onclick 이 든 <a> 태그만 남긴다
    rows = []
    for seq, head in ROW_RE.findall(text):
        rows.append({"seq": seq, "head": re.sub(r"\s+", " ", head).strip()})
    return rows


def regulation_seqs(corpus: list[dict]) -> dict[str, str]:
    seqs: dict[str, str] = {}
    for row in corpus:
        if "law.go.kr" in str(row.get("source_url", "")):
            seqs.setdefault(str(row["source_key"]), row["규정명"])
    return seqs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", nargs="*", help="이 판본 번호만 다시 받는다(나머지는 기존 결과 유지)")
    args = ap.parse_args()
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    seqs = regulation_seqs(corpus)
    prev = json.loads(OUT.read_text(encoding="utf-8"))["heads"] if OUT.exists() else {}
    targets = sorted(args.only) if args.only else sorted(seqs)
    heads = dict(prev) if args.only else {}  # 전체 재수집이어도 실패한 판본은 prev 값을 쓴다
    errors = 0
    for i, seq in enumerate(targets, 1):
        try:
            html = cg.fetch(HIST_URL, data={"schlPubRulSeq": seq}, referer=REFERER, timeout=30)
            rows = parse_history(html)
            own = next((r["head"] for r in rows if r["seq"] == seq), None)
            if not own:
                # 응답은 왔는데 자기 머리글이 없다(오류 페이지·형식 변경). 기존 정상 자료를
                # 덮어쓰지 않고 실패로 센다(2026-11-10 Codex 코드 리뷰 #7).
                errors += 1
                if (prev.get(seq) or {}).get("head"):
                    heads[seq] = prev[seq]
                    continue
            heads[seq] = {
                "규정명": seqs.get(seq, ""),
                "head": own,
                "latest_seq": rows[0]["seq"] if rows else None,
                "latest_head": rows[0]["head"] if rows else None,
                "n_versions": len(rows),
                "stale": bool(rows) and rows[0]["seq"] != seq,
            }
        except Exception as exc:  # 네트워크 오류는 기록하고 계속 — 기존 정상 자료는 지킨다
            errors += 1
            heads[seq] = prev[seq] if (prev.get(seq) or {}).get("head") else {"규정명": seqs.get(seq, ""), "error": str(exc)[:200]}
        if i % 25 == 0:
            print(f"{i}/{len(targets)}", flush=True)
        time.sleep(random.uniform(0.5, 1.0))
    OUT.write_text(json.dumps({
        "수집일시": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "heads": heads,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    no_head = [s for s, h in heads.items() if "error" not in h and not h.get("head")]
    stale = [s for s, h in heads.items() if h.get("stale")]
    print(f"규정 {len(heads)} · 실패 {errors}(기존 값 유지) · 자기 머리글 없음 {len(no_head)} · 연혁상 구판 {len(stale)}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

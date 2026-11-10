#!/usr/bin/env python3
"""현행 규정·지침 레코드에 규정 단위 「최근 개정일」 필드를 붙인다 (W3 Phase A).

왜: 서버에 규정 단위 개정일 칸이 없어, AIONI가 학사 24건의 개정일 질문 중 14건을
「확인 못 함」으로 답했다(2026-11-10 실측). 원천에는 날짜가 있다. 코어를 바꾸지 않고
코퍼스 레코드에 필드를 더하면 search_rule·get_article 응답에 그대로 실린다
(같은 날 운영 1.10.9에서 확인). list_rules·list_articles 노출은 코어 몫(Phase B).

출처 — 추정하지 않는다. 출처가 없으면 필드를 붙이지 않는다(= 「확인 못 함」).
- 규정(law.go.kr): data/regulation_heads.json 의 **그 판본 자신의** 머리글
  `[시행 날짜] [전남대학교학교규정 제n호, 날짜, 개정구분]` (scripts/fetch_regulation_heads.py)
- 지침(학교 사이트): 두 가지를 대조한다.
  ① 같은 이름 구판의 「(날짜 개정전)」 표기 — 그 날짜에 현행판으로 개정됐다는 뜻
     (코퍼스 구판 이름 + data/listing_snapshot_latest.json 학교 목록. 코퍼스엔 구판 일부만 있다)
  ② 원문 md(data/markdown/key_<key>.md)의 부칙 표제 날짜
  ③ 원문 머리의 제·개정 이력 줄('제정 2014. 09. 29.', '| 개정 2024. 07. 26. |')
     — 부칙 표제에 날짜가 없거나 목록 표기에 일이 빠진 지침이 있다(2026-11-10 Codex 대조:
       공익신고 운영지침은 부칙 최신이 2020. 6. 1.인데 이력 줄은 2024. 7. 26.).
  가장 늦은 날짜를 쓰고, 출처마다 날짜가 다르면 근거에 모두 적는다.
  ②③은 본 규정 부분(별표·별지·서식·참고 표제 앞)만 읽고, 시행일·적용일 표제는 쓰지 않는다.

붙이는 필드(현행 규정의 모든 레코드에 같은 값):
  규정_최근개정일 · 규정_개정구분(규정만) · 규정_시행일(규정만) · 규정_개정일_근거
현행이 아닌 레코드에서는 이 필드를 지운다(다시 돌려도 결과가 같다).

사용: python3 scripts/annotate_revision_dates.py [--dry-run]
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "data" / "rules_corpus.json"
HEADS = ROOT / "data" / "regulation_heads.json"
MARKDOWN = ROOT / "data" / "markdown"
LISTING = ROOT / "data" / "listing_snapshot_latest.json"  # 월간 갱신이 남기는 학교 지침 목록(구판 포함)
FIELDS = ("규정_최근개정일", "규정_개정구분", "규정_시행일", "규정_개정일_근거")

DATE = r"((?:19|20)\d{2})\s*\.\s*(\d{1,2})\s*[.\s]\s*(\d{1,2})\s*\.?"
HEAD_RE = re.compile(
    r"\[시행\s*(?P<eff>[^\]]+?)\]\s*\[(?P<body>[^\]]*?),\s*(?P<date>(?:19|20)\d{2}\.[^,\]]+?),\s*(?P<kind>[^\],]+?)\s*\]")
# 구판 이름 꼬리: '(2025. 10. 1. 개정전)', '(2015. 7 14. 개정전)', 오탈자 '(… 개전전)'
OLD_LABEL_RE = re.compile(r"^(?P<base>.+?)\s*\((?P<note>[^)]*\d{4}[^)]*개[정전]\s*전[^)]*)\)\s*$")
# 부칙 표제 실제 표기(2026-11-10 지침 650건): '### 부칙(2024. 7. 15.)', '부 칙 <2024.7.15.>',
# '부칙(제정 2005. 5. 1)', '부칙(개정 2011. 1. 24)', '부칙 <제1호, 2009. 3. 1>',
# '**부 칙(2024. 10. 16.)**', '부 칙(시행일 2022. 10. 28.)'. 괄호 안 날짜 앞 낱말은 건너뛴다.
# 서식 자리표시 '부 칙(제OOOO호, 2022. 0. 0.)'은 날짜 검사에서 떨어진다.
ADDENDA_RE = re.compile(r"^\s*(?:#+\s*)?\**\s*부\s*칙\s*[(<（](?P<pre>[^)>）]*?)" + DATE)


def valid(y, m, d) -> bool:
    return 1 <= int(m) <= 12 and 1 <= int(d) <= 31


def fmt(y, m, d) -> str:
    return f"{int(y)}. {int(m)}. {int(d)}."


def key(date_str: str) -> tuple[int, int, int]:
    y, m, d = re.match(DATE, date_str).groups()
    return int(y), int(m), int(d)


def parse_head(head: str) -> dict | None:
    """법제처 머리글 → {시행일, 공포일, 구분}. 형식이 다르면 None."""
    m = HEAD_RE.search(head or "")
    if not m:
        return None
    eff, date = re.search(DATE, m["eff"]), re.search(DATE, m["date"])
    if not eff or not date:
        return None
    return {"시행일": fmt(*eff.groups()), "공포일": fmt(*date.groups()), "구분": m["kind"].strip()}


def norm_name(name: str) -> str:
    return re.sub(r"[\s「」『』]", "", name)


def old_label_dates(names: set[str]) -> dict[str, list[str]]:
    """정규화한 기본 이름 → 구판 「(날짜 개정전)」 날짜 목록."""
    out: dict[str, list[str]] = {}
    for name in names:
        m = OLD_LABEL_RE.match(name)
        if not m:
            continue
        d = re.search(DATE, m["note"])
        if d and valid(*d.groups()):
            out.setdefault(norm_name(m["base"]), []).append(fmt(*d.groups()))
    return out


# 본 규정 뒤에 붙은 별표·별지·서식·참고 법령은 다른 문서다. 그 아래의 부칙·이력 줄은
# 인용된 다른 법령이나 서식 견본의 날짜일 수 있어 읽지 않는다(2026-11-10 Codex 코드 리뷰 #3).
TAIL_HEADING_RE = re.compile(
    r"^\s*(?:#+\s*)?\**\s*[\[<【]\s*(?:별\s*표|별\s*지|서\s*식)"            # [별표 1] <별지 제1호> 【서식】
    r"|^\s*#+\s*\**\s*(?:별\s*표|별\s*지|서\s*식|붙\s*임|참고\s*법령|관련\s*법령)")  # ### 별표 1 · # 참고 법령


def own_text(md_text: str) -> str:
    """원문 md에서 본 규정 부분만(첫 별표·별지·서식·참고 표제 앞까지)."""
    lines = md_text.splitlines()
    for i, line in enumerate(lines):
        if TAIL_HEADING_RE.match(line):
            return "\n".join(lines[:i])
    return md_text


def addenda(md_text: str) -> list[tuple[str, str]]:
    """부칙 표제 → [(날짜, 구분)]. 구분은 표제 줄에 '제정'이 있으면 제정, 아니면 빈 값."""
    out = []
    for line in md_text.splitlines():
        m = ADDENDA_RE.match(line)
        # '부 칙(시행일 2022. 10. 28.)'·'(적용일 …)'은 시행·적용 날짜다 — 개정일로 쓰지 않는다
        # (2026-11-10 Codex 코드 리뷰 #2).
        if m and valid(*m.groups()[1:4]) and not re.search(r"시행|적용", m["pre"]):
            out.append((fmt(*m.groups()[1:4]), "제정" if "제정" in line else ""))  # '부 칙(2024.12.10. 제정)'처럼 뒤에도 온다
    return out


# 이력 줄: 줄 전체가 '제정|개정 날짜'(표 칸 테두리 '|'만 허용). 조문 안 '<개정 …>' 표시와 구별된다.
# 실제 표기(2026-11-10 Codex 2차 대조): '3차 개정 2024.07.11.', '제10차 개정 2025. 9. 18.',
# '일부개정 2025. 12. 08.', '전부개정 2022. 12. 16.', '2014. 9. 1. 개정'(날짜가 앞).
KIND = r"(?:제?\d+\s*차\s*)?(?:제정|일부개정|전부개정|개정)"
EDGE = r"\s*(?:\|[\s|]*)?$"
HISTORY_RE = re.compile(r"^\s*\|?\s*(?P<kind>" + KIND + r")\s+" + DATE + EDGE)
HISTORY_TAIL_RE = re.compile(r"^\s*\|?\s*" + DATE + r"\s*(?P<kind>" + KIND + r")" + EDGE)


def history_lines(md_text: str) -> list[tuple[str, str]]:
    out = []
    for line in md_text.splitlines():
        m = HISTORY_RE.match(line)
        if m:
            ymd = m.groups()[1:4]
        else:
            m = HISTORY_TAIL_RE.match(line)
            ymd = m.groups()[0:3] if m else None
        if m and valid(*ymd):
            out.append((fmt(*ymd), "제정" if "제정" in m["kind"] else "개정"))
    return out


def addenda_dates(md_text: str) -> list[str]:
    return [d for d, _ in addenda(md_text)]


def regulation_fields(seq: str, heads: dict) -> dict | None:
    h = heads.get(seq) or {}
    parsed = parse_head(h.get("head") or "")
    if not parsed:
        return None
    return {
        "규정_최근개정일": parsed["공포일"],
        "규정_개정구분": parsed["구분"],
        "규정_시행일": parsed["시행일"],
        "규정_개정일_근거": f"법제처 연혁 머리글 {h['head']}",
    }


def guideline_fields(name: str, source_key: str, labels: dict[str, list[str]]) -> dict | None:
    md = MARKDOWN / f"key_{source_key}.md"
    text = own_text(md.read_text(encoding="utf-8", errors="replace")) if md.exists() else ""
    found = addenda(text)
    hist = history_lines(text)
    sources = []  # (설명, 그 출처의 가장 늦은 날짜, 그 날짜의 구분)
    lab = labels.get(norm_name(name), [])
    if lab:
        d = max(lab, key=key)
        sources.append((f"학교 지침 목록의 구판 표기 「({d} 개정전)」", d, "개정"))
    if found:
        d = max((x[0] for x in found), key=key)
        sources.append((f"원문 부칙({d})", d, "제정" if any(x == (d, "제정") for x in found) else ""))
    if hist:
        d = max((x[0] for x in hist), key=key)
        kind = "개정" if any(x == (d, "개정") for x in hist) else "제정"
        sources.append((f"원문 제·개정 이력 줄({kind} {d})", d, kind))
    if not sources:
        return None
    best = max((s_[1] for s_ in sources), key=key)
    basis = " · ".join(s_[0] for s_ in sources)
    dates = {s_[1] for s_ in sources}
    if len(dates) > 1:
        # 어느 쪽이 맞는지 기계로 가릴 수 없다(2026-11-10: 하루 차이·연도만 다름·목록에 구판 누락).
        # 가장 늦은 날짜를 쓰되 모두 밝혀 원문 확인으로 넘긴다.
        basis += f" — 출처마다 날짜가 다름({' / '.join(sorted(dates, key=key))}), 가장 늦은 날짜를 씀, 원문 확인 필요"
    out = {"규정_최근개정일": best, "규정_개정일_근거": basis}
    kinds = {s_[2] for s_ in sources if s_[1] == best}
    # 제정 뒤 개정 흔적이 없을 때만 '제정'이라 밝힌다(개정 흔적이 하나라도 있으면 구분을 비운다).
    if kinds == {"제정"} or (kinds <= {"제정", ""} and "제정" in kinds and not lab):
        out["규정_개정구분"] = "제정"
    return out


def current_rules(path: Path) -> set[tuple[str, str]]:
    """코어 적재 판정으로 현행인 (규정명, source_key). 서버와 같은 규칙을 쓴다."""
    from core.search import RuleSearchIndex

    rows = RuleSearchIndex(path).articles
    rows = list(rows.values()) if isinstance(rows, dict) else list(rows)
    return {(r["규정명"], str(r["source_key"])) for r in rows if r.get("is_current", True)}


def annotate(corpus: list[dict], current: set, heads: dict) -> dict:
    names = {r["규정명"] for r in corpus}
    if LISTING.exists():
        names |= {x["name"] for x in json.loads(LISTING.read_text(encoding="utf-8"))}
    labels = old_label_dates(names)
    cache: dict[tuple[str, str], dict | None] = {}
    stats = {"현행규정": 0, "필드부착규정": 0, "규정층": 0, "지침층": 0, "출처없음": [], "부착레코드": 0, "날짜불일치": []}
    for rule in sorted(current):
        name, sk = rule
        is_reg = sk.startswith("22000")  # 법제처 학교규정 판본 번호
        f = regulation_fields(sk, heads) if is_reg else guideline_fields(name, sk, labels)
        cache[rule] = f
        stats["현행규정"] += 1
        if f:
            stats["필드부착규정"] += 1
            stats["규정층" if is_reg else "지침층"] += 1
            if "출처마다 날짜가 다름" in f["규정_개정일_근거"]:
                stats["날짜불일치"].append(name)
        else:
            stats["출처없음"].append(name)
    for row in corpus:
        for k in FIELDS:
            row.pop(k, None)
        f = cache.get((row["규정명"], str(row["source_key"])))
        if f:
            row.update(f)
            stats["부착레코드"] += 1
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="코퍼스를 쓰지 않고 통계만 낸다")
    args = ap.parse_args()
    if not HEADS.exists():
        print("data/regulation_heads.json 없음 — scripts/fetch_regulation_heads.py 먼저", file=sys.stderr)
        return 2
    heads = json.loads(HEADS.read_text(encoding="utf-8"))["heads"]
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    current = current_rules(CORPUS)
    # 현행 규정의 판본 번호에 머리글이 없으면 필드를 지운 채 성공하지 않는다
    # (2026-11-10 Codex 코드 리뷰 #1). 새 판본을 수집했으면 머리글부터 받는다.
    missing = sorted(k for _, k in current if k.startswith("22000") and not (heads.get(k) or {}).get("head"))
    if missing:
        print(f"[개정일] 현행 규정 {len(missing)}건의 법제처 머리글이 없다 — 코퍼스를 쓰지 않고 멈춘다. "
              f"python3 scripts/fetch_regulation_heads.py --only {' '.join(missing[:20])}", file=sys.stderr)
        return 3
    stats = annotate(corpus, current, heads)
    if not args.dry_run:
        CORPUS.write_text(json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: (v if not isinstance(v, list) else {"수": len(v), "예": v[:15]})
                      for k, v in stats.items()}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

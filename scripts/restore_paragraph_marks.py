"""적재된 본칙 조문에 **빠진 항 번호(①~⑳)만** 끼워 넣는다 (2026-11-02).

왜 필요한가 — kordoc 4.12 는 일부 HWP 에서 자동 번호 문단의 항 번호를 내지
않았다. 「지침 제9조 ④항에 근거하여」가 「제9조 항에 근거하여」로, 「② 총장은 …」이
「총장은 …」으로 적재됐다. 4.24 는 번호를 낸다. 2026-11-02 실측(4.24.38 로 703파일
전량 재변환해 서빙 본문과 대조): HWP 경로 본칙 10,376건 중 205건(77규정)·370개.

왜 reparse_articles.py 로 안 하나 — 그 도구는 본문을 **통째로** 새 파싱으로 바꾼다.
205건 중 26건은 새 파싱 본문 꼬리에 「제4장 …」 장 제목이 붙는다(apply_structure_titles
가 이미 걷어 낸 것). 통째 교체는 후처리를 되돌린다 — 그 도구가 exit 3 으로 거부하는
바로 그 경우다. 그래서 여기서는 **기존 본문을 한 글자도 바꾸지 않고** 번호만 넣는다.

불변식 (건마다 기계 확인, 하나라도 어기면 그 레코드는 손대지 않는다):
  1. 결과에서 넣은 항 번호(와 바로 뒤 공백 1칸)를 빼면 기존 본문과 **정확히** 같다.
  2. 넣는 글자는 ①~⑳ 과 그 뒤 공백뿐이다.
  3. record_id·revision·조문번호·조문제목은 그대로다(「기존 발급 ID 재계산 금지」).

사용:
    python3 scripts/restore_paragraph_marks.py --fresh-dir <4.24 변환 md 폴더> --dry-run
    python3 scripts/restore_paragraph_marks.py --fresh-dir <폴더>
`<폴더>/key_<source_key>.md` 를 읽는다. 적용 뒤에는 change_gate 승인·재배포가 남는다.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("RULE_COMPASS_DATA_DIR", str(ROOT / "data"))
from collect_rules import clean_markdown_text, split_articles  # noqa: E402

MARK = re.compile(r"[①-⑳]")
BOLD = re.compile(r"\*\*(.+?)\*\*")
_STAMP = datetime.now().strftime("%y%m%d_%H%M%S")


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", BOLD.sub(r"\1", MARK.sub("", text)))


def best_match(record: dict, candidates: list[dict]) -> dict | None:
    """같은 조문번호·제목 중 본문이 가장 비슷한 것. 목차 줄(본문 없음)이 먼저 나오는 문서가 있다."""
    same = [a for a in candidates
            if a["조문번호"] == record.get("조문번호") and a["조문제목"] == record.get("조문제목")]
    if not same:
        return None
    target = _squash(str(record.get("본문", "")))
    return max(same, key=lambda a: difflib.SequenceMatcher(
        None, target, _squash(a["본문"]), autojunk=False).ratio())


def insert_marks(old: str, fresh: str) -> tuple[str, list[str]]:
    """old 에 fresh 쪽에만 있는 항 번호를 끼운다. (결과, 넣은 번호들)

    비교는 공백·굵게 표시를 뺀 글자열끼리 한다 — 4.24 는 문단 사이 빈 줄 수가
    달라 원문 위치 그대로는 맞지 않는다. 넣을 자리는 old 의 「공백 아닌 글자」
    순번으로 되돌려 찾고, 번호 뒤 공백 여부는 fresh 원문을 따른다
    (「② 총장은」 은 공백, 「제9조 ④항」 은 공백 없음).
    """
    a = [(i, ch) for i, ch in enumerate(old) if not ch.isspace()]
    plain = BOLD.sub(r"\1", fresh)
    b = [(j, ch) for j, ch in enumerate(plain) if not ch.isspace()]
    a_txt = "".join(ch for _, ch in a)
    b_txt = "".join(ch for _, ch in b)
    annex = ANNEX_HEAD.search(old)
    annex_at = annex.start() if annex else len(old)
    inserts: list[tuple[int, str]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a_txt, b_txt, autojunk=False).get_opcodes():
        seg = b_txt[j1:j2]
        if tag != "insert" or not seg or MARK.sub("", seg):
            continue  # 그 밖의 차이(장 제목 꼬리·글자 차이)는 무시한다 — 기존 본문을 지킨다.
        # 앞뒤 문맥이 양쪽에서 같을 때만 넣는다 — 「제9조 항」에 새판 「제8조 ②항」의
        # ② 를 붙이면 문자 보존 검사는 통과하지만 인용이 틀린다(Codex 검증 #3).
        if (a_txt[max(0, i1 - CONTEXT):i1] != b_txt[max(0, j1 - CONTEXT):j1]
                or a_txt[i1:i1 + CONTEXT] != b_txt[j2:j2 + CONTEXT]):
            continue
        end = b[j2 - 1][0] + 1
        space = " " if end < len(plain) and plain[end].isspace() else ""
        pos = a[i1][0] if i1 < len(a) else len(old)
        if pos > annex_at:
            # 본칙 레코드에 붙어 온 별표·별지의 번호는 항 번호가 아니다(Codex 검증 #2:
            # 국외여행 업무처리지침 제23조 뒤 【별표 1】 ①~⑥). 범위 밖이라 넣지 않는다.
            continue
        inserts.append((pos, seg + space))
    out, last, added = [], 0, []
    for pos, piece in inserts:
        out += [old[last:pos], piece]
        added.extend(MARK.findall(piece))
        last = pos
    out.append(old[last:])
    return "".join(out), added


# 줄 머리에서 시작하는 별표·별지·서식 머리. 본문 속 「[별표 1]에 따른다」 같은 참조는 제외된다.
ANNEX_HEAD = re.compile(r"(?m)^[ \t]*[\[【〔<][ \t]*(?:별표|별지|서식)")
CONTEXT = 6  # 삽입 자리 앞뒤로 같아야 하는 글자 수(공백 제외)

INSERT_ONLY = re.compile(r"^[\u2460-\u2473]+ ?$")


def only_mark_inserts(old: str, new: str) -> bool:
    """불변식 1·2: old→new 의 모든 차이가 「항 번호(+공백 0~1칸) 삽입」인가."""
    ops = difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes()
    return all(t == "equal" or (t == "insert" and INSERT_ONLY.match(new[j1:j2]))
               for t, _, _, j1, j2 in ops) and MARK.sub("", new).replace(" ", "") == MARK.sub("", old).replace(" ", "")


def main() -> int:
    ap = argparse.ArgumentParser(description="빠진 항 번호만 끼워 넣는다")
    ap.add_argument("--fresh-dir", type=Path, required=True, help="kordoc 4.24 변환 md 폴더")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    corpus = ROOT / "data" / "rules_corpus.json"
    rows = json.loads(corpus.read_text(encoding="utf-8"))
    assert isinstance(rows, list), "코퍼스가 list가 아니다 — 형식이 바뀌었으면 멈춘다"

    cache: dict[str, list[dict] | None] = {}
    changes, refused, out = [], [], []
    for r in rows:
        if r.get("record_type") != "본칙":
            out.append(r); continue
        key = str(r.get("source_key"))
        if key not in cache:
            md = a.fresh_dir / f"key_{key}.md"
            cache[key] = (split_articles(clean_markdown_text(md.read_text(encoding="utf-8", errors="replace")))
                          if md.exists() else None)
        fresh = best_match(r, cache[key]) if cache[key] else None
        old = str(r.get("본문", ""))
        if not fresh or len(MARK.findall(fresh["본문"])) <= len(MARK.findall(old)):
            out.append(r); continue
        new, added = insert_marks(old, fresh["본문"])
        want = len(MARK.findall(fresh["본문"])) - len(MARK.findall(old))
        ok = bool(added) and only_mark_inserts(old, new)
        entry = {"source_key": key, "규정명": r.get("규정명"), "조문번호": r.get("조문번호"),
                 "record_id": r.get("record_id"), "넣은_번호": "".join(added),
                 "새판_대비": f"{len(added)}/{want}",
                 "삽입": [{"앞": old[max(0, i1 - 20):i1], "번호": new[j1:j2], "뒤": old[i1:i1 + 20]}
                          for t, i1, _, j1, j2 in difflib.SequenceMatcher(
                              None, old, new, autojunk=False).get_opcodes() if t == "insert"]}
        if not ok:
            refused.append(entry); out.append(r); continue
        changes.append(entry)
        out.append(dict(r, 본문=new))  # record_id·revision·조문번호·조문제목 그대로

    summary = {"대상_레코드": len(changes), "거부": len(refused),
               "넣은_번호": sum(len(c["넣은_번호"]) for c in changes),
               "규정": len({c["규정명"] for c in changes}),
               "부분_복원": sum(1 for c in changes if c["새판_대비"].split("/")[0] != c["새판_대비"].split("/")[1])}
    tag = "dryrun" if a.dry_run else "applied"
    ev = ROOT / "data" / f"paragraph_marks_evidence_{_STAMP}_{tag}.json"
    ev.write_text(json.dumps({"요약": summary, "변경": changes, "거부": refused},
                             ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    print(f"증거: {ev.relative_to(ROOT)}")
    if a.dry_run:
        return 0
    backup = corpus.with_suffix(f".json.pre_marks_{_STAMP}")
    shutil.copy2(corpus, backup)
    corpus.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"적용 완료 · 백업 {backup.name}")
    print("⚠️ change_gate 승인·재배포가 남았다 — 이 도구는 게이트를 대신하지 않는다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

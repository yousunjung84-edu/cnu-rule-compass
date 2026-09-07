"""이미 적재된 조문을 **새 파서로 다시 파싱**해 코퍼스에 소급 반영한다.

왜 필요한가 — 수집 파이프라인은 증분이다. collect_guidelines_full.py는
「누락분만 수집해 병합, 기존 레코드는 건드리지 않는다」. 그래서 파서를 고쳐도
**이미 적재된 조문은 새 규칙을 영영 타지 않는다.** 재수집을 몇 번 돌려도 같다
(2026-09-07 확인: 9/1 새벽 잡 조문 변경 0인데 attachment_cut은 정상 동작).

파서 수정이 데이터에 닿게 하려면 이 도구로 대상 규정을 지정해 재파싱한다.
마크다운 캐시를 쓰므로 네트워크가 필요 없다.

사용:
    python3 scripts/reparse_articles.py --keys 4197,4196 --dry-run
    python3 scripts/reparse_articles.py --keys 4197,4196
그 뒤 change_gate 승인이 필요하다(소실이 발생하므로 설계대로 정지한다).

★ 첫 사용 = P10, 백포트 #2(attachment_cut)를 데이터에 소급 적용.

⚠️⚠️ **이 도구는 파이프라인의 일부만 재현한다.** split_articles만 부르므로
수집 이후의 후처리(apply_structure_titles 등)를 **되돌린다**. 후처리를 거친
조문에 돌리면 장 제목이 본문 꼬리에 다시 붙는다 — 2026-09-07 실측: 연구비
중앙관리지침 등 24판본에서 본문이 **늘어나는** 변경 156건이 전부 「제2장 …」
헤더 유입이었다. P10 대상 10판본은 후처리 전이라 안전했다(짧아짐만 9건).

  → dry-run에서 **본문이 늘어나는 건이 있으면 멈추고 사람이 본다.**
    이 도구는 그 경우 종료코드 3으로 거부한다.

왜 재수집으로 안 되나: collect_guidelines_full.py는 「누락분만 수집해 병합, 기존
레코드는 건드리지 않는다」는 증분 설계다. 그래서 코드를 이식해도(15c044a) 이미
적재된 조문은 새 파서를 영영 타지 않는다. 큐 문서의 「재수집 때 이뤄진다」는
예고가 성립하지 않았다(2026-09-07 확인, 9/1 새벽 잡 조문 변경 0).

무엇을 하나: 대상 10판본만 마크다운 캐시에서 재파싱해
  - 재파싱에 없는 본칙 레코드(별지 서식 견본)를 제거하고
  - 남는 레코드는 **record_id를 승계**한 채 본문만 갱신한다.
record_id를 새로 발급하지 않는 것은 「기존 발급 ID 재계산 금지」 제약이고,
apply_structure_titles가 이미 만든 관행(ID 동결 뒤 본문 변경)과도 같다.

본칙만 손댄다 — 별표·부칙·항목은 다른 경로가 소유한다.
"""
from __future__ import annotations
import json, os, shutil, sys
from pathlib import Path

# 이 스크립트가 있는 레포를 대상으로 한다 — 경로를 박으면 다른
# 체크아웃에서 실행해도 원래 운영 경로를 건드린다(2026-09-07 Codex #5).
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("RULE_COMPASS_DATA_DIR", str(ROOT / "data"))
from collect_rules import split_articles  # noqa: E402

_STAMP = __import__("datetime").datetime.now().strftime("%y%m%d_%H%M%S")


def _write_evidence(removed, grown, *, dry: bool) -> None:
    """삭제·증가 목록을 **실행마다 새 파일**로 남긴다.

    예전에는 같은 이름에 덮어써서, 적용 뒤 멱등성만 확인해도 원래 삭제 목록이
    사라졌다 — 사후 승인을 검토할 증거가 없어진다(2026-09-07 Codex #4).
    """
    tag = "dryrun" if dry else "applied"
    (ROOT / "data" / f"reparse_evidence_{_STAMP}_{tag}.json").write_text(
        json.dumps({"keys": list(KEYS), "제거": removed, "본문_증가": grown},
                   ensure_ascii=False, indent=1), encoding="utf-8")

# P10 대상(연구조교 지침 10판본). --keys 로 덮어쓸 수 있다.
DEFAULT_KEYS = ("3384", "3609", "3654", "3716", "3783", "3902", "4051", "4195", "4196", "4197")
KEYS = DEFAULT_KEYS
for _i, _a in enumerate(sys.argv):
    if _a.startswith("--keys="):
        KEYS = tuple(k.strip() for k in _a.split("=", 1)[1].split(",") if k.strip())
    elif _a == "--keys":
        # 값 누락을 조용히 기본값으로 흘리지 않는다 — 단일 규정만 처리하려던
        # 명령이 기본 10판본에 적용되던 결함(2026-09-07 Codex #6).
        if _i + 1 >= len(sys.argv) or sys.argv[_i + 1].startswith("-"):
            print("[오류] --keys 뒤에 쉼표로 구분한 key가 필요하다", file=sys.stderr)
            raise SystemExit(2)
        KEYS = tuple(k.strip() for k in sys.argv[_i + 1].split(",") if k.strip())
if not KEYS:
    print("[오류] 대상 key가 비어 있다", file=sys.stderr); raise SystemExit(2)
CORPUS = ROOT / "data" / "rules_corpus.json"
rows = json.loads(CORPUS.read_text(encoding="utf-8"))
assert isinstance(rows, list), "코퍼스가 list가 아니다 — 형식이 바뀌었으면 멈춘다"

reparsed = {}
for key in KEYS:
    md = ROOT / "data" / "markdown" / f"key_{key}.md"
    if not md.exists():
        print(f"[중단] key_{key} 마크다운 없음", file=sys.stderr); raise SystemExit(2)
    by_no = {}
    for a in split_articles(md.read_text(encoding="utf-8", errors="replace")):
        by_no.setdefault(str(a.get("조문번호")), a)
    reparsed[key] = by_no

out, removed, grown, updated = [], [], [], 0
seen: dict[tuple[str, str], bool] = {}
for r in rows:
    key = str(r.get("source_key"))
    if key not in reparsed or r.get("record_type") != "본칙":
        out.append(r); continue
    no = str(r.get("조문번호"))
    fresh = reparsed[key].get(no)
    if fresh is None or (key, no) in seen:
        removed.append({"source_key": key, "조문번호": no,
                        "record_id": r.get("record_id"),
                        "본문_앞": str(r.get("본문", ""))[:60]})
        continue
    seen[(key, no)] = True
    if str(r.get("본문")) != str(fresh.get("본문")):
        if len(str(fresh.get("본문"))) > len(str(r.get("본문"))):
            # 본문이 **늘어나는** 것은 후처리를 되돌리는 신호다(2026-09-07 실측).
            grown.append({"source_key": key, "조문번호": no,
                          "before": len(str(r.get("본문"))),
                          "after": len(str(fresh.get("본문"))),
                          "추가분": str(fresh.get("본문"))[len(str(r.get("본문"))):][:120]})
        r = dict(r)
        r["본문"] = fresh.get("본문")          # record_id·revision은 그대로 승계
        if fresh.get("조문제목"):
            r["조문제목"] = fresh.get("조문제목")
        updated += 1
    out.append(r)

print(json.dumps({"이전": len(rows), "이후": len(out),
                  "제거": len(removed), "본문_갱신": updated}, ensure_ascii=False))
if "--dry-run" in sys.argv:
    _write_evidence(removed, grown, dry=True)
    raise SystemExit(3 if grown else 0)
if grown:
    # 후처리를 되돌리는 변경이다. 사람이 보기 전에는 쓰지 않는다.
    _write_evidence(removed, grown, dry=True)
    print(f"[거부] 본문이 늘어나는 변경 {len(grown)}건 — 후처리를 되돌리는 신호다. "
          f"증거: data/reparse_evidence_*.json", file=sys.stderr)
    raise SystemExit(3)

# 백업은 도구가 뜬다 — 사람이 기억하는 절차에 의존하지 않는다(Codex #1).
backup = CORPUS.with_suffix(f".json.pre_reparse_{_STAMP}")
shutil.copy2(CORPUS, backup)
CORPUS.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
_write_evidence(removed, grown, dry=False)
print(f"적용 완료 · 백업 {backup.name}")
print("⚠️ change_gate 승인이 남았다 — 이 도구는 게이트를 대신하지 않는다.")

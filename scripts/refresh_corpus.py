#!/usr/bin/env python3
"""코퍼스 월간 갱신 오케스트레이터 (지침 계층, 2026-08-18 AIONI 상시 운영 전환 후속).

수동 수집 상태로 두면 규정 개정이 반영되지 않아 **AIONI에서 구판 답변이 나간다.**
게시 패턴 실측(2026-08-18): 개정 시 구판이 "(개정전)" 이름의 새 key로 아카이브되고
**현행본도 새 key를 받는다**(기초보호학문 4597→4598→4599→4833). 따라서

1. 신규 key 수집만으로 개정본은 자동으로 들어온다 — 그러나
2. 코퍼스에 남은 **옛 key의 규정명은 무표기 그대로**라, 새 현행본과 같은 이름의
   '현행'이 둘 생긴다(현행 중복 = 구판 텍스트를 현행으로 인용하는 사고).

그래서 수집 전에 **게시 목록과 이름을 대사(reconcile)한다**: 코퍼스의 key가 목록에서
다른 이름(대개 "(개정전)" 표기)으로 바뀌었으면 게시자의 표기를 그대로 옮겨 적는다 —
이름을 만들어내는 것이 아니라 게시자의 지정을 복사하는 것이며, record_id는 저장값이
우선하므로 불변이다. key가 목록에서 사라진 경우는 추정하지 않고 보고만 한다.

순서: 대사 → 신규 수집 → 정규화 체인 → 대조표 → **테스트 게이트**(실패 시 코퍼스
원복) → 리포트·알림. **배포는 자동으로 하지 않는다** — 무인 시점에 Docker 데몬이
없을 수 있고, 운영 서비스 교체는 확인 후가 원칙이다. 성공 시 배포 명령을 리포트에 적는다.

규정 계층(law.go.kr)은 v1 범위 밖 — 과거 부칙 중복 병합 사고 이력이 있어 자동
재수집하지 않는다. 분기별 수동 수집을 권한다(README·리포트에 명시).
"""

from __future__ import annotations

import datetime
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv" / "bin" / "python")
CORPUS = ROOT / "data" / "rules_corpus.json"
SNAPSHOT = ROOT / "data" / "listing_snapshot_latest.json"
LOG = ROOT / "data" / "corpus_refresh.log"
REPORT = ROOT / "data" / "corpus_refresh_report.json"


def log(msg: str) -> None:
    line = f"{datetime.datetime.now().astimezone().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(args: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
    log(f"[실행] {' '.join(str(a) for a in args)}")
    return subprocess.run(
        args, cwd=ROOT, capture_output=True, text=True, timeout=timeout
    )


def corpus_summary() -> dict:
    rows = json.loads(CORPUS.read_text(encoding="utf-8"))
    return {"조문": len(rows), "규정": len({r["규정명"] for r in rows})}


def reconcile_names() -> dict:
    """게시 목록 기준으로 코퍼스 규정명을 대사한다 (현행 중복 방지의 핵심).

    최신 스냅샷은 collect_guidelines_full.py가 방금 받아 둔 것을 쓰지 않고
    여기서 직접 받는다 — 대사가 수집보다 먼저여야 하기 때문이다.
    """
    sys.path.insert(0, str(ROOT))
    from collect_rules import LIST_URL, http_get, parse_rule_list  # noqa: E402

    payload, _ = http_get(LIST_URL, timeout=60.0)
    rules = parse_rule_list(payload.decode("utf-8", errors="replace"))
    SNAPSHOT.write_text(
        json.dumps(
            [{"name": r.name, "key": r.key, "division": r.division, "source_url": r.source_url}
             for r in rules],
            ensure_ascii=False, indent=1,
        ),
        encoding="utf-8",
    )
    listing = {r.key: r.name for r in rules}

    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    renamed: dict[str, tuple[str, str]] = {}
    missing_keys: dict[str, str] = {}
    for row in corpus:
        key = str(row.get("source_key", ""))
        if not key.isdigit() or len(key) > 6:
            continue  # 지침 계층(짧은 숫자 key)만 — 규정 계층은 v1 범위 밖
        posted = listing.get(key)
        if posted is None:
            missing_keys.setdefault(key, row["규정명"])
            continue
        if posted != row["규정명"]:
            renamed.setdefault(key, (row["규정명"], posted))
            row["규정명"] = posted  # 게시자의 표기를 그대로 옮긴다 (생성 아님)
    if renamed:
        CORPUS.write_text(json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")
    for key, (old, new) in renamed.items():
        log(f"[대사] key {key}: {old!r} → {new!r}")
    for key, name in list(missing_keys.items())[:10]:
        log(f"[대사·보고만] key {key} 목록에서 사라짐 (코퍼스명 {name!r}) — 추정 개명 안 함")
    return {"개명": len(renamed), "목록_소실": len(missing_keys),
            "목록_소실_목록": missing_keys}


def main() -> int:
    started = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    log("=== 코퍼스 갱신 시작 ===")
    before = corpus_summary()
    backup = CORPUS.with_suffix(f".json.refresh-backup")
    shutil.copy2(CORPUS, backup)

    report: dict = {"시작": started, "이전": before}
    try:
        report["대사"] = reconcile_names()

        steps = [
            ("수집", [PY, "scripts/collect_guidelines_full.py"]),
            ("record_type", [PY, "scripts/normalize_record_types.py"]),
            ("장절", [PY, "scripts/apply_structure_titles.py"]),
            ("별표", [PY, "scripts/collect_attachments.py"]),
            ("별표제목", [PY, "scripts/fill_attachment_titles.py", "--apply"]),
            ("항목식", [PY, "scripts/collect_item_style.py"]),
            ("대조표", [PY, "scripts/coverage_report.py"]),
        ]
        for name, args in steps:
            result = run(args)
            if result.returncode != 0:
                raise RuntimeError(f"{name} 실패 (exit {result.returncode}): {result.stderr[-400:]}")
            log(f"[완료] {name}")

        gate = run([PY, "-m", "unittest", "discover", "-s", "tests", "-t", "."], timeout=1800)
        tail = (gate.stderr or gate.stdout).strip().splitlines()[-1] if (gate.stderr or gate.stdout) else ""
        report["테스트"] = tail
        if gate.returncode != 0:
            raise RuntimeError(f"테스트 게이트 실패: {tail}")
        log(f"[게이트] {tail}")

    except Exception as exc:
        # 어느 단계든 실패하면 코퍼스를 통째로 되돌린다 — 반쯤 갱신된 코퍼스로
        # 배포 명령이 나가는 것이 최악이다.
        shutil.copy2(backup, CORPUS)
        report["결과"] = f"실패·원복: {exc}"
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"[실패] {exc} — 코퍼스 원복 완료")
        subprocess.run(["osascript", "-e",
            'display notification "갱신 실패 — 코퍼스 원복됨, corpus_refresh.log 확인" with title "규정 나침반 월간 갱신"'],
            capture_output=True)
        return 1

    after = corpus_summary()
    changed = after != before or report["대사"]["개명"] > 0
    report["이후"] = after
    report["변경"] = {"조문": after["조문"] - before["조문"], "규정": after["규정"] - before["규정"]}
    if changed:
        report["결과"] = "성공·배포 대기"
        report["배포_명령"] = (
            "docker buildx build --platform linux/amd64 -t asia-northeast3-docker.pkg.dev/"
            "academyinfo-mcp-2026/cnu-rule-compass/cnu-rule-compass:corpus-"
            f"{datetime.date.today():%y%m%d} --push . && gcloud run deploy cnu-rule-compass "
            "--region=asia-northeast3 --image=asia-northeast3-docker.pkg.dev/academyinfo-mcp-2026/"
            f"cnu-rule-compass/cnu-rule-compass:corpus-{datetime.date.today():%y%m%d} --quiet"
        )
        note = f"신규 {report['변경']['규정']}규정 {report['변경']['조문']}조문 — 배포 대기 (리포트 참조)"
    else:
        report["결과"] = "변경 없음"
        note = "게시 변경 없음 — 배포 불필요"
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"[종료] {report['결과']} {report.get('변경','')}")
    subprocess.run(["osascript", "-e",
        f'display notification "{note}" with title "규정 나침반 월간 갱신"'], capture_output=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

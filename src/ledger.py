"""적재 대장(ingest ledger) — '역전된 승인' 모델의 기록 계층.

전량 사람 승인(모든 조문을 승인해야 색인) 대신, 자동 무결성 검증을 기본으로 두고
검증을 통과하지 못한 이탈분만 사람 승인 큐로 올린다. 대장은 어떤 코퍼스가 언제,
어떤 검증 규칙으로, 몇 건 수락/거부되어 적재됐는지를 추적한다 — "이 답변의 근거는
이렇게 검증됐다"를 답변 밖에서 증명하는 장부다.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LEDGER_PATH = _ROOT / "data" / "ingest_ledger.json"
DEFAULT_REVIEW_PATH = _ROOT / "data" / "pending_review.json"

# 적재 검증 규칙의 판(version). 규칙이 바뀌면 올린다 — 대장 항목이 어떤 기준으로
# 검증됐는지 재현할 수 있게 한다.
VALIDATION_RULES_VERSION = "v1"
VALIDATION_RULES = (
    "empty_body: 빈 본문 제외",
    "oversized_body: 30,000자 초과 본문 제외",
    "invalid_source_url: HTTPS·jnu.ac.kr·key 일치 검증 실패 제외",
    "duplicate_record: 동일 record_id 중복 제외",
)


def corpus_fingerprint(corpus_path: str | Path) -> str:
    """코퍼스 파일의 SHA-256 지문 — 같은 코퍼스의 중복 대장 기록을 막는 키."""
    digest = hashlib.sha256()
    with Path(corpus_path).open("rb") as file:
        for chunk in iter(lambda: file.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def _read_json(path: Path, default):
    try:
        with path.open(encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError):
        return default


def record_ingest(
    index,
    ledger_path: str | Path = DEFAULT_LEDGER_PATH,
    review_path: str | Path = DEFAULT_REVIEW_PATH,
) -> dict:
    """인덱스 적재 결과를 대장에 기록하고, 거부분을 승인 큐로 노출한다.

    같은 코퍼스 지문이 이미 기록돼 있으면 기존 항목을 반환한다(중복 기록 방지).
    반환값은 이번(또는 기존) 대장 항목이다.
    """
    ledger_path = Path(ledger_path)
    review_path = Path(review_path)
    fingerprint = corpus_fingerprint(index.corpus_path)
    entries = _read_json(ledger_path, [])
    for entry in entries:
        if entry.get("corpus_fingerprint") == fingerprint:
            return entry

    rejected = getattr(index, "rejected_articles", [])
    reasons: dict[str, int] = {}
    for row in rejected:
        reason = str(row.get("reason", "unknown"))
        reasons[reason] = reasons.get(reason, 0) + 1
    entry = {
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "corpus_path": Path(index.corpus_path).name,
        "corpus_fingerprint": fingerprint,
        "rules_version": VALIDATION_RULES_VERSION,
        "rules": list(VALIDATION_RULES),
        "accepted": len(index.articles),
        "rejected": len(rejected),
        "rejected_by_reason": reasons,
    }
    entries.append(entry)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    # 이탈분 승인 큐 — 사람이 검토해 '보류 유지/수동 편입(수정 후 재수집)'을 결정한다.
    # 본문 전문 대신 식별 정보와 사유만 노출한다(큐 파일 비대화·민감 노출 방지).
    review_rows = [
        {
            "reason": row.get("reason", "unknown"),
            "규정명": row.get("article", {}).get("규정명", ""),
            "조문번호": row.get("article", {}).get("조문번호", ""),
            "record_id": row.get("article", {}).get("record_id", ""),
            "source_url": row.get("article", {}).get("source_url", ""),
            "status": "pending",
        }
        for row in rejected
    ]
    review_path.write_text(
        json.dumps(
            {"corpus_fingerprint": fingerprint, "items": review_rows},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    return entry

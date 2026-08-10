#!/usr/bin/env python3
"""코퍼스에 편제 구조(장/절)를 부여하고 본문에 흡수된 제목을 분리한다 (T4).

⚠️ record_id 동결이 먼저다. 규정 계층 레코드는 record_id가 저장돼 있지 않고
본문 해시에서 파생되므로, 본문을 고치면 이미 발급된 ID가 바뀐다. 그래서
**현재 본문 기준으로 record_id·revision·record_type을 먼저 계산해 코퍼스에 박은 뒤**
본문을 정리한다. 이후 로더는 저장된 값을 그대로 쓴다(prepare_article 우선순위).

멱등이다. 두 번 돌려도 결과가 같다.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.search import prepare_article  # noqa: E402
from src.structure import apply_to_corpus  # noqa: E402

CORPUS = Path(__file__).resolve().parent.parent / "data" / "rules_corpus.json"


def main() -> int:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))

    frozen = 0
    for article in corpus:
        if article.get("record_id") and article.get("revision") and article.get("record_type"):
            continue
        prepared = prepare_article(article)
        article["record_type"] = prepared["record_type"]
        article["revision"] = prepared["revision"]
        article["record_id"] = prepared["record_id"]
        frozen += 1

    before_ids = [a["record_id"] for a in corpus]
    processed, stats = apply_to_corpus(corpus)
    after_ids = [a["record_id"] for a in processed]
    if before_ids != after_ids:
        print("[중단] record_id가 변동했습니다 — 적용하지 않았습니다.", file=sys.stderr)
        return 1

    backup = CORPUS.with_suffix(".json.pre_structure")
    if not backup.exists():
        shutil.copy2(CORPUS, backup)
    CORPUS.write_text(json.dumps(processed, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"record_id_동결": frozen, **stats, "백업": str(backup)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""CNU 규정 나침반 운영 로그 저장소.

스레드·프로세스 잠금 안에서 임시 파일을 쓴 뒤 원자적으로 교체한다. 질의와
응답 문자열은 이 계층에서 다시 마스킹하므로 호출부가 실수해도 원문 PII가
파일에 남지 않는다.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from src import pii
from src.search import RuleSearchIndex, get_default_index

try:
    import fcntl
except ImportError:  # Windows에서는 스레드 잠금과 원자 교체를 사용한다.
    fcntl = None

try:
    import msvcrt
except ImportError:
    msvcrt = None


_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUNTIME_DIR = Path(
    os.environ.get("RULECOMPASS_RUNTIME_DIR", "").strip() or _ROOT / "runtime"
)
_FILES = {
    "queries": "query_logs.json",
    "candidates": "knowledge_candidates.json",
}
_LOCK = threading.Lock()
_SOURCE_FIELDS = ("record_id", "source_key", "규정명", "조문번호", "source_url", "revision")


class StoreCorruptionError(RuntimeError):
    """운영 로그가 손상되어 자동 쓰기를 중단했음을 나타낸다."""


class JsonStore:
    """운영 컬렉션을 JSON 배열로 관리한다."""

    def __init__(
        self,
        runtime_dir: str | Path = DEFAULT_RUNTIME_DIR,
        index: RuleSearchIndex | None = None,
    ) -> None:
        self.runtime_dir = Path(runtime_dir)
        self.index = index

    def _path(self, collection: str) -> Path:
        try:
            filename = _FILES[collection]
        except KeyError as exc:
            raise ValueError(f"알 수 없는 컬렉션: {collection}") from exc
        return self.runtime_dir / filename

    @contextlib.contextmanager
    def _locked(self):
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            lock_path = self.runtime_dir / ".store.lock"
            with lock_path.open("a+b") as lock_file:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                elif msvcrt is not None:
                    lock_file.seek(0, os.SEEK_END)
                    if lock_file.tell() == 0:
                        lock_file.write(b"0")
                        lock_file.flush()
                    lock_file.seek(0)
                    while True:
                        try:
                            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                            break
                        except OSError:
                            time.sleep(0.05)
                try:
                    yield
                finally:
                    if fcntl is not None:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                    elif msvcrt is not None:
                        lock_file.seek(0)
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)

    @staticmethod
    def _backup_corrupt(path: Path) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = path.with_name(f"{path.name}.corrupt-{timestamp}.bak")
        shutil.copy2(path, backup)
        return backup

    def read(self, collection: str) -> list[dict]:
        """컬렉션을 읽는다. 손상 시 원본을 백업하고 예외로 자동 쓰기를 막는다."""
        path = self._path(collection)
        if not path.exists():
            return []
        try:
            with path.open(encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            backup = self._backup_corrupt(path)
            raise StoreCorruptionError(f"손상 로그를 보존했습니다: {backup}") from exc
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            backup = self._backup_corrupt(path)
            raise StoreCorruptionError(f"로그 스키마가 손상되어 보존했습니다: {backup}")
        return data

    def _write(self, collection: str, items: list[dict]) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        path = self._path(collection)
        descriptor, temporary = tempfile.mkstemp(
            dir=self.runtime_dir, prefix=f".{path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                json.dump(items, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        except BaseException:
            try:
                os.remove(temporary)
            except OSError:
                pass
            raise

    @staticmethod
    def _next_id(prefix: str, items: list[dict]) -> str:
        maximum = 0
        for item in items:
            value = str(item.get("id", ""))
            if not value.startswith(prefix + "-"):
                continue
            try:
                maximum = max(maximum, int(value.rsplit("-", 1)[1]))
            except ValueError:
                continue
        return f"{prefix}-{maximum + 1:04d}"

    def _verified_sources(self, source_rows: object) -> tuple[list[dict], list[str]]:
        """입력 sources를 신뢰하지 않고 코퍼스 record_id로 공식 필드만 재구성한다."""
        if not isinstance(source_rows, list):
            return [], []
        index = self.index or get_default_index()
        catalog = {article["record_id"]: article for article in index.articles}
        sources: list[dict] = []
        pii_kinds: list[str] = []
        for row in source_rows:
            if not isinstance(row, dict):
                continue
            article = catalog.get(str(row.get("record_id", "")))
            if article is None:
                continue
            rebuilt = {field: article.get(field, "") for field in _SOURCE_FIELDS}
            masked, found = pii.redact_value(rebuilt)
            sources.append(masked)
            pii_kinds.extend(found)
        return sources, list(dict.fromkeys(pii_kinds))

    def add_query(self, question: str, answer_result: dict) -> dict:
        """질의·응답을 마스킹해 기록한다. 원문 문자열은 저장하지 않는다."""
        masked_question, question_kinds = pii.redact(question)
        masked_response, response_kinds = pii.redact(answer_result.get("text", ""))
        sources, source_kinds = self._verified_sources(answer_result.get("sources", []))
        with self._locked():
            items = self.read("queries")
            item = {
                "id": self._next_id("query", items),
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "question": masked_question,
                "answered": bool(answer_result.get("answered")),
                "response": masked_response,
                "backend": str(answer_result.get("backend", "none")),
                "sources": sources,
                "pii_kinds": list(dict.fromkeys(question_kinds + response_kinds + source_kinds)),
            }
            items.append(item)
            self._write("queries", items)
        return item

    def register_candidate(self, question: str) -> dict:
        """미확인 질의를 지식 후보로 축적하고 동일 질의는 횟수만 올린다."""
        masked, kinds = pii.redact(question)
        normalized = " ".join(masked.lower().split())
        with self._locked():
            items = self.read("candidates")
            for item in items:
                if item.get("normalized_question") == normalized:
                    item["asked_count"] = int(item.get("asked_count", 1)) + 1
                    item["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    self._write("candidates", items)
                    return dict(item)
            item = {
                "id": self._next_id("candidate", items),
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "question": masked,
                "normalized_question": normalized,
                "asked_count": 1,
                "status": "pending",
                "pii_kinds": kinds,
            }
            items.append(item)
            self._write("candidates", items)
        return item


_DEFAULT_STORE: JsonStore | None = None


def get_default_store() -> JsonStore:
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = JsonStore()
    return _DEFAULT_STORE


def read(collection: str) -> list[dict]:
    return get_default_store().read(collection)


def record_query(question: str, answer_result: dict) -> dict:
    return get_default_store().add_query(question, answer_result)


def register_candidate(question: str) -> dict:
    return get_default_store().register_candidate(question)

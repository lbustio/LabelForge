"""Implementacion JSON atomica del puerto RunRepository."""

from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path

from labelforge_core.domain.run_record import RunRecord, validate_run_id


class JsonRunRepository:
    def __init__(self, root: Path):
        self._root = Path(root)
        self._lock = threading.RLock()

    def _path(self, run_id: str) -> Path:
        return self._root / f"{validate_run_id(run_id)}.json"

    def save(self, record: RunRecord) -> None:
        path = self._path(record.run_id)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(f".json.{uuid.uuid4().hex}.tmp")
            with open(tmp, "w", encoding="utf-8") as stream:
                json.dump(record.to_dict(), stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            tmp.replace(path)

    def get(self, run_id: str) -> RunRecord | None:
        try:
            path = self._path(run_id)
            with self._lock, open(path, "r", encoding="utf-8") as stream:
                return RunRecord.from_dict(json.load(stream))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def list(self) -> list[RunRecord]:
        if not self._root.exists():
            return []
        records = []
        with self._lock:
            paths = list(self._root.glob("*.json"))
        for path in paths:
            try:
                with open(path, "r", encoding="utf-8") as stream:
                    records.append(RunRecord.from_dict(json.load(stream)))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return sorted(
            records,
            key=lambda item: str(item.completed_at or item.updated_at or ""),
            reverse=True,
        )

    def list_by_owner(self, user_id: int) -> list[RunRecord]:
        owner_id = int(user_id)
        return [record for record in self.list() if record.owner and record.owner.user_id == owner_id]

    def find_by_output_file(self, output_file: str) -> RunRecord | None:
        safe_name = Path(str(output_file or "")).name
        if not safe_name:
            return None
        return next((record for record in self.list() if record.output_file == safe_name), None)

    def delete(self, run_id: str) -> bool:
        try:
            path = self._path(run_id)
            with self._lock:
                if path.exists():
                    path.unlink()
                    return True
        except (OSError, ValueError):
            pass
        return False

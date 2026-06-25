"""
checkpoint_io.py
----------------
Autoridad unica para los archivos de checkpoint/runtime de LabelForge.

Este modulo no decide reglas de negocio del pipeline; solo encapsula los
formatos durables compartidos por CLI y webapp.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from labelforge_core.paths import CHECKPOINTS_DIR


log = logging.getLogger(__name__)

CHECKPOINT_PATH = CHECKPOINTS_DIR / "checkpoint.json"
FAILED_QUEUE_PATH = CHECKPOINTS_DIR / "failed_queue.jsonl"
RUN_STATE_PATH = CHECKPOINTS_DIR / "run_state.json"
CLAIM_RESULTS_PATH = CHECKPOINTS_DIR / "claim_results.jsonl"
ARBITER_RESULTS_PATH = CHECKPOINTS_DIR / "arbiter_results.jsonl"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def append_jsonl_durable(path: Path, item: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def load_jsonl_latest(path: Path, key_fields: tuple) -> dict:
    latest = {}
    if not path.exists():
        return latest
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = tuple(str(item.get(k, "")) for k in key_fields)
            latest[key] = item
    return latest


def clear_runtime_checkpoints(
    include_run_state: bool = False,
    include_claim_results: bool = False,
) -> list[str]:
    # claim_results.jsonl is preserved by default so a subsequent run with a
    # higher limit can skip already-processed entries without recomputing them.
    # Pass include_claim_results=True only for --fresh or explicit user resets.
    paths = [CHECKPOINT_PATH, FAILED_QUEUE_PATH, ARBITER_RESULTS_PATH]
    if include_claim_results:
        paths.append(CLAIM_RESULTS_PATH)
    if include_run_state:
        paths.append(RUN_STATE_PATH)

    cleared = []
    for path in paths:
        try:
            if path.exists():
                path.unlink()
                cleared.append(path.name)
        except OSError as exc:
            log.warning("No se pudo limpiar %s: %s", path.name, exc)
    return cleared


def load_run_state() -> dict:
    if not RUN_STATE_PATH.exists():
        return {}
    try:
        with open(RUN_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_run_state(state: dict) -> None:
    write_json_atomic(RUN_STATE_PATH, state)


def append_claim_result(row_index: int, record: dict, status: str = "ok") -> None:
    append_jsonl_durable(CLAIM_RESULTS_PATH, {
        "row_index": row_index,
        "status": status,
        "updated_at": now_iso(),
        "record": record,
    })


def load_claim_results() -> dict:
    rows = {}
    for key, item in load_jsonl_latest(CLAIM_RESULTS_PATH, ("row_index",)).items():
        rows[key[0]] = item.get("record", {})
    return rows


def append_arbiter_result(row_index: int, arbiter_id: str, result: dict) -> None:
    append_jsonl_durable(ARBITER_RESULTS_PATH, {
        "row_index": row_index,
        "arbiter_id": arbiter_id,
        "updated_at": now_iso(),
        "result": result,
    })


def load_arbiter_results() -> dict:
    partial = {}
    for key, item in load_jsonl_latest(ARBITER_RESULTS_PATH, ("row_index", "arbiter_id")).items():
        row_index, arbiter_id = key
        partial.setdefault(row_index, {})[arbiter_id] = item.get("result", {})
    return partial


def load_checkpoint() -> dict:
    results = {}
    last_index = -1
    if CHECKPOINT_PATH.exists():
        with open(CHECKPOINT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        results.update(data.get("results", {}))
        last_index = int(data.get("last_index", -1))

    incremental_results = load_claim_results()
    if incremental_results:
        results.update(incremental_results)
        last_index = max(last_index, max(int(k) for k in incremental_results))

    if last_index >= 0:
        log.info("Checkpoint: hasta indice %s, %s registros persistidos", last_index, len(results))
    return {"last_index": last_index, "results": results}


def save_checkpoint(last_index: int, results: dict) -> None:
    write_json_atomic(CHECKPOINT_PATH, {
        "last_index": last_index,
        "updated_at": now_iso(),
        "results": results,
    })
    log.debug("Checkpoint guardado en indice %s", last_index)


def append_failed_queue(row_index: int, row: dict, reason: str = "") -> None:
    append_jsonl_durable(FAILED_QUEUE_PATH, {
        "index": row_index,
        "row": row,
        "reason": reason,
    })


def append_to_failed_queue(row_index: int, row: dict, reason: str = "") -> None:
    append_failed_queue(row_index, row, reason)


def load_failed_queue() -> list:
    if not FAILED_QUEUE_PATH.exists():
        return []
    seen = {}
    with open(FAILED_QUEUE_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            seen[item["index"]] = item
    return list(seen.values())


def clear_failed_queue() -> None:
    try:
        FAILED_QUEUE_PATH.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("No se pudo limpiar %s: %s", FAILED_QUEUE_PATH.name, exc)

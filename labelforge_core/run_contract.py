"""Contrato semantico para decidir si una ejecucion puede reanudarse."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


_SECRET_MARKERS = ("key", "secret", "token", "password", "credential")


def _without_secrets(value):
    if isinstance(value, dict):
        return {
            str(key): _without_secrets(item)
            for key, item in value.items()
            if not any(marker in str(key).lower() for marker in _SECRET_MARKERS)
        }
    if isinstance(value, list):
        return [_without_secrets(item) for item in value]
    return value


def input_file_identity(path_value: str | Path) -> dict:
    path = Path(path_value)
    identity = {"path": str(path).replace("\\", "/"), "size": None, "mtime_ns": None}
    try:
        stat = path.stat()
        identity["size"] = stat.st_size
        identity["mtime_ns"] = stat.st_mtime_ns
    except OSError:
        pass
    return identity


def build_resume_contract(
    cfg: dict,
    *,
    input_file: str,
    sheet: str,
    text_column: str,
    total_rows: int,
) -> dict:
    task = cfg.get("task", {})
    payload = {
        "dataset": {
            "input": input_file_identity(input_file),
            "sheet": str(sheet or ""),
            "text_column": str(text_column or ""),
            "total_rows": int(total_rows),
        },
        "task": _without_secrets({
            "principles": task.get("principles", {}),
            "user_prompt_template": task.get("user_prompt_template", ""),
            "system_prompt": task.get("system_prompt", ""),
            "include_justifications": bool(task.get("include_justifications", False)),
        }),
        "arbiters": _without_secrets(cfg.get("arbiters", [])),
        "processing": _without_secrets(cfg.get("processing", {})),
        "safety": _without_secrets(cfg.get("safety", {})),
    }
    # El limite no cambia el significado de una evaluacion. Se compara aparte
    # para permitir ampliar una corrida, pero nunca recortarla al reanudar.
    fingerprint_payload = json.loads(json.dumps(payload, ensure_ascii=False))
    fingerprint_payload["dataset"].pop("total_rows", None)
    encoded = json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "version": 2,
        "fingerprint": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "summary": {
            "input_file": payload["dataset"]["input"]["path"],
            "sheet": payload["dataset"]["sheet"],
            "text_column": payload["dataset"]["text_column"],
            "total_rows": payload["dataset"]["total_rows"],
            "principle_count": len(payload["task"]["principles"]),
            "arbiter_ids": [str(item.get("id", "")) for item in payload["arbiters"]],
            "execution_order": payload["processing"].get("execution_order", "row_major"),
        },
    }


def compare_resume_contract(saved: dict | None, current: dict) -> dict:
    if not saved or not saved.get("fingerprint"):
        return {
            "compatible": False,
            "legacy": True,
            "reason": "El checkpoint fue creado sin contrato de compatibilidad.",
        }
    semantic_match = saved.get("fingerprint") == current.get("fingerprint")
    saved_total = int(saved.get("summary", {}).get("total_rows", 0) or 0)
    current_total = int(current.get("summary", {}).get("total_rows", 0) or 0)
    limit_compatible = current_total >= saved_total
    compatible = semantic_match and limit_compatible
    if compatible and current_total > saved_total:
        reason = f"La configuracion coincide; el objetivo se ampliara de {saved_total} a {current_total} entradas."
    elif not semantic_match:
        reason = "El dataset o la configuracion que afecta los resultados cambio."
    elif not limit_compatible:
        reason = f"No se puede reducir el objetivo reanudable de {saved_total} a {current_total} entradas."
    else:
        reason = "La configuracion coincide."
    return {
        "compatible": compatible,
        "legacy": False,
        "reason": reason,
        "saved": saved.get("summary", {}),
        "current": current.get("summary", {}),
    }

"""
saptiva_batch.py
----------------
Modo por lotes para arbitros ``saptiva`` usando la API de lotes de InferNode
(https://docs.saptiva.com/usuario/modelos/lotes).

Flujo por arbitro (un archivo = un endpoint y un modelo):

  1. Construye un JSONL con una linea por entrada (custom_id = ``row-<indice>``).
  2. Sube el archivo (purpose=batch) y crea el lote (completion_window=24h).
  3. Consulta todos los lotes cada ``poll_seconds`` hasta un estado terminal.
  4. Descarga resultados y errores, casa por custom_id y normaliza a 0/1.

Las lineas del lote no gastan rpm; si gastan rpm subir, crear y consultar.
No hay descuento por lote: cada linea cuesta lo mismo que la llamada en linea.

El estado (ids de archivos y lotes) se guarda en checkpoints/ para que una
segunda ejecucion con el mismo dataset y config retome los lotes vivos en vez
de crearlos de nuevo. InferNode ademas deduplica archivos y lotes identicos.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import time
from pathlib import Path
from typing import Callable, Optional

from labelforge_core import saptiva
from labelforge_core.llm_json import normalize_label_result, parse_llm_json

log = logging.getLogger(__name__)

TERMINAL_STATUSES = {"completed", "failed", "expired", "cancelled"}
MAX_LINES_PER_FILE = 50_000
MAX_BYTES_PER_FILE = 95 * 1024 * 1024  # limite documentado: 100 MiB
MAX_LINE_BYTES = 1024 * 1024           # limite documentado: 1 MiB por linea


def custom_id_for(row_index: int) -> str:
    return f"row-{row_index}"


def row_index_from(custom_id: str) -> Optional[int]:
    if not isinstance(custom_id, str) or not custom_id.startswith("row-"):
        return None
    try:
        return int(custom_id[4:])
    except ValueError:
        return None


def build_batch_lines(arbiter: dict, prompts: dict[int, str]) -> list[str]:
    """Una linea JSONL por entrada. ``prompts`` mapea indice de fila -> prompt de usuario."""
    lines = []
    for row_index in sorted(prompts):
        body = saptiva.build_chat_body(arbiter, prompts[row_index])
        line = json.dumps(
            {
                "custom_id": custom_id_for(row_index),
                "method": "POST",
                "url": saptiva.CHAT_ENDPOINT,
                "body": body,
            },
            ensure_ascii=False,
        )
        if len(line.encode("utf-8")) > MAX_LINE_BYTES:
            raise ValueError(
                f"La entrada {row_index} excede 1 MiB en el archivo de lotes. "
                "Baja processing.claim_max_chars."
            )
        lines.append(line)
    return lines


def chunk_lines(lines: list[str], max_lines: int = MAX_LINES_PER_FILE, max_bytes: int = MAX_BYTES_PER_FILE) -> list[list[str]]:
    """Parte en archivos que respetan los limites de lineas y bytes por archivo."""
    chunks: list[list[str]] = []
    current: list[str] = []
    current_bytes = 0
    for line in lines:
        size = len(line.encode("utf-8")) + 1
        if current and (len(current) >= max_lines or current_bytes + size > max_bytes):
            chunks.append(current)
            current, current_bytes = [], 0
        current.append(line)
        current_bytes += size
    if current:
        chunks.append(current)
    return chunks


def parse_result_line(line: dict, principles: list[str]) -> tuple[Optional[int], Optional[dict], str]:
    """
    Convierte una linea del archivo de salida o de errores.
    Devuelve (indice_fila, resultado_normalizado | None, motivo_de_error).
    """
    row_index = row_index_from(line.get("custom_id", ""))
    error = line.get("error")
    response = line.get("response") or {}
    status = response.get("status_code")
    body = response.get("body") or {}

    if error or status != 200:
        detail = error or body.get("error") or body
        return row_index, None, f"lote: status={status} error={json.dumps(detail, ensure_ascii=False)[:240]}"

    try:
        choice = (body.get("choices") or [{}])[0]
        content = saptiva.extract_content(choice.get("message"), choice.get("finish_reason"))
        result, _mode = parse_llm_json(content.strip(), principles)
        normalize_label_result(result, principles, f"lote {line.get('custom_id')}")
    except Exception as exc:  # respuesta invalida: se reintenta en linea
        return row_index, None, f"respuesta invalida: {type(exc).__name__}: {str(exc)[:200]}"

    result["_usage"] = saptiva.usage_dict(body.get("usage"))
    result["_latency_s"] = -1  # el lote no expone latencia por linea
    return row_index, result, ""


def _jsonl(text: str) -> list[dict]:
    out = []
    for raw in (text or "").splitlines():
        raw = raw.strip()
        if raw:
            out.append(json.loads(raw))
    return out


def run_fingerprint(input_file: str, cfg: dict, total: int) -> str:
    """Huella de la corrida: contenido del dataset, task, arbitros y recorte del texto.

    Si cambia cualquiera de esos datos (aunque el archivo se llame igual y tenga
    las mismas filas) los lotes previos no se retoman.
    """
    h = hashlib.sha256()
    try:
        with open(input_file, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
    except OSError:
        h.update(str(input_file).encode("utf-8"))
    payload = json.dumps(
        {
            "total": int(total),
            "task": cfg.get("task", {}),
            "claim_max_chars": cfg.get("processing", {}).get("claim_max_chars"),
            "arbiters": [
                {
                    "id": a["id"],
                    "model": a["provider"].get("model"),
                    "base_url": saptiva.resolve_base_url(a["provider"]),
                    "system_prompt": a.get("system_prompt"),
                    "generation_config": a.get("generation_config"),
                }
                for a in cfg.get("arbiters", [])
            ],
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    h.update(payload.encode("utf-8"))
    return h.hexdigest()[:16]


class BatchState:
    """Persistencia ligera de ids de lotes para poder reanudar."""

    def __init__(self, path: Path, fingerprint: str):
        self.path = Path(path)
        self.fingerprint = fingerprint
        self.data = {"fingerprint": fingerprint, "jobs": {}}
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                if saved.get("fingerprint") == fingerprint:
                    self.data = saved
                else:
                    log.warning("Estado de lotes previo corresponde a otra corrida; se ignora.")
            except Exception:
                log.warning("No se pudo leer %s; se ignora.", self.path)

    def job(self, key: str) -> dict:
        return self.data["jobs"].setdefault(key, {})

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def clear(self):
        if self.path.exists():
            self.path.unlink()


def _is_transient(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status is not None:
        return status == 429 or status >= 500
    name = type(exc).__name__
    return name in {"APIConnectionError", "APITimeoutError"} or "timeout" in name.lower()


def _submit(client, arb: dict, key: str, rows: list[int], prompts: dict[int, str], job: dict, state: "BatchState"):
    """Sube el archivo (una sola vez) y crea el lote, guardando cada id en cuanto existe."""
    if not job.get("input_file_id"):
        lines = build_batch_lines(arb, {i: prompts[i] for i in rows})
        content = ("\n".join(lines) + "\n").encode("utf-8")
        uploaded = client.files.create(file=(f"labelforge_{key.replace('#', '_')}.jsonl", io.BytesIO(content)), purpose="batch")
        job["input_file_id"] = uploaded.id
        state.save()
    batch = client.batches.create(
        input_file_id=job["input_file_id"],
        endpoint=saptiva.CHAT_ENDPOINT,
        completion_window="24h",
        metadata={"app": "labelforge", "arbiter": str(arb["id"]), "part": key.partition("#")[2]},
    )
    job.update({"batch_id": batch.id, "status": batch.status})
    state.save()
    log.info("[%s] Lote creado %s con %d lineas (modelo %s)", key, batch.id, len(rows), job.get("model"))


def run_batches(
    arbiters: list,
    prompts_by_arbiter: dict[str, dict[int, str]],
    principles: list[str],
    resolve_key: Callable[[dict], str],
    state: BatchState,
    poll_seconds: float = 30.0,
    client_factory: Callable[[str, str], object] = saptiva.make_client,
    sleep: Callable[[float], None] = time.sleep,
    on_progress: Optional[Callable[[dict], None]] = None,
    on_result: Optional[Callable[[int, str, dict], None]] = None,
    max_consecutive_poll_errors: int = 20,
) -> tuple[dict, dict]:
    """
    Envia uno o varios lotes por arbitro, espera y descarga.

    ``prompts_by_arbiter`` mapea id de arbitro -> {indice_fila: prompt}; permite
    no reenviar lo que un arbitro ya resolvio en una corrida previa.

    Cada lote guarda en el estado las filas que envio. Al reanudar se usan esos
    lotes tal cual, aunque el conjunto de filas pendientes haya cambiado.

    Devuelve:
      results  : {indice_fila: {arb_id: resultado}}
      failures : {indice_fila: {arb_id: motivo}}
    """
    results: dict[int, dict] = {}
    failures: dict[int, dict] = {}
    by_id = {a["id"]: a for a in arbiters}
    clients: dict[str, object] = {}

    def client_for(arb: dict):
        if arb["id"] not in clients:
            clients[arb["id"]] = client_factory(resolve_key(arb["provider"]), saptiva.resolve_base_url(arb["provider"]))
        return clients[arb["id"]]

    # 1. Planear: retomar los jobs guardados o partir las filas pendientes en archivos
    for arb in arbiters:
        if any(j.get("arbiter_id") == arb["id"] for j in state.data["jobs"].values()):
            continue
        prompts = prompts_by_arbiter.get(arb["id"], {})
        if not prompts:
            continue
        rows_sorted = sorted(prompts)
        chunks = chunk_lines(build_batch_lines(arb, prompts))
        offset = 0
        for n, chunk in enumerate(chunks):
            state.job(f"{arb['id']}#{n}").update({
                "arbiter_id": arb["id"],
                "model": arb["provider"].get("model"),
                "rows": rows_sorted[offset:offset + len(chunk)],
                "lines": len(chunk),
            })
            offset += len(chunk)
    state.save()

    # 2. Subir y crear los que aun no tienen lote
    for key, job in state.data["jobs"].items():
        arb = by_id.get(job.get("arbiter_id"))
        if arb is None:
            continue
        if job.get("batch_id"):
            log.info("[%s] Retomando lote %s (%s)", key, job["batch_id"], job.get("status", "?"))
            continue
        prompts = prompts_by_arbiter.get(arb["id"], {})
        missing = [i for i in job["rows"] if i not in prompts]
        if missing:
            raise RuntimeError(f"[{key}] faltan prompts para {len(missing)} filas al crear el lote; usa --fresh.")
        _submit(client_for(arb), arb, key, job["rows"], prompts, job, state)

    # 3. Consultar hasta estado terminal, tolerando errores transitorios
    errors_in_a_row = 0
    while True:
        snapshot = {}
        for key, job in sorted(state.data["jobs"].items()):
            arb = by_id.get(job.get("arbiter_id"))
            if arb is None or job.get("status") in TERMINAL_STATUSES:
                continue
            try:
                batch = client_for(arb).batches.retrieve(job["batch_id"])
                errors_in_a_row = 0
            except Exception as exc:
                if not _is_transient(exc):
                    raise
                errors_in_a_row += 1
                log.warning("[%s] Consulta fallo (%s); se reintenta en la siguiente vuelta", key, type(exc).__name__)
                if errors_in_a_row >= max_consecutive_poll_errors:
                    raise
                continue
            counts = getattr(batch, "request_counts", None)
            job.update({
                "status": batch.status,
                "output_file_id": getattr(batch, "output_file_id", None),
                "error_file_id": getattr(batch, "error_file_id", None),
                "completed": getattr(counts, "completed", None) if counts else None,
                "failed": getattr(counts, "failed", None) if counts else None,
                "errors": _errors_to_list(getattr(batch, "errors", None)),
            })
            snapshot[key] = {k: job.get(k) for k in ("status", "completed", "failed", "lines")}
        state.save()
        if snapshot:
            log.info("Lotes: %s", "; ".join(
                f"{k}={v['status']} {v.get('completed') or 0}/{v.get('lines')} (fallidas {v.get('failed') or 0})"
                for k, v in snapshot.items()
            ))
            if on_progress:
                on_progress(snapshot)
        if all(j.get("status") in TERMINAL_STATUSES for j in state.data["jobs"].values() if j.get("arbiter_id") in by_id):
            break
        sleep(poll_seconds)

    # 4. Descargar y casar por custom_id con las filas que envio cada lote
    for key, job in state.data["jobs"].items():
        arb = by_id.get(job.get("arbiter_id"))
        if arb is None:
            continue
        arb_id = arb["id"]
        if job.get("status") == "failed":
            log.error("[%s] Lote fallido completo: %s", key, job.get("errors"))
        sent = set(job.get("rows", []))
        seen: set[int] = set()
        for file_key in ("output_file_id", "error_file_id"):
            file_id = job.get(file_key)
            if not file_id:
                continue
            text = client_for(arb).files.content(file_id).text
            for raw in text.splitlines():
                if not raw.strip():
                    continue
                row_index, result, reason = parse_result_line(json.loads(raw), principles)
                if row_index is None or row_index not in sent:
                    continue
                seen.add(row_index)
                if result is not None:
                    results.setdefault(row_index, {})[arb_id] = result
                    if on_result:
                        on_result(row_index, arb_id, result)
                else:
                    failures.setdefault(row_index, {})[arb_id] = reason
        for row_index in sent - seen:
            failures.setdefault(row_index, {})[arb_id] = f"sin resultado en lote ({job.get('status')})"
        job["downloaded"] = True
        state.save()

    return results, failures


def _errors_to_list(errors) -> list:
    if not errors:
        return []
    data = getattr(errors, "data", None)
    if data is None and isinstance(errors, dict):
        data = errors.get("data")
    out = []
    for e in data or []:
        if hasattr(e, "model_dump"):
            e = e.model_dump()
        out.append(e)
    return out[:20]

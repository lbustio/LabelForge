"""Reglas puras para construir la proyeccion de metricas de una ejecucion."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IncidentEffect:
    key: str
    values: dict


@dataclass(frozen=True, slots=True)
class PerformanceEffect:
    values: dict


@dataclass(frozen=True, slots=True)
class ProjectionEffects:
    performance: tuple[PerformanceEffect, ...] = ()
    resolved_rows: tuple[tuple[str, object], ...] = ()


def apply_start_event(
    stats: dict,
    event: dict,
    *,
    checkpoint_every: int = 10,
) -> ProjectionEffects:
    stats["active"] = True
    stats["finished"] = False
    stats["run_id"] = event.get("run_id")
    stats["source_file"] = event.get("output_file")
    stats["output_path"] = event.get("output_path")
    stats["input_file"] = event.get("input_file")
    stats["config_file"] = event.get("config_file")
    stats["started_at"] = event.get("timestamp")
    stats["updated_at"] = event.get("timestamp")
    stats["total_rows"] = int(event.get("total_rows") or 0)
    stats["dataset_total_rows"] = int(event.get("dataset_total_rows") or 0)
    stats["eligible_rows"] = int(event.get("eligible_rows") or stats["total_rows"])
    stats["completed_before_start"] = int(event.get("completed_before_start") or 0)
    stats["execution_order"] = event.get("execution_order") or "row_major"
    stats["arbiter_count"] = int(event.get("arbiter_count") or len(event.get("arbiters", [])))
    stats["arbiter_work_total"] = int(event.get("arbiter_work_total") or 0)
    stats["arbiter_work_completed"] = int(event.get("arbiter_work_completed") or 0)
    stats["arbiter_work_before_start"] = int(
        event.get("arbiter_work_before_start", event.get("arbiter_work_completed")) or 0
    )
    stats["principles"] = event.get("principles", [])
    stats["checkpoint_every"] = checkpoint_every
    for arbiter in event.get("arbiters", []):
        ensure_arbiter(stats, arbiter.get("id"), arbiter.get("name"))
    apply_initial_summary(stats, event.get("initial_summary", {}))
    apply_resource_summary(stats, event.get("initial_resource_summary", {}))
    for arbiter_stats in stats["arbiters"].values():
        arbiter_stats["evaluations_before_start"] = int(arbiter_stats.get("evaluations", 0) or 0)

    if not stats.get("run_id"):
        return ProjectionEffects()
    arbiter_major = stats.get("execution_order") == "arbiter_major"
    effects = [PerformanceEffect({
        "run_id": stats["run_id"],
        "completed": stats["arbiter_work_completed"] if arbiter_major else (
            int(stats.get("successful_rows", 0) or 0) + int(stats.get("failed_rows", 0) or 0)
        ),
        "total": stats["arbiter_work_total"] if arbiter_major else stats["eligible_rows"],
        "total_tokens": stats["total_tokens_global"],
        "unit": "evaluation" if arbiter_major else "entry",
    })]
    if arbiter_major:
        for arbiter_id, arbiter_stats in stats["arbiters"].items():
            effects.append(PerformanceEffect({
                "run_id": stats["run_id"],
                "completed": int(arbiter_stats.get("evaluations", 0) or 0),
                "total": int(stats.get("eligible_rows", 0) or 0),
                "total_tokens": int(arbiter_stats.get("total_tokens", 0) or 0),
                "unit": "evaluation",
                "arbiter_id": arbiter_id,
            }))
    return ProjectionEffects(performance=tuple(effects))


def apply_claim_ok_event(stats: dict, event: dict) -> ProjectionEffects:
    changed = set_claim_status(stats, event.get("row_index"), "ok")
    stats["_queued_indices"].discard(str(event.get("row_index")))
    run_id = stats.get("run_id")
    stats["last_claim"] = event
    if not changed:
        return ProjectionEffects()
    arbiter_major = stats.get("execution_order") == "arbiter_major"
    if not arbiter_major:
        stats["total_tokens_global"] += int(event.get("total_tokens") or 0)
        if event.get("total_latency_s") is not None:
            stats["total_latency_s"] += float(event.get("total_latency_s") or 0)
    if not arbiter_major:
        for arbiter in event.get("arbiters", []):
            arbiter_stats = ensure_arbiter(stats, arbiter.get("id"), arbiter.get("name"))
            arbiter_stats["prompt_tokens"] += int(arbiter.get("prompt_tokens") or 0)
            arbiter_stats["completion_tokens"] += int(arbiter.get("completion_tokens") or 0)
            arbiter_stats["total_tokens"] += int(arbiter.get("total_tokens") or 0)
            if arbiter.get("latency_s") is not None:
                latency = float(arbiter.get("latency_s") or 0)
                arbiter_stats["calls"] += 1
                arbiter_stats["evaluations"] += 1
                arbiter_stats["model_calls"] += max(1, int(arbiter.get("model_calls") or 1))
                arbiter_stats["latency_total_s"] += latency
                arbiter_stats["latency_min_s"] = latency if arbiter_stats["latency_min_s"] is None else min(arbiter_stats["latency_min_s"], latency)
                arbiter_stats["latency_max_s"] = latency if arbiter_stats["latency_max_s"] is None else max(arbiter_stats["latency_max_s"], latency)
    for principle, value in event.get("consensus", {}).items():
        counts = stats["principle_counts"].setdefault(principle, {"ones": 0, "zeros": 0})
        if value == 1:
            counts["ones"] += 1
        elif value == 0:
            counts["zeros"] += 1

    performance = ()
    if run_id and not arbiter_major:
        performance = (PerformanceEffect({
            "run_id": run_id,
            "completed": int(stats.get("successful_rows", 0) or 0) + int(stats.get("failed_rows", 0) or 0),
            "total": int(stats.get("eligible_rows", 0) or 0),
            "total_tokens": int(stats.get("total_tokens_global", 0) or 0),
            "unit": "entry",
        }),)
    resolved = ((run_id, event.get("row_index")),) if run_id else ()
    return ProjectionEffects(performance=performance, resolved_rows=resolved)


def apply_arbiter_result_event(stats: dict, event: dict) -> ProjectionEffects:
    arbiter_id = event.get("arbiter_id")
    result_key = f"{event.get('row_index')}:{arbiter_id}"
    if result_key in stats["_arbiter_result_keys"]:
        return ProjectionEffects()
    stats["_arbiter_result_keys"].add(result_key)
    stats["arbiter_work_completed"] += 1
    stats["total_tokens_global"] += int(event.get("total_tokens") or 0)
    latency = float(event.get("latency_s") or 0)
    stats["total_latency_s"] += latency
    arbiter = ensure_arbiter(stats, arbiter_id, event.get("arbiter_name"))
    arbiter["prompt_tokens"] += int(event.get("prompt_tokens") or 0)
    arbiter["completion_tokens"] += int(event.get("completion_tokens") or 0)
    arbiter["total_tokens"] += int(event.get("total_tokens") or 0)
    arbiter["calls"] += 1
    arbiter["evaluations"] += 1
    arbiter["model_calls"] += max(1, int(event.get("chunks") or 1))
    arbiter["latency_total_s"] += latency
    arbiter["latency_min_s"] = latency if arbiter["latency_min_s"] is None else min(arbiter["latency_min_s"], latency)
    arbiter["latency_max_s"] = latency if arbiter["latency_max_s"] is None else max(arbiter["latency_max_s"], latency)
    if arbiter_id == stats.get("current_arbiter_id"):
        arbiter_total = stats.get("current_pass_work_total") or stats["total_rows"]
        stats["current_pass_completed"] = min(arbiter_total, int(stats.get("current_pass_completed") or 0) + 1)
    if not stats.get("run_id"):
        return ProjectionEffects()
    common = {"run_id": stats["run_id"], "unit": "evaluation"}
    return ProjectionEffects(performance=(
        PerformanceEffect({**common, "completed": stats["arbiter_work_completed"], "total": stats["arbiter_work_total"], "total_tokens": stats["total_tokens_global"]}),
        PerformanceEffect({**common, "completed": arbiter["evaluations"], "total": stats["eligible_rows"], "total_tokens": arbiter["total_tokens"], "arbiter_id": arbiter_id}),
    ))


def ensure_arbiter(stats: dict, arbiter_id: str, name: str | None = None) -> dict:
    if arbiter_id not in stats["arbiters"]:
        stats["arbiters"][arbiter_id] = {
            "id": arbiter_id,
            "name": name or arbiter_id,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "calls": 0,
            "evaluations": 0,
            "model_calls": 0,
            "evaluations_before_start": 0,
            "latency_total_s": 0.0,
            "latency_min_s": None,
            "latency_max_s": None,
        }
    elif name:
        stats["arbiters"][arbiter_id]["name"] = name
    return stats["arbiters"][arbiter_id]


def apply_initial_summary(stats: dict, summary: dict) -> None:
    if not summary:
        return
    for key in (
        "processed_rows", "successful_rows", "failed_rows", "skipped_rows",
        "total_tokens_global", "total_latency_s",
    ):
        stats[key] = summary.get(key, stats.get(key, 0))
    for arbiter_id, incoming in summary.get("arbiters", {}).items():
        arbiter = ensure_arbiter(stats, arbiter_id)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "calls"):
            arbiter[key] = incoming.get(key, arbiter.get(key, 0))
        arbiter["evaluations"] = int(incoming.get("evaluations", incoming.get("calls", 0)) or 0)
        arbiter["model_calls"] = int(incoming.get("model_calls", incoming.get("calls", 0)) or 0)
        arbiter["latency_total_s"] = incoming.get("latency_total_s", 0) or 0
        arbiter["latency_min_s"] = incoming.get("latency_min_s") or None
        arbiter["latency_max_s"] = incoming.get("latency_max_s") or None


def apply_resource_summary(stats: dict, summary: dict) -> None:
    """Reemplaza recursos con una fuente completa en vez de sumarlos de nuevo."""
    if not summary:
        return
    stats["total_tokens_global"] = int(summary.get("total_tokens_global", 0) or 0)
    stats["total_latency_s"] = float(summary.get("total_latency_s", 0) or 0)
    for arbiter_id, incoming in summary.get("arbiters", {}).items():
        arbiter = ensure_arbiter(stats, arbiter_id)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "evaluations", "model_calls"):
            arbiter[key] = int(incoming.get(key, 0) or 0)
        arbiter["calls"] = arbiter["evaluations"]
        arbiter["latency_total_s"] = float(incoming.get("latency_total_s", 0) or 0)
        arbiter["latency_min_s"] = incoming.get("latency_min_s") or None
        arbiter["latency_max_s"] = incoming.get("latency_max_s") or None


def set_claim_status(stats: dict, row_index: object, status: str) -> bool:
    """Aplica una transicion idempotente y devuelve si el estado realmente cambio."""
    key = str(row_index)
    previous = stats["_claim_statuses"].get(key)
    if previous == status:
        return False
    counters = {
        "ok": "successful_rows",
        "failed": "failed_rows",
        "skipped": "skipped_rows",
    }
    if previous in counters:
        stats[counters[previous]] = max(0, stats.get(counters[previous], 0) - 1)
    if status in counters:
        stats[counters[status]] = stats.get(counters[status], 0) + 1
    stats["_claim_statuses"][key] = status
    stats["processed_rows"] = stats.get("completed_before_start", 0) + len(stats["_claim_statuses"])
    return True


def apply_simple_event(stats: dict, event: dict) -> bool:
    """Proyecta eventos sin efectos de infraestructura.

    Devuelve ``True`` cuando reconoce el evento. Los eventos que persisten
    incidencias o rendimiento se coordinan en una capa superior.
    """
    kind = event.get("event")
    if kind == "claim_start":
        stats["last_claim"] = event
    elif kind == "claim_skipped":
        set_claim_status(stats, event.get("row_index"), "skipped")
        stats["last_claim"] = event
    elif kind == "arbiter_claim_start":
        stats["last_claim"] = event
        stats["current_arbiter_id"] = event.get("arbiter_id")
        stats["current_pass_position"] = int(event.get("pass_position") or 0)
        stats["current_pass_total"] = int(event.get("pass_total") or 0)
        pending_total = int(event.get("pass_pending_total") or 0)
        pending_position = int(event.get("position") or 0)
        stats["current_pass_work_total"] = int(
            event.get("pass_work_total") or stats["eligible_rows"]
        )
        stats["current_pass_completed"] = max(
            0,
            stats["current_pass_work_total"] - pending_total + pending_position - 1,
        )
    elif kind == "arbiter_pass_start":
        stats["current_arbiter_id"] = event.get("arbiter_id")
        stats["current_pass_position"] = int(event.get("position") or 0)
        stats["current_pass_total"] = int(event.get("total_arbiters") or 0)
        pending_rows = int(event.get("pending_rows") or 0)
        stats["current_pass_work_total"] = int(
            event.get("pass_work_total") or stats["eligible_rows"]
        )
        stats["current_pass_completed"] = max(
            0, stats["current_pass_work_total"] - pending_rows
        )
    elif kind == "output_written":
        stats["source_file"] = event.get("output_file") or stats.get("source_file")
        stats["output_path"] = event.get("output_path") or stats.get("output_path")
    elif kind == "complete":
        stats["active"] = False
        stats["finished"] = True
        stats["source_file"] = event.get("output_file") or stats.get("source_file")
        stats["output_path"] = event.get("output_path") or stats.get("output_path")
        if event.get("final_summary"):
            apply_initial_summary(stats, event["final_summary"])
    else:
        return False
    return True


def apply_incident_event(stats: dict, event: dict) -> IncidentEffect:
    """Proyecta fallo/pausa y describe la persistencia requerida."""
    kind = event.get("event")
    if kind == "claim_failed":
        if event.get("definitive"):
            set_claim_status(stats, event.get("row_index"), "failed")
            stats["_queued_indices"].discard(str(event.get("row_index")))
        elif event.get("queued"):
            stats["_queued_indices"].add(str(event.get("row_index")))
        stats["last_claim"] = event
        run_id = event.get("run_id") or stats.get("run_id") or "unknown"
        row_index = event.get("row_index")
        arbiter_id = event.get("arbiter_id")
        return IncidentEffect(
            key=f"{run_id}:row:{row_index}:arbiter:{arbiter_id or 'unknown'}",
            values={
                "run_id": run_id,
                "kind": "Fallo definitivo" if event.get("definitive") else "Evaluacion pendiente",
                "message": event.get("reason") or "Fallo de evaluacion sin detalle.",
                "severity": "error" if event.get("definitive") else "warning",
                "status": "final" if event.get("definitive") else "open",
                "row_index": row_index,
                "arbiter_id": arbiter_id,
                "reason_code": event.get("reason_code") or "unknown",
            },
        )
    if kind == "paused":
        stats["active"] = False
        stats["finished"] = False
        stats["pause_kind"] = event.get("pause_kind", "")
        stats["pause_reason"] = event.get("pause_reason", "")
        run_id = event.get("run_id") or stats.get("run_id") or "unknown"
        pause_kind = event.get("pause_kind") or "unknown"
        return IncidentEffect(
            key=f"{run_id}:pause:{pause_kind}",
            values={
                "run_id": run_id,
                "kind": "Ejecucion pausada",
                "message": event.get("pause_reason") or "La ejecucion fue pausada.",
                "severity": "warning",
                "status": "open",
                "arbiter_id": stats.get("current_arbiter_id"),
                "reason_code": pause_kind,
            },
        )
    raise ValueError(f"Evento sin efecto de incidente: {kind!r}")

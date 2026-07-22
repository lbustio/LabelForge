"""Caso de uso para reconstruir la proyeccion desde fuentes durables."""

from __future__ import annotations

from typing import Callable

from labelforge_core.application.run_metrics_projection import (
    apply_initial_summary,
    apply_resource_summary,
    ensure_arbiter,
)


def aggregate_checkpoint_resources(partial: dict) -> dict:
    resources = {"total_tokens_global": 0, "total_latency_s": 0.0, "arbiters": {}}
    for arbiter_results in partial.values():
        for arbiter_id, result in arbiter_results.items():
            usage = result.get("_usage", {}) if isinstance(result, dict) else {}
            latency = float(result.get("_latency_s", 0) or 0) if isinstance(result, dict) else 0.0
            chunks = max(1, int(result.get("_chunks", 1) or 1)) if isinstance(result, dict) else 1
            arbiter = resources["arbiters"].setdefault(arbiter_id, {
                "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                "evaluations": 0, "model_calls": 0, "latency_total_s": 0.0,
                "latency_min_s": None, "latency_max_s": None,
            })
            prompt = int(usage.get("prompt_tokens", 0) or 0)
            completion = int(usage.get("completion_tokens", 0) or 0)
            arbiter["prompt_tokens"] += prompt
            arbiter["completion_tokens"] += completion
            arbiter["total_tokens"] += prompt + completion
            arbiter["evaluations"] += 1
            arbiter["model_calls"] += chunks
            arbiter["latency_total_s"] += latency
            arbiter["latency_min_s"] = latency if arbiter["latency_min_s"] is None else min(arbiter["latency_min_s"], latency)
            arbiter["latency_max_s"] = latency if arbiter["latency_max_s"] is None else max(arbiter["latency_max_s"], latency)
            resources["total_tokens_global"] += prompt + completion
            resources["total_latency_s"] += latency
    return resources


class RunMetricsHydrator:
    def __init__(
        self,
        *,
        store,
        empty_factory: Callable[[], dict],
        run_state_loader: Callable[[], dict | None],
        arbiter_results_loader: Callable[[], dict],
        failed_queue_loader: Callable[[], list],
        input_rows_loader: Callable[[dict], tuple[list[dict], list[str]]],
        execution_order_provider: Callable[[], str],
    ) -> None:
        self._store = store
        self._empty_factory = empty_factory
        self._run_state_loader = run_state_loader
        self._arbiter_results_loader = arbiter_results_loader
        self._failed_queue_loader = failed_queue_loader
        self._input_rows_loader = input_rows_loader
        self._execution_order_provider = execution_order_provider

    def hydrate(self) -> bool:
        if self._store.has_run():
            return False
        state = self._run_state_loader()
        if not state:
            return False
        restored = self._empty_factory()
        restored.update({
            "run_id": state.get("run_id"),
            "active": False,
            "finished": state.get("status") == "completed",
            "source_file": state.get("output_file"),
            "output_path": state.get("output_path"),
            "input_file": state.get("input_file"),
            "config_file": state.get("config_file"),
            "started_at": state.get("created_at"),
            "updated_at": state.get("updated_at"),
            "total_rows": int(state.get("total_rows") or 0),
            "dataset_total_rows": int(state.get("dataset_total_rows") or state.get("total_rows") or 0),
            "eligible_rows": int(state.get("eligible_rows") or 0),
            "pause_kind": state.get("pause_kind", ""),
            "pause_reason": state.get("pause_reason", ""),
            "principles": state.get("principles", []),
            "arbiter_count": len(state.get("arbiters", [])),
        })
        try:
            restored["execution_order"] = self._execution_order_provider()
        except Exception:
            restored["execution_order"] = "row_major"
        for arbiter in state.get("arbiters", []):
            ensure_arbiter(restored, arbiter.get("id"), arbiter.get("name"))

        summary = state.get("summary", {})
        if summary:
            apply_initial_summary(restored, summary)
            restored["completed_before_start"] = int(summary.get("processed_rows", 0) or 0)
        partial = self._arbiter_results_loader()
        if not restored["eligible_rows"]:
            input_rows, _ = self._input_rows_loader(state)
            text_column = state.get("text_column") or ""
            restored["eligible_rows"] = sum(
                1 for row in input_rows if str(row.get(text_column, "") or "").strip()
            ) if text_column else restored["total_rows"]
        restored["arbiter_work_total"] = restored["eligible_rows"] * restored["arbiter_count"]
        partial_evaluations = sum(len(results) for results in partial.values())
        restored["arbiter_work_completed"] = partial_evaluations or (
            (int(summary.get("successful_rows", 0) or 0) + int(summary.get("failed_rows", 0) or 0))
            * restored["arbiter_count"]
        )
        restored["arbiter_work_before_start"] = restored["arbiter_work_completed"]
        if partial:
            apply_resource_summary(restored, aggregate_checkpoint_resources(partial))
        for arbiter in restored["arbiters"].values():
            arbiter["evaluations_before_start"] = int(arbiter.get("evaluations", 0) or 0)
        restored["_queued_indices"] = {
            str(item.get("index")) for item in self._failed_queue_loader()
        }
        return self._store.replace_if_empty(restored)

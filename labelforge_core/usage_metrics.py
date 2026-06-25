"""
Shared token and latency metrics for labeled entries.
"""

from __future__ import annotations

from labelforge_core.utils import safe_float, safe_int


ENTRY_USAGE_FIELDS = [
    "entry_prompt_tokens",
    "entry_completion_tokens",
    "entry_total_tokens",
    "entry_latency_s",
    "entry_latency_avg_s",
    "entry_arbiter_calls",
    "entry_tokens_per_call",
]


def _arbiter_ids(arbiters_or_ids: list) -> list[str]:
    ids = []
    for item in arbiters_or_ids:
        if isinstance(item, dict):
            ids.append(str(item["id"]))
        else:
            ids.append(str(item))
    return ids


def build_entry_usage_metric_fieldnames() -> list[str]:
    return list(ENTRY_USAGE_FIELDS)


def compute_entry_usage_metrics(record: dict, arbiters_or_ids: list) -> dict:
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    total_latency = 0.0
    latency_calls = 0
    arbiter_calls = 0

    for arb_id in _arbiter_ids(arbiters_or_ids):
        p_tok = safe_int(record.get(f"prompt_tokens_{arb_id}"), 0) or 0
        c_tok = safe_int(record.get(f"completion_tokens_{arb_id}"), 0) or 0
        t_tok = safe_int(record.get(f"total_tokens_{arb_id}"), p_tok + c_tok) or 0
        lat = safe_float(record.get(f"latency_s_{arb_id}"), -1.0)

        if p_tok > 0:
            prompt_tokens += p_tok
        if c_tok > 0:
            completion_tokens += c_tok
        if t_tok > 0:
            total_tokens += t_tok
        if t_tok >= 0 or lat >= 0:
            arbiter_calls += 1
        if lat >= 0:
            total_latency += lat
            latency_calls += 1

    return {
        "entry_prompt_tokens": prompt_tokens,
        "entry_completion_tokens": completion_tokens,
        "entry_total_tokens": total_tokens,
        "entry_latency_s": round(total_latency, 3) if latency_calls else None,
        "entry_latency_avg_s": round(total_latency / latency_calls, 3) if latency_calls else None,
        "entry_arbiter_calls": arbiter_calls,
        "entry_tokens_per_call": round(total_tokens / arbiter_calls, 2) if arbiter_calls else None,
    }


def add_entry_usage_metrics(record: dict, arbiters_or_ids: list) -> dict:
    metrics = compute_entry_usage_metrics(record, arbiters_or_ids)
    record.update(metrics)
    return metrics


def entry_usage_metrics_from_row(row: dict, arbiters_or_ids: list) -> dict:
    prompt_tokens = safe_int(row.get("entry_prompt_tokens"))
    completion_tokens = safe_int(row.get("entry_completion_tokens"))
    total_tokens = safe_int(row.get("entry_total_tokens"))
    latency_s = safe_float(row.get("entry_latency_s"))
    latency_avg_s = safe_float(row.get("entry_latency_avg_s"))
    arbiter_calls = safe_int(row.get("entry_arbiter_calls"))
    tokens_per_call = safe_float(row.get("entry_tokens_per_call"))

    if total_tokens is None:
        computed = compute_entry_usage_metrics(row, arbiters_or_ids)
        prompt_tokens = computed["entry_prompt_tokens"]
        completion_tokens = computed["entry_completion_tokens"]
        total_tokens = computed["entry_total_tokens"]
        latency_s = computed["entry_latency_s"]
        latency_avg_s = computed["entry_latency_avg_s"]
        arbiter_calls = computed["entry_arbiter_calls"]
        tokens_per_call = computed["entry_tokens_per_call"]

    return {
        "prompt_tokens": prompt_tokens or 0,
        "completion_tokens": completion_tokens or 0,
        "total_tokens": total_tokens or 0,
        "latency_s": round(latency_s, 3) if latency_s is not None and latency_s >= 0 else None,
        "latency_avg_s": round(latency_avg_s, 3) if latency_avg_s is not None and latency_avg_s >= 0 else None,
        "arbiter_calls": arbiter_calls or 0,
        "tokens_per_call": tokens_per_call,
    }

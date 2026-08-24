"""
run_reporting.py
----------------
Contrato compartido para interpretar salidas del pipeline.

Este modulo no sabe de Flask, usuarios ni permisos. Recibe filas ya leidas desde
CSV/checkpoints y produce estructuras listas para UI, CLI o reportes.
"""

from __future__ import annotations

import time
from collections import Counter
from pathlib import Path

from labelforge_core.agreement_metrics import cohen_kappa_for_pairs, compute_agreement_metrics
from labelforge_core.usage_metrics import entry_usage_metrics_from_row
from labelforge_core.utils import safe_float, safe_int


def parse_labeled_timestamp(path: Path) -> str:
    stem = path.stem
    prefix = "labeled_"
    if not stem.startswith(prefix):
        return ""
    raw = stem[len(prefix):]
    try:
        parsed = time.strptime(raw, "%Y%m%d_%H%M%S")
        return time.strftime("%Y-%m-%d %H:%M:%S", parsed)
    except ValueError:
        return ""


def detect_principles(fieldnames: list[str]) -> list[str]:
    suffix = "_consensus"
    return [fn[:-len(suffix)] for fn in fieldnames if fn.endswith(suffix)]


def detect_arbiters(fieldnames: list[str]) -> list[str]:
    return [
        fn.replace("prompt_tokens_", "")
        for fn in fieldnames
        if fn.startswith("prompt_tokens_")
    ]


def row_processing_status(row: dict, principles: list[str]) -> str:
    values = [safe_int(row.get(f"{p}_consensus")) for p in principles]
    values = [v for v in values if v is not None]
    if not values:
        return "pending"
    if any(v < 0 for v in values):
        return "failed"
    return "ok"


def entry_consensus_metrics_from_row(row: dict, principles: list[str]) -> dict:
    per_principle = {}
    for principle in principles:
        per_principle[principle] = {
            "support": safe_int(row.get(f"{principle}_entry_consensus_support")),
            "raters": safe_int(row.get(f"{principle}_entry_consensus_raters")),
            "support_ratio": safe_float(row.get(f"{principle}_entry_consensus_support_ratio")),
            "votes_0": safe_int(row.get(f"{principle}_entry_consensus_votes_0")),
            "votes_1": safe_int(row.get(f"{principle}_entry_consensus_votes_1")),
            "unanimous": str(row.get(f"{principle}_entry_consensus_unanimous", "")).lower() in {"true", "1"},
        }
    observed_agreement = safe_float(row.get("entry_consensus_observed_agreement"))
    mean_support_ratio = safe_float(row.get("entry_consensus_mean_support_ratio"))
    observed_agreement_percent = safe_float(row.get("entry_consensus_observed_agreement_percent"))
    mean_support_percent = safe_float(row.get("entry_consensus_mean_support_percent"))
    if observed_agreement_percent is None and observed_agreement is not None:
        observed_agreement_percent = round(observed_agreement * 100, 1)
    if mean_support_percent is None and mean_support_ratio is not None:
        mean_support_percent = round(mean_support_ratio * 100, 1)
    return {
        "observed_agreement": observed_agreement,
        "observed_agreement_percent": observed_agreement_percent,
        "mean_support_ratio": mean_support_ratio,
        "mean_support_percent": mean_support_percent,
        "unanimous_principles": safe_int(row.get("entry_consensus_unanimous_principles"), 0),
        "split_principles": safe_int(row.get("entry_consensus_split_principles"), 0),
        "principle_count": safe_int(row.get("entry_consensus_principle_count"), len(principles)),
        "per_principle": per_principle,
    }


def build_result_rows(
    rows: list[dict],
    principles: list[str],
    arbiter_ids: list[str],
    limit: int,
    newest_first: bool = True,
    offset: int = 0,
) -> list[dict]:
    result_rows = []
    indexed_rows = list(enumerate(rows))
    if newest_first:
        indexed_rows = [
            item for item in indexed_rows
            if row_processing_status(item[1], principles) != "pending"
        ]
        indexed_rows.sort(key=lambda item: item[0], reverse=True)
    else:
        indexed_rows = indexed_rows[:limit]

    offset = max(0, offset)
    limit = max(0, limit)
    for idx, row in indexed_rows[offset:offset + limit]:
        usage_metrics = entry_usage_metrics_from_row(row, arbiter_ids)
        arbiter_results = []

        for arb_id in arbiter_ids:
            prompt_tokens = safe_int(row.get(f"prompt_tokens_{arb_id}"), 0) or 0
            completion_tokens = safe_int(row.get(f"completion_tokens_{arb_id}"), 0) or 0
            total_tokens = safe_int(row.get(f"total_tokens_{arb_id}"), 0) or 0
            latency_s = safe_float(row.get(f"latency_s_{arb_id}"))

            labels = {}
            for p in principles:
                labels[p] = {
                    "value": safe_int(row.get(f"{p}_{arb_id}")),
                    "why": row.get(f"{p}_{arb_id}_why", ""),
                }

            arbiter_results.append({
                "id": arb_id,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "latency_s": round(latency_s, 3) if latency_s is not None and latency_s >= 0 else None,
                "labels": labels,
            })

        result_rows.append({
            "index": idx,
            "status": row_processing_status(row, principles),
            "claim": row.get("claim", ""),
            "claim_es": row.get("claim_es", ""),
            "dataset": row.get("dataset", ""),
            "class": row.get("class", ""),
            "consensus": {
                p: safe_int(row.get(f"{p}_consensus"))
                for p in principles
            },
            "consensus_metrics": entry_consensus_metrics_from_row(row, principles),
            "usage_metrics": usage_metrics,
            "total_tokens": usage_metrics["total_tokens"],
            "total_latency_s": usage_metrics["latency_s"],
            "latency_avg_s": usage_metrics["latency_avg_s"],
            "arbiter_calls": usage_metrics["arbiter_calls"],
            "arbiters": arbiter_results,
        })

    return result_rows


def principle_counts_from_rows(rows: list[dict], principles: list[str]) -> dict:
    counts = {p: {"ones": 0, "zeros": 0} for p in principles}
    for row in rows:
        for p in principles:
            v = row.get(f"{p}_consensus", "")
            if v == "1" or v == 1:
                counts[p]["ones"] += 1
            elif v == "0" or v == 0:
                counts[p]["zeros"] += 1
    return counts


def describe_output_csv(path: Path, rows: list[dict] | None = None, fieldnames: list[str] | None = None) -> dict:
    info = {
        "run_timestamp": parse_labeled_timestamp(path),
        "row_count": None,
        "successful_rows": None,
        "failed_rows": None,
        "principles": [],
        "arbiters": [],
        "has_tokens": False,
        "has_latency": False,
        "total_tokens_global": None,
        "description": "CSV etiquetado generado por el pipeline.",
    }

    if fieldnames is None:
        fieldnames = []
    principles = detect_principles(fieldnames)
    arbiters = detect_arbiters(fieldnames)
    info["principles"] = principles
    info["arbiters"] = arbiters
    info["has_tokens"] = bool(arbiters)
    info["has_latency"] = any(fn.startswith("latency_s_") for fn in fieldnames)

    if rows is not None:
        info["row_count"] = len(rows)
        status_counts = {"ok": 0, "failed": 0, "pending": 0}
        total_tokens = 0
        for row in rows:
            status_counts[row_processing_status(row, principles)] += 1
            total_tokens += entry_usage_metrics_from_row(row, arbiters)["total_tokens"]
        info["successful_rows"] = status_counts["ok"]
        info["failed_rows"] = status_counts["failed"]
        info["total_tokens_global"] = total_tokens

    run_label = f"Ejecucion del {info['run_timestamp']}" if info["run_timestamp"] else "Resultado etiquetado"
    parts = [run_label]
    if info["row_count"] is not None:
        parts.append(f"{info['row_count']} entradas")
    else:
        parts.append("archivo grande; conteo no calculado en la vista")
    if info["principles"]:
        parts.append(f"{len(info['principles'])} principios")
    if info["arbiters"]:
        parts.append(f"{len(info['arbiters'])} arbitros")
    parts.append("con votos, columnas de justificacion y consenso")
    if info["has_tokens"] and info["has_latency"]:
        parts.append("incluye tokens y tiempos de respuesta")
    elif info["has_tokens"]:
        parts.append("incluye tokens; no trae tiempos por ser una ejecucion anterior")
    info["description"] = "; ".join(parts) + "."
    return info


def _mean(values: list[float]) -> float | None:
    values = [float(v) for v in values if v is not None]
    return round(sum(values) / len(values), 4) if values else None


def _median(values: list[float]) -> float | None:
    values = sorted(float(v) for v in values if v is not None)
    if not values:
        return None
    mid = len(values) // 2
    if len(values) % 2:
        return round(values[mid], 4)
    return round((values[mid - 1] + values[mid]) / 2, 4)


def _percentile(values: list[float], pct: float) -> float | None:
    values = sorted(float(v) for v in values if v is not None)
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 4)
    pos = (len(values) - 1) * pct
    lo = int(pos)
    hi = min(lo + 1, len(values) - 1)
    weight = pos - lo
    return round(values[lo] * (1 - weight) + values[hi] * weight, 4)


def _distribution(values: list[str], limit: int = 30) -> dict:
    counts = Counter(str(v or "").strip() or "(vacio)" for v in values)
    return dict(counts.most_common(limit))


def numeric_summary(values: list[float]) -> dict:
    clean = [float(v) for v in values if v is not None]
    return {
        "n": len(clean),
        "min": round(min(clean), 4) if clean else None,
        "p25": _percentile(clean, 0.25),
        "median": _median(clean),
        "mean": _mean(clean),
        "p75": _percentile(clean, 0.75),
        "p95": _percentile(clean, 0.95),
        "max": round(max(clean), 4) if clean else None,
    }


def paper_reporting_summary(
    rows: list[dict],
    principles: list[str],
    arbiter_ids: list[str],
    agreement: dict,
    status_counts: dict,
) -> dict:
    processed_rows = [
        row for row in rows
        if row_processing_status(row, principles) != "pending"
    ]
    ok_rows = [
        row for row in rows
        if row_processing_status(row, principles) == "ok"
    ]
    entry_metrics = [entry_consensus_metrics_from_row(row, principles) for row in ok_rows]
    usage_metrics = [entry_usage_metrics_from_row(row, arbiter_ids) for row in processed_rows]

    principle_summary = {}
    for principle in principles:
        consensus_values = [safe_int(row.get(f"{principle}_consensus")) for row in ok_rows]
        positives = sum(1 for v in consensus_values if v == 1)
        negatives = sum(1 for v in consensus_values if v == 0)
        errors = sum(1 for v in consensus_values if v is not None and v < 0)
        missing = sum(1 for v in consensus_values if v is None)
        supports = []
        for metrics in entry_metrics:
            p_metrics = metrics.get("per_principle", {}).get(principle, {})
            raters = p_metrics.get("raters") or 0
            support = p_metrics.get("support")
            if support is not None and raters:
                supports.append(support / raters)
        principle_summary[principle] = {
            "n": len(consensus_values),
            "positive": positives,
            "negative": negatives,
            "errors": errors,
            "missing": missing,
            "positive_rate_percent": round((positives / (positives + negatives)) * 100, 1) if positives + negatives else None,
            "mean_majority_support_percent": round((_mean(supports) or 0) * 100, 1) if supports else None,
            "agreement": agreement.get("per_principle", {}).get(principle, {}),
        }

    observed_values = [
        m.get("observed_agreement_percent")
        for m in entry_metrics
        if m.get("observed_agreement_percent") is not None
    ]
    mean_support_values = [
        m.get("mean_support_percent")
        for m in entry_metrics
        if m.get("mean_support_percent") is not None
    ]
    split_counts = [m.get("split_principles", 0) or 0 for m in entry_metrics]
    unanimous_counts = [m.get("unanimous_principles", 0) or 0 for m in entry_metrics]

    arbiter_summary = {}
    for arb_id in arbiter_ids:
        prompt_tokens = []
        completion_tokens = []
        total_tokens = []
        latencies = []
        votes = []
        missing_votes = 0
        for row in processed_rows:
            prompt_tokens.append(safe_int(row.get(f"prompt_tokens_{arb_id}"), 0) or 0)
            completion_tokens.append(safe_int(row.get(f"completion_tokens_{arb_id}"), 0) or 0)
            total_tokens.append(safe_int(row.get(f"total_tokens_{arb_id}"), 0) or 0)
            lat = safe_float(row.get(f"latency_s_{arb_id}"))
            if lat is not None and lat >= 0:
                latencies.append(lat)
            for principle in principles:
                vote = safe_int(row.get(f"{principle}_{arb_id}"))
                if vote in (0, 1):
                    votes.append(vote)
                else:
                    missing_votes += 1
        arbiter_summary[arb_id] = {
            "calls": len(latencies),
            "vote_count": len(votes),
            "missing_votes": missing_votes,
            "positive_vote_rate_percent": round((sum(votes) / len(votes)) * 100, 1) if votes else None,
            "prompt_tokens": numeric_summary(prompt_tokens),
            "completion_tokens": numeric_summary(completion_tokens),
            "total_tokens": numeric_summary(total_tokens),
            "latency_s": numeric_summary(latencies),
        }

    pairwise = []
    for i, arb_a in enumerate(arbiter_ids):
        for arb_b in arbiter_ids[i + 1:]:
            pairs = []
            for row in processed_rows:
                for principle in principles:
                    a = safe_int(row.get(f"{principle}_{arb_a}"))
                    b = safe_int(row.get(f"{principle}_{arb_b}"))
                    if a in (0, 1) and b in (0, 1):
                        pairs.append((a, b))
            agreements = sum(1 for a, b in pairs if a == b)
            pairwise.append({
                "arbiter_a": arb_a,
                "arbiter_b": arb_b,
                "n": len(pairs),
                "observed_agreement_percent": round((agreements / len(pairs)) * 100, 1) if pairs else None,
                "cohen_kappa": cohen_kappa_for_pairs(pairs),
                "disagreements": len(pairs) - agreements,
            })

    total_prompt = sum(m.get("prompt_tokens", 0) or 0 for m in usage_metrics)
    total_completion = sum(m.get("completion_tokens", 0) or 0 for m in usage_metrics)
    total_tokens = sum(m.get("total_tokens", 0) or 0 for m in usage_metrics)
    latencies = [m.get("latency_s") for m in usage_metrics if m.get("latency_s") is not None]
    return {
        "dataset": {
            "total_rows": len(rows),
            "processed_rows": len(processed_rows),
            "successful_rows": status_counts.get("ok", 0),
            "failed_rows": status_counts.get("failed", 0),
            "pending_rows": status_counts.get("pending", 0),
            "principle_count": len(principles),
            "arbiter_count": len(arbiter_ids),
            "dataset_distribution": _distribution([row.get("dataset", "") for row in rows]),
            "class_distribution": _distribution([row.get("class", "") for row in rows]),
        },
        "consensus_quality": {
            "entry_observed_agreement_percent": numeric_summary(observed_values),
            "entry_mean_support_percent": numeric_summary(mean_support_values),
            "split_principles_per_entry": numeric_summary(split_counts),
            "unanimous_principles_per_entry": numeric_summary(unanimous_counts),
            "entries_with_any_split_principle": sum(1 for v in split_counts if v > 0),
            "entries_below_60_agreement": sum(1 for v in observed_values if v < 60),
            "entries_below_80_agreement": sum(1 for v in observed_values if v < 80),
        },
        "principles": principle_summary,
        "arbiters": arbiter_summary,
        "pairwise_arbiters": pairwise,
        "resources": {
            "prompt_tokens_total": total_prompt,
            "completion_tokens_total": total_completion,
            "tokens_total": total_tokens,
            "prompt_share_percent": round((total_prompt / total_tokens) * 100, 1) if total_tokens else None,
            "completion_share_percent": round((total_completion / total_tokens) * 100, 1) if total_tokens else None,
            "tokens_per_entry": numeric_summary([m.get("total_tokens") for m in usage_metrics]),
            "latency_s_per_entry": numeric_summary(latencies),
            "arbiter_calls_per_entry": numeric_summary([m.get("arbiter_calls") for m in usage_metrics]),
        },
    }


def build_run_stats(
    rows: list[dict],
    fieldnames: list[str],
    source_file: str,
    *,
    result_limit: int | None = 250,
    result_offset: int = 0,
) -> dict:
    arbiter_ids = detect_arbiters(fieldnames)
    principles = detect_principles(fieldnames)

    stats = {}
    for arb_id in arbiter_ids:
        prompt_sum, completion_sum, total_sum = 0, 0, 0
        model_calls = 0
        latencies = []
        for row in rows:
            pt = safe_int(row.get(f"prompt_tokens_{arb_id}", 0), 0) or 0
            ct = safe_int(row.get(f"completion_tokens_{arb_id}", 0), 0) or 0
            tt = safe_int(row.get(f"total_tokens_{arb_id}", 0), 0) or 0
            if pt > 0:
                prompt_sum += pt
            if ct > 0:
                completion_sum += ct
            if tt > 0:
                total_sum += tt
            model_calls += max(0, safe_int(row.get(f"model_calls_{arb_id}", 1), 1) or 0)
            lat = safe_float(row.get(f"latency_s_{arb_id}", -1), -1)
            if lat >= 0:
                latencies.append(lat)
        stats[arb_id] = {
            "prompt_tokens": prompt_sum,
            "completion_tokens": completion_sum,
            "total_tokens": total_sum,
            "calls": len(latencies),
            "evaluations": len(latencies),
            "model_calls": model_calls,
            "latency_avg_s": round(sum(latencies) / len(latencies), 3) if latencies else 0,
            "latency_min_s": round(min(latencies), 3) if latencies else 0,
            "latency_max_s": round(max(latencies), 3) if latencies else 0,
            "latency_total_s": round(sum(latencies), 3) if latencies else 0,
        }

    status_counts = {"ok": 0, "failed": 0, "pending": 0}
    total_tokens_global = 0
    total_latency_global = 0.0
    for row in rows:
        status_counts[row_processing_status(row, principles)] += 1
        usage_metrics = entry_usage_metrics_from_row(row, arbiter_ids)
        total_tokens_global += usage_metrics["total_tokens"]
        if usage_metrics["latency_s"] is not None:
            total_latency_global += usage_metrics["latency_s"]

    processed_result_rows = status_counts["ok"] + status_counts["failed"]
    effective_result_limit = processed_result_rows if result_limit is None else result_limit
    result_offset = max(0, result_offset)
    result_offset = min(result_offset, max(processed_result_rows - 1, 0)) if processed_result_rows else 0
    result_count = min(max(processed_result_rows - result_offset, 0), effective_result_limit)
    agreement = compute_agreement_metrics(rows, principles, arbiter_ids)
    paper_summary = paper_reporting_summary(rows, principles, arbiter_ids, agreement, status_counts)

    return {
        "source_file": source_file,
        "total_rows": len(rows),
        "successful_rows": status_counts["ok"],
        "failed_rows": status_counts["failed"],
        "pending_rows": status_counts["pending"],
        "total_tokens_global": total_tokens_global,
        "tokens_per_row": round(total_tokens_global / len(rows)) if rows else 0,
        "tokens_per_processed_row": round(total_tokens_global / processed_result_rows) if processed_result_rows else 0,
        "tokens_per_successful_row": round(total_tokens_global / status_counts["ok"]) if status_counts["ok"] else 0,
        "total_latency_s": round(total_latency_global, 3),
        "arbiters": stats,
        "principles": principles,
        "agreement": agreement,
        "paper_summary": paper_summary,
        "results_total": processed_result_rows,
        "results_count": result_count,
        "results_offset": result_offset,
        "results_limit": effective_result_limit,
        "results_truncated": result_offset + result_count < processed_result_rows,
        "results_has_prev": result_offset > 0,
        "results_has_next": result_offset + result_count < processed_result_rows,
        "results": build_result_rows(
            rows,
            principles,
            arbiter_ids,
            effective_result_limit,
            offset=result_offset,
        ),
    }

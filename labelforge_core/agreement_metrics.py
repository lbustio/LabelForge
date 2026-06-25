"""
agreement_metrics.py
--------------------
Metricas de acuerdo/consenso entre arbitros (compartido entre CLI y WebApp).

Metricas calculadas:
- observed_agreement: promedio de acuerdos por pares entre arbitros
- fleiss_kappa: kappa multi-rater para items con todos los arbitros presentes
- pairwise_cohen_kappa_avg: promedio de Cohen kappa para todos los pares de arbitros
- krippendorff_alpha_nominal: alpha nominal para etiquetas binarias, tolera datos faltantes
- positive_rate: proporcion de etiquetas positivas (1)
"""

from typing import Optional
from labelforge_core.utils import safe_binary_int


CONSENSUS_METRIC_KEYS = [
    "items",
    "raters",
    "observed_agreement",
    "fleiss_kappa",
    "pairwise_cohen_kappa_avg",
    "krippendorff_alpha_nominal",
    "positive_rate",
]


def _percent(value: Optional[float]) -> Optional[float]:
    return round(value * 100, 1) if value is not None else None


def _kappa_tone(kappa: Optional[float]) -> str:
    if kappa is None:
        return "secondary"
    if kappa >= 0.8:
        return "success"
    if kappa >= 0.6:
        return "info"
    if kappa >= 0.4:
        return "warning"
    return "danger"


def _kappa_interpretation(kappa: Optional[float]) -> str:
    if kappa is None:
        return "sin datos"
    if kappa >= 0.8:
        return "casi perfecto"
    if kappa >= 0.6:
        return "sustancial"
    if kappa >= 0.4:
        return "moderado"
    if kappa >= 0.2:
        return "bajo"
    if kappa >= 0:
        return "leve"
    return "menor que azar"


def _percent_tone(value: Optional[float]) -> str:
    if value is None:
        return "secondary"
    if value >= 80:
        return "success"
    if value >= 60:
        return "info"
    if value >= 40:
        return "warning"
    return "danger"


def _with_display_fields(metrics: dict) -> dict:
    metrics = dict(metrics)
    metrics["observed_agreement_percent"] = _percent(metrics.get("observed_agreement"))
    metrics["positive_rate_percent"] = _percent(metrics.get("positive_rate"))
    metrics["fleiss_kappa_tone"] = _kappa_tone(metrics.get("fleiss_kappa"))
    metrics["fleiss_kappa_interpretation"] = _kappa_interpretation(metrics.get("fleiss_kappa"))
    metrics["pairwise_cohen_kappa_avg_tone"] = _kappa_tone(metrics.get("pairwise_cohen_kappa_avg"))
    metrics["krippendorff_alpha_nominal_tone"] = _kappa_tone(metrics.get("krippendorff_alpha_nominal"))
    metrics["observed_agreement_tone"] = _percent_tone(metrics.get("observed_agreement_percent"))
    metrics["positive_rate_tone"] = _percent_tone(metrics.get("positive_rate_percent"))
    return metrics


def _cohen_kappa(values_a: list, values_b: list) -> Optional[float]:
    return cohen_kappa_for_pairs([
        (a, b) for a, b in zip(values_a, values_b)
        if a in (0, 1) and b in (0, 1)
    ])


def cohen_kappa_for_pairs(pairs: list[tuple[int, int]]) -> Optional[float]:
    """Cohen kappa para pares binarios ya filtrados."""
    n = len(pairs)
    if n == 0:
        return None
    observed = sum(1 for a, b in pairs if a == b) / n
    p_a_1 = sum(1 for a, _ in pairs if a == 1) / n
    p_b_1 = sum(1 for _, b in pairs if b == 1) / n
    expected = (p_a_1 * p_b_1) + ((1 - p_a_1) * (1 - p_b_1))
    if expected == 1:
        return 1.0 if observed == 1 else None
    return (observed - expected) / (1 - expected)


def _agreement_for_items(items: list, arbiter_ids: list) -> dict:
    """
    items: lista de items, cada item es lista de tuplas (arbiter_id, value) con value en {0,1}
    """
    valid_items = [item for item in items if len(item) >= 2]
    if not valid_items:
        return _with_display_fields(dict.fromkeys(CONSENSUS_METRIC_KEYS, None) | {
            "items": 0,
            "raters": len(arbiter_ids),
        })

    observed_values = []
    for item in valid_items:
        n = len(item)
        pairs = n * (n - 1) / 2
        agreements = 0
        for i in range(n):
            for j in range(i + 1, n):
                if item[i][1] == item[j][1]:
                    agreements += 1
        observed_values.append(agreements / pairs)
    observed_agreement = sum(observed_values) / len(observed_values)

    complete_items = [item for item in valid_items if len(item) == len(arbiter_ids)]
    fleiss_kappa = None
    if complete_items and len(arbiter_ids) >= 2:
        n_raters = len(arbiter_ids)
        p_counts = {0: 0, 1: 0}
        p_i_values = []
        for item in complete_items:
            counts = {0: 0, 1: 0}
            for _, value in item:
                counts[value] += 1
                p_counts[value] += 1
            p_i_values.append(
                ((counts[0] ** 2 + counts[1] ** 2) - n_raters)
                / (n_raters * (n_raters - 1))
            )
        p_bar = sum(p_i_values) / len(p_i_values)
        total_ratings = len(complete_items) * n_raters
        p_0 = p_counts[0] / total_ratings
        p_1 = p_counts[1] / total_ratings
        p_e = (p_0 ** 2) + (p_1 ** 2)
        fleiss_kappa = 1.0 if p_e == 1 and p_bar == 1 else (p_bar - p_e) / (1 - p_e) if p_e != 1 else None

    pairwise_kappas = []
    item_maps = [{arb_id: value for arb_id, value in item} for item in valid_items]
    for i, arb_a in enumerate(arbiter_ids):
        for arb_b in arbiter_ids[i + 1:]:
            values_a, values_b = [], []
            for item in item_maps:
                if arb_a in item and arb_b in item:
                    values_a.append(item[arb_a])
                    values_b.append(item[arb_b])
            kappa = _cohen_kappa(values_a, values_b)
            if kappa is not None:
                pairwise_kappas.append(kappa)

    ordered_disagreements = 0
    ordered_pairs = 0
    category_totals = {0: 0, 1: 0}
    total_values = 0
    for item in valid_items:
        counts = {0: 0, 1: 0}
        for _, value in item:
            counts[value] += 1
            category_totals[value] += 1
            total_values += 1
        n = len(item)
        ordered_disagreements += (n * n) - (counts[0] ** 2 + counts[1] ** 2)
        ordered_pairs += n * (n - 1)
    do = ordered_disagreements / ordered_pairs if ordered_pairs else None
    de_den = total_values * (total_values - 1)
    de = (
        ((total_values ** 2) - (category_totals[0] ** 2 + category_totals[1] ** 2)) / de_den
        if de_den else None
    )
    alpha = None
    if do is not None and de is not None:
        alpha = 1.0 if de == 0 and do == 0 else 1 - (do / de) if de != 0 else None

    positives = sum(value for item in valid_items for _, value in item)
    total = sum(len(item) for item in valid_items)
    return _with_display_fields({
        "items": len(valid_items),
        "raters": len(arbiter_ids),
        "observed_agreement": round(observed_agreement, 4),
        "fleiss_kappa": round(fleiss_kappa, 4) if fleiss_kappa is not None else None,
        "pairwise_cohen_kappa_avg": round(sum(pairwise_kappas) / len(pairwise_kappas), 4) if pairwise_kappas else None,
        "krippendorff_alpha_nominal": round(alpha, 4) if alpha is not None else None,
        "positive_rate": round(positives / total, 4) if total else None,
    })


def compute_agreement_metrics(rows: list, principles: list, arbiter_ids: list) -> dict:
    """
    rows: lista de dicts con columnas {principle}_{arbiter_id} = 0/1 (como int o str)
    principles: lista de nombres de principios
    arbiter_ids: lista de IDs de arbitros
    """
    per_principle = {}
    global_items = []
    for principle in principles:
        items = []
        for row in rows:
            item = []
            for arb_id in arbiter_ids:
                val = row.get(f"{principle}_{arb_id}")
                value = safe_binary_int(val)
                if value is not None:
                    item.append((arb_id, value))
            if item:
                items.append(item)
                global_items.append(item)
        per_principle[principle] = _agreement_for_items(items, arbiter_ids)
    return {
        "global": _agreement_for_items(global_items, arbiter_ids),
        "per_principle": per_principle,
        "notes": [
            "observed_agreement = promedio de acuerdos por pares entre arbitros.",
            "fleiss_kappa = kappa multi-rater para items con todos los arbitros presentes.",
            "pairwise_cohen_kappa_avg = promedio de Cohen kappa para todos los pares de arbitros.",
            "krippendorff_alpha_nominal = alpha nominal para etiquetas binarias, tolera datos faltantes.",
        ],
    }


def compute_majority_consensus(arb_results: list, principles: list) -> dict:
    """Binary majority consensus for one entry across arbiter result dicts."""
    n = len(arb_results)
    majority = (n // 2) + 1
    return {
        principle: 1 if sum(result.get(principle, 0) for result in arb_results) >= majority else 0
        for principle in principles
    }


def compute_consensus_metrics(results: dict, principles: list, arbiters: list) -> dict:
    """
    Calculo de metricas usado desde labelforge_core/label_persuasion.py
    Traduce el formato de entrada (dict de dicts y lista de arbitros) al esperado por compute_agreement_metrics.
    """
    rows = list(results.values())
    arbiter_ids = [arb["id"] for arb in arbiters]
    return compute_agreement_metrics(rows, principles, arbiter_ids)


def compute_entry_consensus_metrics(record: dict, arbiters: list, principles: list) -> dict:
    """Metricas de solidez del consenso para una sola entrada."""
    per_principle = {}
    total_pairs = 0
    agreed_pairs = 0
    support_ratios = []
    unanimous = 0
    split = 0

    for principle in principles:
        values = []
        for arb in arbiters:
            value = record.get(f"{principle}_{arb['id']}")
            if value in (0, 1):
                values.append(value)
        if not values:
            continue

        ones = sum(1 for value in values if value == 1)
        zeros = len(values) - ones
        consensus_value = record.get(f"{principle}_consensus")
        support = ones if consensus_value == 1 else zeros
        support_ratio = support / len(values)
        support_ratios.append(support_ratio)

        pairs = len(values) * (len(values) - 1) // 2
        principle_agreed_pairs = 0
        for i in range(len(values)):
            for j in range(i + 1, len(values)):
                if values[i] == values[j]:
                    principle_agreed_pairs += 1
        total_pairs += pairs
        agreed_pairs += principle_agreed_pairs

        is_unanimous = support == len(values)
        if is_unanimous:
            unanimous += 1
        elif len(values) == len(arbiters):
            split += 1

        per_principle[principle] = {
            "consensus": consensus_value,
            "support": support,
            "raters": len(values),
            "support_ratio": round(support_ratio, 4),
            "votes_0": zeros,
            "votes_1": ones,
            "unanimous": is_unanimous,
        }

    observed_agreement = agreed_pairs / total_pairs if total_pairs else None
    mean_support_ratio = sum(support_ratios) / len(support_ratios) if support_ratios else None
    return {
        "observed_agreement": round(observed_agreement, 4) if observed_agreement is not None else None,
        "observed_agreement_percent": round(observed_agreement * 100, 1) if observed_agreement is not None else None,
        "mean_support_ratio": round(mean_support_ratio, 4) if mean_support_ratio is not None else None,
        "mean_support_percent": round(mean_support_ratio * 100, 1) if mean_support_ratio is not None else None,
        "unanimous_principles": unanimous,
        "split_principles": split,
        "principle_count": len(per_principle),
        "per_principle": per_principle,
    }


def format_entry_consensus_metrics(metrics: dict, principles: list) -> str:
    agreement = metrics.get("observed_agreement")
    agreement_text = f"{agreement * 100:.1f}%" if agreement is not None else "N/A"
    principle_count = metrics.get("principle_count", 0)
    support_parts = []
    per_principle = metrics.get("per_principle", {})
    for principle in principles:
        item = per_principle.get(principle)
        if item:
            support_parts.append(f"{principle}:{item['support']}/{item['raters']}")
    return (
        f"acuerdo={agreement_text} "
        f"unanimidad={metrics.get('unanimous_principles', 0)}/{principle_count} "
        f"divididos={metrics.get('split_principles', 0)}/{principle_count} "
        f"apoyo=" + ",".join(support_parts)
    )


def flatten_entry_consensus_metrics(metrics: dict, principles: list) -> dict:
    flat = {
        "entry_consensus_observed_agreement": metrics.get("observed_agreement"),
        "entry_consensus_observed_agreement_percent": metrics.get("observed_agreement_percent"),
        "entry_consensus_mean_support_ratio": metrics.get("mean_support_ratio"),
        "entry_consensus_mean_support_percent": metrics.get("mean_support_percent"),
        "entry_consensus_unanimous_principles": metrics.get("unanimous_principles"),
        "entry_consensus_split_principles": metrics.get("split_principles"),
        "entry_consensus_principle_count": metrics.get("principle_count"),
    }
    per_principle = metrics.get("per_principle", {})
    for principle in principles:
        item = per_principle.get(principle, {})
        flat[f"{principle}_entry_consensus_support"] = item.get("support")
        flat[f"{principle}_entry_consensus_raters"] = item.get("raters")
        flat[f"{principle}_entry_consensus_support_ratio"] = item.get("support_ratio")
        flat[f"{principle}_entry_consensus_votes_0"] = item.get("votes_0")
        flat[f"{principle}_entry_consensus_votes_1"] = item.get("votes_1")
        flat[f"{principle}_entry_consensus_unanimous"] = item.get("unanimous")
    return flat


def build_entry_consensus_metric_fieldnames(principles: list) -> list:
    fields = [
        "entry_consensus_observed_agreement",
        "entry_consensus_observed_agreement_percent",
        "entry_consensus_mean_support_ratio",
        "entry_consensus_mean_support_percent",
        "entry_consensus_unanimous_principles",
        "entry_consensus_split_principles",
        "entry_consensus_principle_count",
    ]
    for principle in principles:
        fields.extend([
            f"{principle}_entry_consensus_support",
            f"{principle}_entry_consensus_raters",
            f"{principle}_entry_consensus_support_ratio",
            f"{principle}_entry_consensus_votes_0",
            f"{principle}_entry_consensus_votes_1",
            f"{principle}_entry_consensus_unanimous",
        ])
    return fields


def flatten_consensus_metrics(metrics: dict, principles: list) -> dict:
    """Aplana metricas a dict plano para escribir como columnas CSV."""
    flat = {}
    for key in CONSENSUS_METRIC_KEYS:
        flat[f"consensus_global_{key}"] = metrics.get("global", {}).get(key)
    per_principle = metrics.get("per_principle", {})
    for principle in principles:
        principle_metrics = per_principle.get(principle, {})
        for key in CONSENSUS_METRIC_KEYS:
            flat[f"{principle}_consensus_{key}"] = principle_metrics.get(key)
    return flat


def build_consensus_metric_fieldnames(principles: list) -> list:
    fields = [f"consensus_global_{key}" for key in CONSENSUS_METRIC_KEYS]
    for principle in principles:
        fields.extend(f"{principle}_consensus_{key}" for key in CONSENSUS_METRIC_KEYS)
    return fields

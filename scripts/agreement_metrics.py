"""
agreement_metrics.py
--------------------
Métricas de acuerdo/consenso entre árbitros (compartido entre CLI y WebApp).

Métricas calculadas:
- observed_agreement: promedio de acuerdos por pares entre árbitros
- fleiss_kappa: kappa multi-rater para items con todos los árbitros presentes
- pairwise_cohen_kappa_avg: promedio de Cohen kappa para todos los pares de árbitros
- krippendorff_alpha_nominal: alpha nominal para etiquetas binarias, tolera datos faltantes
- positive_rate: proporción de etiquetas positivas (1)
"""

from typing import Optional
from scripts.utils import safe_binary_int


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
    pairs = [
        (a, b) for a, b in zip(values_a, values_b)
        if a in (0, 1) and b in (0, 1)
    ]
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
    arbiter_ids: lista de IDs de árbitros
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


def compute_consensus_metrics(results: dict, principles: list, arbiters: list) -> dict:
    """
    Cálculo de métricas usado desde scripts/label_persuasion.py
    Traduce el formato de entrada (dict de dicts y lista de árbitros) al esperado por compute_agreement_metrics.
    """
    rows = list(results.values())
    arbiter_ids = [arb["id"] for arb in arbiters]
    return compute_agreement_metrics(rows, principles, arbiter_ids)


def flatten_consensus_metrics(metrics: dict, principles: list) -> dict:
    """Aplana métricas a dict plano para escribir como columnas CSV."""
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

"""
app.py - Flask webapp para configurar y lanzar el proceso de etiquetado
de principios de persuasion de Ana Ferreira.
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, send_from_directory, stream_with_context

# ---------------------------------------------------------------------------
# Módulo compartido de métricas de acuerdo y utilidades
# ---------------------------------------------------------------------------
from scripts.agreement_metrics import compute_agreement_metrics
from scripts.utils import safe_int as _safe_int, safe_float as _safe_float

# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------
BASE_DIR        = Path(__file__).resolve().parent.parent
WEBAPP_DIR      = Path(__file__).resolve().parent
CONFIG_PATH     = BASE_DIR / "scripts" / "arbiters_config.json"
SCRIPTS_DIR     = BASE_DIR / "scripts"
OUTPUT_DIR      = BASE_DIR / "output"
CHECKPOINTS_DIR = BASE_DIR / "checkpoints"

load_dotenv(BASE_DIR / "secrets.env")

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Descripciones orientadas al problema por modelo Gemini
# ---------------------------------------------------------------------------
GEMINI_MODEL_DESCRIPTIONS = {
    "gemini-2.5-pro":               "Maximo razonamiento. Ideal para analisis matizado y profundo de persuasion.",
    "gemini-3.1-pro-preview":       "Siguiente generacion Pro. Razonamiento avanzado para deteccion compleja.",
    "gemini-3-pro-preview":         "Pro de nueva generacion. Muy alto razonamiento para analisis sutil.",
    "gemini-pro-latest":            "Ultima version Pro estable. Buena capacidad de razonamiento general.",
    "gemini-2.5-flash":             "Balance optimo calidad/velocidad. Recomendado para la mayoria de arbitros.",
    "gemini-3.5-flash":             "Flash de nueva generacion. Rapido con buen razonamiento contextual.",
    "gemini-3.1-flash-lite":        "Flash-Lite moderno. Rapido y eficiente para analisis estructurado.",
    "gemini-3-flash-preview":       "Flash de nueva generacion. Alta velocidad con capacidad de razonamiento.",
    "gemini-flash-latest":          "Ultima version Flash estable. Rapido y preciso para etiquetado masivo.",
    "gemini-2.0-flash":             "Flash rapido y versatil. Buena relacion velocidad/precision.",
    "gemini-2.0-flash-001":         "Version estable de 2.0 Flash. Comportamiento predecible en produccion.",
    "gemini-2.5-flash-lite":        "Version ligera de 2.5 Flash. Optimo para alto volumen con bajo costo.",
    "gemini-2.0-flash-lite":        "Version mas ligera. Maximo throughput, menor precision analitica.",
    "gemini-2.0-flash-lite-001":    "Flash-Lite estable. Para procesar grandes volumenes rapidamente.",
    "gemini-flash-lite-latest":     "Ultima Flash-Lite. Minimo costo por entrada, menor razonamiento.",
    "gemma-4-26b-a4b-it":           "Modelo abierto Gemma 26B. Buena capacidad para analisis textual.",
    "gemma-4-31b-it":               "Modelo abierto Gemma 31B. Mayor capacidad que 26B para razonamiento.",
}

ANTHROPIC_MODEL_DESCRIPTIONS = {
    "claude-opus-4-5":              "Maximo razonamiento de Anthropic. Analisis profundo y matizado.",
    "claude-opus-4-0":              "Opus de ultima generacion. Capacidad analitica excepcional.",
    "claude-sonnet-4-5":            "Balance ideal calidad/velocidad. Muy recomendado para arbitros.",
    "claude-sonnet-4-0":            "Sonnet de alta capacidad. Excelente comprension contextual.",
    "claude-haiku-4-5":             "Haiku rapido y eficiente. Ideal para alto volumen de entradas.",
    "claude-3-5-sonnet-20241022":   "Sonnet 3.5 estable. Excelente razonamiento y velocidad.",
    "claude-3-5-haiku-20241022":    "Haiku 3.5 rapido. Buen balance velocidad/precision.",
    "claude-3-opus-20240229":       "Opus 3 de alta capacidad. Muy buen razonamiento analitico.",
}

OPENAI_MODEL_DESCRIPTIONS = {
    "gpt-4o":               "GPT-4o multimodal. Maximo razonamiento y comprension contextual.",
    "gpt-4o-mini":          "GPT-4o Mini. Rapido y eficiente, buena precision para etiquetado.",
    "gpt-4-turbo":          "GPT-4 Turbo. Alta capacidad analitica con buen contexto.",
    "gpt-4":                "GPT-4 clasico. Razonamiento solido y confiable.",
    "gpt-3.5-turbo":        "GPT-3.5 Turbo. Rapido y economico, menor profundidad analitica.",
    "o1":                   "o1 con razonamiento extendido. Maximo para analisis complejo.",
    "o1-mini":              "o1-mini. Razonamiento avanzado en menor escala.",
    "o3-mini":              "o3-mini. Razonamiento de siguiente generacion, eficiente.",
}

# Cache por proveedor {provider_type: {data, ts}}
_models_cache: dict = {}
MODELS_CACHE_TTL = 300

# ---------------------------------------------------------------------------
# Limites de rate por modelo y tier (fuente: documentacion oficial Google AI)
# Estructura: { model_pattern: { tier: { rpm, tpm, rpd } } }
# Se usa pattern matching: si el model_id contiene el patron, aplican esos limites.
# ---------------------------------------------------------------------------

# Gemini free tier (sin tarjeta de credito / sin facturacion activada)
# Gemini paid tier (pay-as-you-go con facturacion activada)
GEMINI_RATE_LIMITS = {
    # --- Gemini 2.5 ---
    "gemini-2.5-pro": {
        "free": {"rpm": 5,    "tpm": 250_000,    "rpd": 25},
        "paid": {"rpm": 150,  "tpm": 2_000_000,  "rpd": None},
    },
    "gemini-2.5-flash": {
        "free": {"rpm": 10,   "tpm": 250_000,    "rpd": 500},
        "paid": {"rpm": 1000, "tpm": 4_000_000,  "rpd": None},
    },
    "gemini-2.5-flash-lite": {
        "free": {"rpm": 30,   "tpm": 1_000_000,  "rpd": 1500},
        "paid": {"rpm": 2000, "tpm": 4_000_000,  "rpd": None},
    },
    # --- Gemini 2.0 ---
    "gemini-2.0-flash": {
        "free": {"rpm": 15,   "tpm": 1_000_000,  "rpd": 1500},
        "paid": {"rpm": 2000, "tpm": 4_000_000,  "rpd": None},
    },
    "gemini-2.0-flash-lite": {
        "free": {"rpm": 30,   "tpm": 1_000_000,  "rpd": 1500},
        "paid": {"rpm": 4000, "tpm": 4_000_000,  "rpd": None},
    },
    # --- Gemini 1.5 (referencia historica) ---
    "gemini-1.5-pro": {
        "free": {"rpm": 2,    "tpm": 32_000,     "rpd": 50},
        "paid": {"rpm": 360,  "tpm": 4_000_000,  "rpd": None},
    },
    "gemini-1.5-flash": {
        "free": {"rpm": 15,   "tpm": 1_000_000,  "rpd": 1500},
        "paid": {"rpm": 1000, "tpm": 4_000_000,  "rpd": None},
    },
    # --- Gemini 3.x / nuevos (sin limites publicados aun, estimacion conservadora) ---
    "gemini-3": {
        "free": {"rpm": 5,    "tpm": 250_000,    "rpd": 25},
        "paid": {"rpm": 150,  "tpm": 2_000_000,  "rpd": None},
    },
    # --- Gemma (open models via API) ---
    "gemma": {
        "free": {"rpm": 30,   "tpm": 1_000_000,  "rpd": 1500},
        "paid": {"rpm": 1000, "tpm": 4_000_000,  "rpd": None},
    },
    # --- Fallback generico ---
    "_default": {
        "free": {"rpm": 10,   "tpm": 250_000,    "rpd": 500},
        "paid": {"rpm": 500,  "tpm": 2_000_000,  "rpd": None},
    },
}


def get_rate_limits(model_id: str, tier: str = "free") -> dict:
    """
    Devuelve los limites de rate para un modelo dado.
    Busca por coincidencia de prefijo, de mas especifico a menos.
    """
    model_id = model_id.lower()
    # Ordenar por longitud descendente para que los patrones mas especificos tengan prioridad
    for pattern in sorted(GEMINI_RATE_LIMITS.keys(), key=len, reverse=True):
        if pattern == "_default":
            continue
        if model_id.startswith(pattern) or pattern in model_id:
            limits = GEMINI_RATE_LIMITS[pattern].get(tier, GEMINI_RATE_LIMITS[pattern]["free"])
            return {**limits, "pattern_matched": pattern}
    # Fallback
    limits = GEMINI_RATE_LIMITS["_default"].get(tier, GEMINI_RATE_LIMITS["_default"]["free"])
    return {**limits, "pattern_matched": "_default"}


def compute_min_delay(model_id: str, tier: str, n_arbiters: int = 1) -> dict:
    """
    Calcula el delay minimo recomendado en segundos dado:
      - Los limites RPM del modelo
      - El numero de arbitros (cada entrada genera N llamadas, una por arbitro)

    Formula: delay_min = (60 / RPM) * n_arbiters
    Se agrega un 20% de margen de seguridad.
    """
    limits = get_rate_limits(model_id, tier)
    rpm    = limits.get("rpm", 10)

    # Segundos por request a este RPM
    secs_per_req = 60.0 / rpm
    # Con N arbitros por entrada, el delay entre entradas debe ser al menos:
    delay_min = secs_per_req * n_arbiters * 1.2  # 20% de margen

    # Nivel de alerta
    if rpm <= 5:
        alert_level = "danger"
        alert_msg   = (f"Modelo muy restrictivo en nivel gratuito ({rpm} RPM). "
                       f"Con {n_arbiters} arbitros necesitas al menos {delay_min:.1f}s entre entradas. "
                       f"Considera activar facturacion o usar un modelo Flash.")
    elif rpm <= 15:
        alert_level = "warning"
        alert_msg   = (f"Limite moderado ({rpm} RPM). "
                       f"Espera minima recomendada: {delay_min:.1f}s con {n_arbiters} arbitros.")
    else:
        alert_level = "info"
        alert_msg   = (f"Limite adecuado ({rpm} RPM). "
                       f"Espera minima recomendada: {delay_min:.1f}s con {n_arbiters} arbitros.")

    return {
        "model":           model_id,
        "tier":            tier,
        "rpm":             rpm,
        "tpm":             limits.get("tpm"),
        "rpd":             limits.get("rpd"),
        "n_arbiters":      n_arbiters,
        "min_delay_s":     round(delay_min, 1),
        "safe_delay_s":    round(delay_min * 1.5, 1),  # margen extra para produccion
        "alert_level":     alert_level,
        "alert_msg":       alert_msg,
    }


# ---------------------------------------------------------------------------
# Estado global del proceso
# ---------------------------------------------------------------------------
METRIC_PREFIX = "__RUN_METRIC__ "


def _empty_current_run_stats() -> dict:
    return {
        "active": False,
        "finished": False,
        "run_id": None,
        "source_file": None,
        "output_path": None,
        "input_file": None,
        "config_file": None,
        "started_at": None,
        "updated_at": None,
        "completed_before_start": 0,
        "total_rows": 0,
        "processed_rows": 0,
        "successful_rows": 0,
        "failed_rows": 0,
        "skipped_rows": 0,
        "queued_rows": 0,
        "pending_rows": 0,
        "total_tokens_global": 0,
        "total_latency_s": 0.0,
        "pause_kind": "",
        "pause_reason": "",
        "principles": [],
        "arbiters": {},
        "last_claim": None,
        "_claim_statuses": {},
        "_queued_indices": set(),
    }


_process_state = {
    "running":   False,
    "pid":       None,
    "log_queue": queue.Queue(),
    "proc":      None,
    "current_stats": _empty_current_run_stats(),
    "stats_lock": threading.Lock(),
}


def _public_current_stats(stats: dict = None) -> dict:
    stats = stats or _process_state["current_stats"]
    public = {k: v for k, v in stats.items() if not k.startswith("_")}
    processed_rows = int(public.get("processed_rows", 0) or 0)
    successful_rows = int(public.get("successful_rows", 0) or 0)
    total_tokens = int(public.get("total_tokens_global", 0) or 0)
    public["arbiters"] = {
        arb_id: {
            **arb_stats,
            "latency_avg_s": round(
                arb_stats.get("latency_total_s", 0) / arb_stats.get("calls", 1),
                3,
            ) if arb_stats.get("calls", 0) else 0,
            "latency_min_s": arb_stats.get("latency_min_s") or 0,
            "latency_max_s": arb_stats.get("latency_max_s") or 0,
            "latency_total_s": round(arb_stats.get("latency_total_s", 0), 3),
        }
        for arb_id, arb_stats in stats.get("arbiters", {}).items()
    }
    public["pending_rows"] = max(
        0,
        int(public.get("total_rows", 0) or 0) - processed_rows,
    )
    public["queued_rows"] = len(stats.get("_queued_indices", set()))
    public["total_latency_s"] = round(public.get("total_latency_s", 0) or 0, 3)
    public["tokens_per_processed_row"] = round(total_tokens / processed_rows) if processed_rows else 0
    public["tokens_per_successful_row"] = round(total_tokens / successful_rows) if successful_rows else 0
    return public


def _reset_current_stats():
    with _process_state["stats_lock"]:
        _process_state["current_stats"] = _empty_current_run_stats()


def _ensure_live_arbiter(stats: dict, arb_id: str, name: str = None) -> dict:
    if arb_id not in stats["arbiters"]:
        stats["arbiters"][arb_id] = {
            "id": arb_id,
            "name": name or arb_id,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "calls": 0,
            "latency_total_s": 0.0,
            "latency_min_s": None,
            "latency_max_s": None,
        }
    elif name:
        stats["arbiters"][arb_id]["name"] = name
    return stats["arbiters"][arb_id]


def _apply_initial_summary(stats: dict, summary: dict):
    if not summary:
        return
    for key in ["processed_rows", "successful_rows", "failed_rows", "skipped_rows",
                "total_tokens_global", "total_latency_s"]:
        stats[key] = summary.get(key, stats.get(key, 0))
    for arb_id, incoming in summary.get("arbiters", {}).items():
        arb_stats = _ensure_live_arbiter(stats, arb_id)
        for key in ["prompt_tokens", "completion_tokens", "total_tokens", "calls"]:
            arb_stats[key] = incoming.get(key, arb_stats.get(key, 0))
        arb_stats["latency_total_s"] = incoming.get("latency_total_s", 0) or 0
        arb_stats["latency_min_s"] = incoming.get("latency_min_s") or None
        arb_stats["latency_max_s"] = incoming.get("latency_max_s") or None


def _set_live_claim_status(stats: dict, row_index, status: str):
    key = str(row_index)
    old = stats["_claim_statuses"].get(key)
    if old == status:
        return
    counters = {
        "ok": "successful_rows",
        "failed": "failed_rows",
        "skipped": "skipped_rows",
    }
    if old in counters:
        stats[counters[old]] = max(0, stats.get(counters[old], 0) - 1)
    if status in counters:
        stats[counters[status]] = stats.get(counters[status], 0) + 1
    stats["_claim_statuses"][key] = status
    stats["processed_rows"] = (
        stats.get("completed_before_start", 0)
        + len(stats["_claim_statuses"])
    )


def _apply_run_metric_event(event: dict) -> dict:
    with _process_state["stats_lock"]:
        stats = _process_state["current_stats"]
        kind = event.get("event")
        stats["updated_at"] = event.get("timestamp") or time.strftime("%Y-%m-%dT%H:%M:%S")

        if kind == "start":
            stats = _empty_current_run_stats()
            _process_state["current_stats"] = stats
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
            stats["completed_before_start"] = int(event.get("completed_before_start") or 0)
            stats["principles"] = event.get("principles", [])
            for arb in event.get("arbiters", []):
                _ensure_live_arbiter(stats, arb.get("id"), arb.get("name"))
            _apply_initial_summary(stats, event.get("initial_summary", {}))

        elif kind == "claim_ok":
            _set_live_claim_status(stats, event.get("row_index"), "ok")
            stats["_queued_indices"].discard(str(event.get("row_index")))
            stats["total_tokens_global"] += int(event.get("total_tokens") or 0)
            if event.get("total_latency_s") is not None:
                stats["total_latency_s"] += float(event.get("total_latency_s") or 0)
            stats["last_claim"] = event
            for arb in event.get("arbiters", []):
                arb_stats = _ensure_live_arbiter(stats, arb.get("id"), arb.get("name"))
                arb_stats["prompt_tokens"] += int(arb.get("prompt_tokens") or 0)
                arb_stats["completion_tokens"] += int(arb.get("completion_tokens") or 0)
                arb_stats["total_tokens"] += int(arb.get("total_tokens") or 0)
                if arb.get("latency_s") is not None:
                    latency = float(arb.get("latency_s") or 0)
                    arb_stats["calls"] += 1
                    arb_stats["latency_total_s"] += latency
                    arb_stats["latency_min_s"] = latency if arb_stats["latency_min_s"] is None else min(arb_stats["latency_min_s"], latency)
                    arb_stats["latency_max_s"] = latency if arb_stats["latency_max_s"] is None else max(arb_stats["latency_max_s"], latency)

        elif kind == "claim_failed":
            _set_live_claim_status(stats, event.get("row_index"), "failed")
            if event.get("queued"):
                stats["_queued_indices"].add(str(event.get("row_index")))
            stats["last_claim"] = event

        elif kind == "claim_skipped":
            _set_live_claim_status(stats, event.get("row_index"), "skipped")
            stats["last_claim"] = event

        elif kind == "output_written":
            stats["source_file"] = event.get("output_file") or stats.get("source_file")
            stats["output_path"] = event.get("output_path") or stats.get("output_path")

        elif kind == "complete":
            stats["active"] = False
            stats["finished"] = True
            stats["source_file"] = event.get("output_file") or stats.get("source_file")
            stats["output_path"] = event.get("output_path") or stats.get("output_path")
            if event.get("final_summary"):
                _apply_initial_summary(stats, event["final_summary"])

        elif kind == "paused":
            stats["active"] = False
            stats["finished"] = False
            stats["pause_kind"] = event.get("pause_kind", "")
            stats["pause_reason"] = event.get("pause_reason", "")

        return _public_current_stats(stats)


# ---------------------------------------------------------------------------
# Helpers de config
# ---------------------------------------------------------------------------
def read_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def write_config(cfg: dict):
    tmp = CONFIG_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    tmp.replace(CONFIG_PATH)


# ---------------------------------------------------------------------------
# API: Modelos por proveedor
# ---------------------------------------------------------------------------

def _get_api_key(provider_type: str, override: str = "") -> str:
    if override:
        return override
    env_map = {
        "gemini":        "GEMINI_API_KEY",
        "anthropic":     "ANTHROPIC_API_KEY",
        "openai":        "OPENAI_API_KEY",
        "openai_compat": "OPENAI_API_KEY",
        "ollama":        "",
    }
    env_var = env_map.get(provider_type, "")
    return os.environ.get(env_var, "") if env_var else ""


@app.route("/api/models", methods=["GET"])
def api_models():
    """
    Devuelve los modelos disponibles para un proveedor dado.
    Query params:
      - provider : gemini | vertex_gemini | anthropic | openai | ollama | openai_compat
      - api_key  : opcional, reemplazo directo de la clave en secrets.env
      - base_url : para ollama / openai_compat
      - project  : Google Cloud project para vertex_gemini
      - location : region Vertex AI para vertex_gemini
    """
    provider = request.args.get("provider", "gemini")
    api_key  = request.args.get("api_key", "").strip() or _get_api_key(provider)
    base_url = request.args.get("base_url", "").strip()
    project  = request.args.get("project", "").strip() or os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    location = request.args.get("location", "").strip() or os.environ.get("VERTEX_LOCATION", "us-central1").strip()

    cache_key = f"{provider}:{base_url}:{project}:{location}"
    now = time.time()
    if cache_key in _models_cache and (now - _models_cache[cache_key]["ts"]) < MODELS_CACHE_TTL:
        return jsonify(_models_cache[cache_key]["data"])

    try:
        if provider == "gemini":
            result = _list_gemini_models(api_key)
        elif provider == "vertex_gemini":
            result = _list_vertex_gemini_models(project, location)
        elif provider == "anthropic":
            result = _list_anthropic_models(api_key)
        elif provider == "openai":
            result = _list_openai_models(api_key)
        elif provider in ("ollama", "openai_compat"):
            result = _list_openai_compat_models(api_key, base_url)
        else:
            return jsonify({"error": f"Proveedor desconocido: {provider}"}), 400
    except Exception as e:
        return jsonify({"error": str(e)[:300]}), 502

    _models_cache[cache_key] = {"data": result, "ts": now}
    return jsonify(result)


def _list_gemini_models(api_key: str) -> list:
    if not api_key:
        raise ValueError("Se requiere GEMINI_API_KEY en secrets.env o como parametro.")
    from google import genai as gai
    client = gai.Client(api_key=api_key)
    raw    = list(client.models.list())

    EXCLUDE = ["tts", "image", "robotics", "lyria", "computer-use",
               "deep-research", "antigravity", "nano-banana", "clip"]
    result  = []
    for m in raw:
        if "generateContent" not in (m.supported_actions or []):
            continue
        mid = m.name.replace("models/", "")
        if any(k in mid.lower() for k in EXCLUDE):
            continue
        desc = GEMINI_MODEL_DESCRIPTIONS.get(mid, _generic_desc(mid))
        result.append({"id": mid, "display_name": m.display_name or mid, "task_description": desc})

    result.sort(key=lambda m: _tier_sort(m["id"]))
    return result


def _list_vertex_gemini_models(project: str, location: str) -> list:
    if not project:
        raise ValueError("Se requiere GOOGLE_CLOUD_PROJECT o project para Vertex AI.")

    # Vertex AI no necesita API Key y se autentica con ADC / Service Account.
    # Mantenemos una lista curada de modelos generativos de texto para evitar
    # depender del endpoint de AI Studio al configurar arbitros Vertex.
    model_ids = [
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash-001",
        "gemini-2.0-flash-lite-001",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
    ]
    result = [
        {"id": mid, "display_name": mid, "task_description": GEMINI_MODEL_DESCRIPTIONS.get(mid, _generic_desc(mid))}
        for mid in model_ids
    ]

    result.sort(key=lambda m: _tier_sort(m["id"]))
    return result


def _list_anthropic_models(api_key: str) -> list:
    if not api_key:
        raise ValueError("Se requiere ANTHROPIC_API_KEY en secrets.env o como parametro.")
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    raw    = client.models.list(limit=50)
    result = []
    for m in raw.data:
        mid  = m.id
        desc = ANTHROPIC_MODEL_DESCRIPTIONS.get(mid, _generic_desc(mid))
        result.append({"id": mid, "display_name": m.display_name or mid, "task_description": desc})
    result.sort(key=lambda m: _tier_sort(m["id"]))
    return result


def _list_openai_models(api_key: str) -> list:
    if not api_key:
        raise ValueError("Se requiere OPENAI_API_KEY en secrets.env o como parametro.")
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    raw    = client.models.list()
    CHAT_PREFIXES = ("gpt-", "o1", "o3", "o4", "chatgpt")
    result = []
    for m in raw.data:
        if not any(m.id.startswith(p) for p in CHAT_PREFIXES):
            continue
        desc = OPENAI_MODEL_DESCRIPTIONS.get(m.id, _generic_desc(m.id))
        result.append({"id": m.id, "display_name": m.id, "task_description": desc})
    result.sort(key=lambda m: _tier_sort(m["id"]))
    return result


def _list_openai_compat_models(api_key: str, base_url: str) -> list:
    if not base_url:
        raise ValueError("Se requiere base_url para proveedor openai_compat / ollama.")
    from openai import OpenAI
    client = OpenAI(api_key=api_key or "local", base_url=base_url)
    raw    = client.models.list()
    result = [
        {"id": m.id, "display_name": m.id, "task_description": _generic_desc(m.id)}
        for m in raw.data
    ]
    result.sort(key=lambda m: m["id"])
    return result


def _generic_desc(mid: str) -> str:
    if "opus" in mid or "pro" in mid:   return "Modelo de alta capacidad analitica."
    if "lite" in mid or "haiku" in mid: return "Modelo ligero. Alta velocidad, menor profundidad."
    if "flash" in mid or "sonnet" in mid or "mini" in mid: return "Balance velocidad/calidad."
    return "Modelo de proposito general."


def _tier_sort(mid: str) -> tuple:
    if "opus" in mid or ("pro" in mid and "lite" not in mid): tier = 0
    elif "lite" in mid or "haiku" in mid or "mini" in mid:    tier = 2
    else:                                                      tier = 1
    return (tier, mid)


# ---------------------------------------------------------------------------
# API: Rate limits por modelo
# ---------------------------------------------------------------------------
@app.route("/api/rate-limits", methods=["GET"])
def api_rate_limits():
    """
    Devuelve los limites de rate y el delay minimo recomendado para un modelo dado.
    Query params:
      - model   : id del modelo (ej: gemini-2.5-pro)
      - tier    : free | paid (default: free)
      - arbiters: numero de arbitros configurados (default: cuenta los del config actual)
    """
    model  = request.args.get("model", "").strip()
    tier   = request.args.get("tier", "free").strip()
    if tier not in ("free", "paid"):
        tier = "free"

    # Numero de arbitros activos
    try:
        n_arbiters = int(request.args.get("arbiters", 0))
        if n_arbiters <= 0:
            cfg        = read_config()
            n_arbiters = len(cfg["arbiters"])
    except (ValueError, TypeError):
        n_arbiters = 1

    if not model:
        # Devolver los limites del modelo mas restrictivo de los configurados (worst case)
        cfg    = read_config()
        models = [a["provider"].get("model", "") for a in cfg["arbiters"]
                  if a.get("provider", {}).get("type") in ("gemini", "vertex_gemini")]
        if not models:
            return jsonify({"error": "No hay arbitros Gemini configurados"}), 400

        # Calcular para cada modelo y devolver el mas restrictivo (menor RPM)
        results   = [compute_min_delay(m, tier, n_arbiters) for m in models if m]
        worst     = min(results, key=lambda r: r["rpm"]) if results else None
        if not worst:
            return jsonify({"error": "Ningun modelo Gemini configurado tiene limites disponibles"}), 400
        worst["all_models"] = results
        return jsonify(worst)

    return jsonify(compute_min_delay(model, tier, n_arbiters))


@app.route("/api/rate-limits/summary", methods=["GET"])
def api_rate_limits_summary():
    """
    Devuelve un resumen de limites para TODOS los arbitros configurados,
    con la recomendacion global de delay minimo (el mas restrictivo gana).
    Query params:
      - tier: free | paid (default: free)
    """
    tier = request.args.get("tier", "free").strip()
    if tier not in ("free", "paid"):
        tier = "free"

    cfg        = read_config()
    arbiters   = cfg["arbiters"]
    n_arbiters = len(arbiters)

    results = []
    for arb in arbiters:
        p     = arb.get("provider", {})
        model = p.get("model", "")
        ptype = p.get("type", "")
        if ptype in ("gemini", "vertex_gemini") and model:
            r = compute_min_delay(model, tier, n_arbiters)
            r["arb_id"]   = arb["id"]
            r["arb_name"] = arb["name"]
            results.append(r)
        else:
            # Proveedor no-Gemini: no tenemos limites conocidos, informamos
            results.append({
                "arb_id":      arb["id"],
                "arb_name":    arb["name"],
                "model":       model,
                "tier":        tier,
                "rpm":         None,
                "tpm":         None,
                "rpd":         None,
                "min_delay_s": None,
                "safe_delay_s": None,
                "alert_level": "info",
                "alert_msg":   f"Proveedor '{ptype}': consulta la documentacion de su API para conocer los limites.",
            })

    # Delay global recomendado = el mayor de todos los delays minimos
    gemini_results = [r for r in results if r.get("min_delay_s") is not None]
    if gemini_results:
        global_min_delay  = max(r["min_delay_s"]  for r in gemini_results)
        global_safe_delay = max(r["safe_delay_s"] for r in gemini_results)
        worst             = max(gemini_results, key=lambda r: r["min_delay_s"])
        global_alert      = worst["alert_level"]
        global_msg        = (f"Espera minima global: {global_min_delay}s "
                             f"(limitado por {worst['model']} a {worst['rpm']} RPM). "
                             f"Espera segura para produccion: {global_safe_delay}s.")
    else:
        global_min_delay  = 1.5
        global_safe_delay = 3.0
        global_alert      = "info"
        global_msg        = "Sin arbitros Gemini configurados. Se usan valores por defecto."

    return jsonify({
        "tier":              tier,
        "n_arbiters":        n_arbiters,
        "global_min_delay":  global_min_delay,
        "global_safe_delay": global_safe_delay,
        "global_alert":      global_alert,
        "global_msg":        global_msg,
        "per_arbiter":       results,
    })


# ---------------------------------------------------------------------------
# Rutas de paginas
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/arbiters")
def page_arbiters():
    return render_template("arbiters.html")

@app.route("/principles")
def page_principles():
    return render_template("principles.html")

@app.route("/processing")
def page_processing():
    return render_template("processing.html")

@app.route("/run")
def page_run():
    return render_template("run.html")


# ---------------------------------------------------------------------------
# API: Dataset de entrada (archivo, sheet, columna)
# ---------------------------------------------------------------------------
@app.route("/api/dataset/config", methods=["GET"])
def api_get_dataset_config():
    """Devuelve la configuracion actual del dataset de entrada."""
    cfg = read_config()
    return jsonify(cfg.get("dataset", {
        "input_file": "",
        "sheet": "",
        "text_column": "claim",
    }))


@app.route("/api/dataset/config", methods=["PUT"])
def api_update_dataset_config():
    """Guarda la configuracion del dataset (input_file, sheet, text_column)."""
    cfg  = read_config()
    data = request.get_json() or {}

    allowed = {"input_file": str, "sheet": str, "text_column": str}
    errors, updated = [], {}
    for key, cast in allowed.items():
        if key in data:
            try:
                updated[key] = cast(data[key])
            except Exception:
                errors.append(f"Valor invalido para '{key}'")
    if errors:
        return jsonify({"error": "; ".join(errors)}), 400

    if not updated.get("input_file", cfg.get("dataset", {}).get("input_file", "")):
        return jsonify({"error": "El archivo de entrada es obligatorio"}), 400

    if "dataset" not in cfg:
        cfg["dataset"] = {}
    cfg["dataset"].update(updated)
    write_config(cfg)
    return jsonify(cfg["dataset"])


@app.route("/api/dataset/inspect", methods=["POST"])
def api_dataset_inspect():
    """
    Inspecciona un archivo CSV o Excel y devuelve sus sheets y columnas.
    Body: {"file": "ruta/relativa/o/absoluta"}
    Respuesta: {"sheets": [...], "columns": [...], "is_excel": bool}
    """
    data = request.get_json() or {}
    raw_path = data.get("file", "").strip()
    if not raw_path:
        return jsonify({"error": "El campo de archivo es obligatorio"}), 400

    path = Path(raw_path)
    if not path.is_absolute():
        path = BASE_DIR / path

    if not path.exists():
        return jsonify({"error": f"Archivo no encontrado: {raw_path}"}), 404

    suffix = path.suffix.lower()

    if suffix in (".xlsx", ".xls"):
        try:
            import openpyxl  # noqa: PLC0415
        except ImportError:
            return jsonify({"error": "openpyxl no esta instalado. Ejecuta: pip install openpyxl"}), 500

        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        sheets = wb.sheetnames

        # Columnas de la primera hoja (o la indicada)
        requested_sheet = data.get("sheet", "")
        ws = wb[requested_sheet] if requested_sheet in sheets else wb.active
        rows = list(ws.iter_rows(values_only=True))
        wb.close()

        columns = []
        if rows:
            columns = [str(h) for h in rows[0] if h is not None]

        return jsonify({"is_excel": True, "sheets": sheets, "columns": columns})

    elif suffix == ".csv":
        try:
            import csv as _csv  # noqa: PLC0415
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                reader = _csv.DictReader(f)
                columns = list(reader.fieldnames or [])
        except Exception as exc:
            return jsonify({"error": f"Error al leer CSV: {exc}"}), 500
        return jsonify({"is_excel": False, "sheets": [], "columns": columns})

    else:
        return jsonify({
            "error": f"Formato no soportado: '{suffix}'. Usa .csv, .xlsx o .xls."
        }), 400


@app.route("/api/dataset/upload", methods=["POST"])
def api_dataset_upload():
    """
    Recibe un archivo (CSV o Excel) via multipart/form-data,
    lo guarda en data/uploads/ y devuelve su ruta relativa + sheets + columnas.
    """
    if "file" not in request.files:
        return jsonify({"error": "No se recibio ningun archivo"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Nombre de archivo vacio"}), 400

    suffix = Path(f.filename).suffix.lower()
    if suffix not in (".csv", ".xlsx", ".xls"):
        return jsonify({"error": f"Formato '{suffix}' no soportado. Usa .csv, .xlsx o .xls."}), 400

    upload_dir = BASE_DIR / "data" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    save_path = upload_dir / f.filename
    f.save(str(save_path))

    # Reutilizar logica de inspect
    rel_path = str(save_path.relative_to(BASE_DIR)).replace("\\", "/")

    # Inspeccionar el archivo recien guardado
    if suffix in (".xlsx", ".xls"):
        try:
            import openpyxl  # noqa
        except ImportError:
            return jsonify({"error": "openpyxl no instalado"}), 500
        wb = openpyxl.load_workbook(str(save_path), read_only=True, data_only=True)
        sheets = wb.sheetnames
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        wb.close()
        columns = [str(h) for h in rows[0] if h is not None] if rows else []
        return jsonify({"is_excel": True, "sheets": sheets, "columns": columns, "path": rel_path})
    else:
        import csv as _csv  # noqa
        with open(save_path, "r", encoding="utf-8", errors="replace") as fp:
            reader = _csv.DictReader(fp)
            columns = list(reader.fieldnames or [])
        return jsonify({"is_excel": False, "sheets": [], "columns": columns, "path": rel_path})


# ---------------------------------------------------------------------------
# API: Config completa
# ---------------------------------------------------------------------------
@app.route("/api/config", methods=["GET"])
def api_get_config():
    return jsonify(read_config())


# ---------------------------------------------------------------------------
# API: Arbitros (CRUD actualizado para estructura provider{})
# ---------------------------------------------------------------------------
@app.route("/api/arbiters", methods=["GET"])
def api_get_arbiters():
    return jsonify(read_config()["arbiters"])


@app.route("/api/arbiters", methods=["POST"])
def api_create_arbiter():
    cfg  = read_config()
    data = request.get_json()

    for key in ["id", "name", "provider", "system_prompt"]:
        if not data.get(key):
            return jsonify({"error": f"Campo requerido: {key}"}), 400

    if data["id"] in {a["id"] for a in cfg["arbiters"]}:
        return jsonify({"error": f"Ya existe un arbitro con id '{data['id']}'"}), 409

    new_arb = _build_arbiter(data)
    cfg["arbiters"].append(new_arb)
    write_config(cfg)
    return jsonify(new_arb), 201


@app.route("/api/arbiters/<arb_id>", methods=["GET"])
def api_get_arbiter(arb_id):
    for arb in read_config()["arbiters"]:
        if arb["id"] == arb_id:
            return jsonify(arb)
    return jsonify({"error": f"Arbitro '{arb_id}' no encontrado"}), 404


@app.route("/api/arbiters/<arb_id>", methods=["PUT"])
def api_update_arbiter(arb_id):
    cfg  = read_config()
    data = request.get_json()
    for i, arb in enumerate(cfg["arbiters"]):
        if arb["id"] == arb_id:
            cfg["arbiters"][i] = _build_arbiter(data, existing=arb)
            write_config(cfg)
            return jsonify(cfg["arbiters"][i])
    return jsonify({"error": f"Arbitro '{arb_id}' no encontrado"}), 404


@app.route("/api/arbiters/<arb_id>", methods=["DELETE"])
def api_delete_arbiter(arb_id):
    cfg = read_config()
    before = len(cfg["arbiters"])
    cfg["arbiters"] = [a for a in cfg["arbiters"] if a["id"] != arb_id]
    if len(cfg["arbiters"]) == before:
        return jsonify({"error": f"Arbitro '{arb_id}' no encontrado"}), 404
    if not cfg["arbiters"]:
        return jsonify({"error": "Debe quedar al menos un arbitro"}), 400
    write_config(cfg)
    return jsonify({"ok": True})


def _build_arbiter(data: dict, existing: dict = None) -> dict:
    """Construye un dict de arbitro limpio desde los datos del request."""
    base = existing.copy() if existing else {}
    if "name"          in data: base["name"]    = data["name"].strip()
    if "persona"       in data: base["persona"] = data.get("persona", "").strip()
    if "system_prompt" in data: base["system_prompt"] = data["system_prompt"].strip()
    if "id"            in data and not existing: base["id"] = data["id"].strip()

    if "provider" in data:
        p = data["provider"]
        base["provider"] = {
            "type":             p.get("type", "gemini"),
            "model":            p.get("model", ""),
            "api_key_env":      p.get("api_key_env", ""),
            "api_key_override": p.get("api_key_override", ""),
            "base_url":         p.get("base_url", ""),
            "vertex_project":   p.get("vertex_project", ""),
            "vertex_location":  p.get("vertex_location", "us-central1"),
        }

    if "generation_config" in data:
        gc = data["generation_config"]
        base["generation_config"] = {
            "temperature":       float(gc.get("temperature", 0.1)),
            "max_output_tokens": int(gc.get("max_output_tokens", 600)),
        }
    return base


# ---------------------------------------------------------------------------
# API: Principios
# ---------------------------------------------------------------------------
@app.route("/api/principles", methods=["GET"])
def api_get_principles():
    return jsonify(read_config()["task"]["principles"])

@app.route("/api/principles", methods=["PUT"])
def api_update_principles():
    cfg  = read_config()
    data = request.get_json()
    if not isinstance(data, dict) or not data:
        return jsonify({"error": "Se esperaba un objeto JSON"}), 400
    cfg["task"]["principles"] = data
    write_config(cfg)
    return jsonify(cfg["task"]["principles"])


# ---------------------------------------------------------------------------
# API: Prompt template
# ---------------------------------------------------------------------------
@app.route("/api/prompt-template", methods=["GET"])
def api_get_prompt_template():
    task = read_config()["task"]
    return jsonify({
        "user_prompt_template": task["user_prompt_template"],
        "include_justifications": bool(task.get("include_justifications", False)),
    })

@app.route("/api/prompt-template", methods=["PUT"])
def api_update_prompt_template():
    cfg      = read_config()
    data     = request.get_json()
    template = data.get("user_prompt_template", "").strip()
    if not template:
        return jsonify({"error": "El template no puede estar vacio"}), 400
    if "{claim}" not in template:
        return jsonify({"error": "El template debe contener {claim}, que representa el texto de la entrada"}), 400
    cfg["task"]["user_prompt_template"] = template
    if "include_justifications" in data:
        cfg["task"]["include_justifications"] = bool(data.get("include_justifications"))
    write_config(cfg)
    return jsonify({
        "user_prompt_template": template,
        "include_justifications": bool(cfg["task"].get("include_justifications", False)),
    })


# ---------------------------------------------------------------------------
# API: Processing params
# ---------------------------------------------------------------------------
@app.route("/api/processing", methods=["GET"])
def api_get_processing():
    return jsonify(read_config()["processing"])

@app.route("/api/processing", methods=["PUT"])
def api_update_processing():
    cfg     = read_config()
    data    = request.get_json()
    allowed = {
        "checkpoint_every": int, "claim_max_chars": int,
        "base_delay_seconds": float, "min_delay_seconds": float, "max_delay_seconds": float,
        "rate_limit_backoff_multiplier": float, "server_error_backoff_multiplier": float,
        "recovery_threshold_successes": int, "recovery_delay_multiplier": float,
    }
    errors, updated = [], {}
    for key, cast in allowed.items():
        if key in data:
            try:    updated[key] = cast(data[key])
            except: errors.append(f"Valor invalido para '{key}'")
    if errors:
        return jsonify({"error": "; ".join(errors)}), 400

    if "base_delay_seconds" in updated:
        n_arbiters = len(cfg.get("arbiters", [])) or 1
        gemini_delays = []
        for arb in cfg.get("arbiters", []):
            provider = arb.get("provider", {})
            if provider.get("type") in ("gemini", "vertex_gemini") and provider.get("model"):
                gemini_delays.append(compute_min_delay(provider["model"], "free", n_arbiters))
        if gemini_delays:
            min_delay = max(r["min_delay_s"] for r in gemini_delays)
            if updated["base_delay_seconds"] < min_delay:
                worst = max(gemini_delays, key=lambda r: r["min_delay_s"])
                return jsonify({
                    "error": (f"La espera base es demasiado baja para el nivel gratuito de Gemini. "
                              f"Minimo recomendado: {min_delay}s "
                              f"(limitado por {worst['model']} a {worst['rpm']} RPM)."),
                    "min_delay_seconds": min_delay,
                    "safe_delay_seconds": max(r["safe_delay_s"] for r in gemini_delays),
                }), 400

    cfg["processing"].update(updated)
    write_config(cfg)
    return jsonify(cfg["processing"])


# ---------------------------------------------------------------------------
# API: Lanzar proceso
# ---------------------------------------------------------------------------
@app.route("/api/run/start", methods=["POST"])
def api_run_start():
    if _process_state["running"]:
        return jsonify({"error": "Ya hay un proceso corriendo"}), 409

    data  = request.get_json() or {}
    limit = data.get("limit")

    # Leer configuracion de dataset desde el config central
    cfg_data    = read_config()
    dataset_cfg = cfg_data.get("dataset", {})
    input_file  = dataset_cfg.get("input_file", "")
    sheet       = dataset_cfg.get("sheet", "")
    text_column = dataset_cfg.get("text_column", "claim")

    cmd = [sys.executable, str(BASE_DIR / "main_label.py")]
    if input_file:
        cmd += ["--input", input_file]
    if sheet:
        cmd += ["--sheet", sheet]
    if text_column:
        cmd += ["--column", text_column]
    if limit:
        cmd += ["--limit", str(int(limit))]

    _process_state["log_queue"] = queue.Queue()
    _reset_current_stats()

    def run_and_capture():
        _process_state["running"] = True
        try:
            env = os.environ.copy()
            env["DATA_LABELING_WEBAPP_METRICS"] = "1"
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, cwd=str(BASE_DIR), env=env,
            )
            _process_state["proc"] = proc
            _process_state["pid"]  = proc.pid
            for line in iter(proc.stdout.readline, ""):
                line = line.rstrip("\n")
                if not line:
                    continue
                if line.startswith(METRIC_PREFIX):
                    try:
                        event = json.loads(line[len(METRIC_PREFIX):])
                        snapshot = _apply_run_metric_event(event)
                        _process_state["log_queue"].put({"type": "metric", "data": snapshot})
                    except json.JSONDecodeError:
                        _process_state["log_queue"].put(line)
                    continue
                _process_state["log_queue"].put(line)
            proc.stdout.close()
            proc.wait()
            _process_state["log_queue"].put(f"__EXIT__ {proc.returncode}")
        except Exception as e:
            _process_state["log_queue"].put(f"__EXIT__ ERROR: {e}")
        finally:
            _process_state["running"] = False
            _process_state["proc"]    = None

    threading.Thread(target=run_and_capture, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/run/stop", methods=["POST"])
def api_run_stop():
    proc = _process_state.get("proc")
    if proc and _process_state["running"]:
        proc.terminate()
        _process_state["running"] = False
        return jsonify({"ok": True})
    return jsonify({"error": "No hay proceso activo"}), 400


@app.route("/api/run/status", methods=["GET"])
def api_run_status():
    return jsonify({"running": _process_state["running"], "pid": _process_state["pid"]})


@app.route("/api/run/current-stats", methods=["GET"])
def api_run_current_stats():
    with _process_state["stats_lock"]:
        return jsonify(_public_current_stats(_process_state["current_stats"]))


# ---------------------------------------------------------------------------
# SSE: Streaming de logs
# ---------------------------------------------------------------------------
@app.route("/api/run/logs")
def api_run_logs():
    def generate():
        while True:
            try:
                item = _process_state["log_queue"].get(timeout=30)
                if isinstance(item, dict):
                    yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                    continue
                line = item
                if line.startswith("__EXIT__"):
                    yield f"data: {json.dumps({'type':'exit','msg':line})}\n\n"
                    break
                yield f"data: {json.dumps({'type':'log','msg':line})}\n\n"
            except queue.Empty:
                yield "data: {\"type\":\"heartbeat\"}\n\n"
                if not _process_state["running"]:
                    break
    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})


# ---------------------------------------------------------------------------
# API: Outputs y checkpoint
# ---------------------------------------------------------------------------
def _parse_labeled_timestamp(path: Path) -> str:
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


def _describe_output_csv(path: Path) -> dict:
    import csv as _csv

    info = {
        "run_timestamp": _parse_labeled_timestamp(path),
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
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = _csv.DictReader(f)
            fieldnames = reader.fieldnames or []
            principles = _detect_principles(fieldnames)
            arbiters = [
                fn.replace("prompt_tokens_", "")
                for fn in fieldnames
                if fn.startswith("prompt_tokens_")
            ]
            info["principles"] = principles
            info["arbiters"] = arbiters
            info["has_tokens"] = bool(arbiters)
            info["has_latency"] = any(fn.startswith("latency_s_") for fn in fieldnames)

            if path.stat().st_size <= 15 * 1024 * 1024:
                rows = list(reader)
                info["row_count"] = len(rows)
                status_counts = {"ok": 0, "failed": 0, "pending": 0}
                total_tokens = 0
                for row in rows:
                    status_counts[_row_processing_status(row, principles)] += 1
                    total_tokens += _entry_usage_metrics_from_row(row, arbiters)["total_tokens"]
                info["successful_rows"] = status_counts["ok"]
                info["failed_rows"] = status_counts["failed"]
                info["total_tokens_global"] = total_tokens
    except Exception:
        return info

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


@app.route("/api/outputs", methods=["GET"])
def api_outputs():
    files = []
    if OUTPUT_DIR.exists():
        for f in sorted(OUTPUT_DIR.glob("labeled_*.csv"), reverse=True):
            files.append({
                "name": f.name,
                "size_kb": round(f.stat().st_size / 1024, 1),
                "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(f.stat().st_mtime)),
                **_describe_output_csv(f),
            })
    return jsonify(files)


@app.route("/outputs/<name>")
def api_serve_output(name):
    import re
    if not re.match(r"^labeled_\d{8}_\d{6}\.csv$", name):
        return "Nombre de archivo invalido", 400
    path = OUTPUT_DIR / name
    if not path.exists():
        return "Archivo no encontrado", 404
    return path.read_bytes(), 200, {"Content-Type": "text/csv; charset=utf-8", "Content-Disposition": f'inline; filename="{name}"'}


@app.route("/api/outputs/<name>", methods=["DELETE"])
def api_delete_output(name):
    import re
    if not re.match(r"^labeled_\d{8}_\d{6}\.csv$", name):
        return jsonify({"error": "Nombre de archivo invalido"}), 400
    path = OUTPUT_DIR / name
    if not path.exists():
        return jsonify({"error": "Archivo no encontrado"}), 404
    path.unlink()
    return jsonify({"ok": True})



def _detect_principles(fieldnames: list) -> list:
    suffix = "_consensus"
    return [fn[:-len(suffix)] for fn in fieldnames if fn.endswith(suffix)]


def _row_processing_status(row: dict, principles: list) -> str:
    values = [_safe_int(row.get(f"{p}_consensus")) for p in principles]
    values = [v for v in values if v is not None]
    if not values:
        return "pending"
    if any(v < 0 for v in values):
        return "failed"
    return "ok"


def _entry_consensus_metrics_from_row(row: dict, principles: list) -> dict:
    per_principle = {}
    for principle in principles:
        per_principle[principle] = {
            "support": _safe_int(row.get(f"{principle}_entry_consensus_support")),
            "raters": _safe_int(row.get(f"{principle}_entry_consensus_raters")),
            "support_ratio": _safe_float(row.get(f"{principle}_entry_consensus_support_ratio")),
            "votes_0": _safe_int(row.get(f"{principle}_entry_consensus_votes_0")),
            "votes_1": _safe_int(row.get(f"{principle}_entry_consensus_votes_1")),
            "unanimous": str(row.get(f"{principle}_entry_consensus_unanimous", "")).lower() in {"true", "1"},
        }
    observed_agreement = _safe_float(row.get("entry_consensus_observed_agreement"))
    mean_support_ratio = _safe_float(row.get("entry_consensus_mean_support_ratio"))
    observed_agreement_percent = _safe_float(row.get("entry_consensus_observed_agreement_percent"))
    mean_support_percent = _safe_float(row.get("entry_consensus_mean_support_percent"))
    if observed_agreement_percent is None and observed_agreement is not None:
        observed_agreement_percent = round(observed_agreement * 100, 1)
    if mean_support_percent is None and mean_support_ratio is not None:
        mean_support_percent = round(mean_support_ratio * 100, 1)
    return {
        "observed_agreement": observed_agreement,
        "observed_agreement_percent": observed_agreement_percent,
        "mean_support_ratio": mean_support_ratio,
        "mean_support_percent": mean_support_percent,
        "unanimous_principles": _safe_int(row.get("entry_consensus_unanimous_principles"), 0),
        "split_principles": _safe_int(row.get("entry_consensus_split_principles"), 0),
        "principle_count": _safe_int(row.get("entry_consensus_principle_count"), len(principles)),
        "per_principle": per_principle,
    }


def _entry_usage_metrics_from_row(row: dict, arbiter_ids: list) -> dict:
    prompt_tokens = _safe_int(row.get("entry_prompt_tokens"))
    completion_tokens = _safe_int(row.get("entry_completion_tokens"))
    total_tokens = _safe_int(row.get("entry_total_tokens"))
    latency_s = _safe_float(row.get("entry_latency_s"))
    latency_avg_s = _safe_float(row.get("entry_latency_avg_s"))
    arbiter_calls = _safe_int(row.get("entry_arbiter_calls"))
    tokens_per_call = _safe_float(row.get("entry_tokens_per_call"))

    if total_tokens is None:
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        latency_total = 0.0
        latency_count = 0
        arbiter_calls = 0

        for arb_id in arbiter_ids:
            p_tok = _safe_int(row.get(f"prompt_tokens_{arb_id}"), 0) or 0
            c_tok = _safe_int(row.get(f"completion_tokens_{arb_id}"), 0) or 0
            t_tok = _safe_int(row.get(f"total_tokens_{arb_id}"), p_tok + c_tok) or 0
            lat = _safe_float(row.get(f"latency_s_{arb_id}"))

            if p_tok > 0:
                prompt_tokens += p_tok
            if c_tok > 0:
                completion_tokens += c_tok
            if t_tok > 0:
                total_tokens += t_tok
            if t_tok >= 0 or (lat is not None and lat >= 0):
                arbiter_calls += 1
            if lat is not None and lat >= 0:
                latency_total += lat
                latency_count += 1

        latency_s = round(latency_total, 3) if latency_count else None
        latency_avg_s = round(latency_total / latency_count, 3) if latency_count else None
        tokens_per_call = round(total_tokens / arbiter_calls, 2) if arbiter_calls else None

    return {
        "prompt_tokens": prompt_tokens or 0,
        "completion_tokens": completion_tokens or 0,
        "total_tokens": total_tokens or 0,
        "latency_s": round(latency_s, 3) if latency_s is not None and latency_s >= 0 else None,
        "latency_avg_s": round(latency_avg_s, 3) if latency_avg_s is not None and latency_avg_s >= 0 else None,
        "arbiter_calls": arbiter_calls or 0,
        "tokens_per_call": tokens_per_call,
    }


def _build_result_rows(rows: list, principles: list, arbiter_ids: list, limit: int) -> list:
    result_rows = []
    for idx, row in enumerate(rows[:limit]):
        usage_metrics = _entry_usage_metrics_from_row(row, arbiter_ids)
        arbiter_results = []

        for arb_id in arbiter_ids:
            prompt_tokens = _safe_int(row.get(f"prompt_tokens_{arb_id}"), 0) or 0
            completion_tokens = _safe_int(row.get(f"completion_tokens_{arb_id}"), 0) or 0
            total_tokens = _safe_int(row.get(f"total_tokens_{arb_id}"), 0) or 0
            latency_s = _safe_float(row.get(f"latency_s_{arb_id}"))

            labels = {}
            for p in principles:
                labels[p] = {
                    "value": _safe_int(row.get(f"{p}_{arb_id}")),
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
            "status": _row_processing_status(row, principles),
            "claim": row.get("claim", ""),
            "claim_es": row.get("claim_es", ""),
            "dataset": row.get("dataset", ""),
            "class": row.get("class", ""),
            "consensus": {
                p: _safe_int(row.get(f"{p}_consensus"))
                for p in principles
            },
            "consensus_metrics": _entry_consensus_metrics_from_row(row, principles),
            "usage_metrics": usage_metrics,
            "total_tokens": usage_metrics["total_tokens"],
            "total_latency_s": usage_metrics["latency_s"],
            "latency_avg_s": usage_metrics["latency_avg_s"],
            "arbiter_calls": usage_metrics["arbiter_calls"],
            "arbiters": arbiter_results,
        })

    return result_rows





@app.route("/api/run/stats", methods=["GET"])
def api_run_stats():
    """
    Lee el ultimo CSV etiquetado y calcula estadisticas de tokens,
    latencia acumulada por arbitro y una tabla de resultados por entrada.
    """
    import csv as _csv

    if not OUTPUT_DIR.exists():
        return jsonify({"error": "No hay outputs generados aun"}), 404

    csvs = sorted(OUTPUT_DIR.glob("labeled_*.csv"), reverse=True)
    if not csvs:
        return jsonify({"error": "No hay outputs generados aun"}), 404

    latest = csvs[0]
    try:
        with open(latest, "r", encoding="utf-8") as f:
            reader = _csv.DictReader(f)
            fieldnames = reader.fieldnames or []
            rows = list(reader)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    try:
        result_limit = int(request.args.get("limit", 250))
    except (ValueError, TypeError):
        result_limit = 250
    result_limit = max(1, min(result_limit, 500))

    # Detectar arbitros desde columnas prompt_tokens_*
    arbiter_ids = [
        fn.replace("prompt_tokens_", "")
        for fn in fieldnames
        if fn.startswith("prompt_tokens_")
    ]
    principles = _detect_principles(fieldnames)

    stats = {}
    for arb_id in arbiter_ids:
        prompt_sum, completion_sum, total_sum = 0, 0, 0
        latencies = []
        for row in rows:
            try:
                pt = int(row.get(f"prompt_tokens_{arb_id}", 0) or 0)
                ct = int(row.get(f"completion_tokens_{arb_id}", 0) or 0)
                tt = int(row.get(f"total_tokens_{arb_id}", 0) or 0)
                if pt > 0: prompt_sum += pt
                if ct > 0: completion_sum += ct
                if tt > 0: total_sum += tt
            except (ValueError, TypeError):
                pass
            try:
                lat = float(row.get(f"latency_s_{arb_id}", -1) or -1)
                if lat >= 0:
                    latencies.append(lat)
            except (ValueError, TypeError):
                pass
        stats[arb_id] = {
            "prompt_tokens":     prompt_sum,
            "completion_tokens": completion_sum,
            "total_tokens":      total_sum,
            "calls":             len(latencies),
            "latency_avg_s":     round(sum(latencies) / len(latencies), 3) if latencies else 0,
            "latency_min_s":     round(min(latencies), 3) if latencies else 0,
            "latency_max_s":     round(max(latencies), 3) if latencies else 0,
            "latency_total_s":   round(sum(latencies), 3) if latencies else 0,
        }

    status_counts = {"ok": 0, "failed": 0, "pending": 0}
    total_tokens_global = 0
    total_latency_global = 0.0
    for row in rows:
        status_counts[_row_processing_status(row, principles)] += 1
        usage_metrics = _entry_usage_metrics_from_row(row, arbiter_ids)
        total_tokens_global += usage_metrics["total_tokens"]
        if usage_metrics["latency_s"] is not None:
            total_latency_global += usage_metrics["latency_s"]

    return jsonify({
        "source_file":         latest.name,
        "total_rows":          len(rows),
        "successful_rows":     status_counts["ok"],
        "failed_rows":         status_counts["failed"],
        "pending_rows":        status_counts["pending"],
        "total_tokens_global": total_tokens_global,
        "tokens_per_row":      round(total_tokens_global / len(rows)) if rows else 0,
        "tokens_per_successful_row": round(total_tokens_global / status_counts["ok"]) if status_counts["ok"] else 0,
        "total_latency_s":     round(total_latency_global, 3),
        "arbiters":            stats,
        "principles":          principles,
        "agreement":           compute_agreement_metrics(rows, principles, arbiter_ids),
        "results_total":       len(rows),
        "results_count":       min(len(rows), result_limit),
        "results_limit":       result_limit,
        "results_truncated":   len(rows) > result_limit,
        "results":             _build_result_rows(rows, principles, arbiter_ids, result_limit),
    })


@app.route("/api/checkpoint/status", methods=["GET"])
def api_checkpoint_status():
    cp = CHECKPOINTS_DIR / "checkpoint.json"
    run_state_path = CHECKPOINTS_DIR / "run_state.json"
    run_state = None
    if run_state_path.exists():
        try:
            with open(run_state_path, "r", encoding="utf-8") as f:
                raw_state = json.load(f)
            summary = raw_state.get("summary", {})
            run_state = {
                "run_id": raw_state.get("run_id"),
                "status": raw_state.get("status"),
                "updated_at": raw_state.get("updated_at"),
                "output_file": raw_state.get("output_file"),
                "total_rows": raw_state.get("total_rows"),
                "pause_kind": raw_state.get("pause_kind", ""),
                "pause_reason": raw_state.get("pause_reason", ""),
                "processed_rows": summary.get("processed_rows", 0),
                "successful_rows": summary.get("successful_rows", 0),
                "failed_rows": summary.get("failed_rows", 0),
            }
        except (OSError, json.JSONDecodeError):
            run_state = None
    if not cp.exists():
        return jsonify({"exists": False, "run_state": run_state})
    with open(cp) as f:
        data = json.load(f)
    return jsonify({"exists": True, "last_index": data.get("last_index", -1),
                    "claims_done": data.get("last_index", -1) + 1,
                    "run_state": run_state})


@app.route("/api/failed-queue", methods=["GET"])
def api_failed_queue():
    path = CHECKPOINTS_DIR / "failed_queue.jsonl"
    if not path.exists():
        return jsonify([])
    items = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                row = item.get("row", {})
                items.append({
                    "index": item.get("index"),
                    "reason": item.get("reason", "sin detalle"),
                    "claim": row.get("claim", ""),
                })
            except json.JSONDecodeError:
                continue
    return jsonify(items)


@app.route("/api/checkpoint/clear", methods=["POST"])
def api_checkpoint_clear():
    cleared = []
    for name in ["checkpoint.json", "failed_queue.jsonl", "run_state.json",
                 "claim_results.jsonl", "arbiter_results.jsonl"]:
        p = CHECKPOINTS_DIR / name
        if p.exists():
            p.unlink(); cleared.append(name)
    return jsonify({"ok": True, "cleared": cleared})


# ---------------------------------------------------------------------------
# Entry point  (ejecucion directa: python webapp/app.py)
# Para produccion / uso normal usa main_webapp.py que acepta --host y --port.
# Puerto 7860: libre en macOS (5000 ocupado por AirPlay), Windows y Linux.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import os as _os
    _host = _os.environ.get("LABELFORGE_HOST", "127.0.0.1")
    _port = int(_os.environ.get("LABELFORGE_PORT", "7860"))
    print(f"  LabelForge  →  http://{_host}:{_port}")
    app.run(debug=True, host=_host, port=_port, threaded=True)

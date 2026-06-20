"""
app.py - Flask webapp para configurar y lanzar el proceso de etiquetado
de principios de persuasion de Ana Ferreira.
"""

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, g, jsonify, render_template, request, send_from_directory, stream_with_context

# ---------------------------------------------------------------------------
# Módulo compartido de métricas de acuerdo y utilidades
# ---------------------------------------------------------------------------
from scripts.agreement_metrics import compute_agreement_metrics
from scripts.arbiters_io import (
    build_arbiters_export_payload,
    decrypt_arbiters_export,
    encrypt_arbiters_export,
    merge_arbiters,
    normalize_arbiter,
    require_export_password,
    validate_imported_arbiters,
)
from scripts.secrets_io import provider_api_key_env, resolve_gcp_credentials, secrets_env_path, set_secret_value
from scripts.rate_limits import compute_min_delay
from scripts.provider_preflight import validate_provider_preflight
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
MODELS_DIR      = BASE_DIR / "models"
HW_PROFILE_PATH = MODELS_DIR / "hw_profile.json"

SECRETS_ENV_PATH = secrets_env_path(BASE_DIR)
load_dotenv(SECRETS_ENV_PATH)
resolve_gcp_credentials(BASE_DIR)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB máximo por upload

# ---------------------------------------------------------------------------
# Logging — silencia Werkzeug, reemplaza con log de requests legible
# ---------------------------------------------------------------------------
import logging as _logging

_logging.getLogger("werkzeug").setLevel(_logging.ERROR)

_req_log = _logging.getLogger("labelforge.http")
if not _req_log.handlers:
    _rh = _logging.StreamHandler()
    _rh.setFormatter(_logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    _req_log.addHandler(_rh)
    _req_log.setLevel(_logging.INFO)
    _req_log.propagate = False

_log = _logging.getLogger(__name__)

_HTTP_SKIP       = frozenset({"/favicon.ico"})
_HTTP_SKIP_PREFX = ("/static/",)


@app.before_request
def _req_timer():
    g._t0 = time.perf_counter()


@app.after_request
def _log_http(response):
    path = request.path
    if path in _HTTP_SKIP or any(path.startswith(p) for p in _HTTP_SKIP_PREFX):
        return response
    elapsed = round((time.perf_counter() - getattr(g, "_t0", time.perf_counter())) * 1000)
    status  = response.status_code
    if status == 304:
        return response
    msg = f"{request.method:<5} {path}  →  {status}  ({elapsed}ms)"
    if status >= 500:
        _req_log.error(msg)
    elif status >= 400:
        _req_log.warning(msg)
    else:
        _req_log.info(msg)
    # Cabeceras de seguridad en todas las respuestas HTML
    if "text/html" in response.content_type:
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "font-src 'self' https://cdn.jsdelivr.net; "
            "img-src 'self' data:; connect-src 'self'"
        )
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response


# ---------------------------------------------------------------------------
# Autenticación — HTTP Basic Auth
# ---------------------------------------------------------------------------
import base64 as _b64
import secrets as _sec

# Secret key para sesiones Flask (necesaria para cookies seguras)
app.secret_key = os.environ.get("LABELFORGE_SECRET_KEY") or _sec.token_hex(32)

_AUTH_EXEMPT = frozenset({"/favicon.ico"})
_AUTH_EXEMPT_PREFIX = ("/static/",)


def _is_localhost() -> bool:
    return request.remote_addr in ("127.0.0.1", "::1")


def _basic_auth_ok(password: str) -> bool:
    hdr = request.headers.get("Authorization", "")
    if not hdr.startswith("Basic "):
        return False
    try:
        _, _, provided = _b64.b64decode(hdr[6:]).decode("utf-8", errors="replace").partition(":")
        return _sec.compare_digest(provided, password)
    except Exception:
        return False


@app.before_request
def _require_auth():
    path = request.path
    if path in _AUTH_EXEMPT or any(path.startswith(p) for p in _AUTH_EXEMPT_PREFIX):
        return

    _password = os.environ.get("LABELFORGE_PASSWORD", "").strip()

    if not _password:
        # Sin contraseña configurada: solo permite acceso desde localhost
        if not _is_localhost():
            return Response(
                "Acceso denegado. Configura LABELFORGE_PASSWORD en secrets/secrets.env "
                "para habilitar acceso remoto.",
                status=403,
                content_type="text/plain; charset=utf-8",
            )
        return

    if not _basic_auth_ok(_password):
        return Response(
            "Autenticación requerida",
            status=401,
            content_type="text/plain; charset=utf-8",
            headers={"WWW-Authenticate": 'Basic realm="LabelForge"'},
        )


# ---------------------------------------------------------------------------
# Hardware profile (no aborta la webapp si falta — solo lo logea)
# ---------------------------------------------------------------------------

def _load_hw_profile() -> dict:
    if not HW_PROFILE_PATH.exists():
        _log.warning(
            "models/hw_profile.json no encontrado. "
            "Los modelos locales GGUF no estarán disponibles. "
            "Ejecuta 'python setup_env.py' para habilitarlos."
        )
        return {}
    try:
        with open(HW_PROFILE_PATH, encoding="utf-8") as f:
            profile = json.load(f)
        if not profile.get("setup_ok"):
            _log.warning(
                "hw_profile.json indica setup_ok=false. "
                "Revisa logs/setup_*.log y vuelve a ejecutar 'python setup_env.py'."
            )
        return profile
    except Exception as exc:
        _log.warning("No se pudo leer hw_profile.json: %s", exc)
        return {}

_HW_PROFILE: dict = _load_hw_profile()

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
        "dataset_total_rows": 0,
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
        "principle_counts": {},
        "checkpoint_every": 10,
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
            "tokens_in":  arb_stats.get("prompt_tokens", 0),
            "tokens_out": arb_stats.get("completion_tokens", 0),
            "ok": arb_stats.get("calls", 0),
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
            stats["dataset_total_rows"] = int(event.get("dataset_total_rows") or 0)
            stats["completed_before_start"] = int(event.get("completed_before_start") or 0)
            stats["principles"] = event.get("principles", [])
            try:
                _cfg = read_config()
                stats["checkpoint_every"] = int(_cfg.get("processing", {}).get("checkpoint_every", 10))
            except Exception:
                pass
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
            for p, val in event.get("consensus", {}).items():
                pc = stats["principle_counts"]
                if p not in pc:
                    pc[p] = {"ones": 0, "zeros": 0}
                if val == 1:
                    pc[p]["ones"] += 1
                elif val == 0:
                    pc[p]["zeros"] += 1

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
    with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
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
    env_var = provider_api_key_env(provider_type)
    return os.environ.get(env_var, "") if env_var else ""


@app.route("/api/models", methods=["POST"])
def api_models():
    """
    Devuelve los modelos disponibles para un proveedor dado.
    Body JSON:
      - provider : gemini | vertex_gemini | anthropic | openai | ollama | openai_compat
      - api_key  : opcional, reemplazo directo de la clave en secrets/secrets.env
      - base_url : para ollama / openai_compat
      - project  : Google Cloud project para vertex_gemini
      - location : region Vertex AI para vertex_gemini
    """
    body     = request.get_json(silent=True) or {}
    provider = body.get("provider", "gemini")
    env_var  = (body.get("api_key_env") or "").strip() or provider_api_key_env(provider)
    override = (body.get("api_key") or "").strip()
    api_key  = override or (os.environ.get(env_var, "").strip() if env_var else "") or _get_api_key(provider)
    base_url = (body.get("base_url") or "").strip()
    project  = (body.get("project") or "").strip() or os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    location = (body.get("location") or "").strip() or os.environ.get("VERTEX_LOCATION", "us-central1").strip()
    force    = body.get("force") == "1"

    cache_key = f"{provider}:{env_var}:{base_url}:{project}:{location}"
    now = time.time()
    if not force and cache_key in _models_cache and (now - _models_cache[cache_key]["ts"]) < MODELS_CACHE_TTL:
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
        elif provider == "llama_cpp":
            result = _list_local_gguf_models()
        else:
            return jsonify({"error": f"Proveedor desconocido: {provider}"}), 400
    except Exception as e:
        return jsonify({"error": str(e)[:300]}), 502

    _models_cache[cache_key] = {"data": result, "ts": now}
    return jsonify(result)


@app.route("/api/provider-key", methods=["POST"])
def api_provider_key():
    data = request.get_json() or {}
    provider = data.get("provider", "").strip()
    api_key = data.get("api_key", "").strip()
    env_var = data.get("api_key_env", "").strip() or provider_api_key_env(provider)
    base_url = data.get("base_url", "").strip()

    if provider in {"ollama", "vertex_gemini"}:
        return jsonify({"error": "Este proveedor no usa clave de API directa en secrets/secrets.env."}), 400
    if not env_var:
        return jsonify({"error": "Proveedor sin variable de API key configurada."}), 400
    if not api_key:
        return jsonify({"error": "Pega una clave de API para verificarla."}), 400

    try:
        if provider == "gemini":
            models = _list_gemini_models(api_key)
        elif provider == "anthropic":
            models = _list_anthropic_models(api_key)
        elif provider == "openai":
            models = _list_openai_models(api_key)
        elif provider == "openai_compat":
            models = _list_openai_compat_models(api_key, base_url)
        else:
            return jsonify({"error": f"Proveedor desconocido: {provider}"}), 400
    except Exception as exc:
        return jsonify({"error": f"No se pudo verificar la clave: {str(exc)[:260]}"}), 400

    set_secret_value(SECRETS_ENV_PATH, env_var, api_key)
    load_dotenv(SECRETS_ENV_PATH, override=True)
    for key in list(_models_cache.keys()):
        if key.startswith(f"{provider}:"):
            _models_cache.pop(key, None)

    return jsonify({
        "ok": True,
        "provider": provider,
        "api_key_env": env_var,
        "models": models,
        "count": len(models),
    })


def _list_gemini_models(api_key: str) -> list:
    if not api_key:
        raise ValueError("Se requiere GEMINI_API_KEY en secrets/secrets.env o como parametro.")
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
        raise ValueError("Se requiere ANTHROPIC_API_KEY en secrets/secrets.env o como parametro.")
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
        raise ValueError("Se requiere OPENAI_API_KEY en secrets/secrets.env o como parametro.")
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


def _validate_base_url(base_url: str) -> None:
    """Block non-http(s) schemes and cloud metadata endpoints to prevent SSRF."""
    from urllib.parse import urlparse
    import ipaddress
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("base_url debe usar esquema http o https.")
    host = (parsed.hostname or "").lower()
    _blocked_hosts = {"metadata.google.internal", "metadata.google"}
    if host in _blocked_hosts:
        raise ValueError(f"base_url apunta a un host no permitido: {host}")
    try:
        addr = ipaddress.ip_address(host)
        if addr.is_link_local:
            raise ValueError("base_url no puede apuntar a direcciones link-local (169.254.x.x).")
    except ValueError:
        pass  # hostname, not an IP literal — allowed


def _list_openai_compat_models(api_key: str, base_url: str) -> list:
    if not base_url:
        raise ValueError("Se requiere base_url para proveedor openai_compat / ollama.")
    _validate_base_url(base_url)
    from openai import OpenAI
    client = OpenAI(api_key=api_key or "local", base_url=base_url)
    raw    = client.models.list()
    result = [
        {"id": m.id, "display_name": m.id, "task_description": _generic_desc(m.id)}
        for m in raw.data
    ]
    result.sort(key=lambda m: m["id"])
    return result


def _list_local_gguf_models() -> list:
    """Lista los archivos .gguf disponibles en models/."""
    MODELS_DIR.mkdir(exist_ok=True)
    result = []
    for gguf in sorted(MODELS_DIR.glob("*.gguf")):
        size_gb = round(gguf.stat().st_size / (1024 ** 3), 2)
        result.append({
            "id": gguf.name,
            "display_name": gguf.stem,
            "task_description": f"Modelo local GGUF — {size_gb} GB",
            "path": str(gguf.relative_to(BASE_DIR)),
            "size_gb": size_gb,
        })
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
      - provider: proveedor (gemini, vertex_gemini, anthropic, openai, ...)
      - model   : id del modelo (ej: gemini-2.5-pro)
      - tier    : free | paid (default: free)
      - arbiters: numero de arbitros configurados (default: cuenta los del config actual)
    """
    provider = request.args.get("provider", "gemini").strip() or "gemini"
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
        providers_models = [
            (a.get("provider", {}).get("type", "gemini"), a.get("provider", {}).get("model", ""))
            for a in cfg["arbiters"]
            if a.get("provider", {}).get("model")
        ]
        if not providers_models:
            return jsonify({"error": "No hay arbitros con modelo configurado"}), 400

        # Calcular para cada modelo y devolver el mas restrictivo (menor RPM)
        results   = [compute_min_delay(p, m, tier, n_arbiters) for p, m in providers_models if m]
        worst     = min(results, key=lambda r: r["rpm"]) if results else None
        if not worst:
            return jsonify({"error": "Ningun modelo configurado tiene limites disponibles"}), 400
        worst["all_models"] = results
        return jsonify(worst)

    return jsonify(compute_min_delay(provider, model, tier, n_arbiters))


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
        if model:
            r = compute_min_delay(ptype, model, tier, n_arbiters)
            r["arb_id"]   = arb["id"]
            r["arb_name"] = arb["name"]
            results.append(r)
        else:
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
                "alert_msg":   f"Proveedor '{ptype}': sin modelo configurado.",
            })

    # Delay global recomendado = el mayor de todos los delays minimos
    provider_results = [r for r in results if r.get("min_delay_s") is not None]
    if provider_results:
        global_min_delay  = max(r["min_delay_s"]  for r in provider_results)
        global_safe_delay = max(r["safe_delay_s"] for r in provider_results)
        worst             = max(provider_results, key=lambda r: r["min_delay_s"])
        global_alert      = worst["alert_level"]
        global_msg        = (f"Espera minima global: {global_min_delay}s "
                             f"(limitado por {worst.get('provider')} / {worst['model']} a {worst['rpm']} RPM). "
                             f"Espera segura para produccion: {global_safe_delay}s.")
    else:
        global_min_delay  = 1.5
        global_safe_delay = 3.0
        global_alert      = "info"
        global_msg        = "Sin arbitros con modelo configurado. Se usan valores por defecto."

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
# API: Modelos locales GGUF
# ---------------------------------------------------------------------------

@app.route("/api/local/hardware", methods=["GET"])
def api_local_hardware():
    """Retorna hw_profile.json (leído fresco en cada llamada)."""
    return jsonify(_load_hw_profile())


@app.route("/api/local/recommended", methods=["GET"])
def api_local_recommended():
    """
    Retorna lista de modelos recomendados según el hardware.
    Query param: ?refresh=1 para forzar nueva consulta a los LLMs.
    Siempre lee hw_profile.json fresco para reflejar cambios post-setup.
    """
    force = request.args.get("refresh") == "1"
    try:
        from scripts.local_models import recommended_models
        hw = _load_hw_profile()
        result = recommended_models(hw if hw else None, force_refresh=force)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/local/cached", methods=["GET"])
def api_local_cached():
    """Lista los GGUFs descargados en models/."""
    try:
        from scripts.local_models import list_cached_models
        return jsonify(list_cached_models())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


_download_jobs: dict = {}
_download_jobs_lock = threading.Lock()


def _valid_gguf_filename(filename: str) -> bool:
    return (
        bool(filename)
        and filename.endswith(".gguf")
        and "/" not in filename
        and "\\" not in filename
        and ".." not in filename
    )


@app.route("/api/local/download", methods=["POST"])
def api_local_download():
    """
    Inicia descarga de un modelo GGUF en background.
    Body JSON: {repo_id, filename}
    Retorna: {job_id}
    """
    body = request.get_json(silent=True) or {}
    repo_id  = (body.get("repo_id") or "").strip()
    filename = (body.get("filename") or "").strip()
    description = (body.get("description") or "").strip()

    if not repo_id or not filename:
        return jsonify({"error": "repo_id y filename son obligatorios"}), 400
    if not _valid_gguf_filename(filename):
        return jsonify({"error": "Nombre de archivo inválido"}), 400

    job_id = f"{int(time.time())}_{uuid.uuid4().hex[:8]}_{filename}"
    job = {
        "id": job_id,
        "repo_id": repo_id,
        "filename": filename,
        "status": "pending",
        "progress": 0,
        "error": None,
        "log_lines": [],
    }

    with _download_jobs_lock:
        _download_jobs[job_id] = job

    def _run():
        job["status"] = "downloading"
        try:
            from scripts.local_models import download_model

            def _progress(downloaded: int, total: int):
                if total:
                    job["progress"] = int(downloaded * 100 / total)

            download_model(repo_id, filename, progress_callback=_progress, description=description)
            job["status"] = "done"
            job["progress"] = 100
        except Exception as exc:
            job["status"] = "error"
            job["error"] = str(exc)

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    return jsonify({"job_id": job_id})


@app.route("/api/local/download/<job_id>", methods=["GET"])
def api_local_download_status(job_id: str):
    """SSE de progreso de descarga."""
    with _download_jobs_lock:
        job = _download_jobs.get(job_id)
    if not job:
        return jsonify({"error": "job no encontrado"}), 404

    def generate():
        # Ping inicial: establece la conexión SSE antes de que llegue el primer dato
        yield ": ping\n\n"
        while True:
            with _download_jobs_lock:
                current = dict(_download_jobs.get(job_id, {}))
            data = json.dumps({
                "status": current.get("status"),
                "progress": current.get("progress", 0),
                "error": current.get("error"),
            })
            yield f"data: {data}\n\n"
            if current.get("status") in ("done", "error"):
                break
            time.sleep(1)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"},
    )


@app.route("/api/local/model/<filename>", methods=["DELETE"])
def api_local_delete_model(filename: str):
    """Elimina un archivo GGUF del cache."""
    if not _valid_gguf_filename(filename):
        return jsonify({"error": "Nombre de archivo inválido"}), 400
    target = MODELS_DIR / filename
    if not target.exists():
        return jsonify({"error": "Archivo no encontrado"}), 404
    target.unlink()
    _log.info("Modelo local eliminado: %s", filename)
    return jsonify({"ok": True, "deleted": filename})


@app.route("/api/local/archive", methods=["GET"])
def api_local_archive():
    """Lista los GGUFs archivados en models/archive/."""
    try:
        from scripts.local_models import list_archived_models
        return jsonify(list_archived_models())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/local/model/<filename>/archive", methods=["POST"])
def api_local_archive_model(filename: str):
    """Mueve un GGUF descargado a models/archive/ para guardarlo fuera del cache activo."""
    if not _valid_gguf_filename(filename):
        return jsonify({"error": "Nombre de archivo invÃ¡lido"}), 400
    try:
        from scripts.local_models import archive_model
        archive_model(filename)
        _log.info("Modelo local archivado: %s", filename)
        return jsonify({"ok": True, "archived": filename})
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except FileExistsError as exc:
        return jsonify({"error": str(exc)}), 409
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/local/archive/<filename>/restore", methods=["POST"])
def api_local_restore_model(filename: str):
    """Restaura un GGUF desde models/archive/ hacia models/."""
    if not _valid_gguf_filename(filename):
        return jsonify({"error": "Nombre de archivo invÃ¡lido"}), 400
    try:
        from scripts.local_models import restore_model
        restore_model(filename)
        _log.info("Modelo local restaurado: %s", filename)
        return jsonify({"ok": True, "restored": filename})
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except FileExistsError as exc:
        return jsonify({"error": str(exc)}), 409
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/local/archive/<filename>", methods=["DELETE"])
def api_local_delete_archived_model(filename: str):
    """Elimina permanentemente un GGUF archivado."""
    if not _valid_gguf_filename(filename):
        return jsonify({"error": "Nombre de archivo invÃ¡lido"}), 400
    try:
        from scripts.local_models import delete_archived_model
        delete_archived_model(filename)
        _log.info("Modelo local archivado eliminado: %s", filename)
        return jsonify({"ok": True, "deleted": filename})
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


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

    raw_input = updated.get("input_file", cfg.get("dataset", {}).get("input_file", ""))
    if not raw_input:
        return jsonify({"error": "El archivo de entrada es obligatorio"}), 400
    resolved = Path(raw_input)
    if not resolved.is_absolute():
        resolved = BASE_DIR / resolved
    if not str(resolved.resolve()).startswith(str(BASE_DIR.resolve())):
        return jsonify({"error": "La ruta del dataset debe estar dentro del directorio del proyecto"}), 400

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
    path = path.resolve()
    if not str(path).startswith(str(BASE_DIR.resolve())):
        return jsonify({"error": "Ruta fuera del directorio del proyecto"}), 403

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

    from werkzeug.utils import secure_filename
    safe_name = secure_filename(f.filename)
    if not safe_name:
        return jsonify({"error": "Nombre de archivo no valido"}), 400

    suffix = Path(safe_name).suffix.lower()
    if suffix not in (".csv", ".xlsx", ".xls"):
        return jsonify({"error": f"Formato '{suffix}' no soportado. Usa .csv, .xlsx o .xls."}), 400

    upload_dir = BASE_DIR / "data" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    save_path = (upload_dir / safe_name).resolve()
    if not str(save_path).startswith(str(upload_dir.resolve())):
        return jsonify({"error": "Ruta de destino no permitida"}), 400
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


def _preflight_status_by_arbiter(cfg: dict) -> dict:
    arbiters = cfg.get("arbiters", [])
    statuses = {
        a.get("id", ""): {
            "id": a.get("id", ""),
            "ok": True,
            "issues": [],
            "access_checked": False,
        }
        for a in arbiters
        if a.get("id")
    }
    global_issues = []

    for issue in validate_provider_preflight(cfg):
        matched = False
        for arb_id in statuses:
            if issue.startswith(f"{arb_id}:"):
                statuses[arb_id]["ok"] = False
                statuses[arb_id]["issues"].append(issue)
                matched = True
        vertex_match = re.search(r"Vertex AI \(([^)]+)\):", issue)
        if vertex_match:
            for arb_id in [x.strip() for x in vertex_match.group(1).split(",")]:
                if arb_id in statuses:
                    statuses[arb_id]["ok"] = False
                    statuses[arb_id]["issues"].append(issue)
                    matched = True
        if not matched:
            global_issues.append(issue)

    for arb in arbiters:
        arb_id = arb.get("id", "")
        status = statuses.get(arb_id)
        if not status or not status["ok"]:
            continue
        provider = arb.get("provider", {})
        ptype = provider.get("type", "")
        model = provider.get("model", "")
        try:
            if ptype == "gemini":
                models = _list_gemini_models(_get_api_key("gemini"))
            elif ptype == "anthropic":
                models = _list_anthropic_models(_get_api_key("anthropic"))
            elif ptype == "openai":
                models = _list_openai_models(_get_api_key("openai"))
            elif ptype in {"ollama", "openai_compat"}:
                models = _list_openai_compat_models(_get_api_key(ptype), provider.get("base_url", ""))
            elif ptype == "vertex_gemini":
                # validate_provider_preflight already checks ADC/project. Vertex model discovery
                # is curated locally to avoid extra provider calls in this screen.
                models = _list_vertex_gemini_models(
                    provider.get("vertex_project", "") or os.environ.get("GOOGLE_CLOUD_PROJECT", ""),
                    provider.get("vertex_location", "") or os.environ.get("VERTEX_LOCATION", "us-central1"),
                )
            else:
                models = []
            status["access_checked"] = True
            ids = {m.get("id") for m in models if isinstance(m, dict)}
            if model and ids and model not in ids:
                status["ok"] = False
                status["issues"].append(
                    f"{arb_id}: el modelo seleccionado '{model}' no aparece en la lista disponible de {ptype}."
                )
        except Exception as exc:
            status["ok"] = False
            status["issues"].append(
                f"{arb_id}: no se pudo verificar acceso a {ptype}: {str(exc)[:260]}"
            )

    return {
        "ok": not global_issues and all(s["ok"] for s in statuses.values()),
        "issues": global_issues + [
            issue
            for status in statuses.values()
            for issue in status["issues"]
        ],
        "arbiters": list(statuses.values()),
    }


@app.route("/api/arbiters/preflight", methods=["GET"])
def api_arbiters_preflight():
    """Estado de configuracion/credenciales de arbitros antes de ejecutar."""
    return jsonify(_preflight_status_by_arbiter(read_config()))


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
    write_config(cfg)
    return jsonify({"ok": True})


@app.route("/api/arbiters/export", methods=["POST"])
def api_export_arbiters():
    data = request.get_json() or {}
    try:
        password = require_export_password(data.get("password", ""))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    cfg = read_config()
    payload = build_arbiters_export_payload(cfg.get("arbiters", []))
    encrypted = encrypt_arbiters_export(payload, password)
    filename = f"labelforge-arbiters-{time.strftime('%Y%m%d-%H%M%S')}.lfarb"
    return Response(
        encrypted,
        mimetype="application/vnd.labelforge.arbiters+json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.route("/api/arbiters/import", methods=["POST"])
def api_import_arbiters():
    password = request.form.get("password", "")
    mode = request.form.get("mode", "merge")
    if mode not in {"merge", "replace"}:
        return jsonify({"error": "Modo de importacion invalido."}), 400
    if "file" not in request.files:
        return jsonify({"error": "Selecciona un archivo de exportacion."}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Selecciona un archivo de exportacion."}), 400

    try:
        password = require_export_password(password)
        payload = decrypt_arbiters_export(f.read(), password)
        imported = validate_imported_arbiters(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    cfg = read_config()
    cfg["arbiters"], stats = merge_arbiters(cfg.get("arbiters", []), imported, mode)

    write_config(cfg)
    return jsonify({
        "ok": True,
        **stats,
    })


def _build_arbiter(data: dict, existing: dict = None) -> dict:
    """Construye un dict de arbitro limpio desde los datos del request."""
    return normalize_arbiter(data, existing=existing)


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
    if "{item}" not in template and "{claim}" not in template:
        return jsonify({"error": "El template debe contener {item}, que representa el texto de la entrada"}), 400
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
        provider_delays = []
        for arb in cfg.get("arbiters", []):
            provider = arb.get("provider", {})
            if provider.get("model"):
                provider_delays.append(
                    compute_min_delay(
                        provider.get("type", "gemini"),
                        provider["model"],
                        "free",
                        n_arbiters,
                    )
                )
        if provider_delays:
            min_delay = max(r["min_delay_s"] for r in provider_delays)
            if updated["base_delay_seconds"] < min_delay:
                worst = max(provider_delays, key=lambda r: r["min_delay_s"])
                return jsonify({
                    "error": (f"La espera base es demasiado baja para los limites del proveedor/modelo. "
                              f"Minimo recomendado: {min_delay}s "
                              f"(limitado por {worst.get('provider')} / {worst['model']} a {worst['rpm']} RPM)."),
                    "min_delay_seconds": min_delay,
                    "safe_delay_seconds": max(r["safe_delay_s"] for r in provider_delays),
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
    preflight_issues = validate_provider_preflight(cfg_data)
    if preflight_issues:
        return jsonify({
            "error": "No se puede iniciar: hay problemas de credenciales o proveedores.",
            "issues": preflight_issues,
        }), 400

    dataset_cfg = cfg_data.get("dataset", {})
    input_file  = dataset_cfg.get("input_file", "")
    sheet       = dataset_cfg.get("sheet", "")
    text_column = dataset_cfg.get("text_column", "claim")

    cmd = [sys.executable, str(BASE_DIR / "main_cli.py")]
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


@app.route("/api/outputs/archive-all", methods=["POST"])
def api_archive_all_outputs():
    import re
    from datetime import datetime
    files = list(OUTPUT_DIR.glob("labeled_*.csv")) if OUTPUT_DIR.exists() else []
    files = [f for f in files if re.match(r"^labeled_\d{8}_\d{6}\.csv$", f.name)]
    if not files:
        return jsonify({"ok": True, "archived": 0, "archive_dir": None})
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_dir = BASE_DIR / "archive" / f"partial_{stamp}"
    archive_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for f in files:
        dest = archive_dir / f.name
        f.rename(dest)
        count += 1
    return jsonify({"ok": True, "archived": count, "archive_dir": f"archive/partial_{stamp}"})


@app.route("/api/run/principle-counts", methods=["GET"])
def api_principle_counts():
    import csv as csv_mod
    stats      = _process_state["current_stats"]
    principles  = stats.get("principles") or []
    if not principles:
        return jsonify({"ok": False, "counts": {}, "principles": []})

    # Use live in-memory counts (updated on every claim_ok, no CSV read needed)
    live_counts = stats.get("principle_counts", {})
    if live_counts and any(
        live_counts.get(p, {}).get("ones", 0) + live_counts.get(p, {}).get("zeros", 0) > 0
        for p in principles
    ):
        counts = {p: live_counts.get(p, {"ones": 0, "zeros": 0}) for p in principles}
        return jsonify({"ok": True, "counts": counts, "principles": principles, "source": "live"})

    # Fallback: read from output CSV (written at checkpoint boundaries)
    output_path = stats.get("output_path")
    if not output_path:
        return jsonify({"ok": False, "counts": {}, "principles": principles})
    path = Path(output_path)
    if not path.exists():
        return jsonify({"ok": False, "counts": {}, "principles": principles})
    counts = {p: {"ones": 0, "zeros": 0} for p in principles}
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv_mod.DictReader(f):
                for p in principles:
                    v = row.get(f"{p}_consensus", "")
                    if v == "1":
                        counts[p]["ones"] += 1
                    elif v == "0":
                        counts[p]["zeros"] += 1
    except Exception:
        pass
    return jsonify({"ok": True, "counts": counts, "principles": principles, "source": "csv"})


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


def _build_result_rows(
    rows: list,
    principles: list,
    arbiter_ids: list,
    limit: int,
    newest_first: bool = True,
    offset: int = 0,
) -> list:
    result_rows = []
    indexed_rows = list(enumerate(rows))
    if newest_first:
        indexed_rows = [
            item for item in indexed_rows
            if _row_processing_status(item[1], principles) != "pending"
        ]
        indexed_rows.sort(key=lambda item: item[0], reverse=True)
    else:
        indexed_rows = indexed_rows[:limit]

    offset = max(0, offset)
    limit = max(0, limit)
    for idx, row in indexed_rows[offset:offset + limit]:
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





def _resolve_project_path(path_value: str | None) -> Path | None:
    if not path_value:
        return None
    path = Path(path_value)
    if not path.is_absolute():
        path = BASE_DIR / path
    resolved = path.resolve()
    if not str(resolved).startswith(str(BASE_DIR.resolve())):
        return None
    return resolved


def _load_input_rows_for_run_state(run_state: dict) -> tuple[list[dict], list[str]]:
    import csv as _csv

    input_path = _resolve_project_path(run_state.get("input_file"))
    if not input_path or not input_path.exists():
        return [], []

    suffix = input_path.suffix.lower()
    if suffix in {".xlsx", ".xlsm", ".xls"}:
        try:
            from openpyxl import load_workbook
        except Exception:
            return [], []

        wb = load_workbook(input_path, read_only=True, data_only=True)
        try:
            sheet_name = run_state.get("sheet") or wb.sheetnames[0]
            ws = wb[sheet_name] if sheet_name in wb.sheetnames else wb[wb.sheetnames[0]]
            iterator = ws.iter_rows(values_only=True)
            headers = [str(value or "").strip() for value in next(iterator, [])]
            rows = []
            for values in iterator:
                rows.append({
                    headers[idx]: "" if value is None else value
                    for idx, value in enumerate(values[:len(headers)])
                    if headers[idx]
                })
            return rows, [header for header in headers if header]
        finally:
            wb.close()

    with open(input_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = _csv.DictReader(f)
        return list(reader), reader.fieldnames or []


def _load_checkpoint_result_rows() -> tuple[list[dict], list[str], str]:
    run_state_path = CHECKPOINTS_DIR / "run_state.json"
    claim_results_path = CHECKPOINTS_DIR / "claim_results.jsonl"
    if not run_state_path.exists() or not claim_results_path.exists():
        return [], [], ""

    try:
        with open(run_state_path, "r", encoding="utf-8") as f:
            run_state = json.load(f)
        input_rows, input_fields = _load_input_rows_for_run_state(run_state)
        merged_by_index: dict[int, dict] = {}
        fieldnames = list(input_fields)

        with open(claim_results_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                idx = _safe_int(item.get("row_index"))
                if idx is None or idx < 0:
                    continue
                base_row = dict(input_rows[idx]) if idx < len(input_rows) else {}
                record = item.get("record") if isinstance(item.get("record"), dict) else {}
                merged_row = {**base_row, **record}
                merged_by_index[idx] = merged_row
                for key in merged_row.keys():
                    if key not in fieldnames:
                        fieldnames.append(key)

        rows = [merged_by_index[idx] for idx in sorted(merged_by_index)]
        source = f"checkpoint:{run_state.get('run_id') or claim_results_path.name}"
        return rows, fieldnames, source
    except Exception:
        return [], [], ""


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
    source_file = latest.name

    checkpoint_rows, checkpoint_fieldnames, checkpoint_source = _load_checkpoint_result_rows()
    if len(checkpoint_rows) > len(rows):
        rows = checkpoint_rows
        fieldnames = checkpoint_fieldnames
        source_file = checkpoint_source or "checkpoint"

    raw_limit = str(request.args.get("limit", "250")).strip().lower()
    try:
        result_offset = int(request.args.get("offset", 0))
    except (ValueError, TypeError):
        result_offset = 0
    result_offset = max(0, result_offset)

    result_limit = None if raw_limit in {"all", "todo", "todos", "0"} else 250
    if result_limit is not None:
        try:
            result_limit = int(raw_limit)
        except (ValueError, TypeError):
            result_limit = 250
        result_limit = max(1, min(result_limit, 1000))

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
    processed_result_rows = status_counts["ok"] + status_counts["failed"]
    effective_result_limit = processed_result_rows if result_limit is None else result_limit
    result_offset = min(result_offset, max(processed_result_rows - 1, 0)) if processed_result_rows else 0
    result_count = min(max(processed_result_rows - result_offset, 0), effective_result_limit)

    return jsonify({
        "source_file":         source_file,
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
        "results_total":       processed_result_rows,
        "results_count":       result_count,
        "results_offset":      result_offset,
        "results_limit":       effective_result_limit,
        "results_truncated":   result_offset + result_count < processed_result_rows,
        "results_has_prev":    result_offset > 0,
        "results_has_next":    result_offset + result_count < processed_result_rows,
        "results":             _build_result_rows(
            rows,
            principles,
            arbiter_ids,
            effective_result_limit,
            offset=result_offset,
        ),
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

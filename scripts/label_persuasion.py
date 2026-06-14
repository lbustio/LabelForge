"""
label_persuasion.py
-------------------
Etiqueta un dataset CSV o Excel con los principios de persuasion de Ana Ferreira
usando N arbitros configurables, cada uno con su propio proveedor de LLM.

Proveedores soportados:
  - gemini       : Google Gemini (google-genai)
  - vertex_gemini: Google Gemini via Vertex AI (google-cloud-aiplatform)
  - anthropic    : Anthropic Claude (anthropic)
  - openai       : OpenAI ChatGPT (openai)
  - openai_compat: Cualquier API compatible con OpenAI (Ollama, LM Studio, vLLM...)
  - ollama       : Ollama local (via openai-compatible endpoint)

Formatos de entrada soportados:
  - CSV  (.csv)  : cualquier CSV con cabeceras
  - Excel (.xlsx, .xls): requiere openpyxl; soporta seleccion de sheet

Toda la configuracion se lee desde arbiters_config.json.
Nada esta hardcodeado en este script.
"""

import os
import csv
import json
import time
import logging
import argparse
import warnings
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

# Silenciar warnings de deprecación del SDK de Vertex AI
warnings.filterwarnings("ignore", category=UserWarning, module="vertexai")

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Módulo compartido de métricas de acuerdo
# ---------------------------------------------------------------------------
from scripts.agreement_metrics import (
    CONSENSUS_METRIC_KEYS,
    build_consensus_metric_fieldnames,
    compute_consensus_metrics,
    flatten_consensus_metrics,
)
from scripts.utils import number as _number

# ---------------------------------------------------------------------------
# Rutas base
# ---------------------------------------------------------------------------
BASE_DIR    = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / "secrets.env")

OUTPUT_DIR     = BASE_DIR / "output"
LOG_DIR        = BASE_DIR / "logs"
CHECKPOINT_DIR = BASE_DIR / "checkpoints"

OUTPUT_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)
CHECKPOINT_DIR.mkdir(exist_ok=True)

RUN_ID             = datetime.now().strftime("%Y%m%d_%H%M%S")
CHECKPOINT_PATH    = CHECKPOINT_DIR / "checkpoint.json"
FAILED_QUEUE_PATH  = CHECKPOINT_DIR / "failed_queue.jsonl"
RUN_STATE_PATH     = CHECKPOINT_DIR / "run_state.json"
CLAIM_RESULTS_PATH = CHECKPOINT_DIR / "claim_results.jsonl"
ARBITER_RESULTS_PATH = CHECKPOINT_DIR / "arbiter_results.jsonl"
LOG_PATH           = LOG_DIR / f"run_{RUN_ID}.log"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

METRIC_PREFIX = "__RUN_METRIC__ "
WEBAPP_METRICS_ENABLED = os.environ.get("DATA_LABELING_WEBAPP_METRICS") == "1"


def emit_run_metric(event: str, **payload):
    """
    Emite eventos JSON para que la webapp actualice metricas en vivo.
    Se activa solo cuando la webapp lanza el proceso con DATA_LABELING_WEBAPP_METRICS=1.
    """
    if not WEBAPP_METRICS_ENABLED:
        return
    message = {
        "event": event,
        "run_id": RUN_ID,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        **payload,
    }
    print(METRIC_PREFIX + json.dumps(message, ensure_ascii=False), flush=True)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def write_json_atomic(path: Path, data: dict):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def append_jsonl_durable(path: Path, item: dict):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


# ---------------------------------------------------------------------------
# Carga de dataset (CSV y Excel)
# ---------------------------------------------------------------------------
def load_dataset(input_path: Path, sheet: str = "", text_column: str = "claim") -> tuple[list, list]:
    """
    Carga un dataset desde un archivo CSV o Excel.

    Args:
        input_path: ruta al archivo (.csv, .xlsx, .xls).
        sheet: nombre o indice (str) de la hoja de calculo. Solo aplica a Excel.
               Si es cadena vacia o None, se usa la primera hoja.
        text_column: nombre de la columna que contiene el texto a etiquetar.
                     Solo se valida existencia; no se filtra aqui.

    Returns:
        (rows, fieldnames) donde rows es lista de dicts y fieldnames es lista de str.

    Raises:
        SystemExit: si el archivo no existe, el formato no es soportado,
                    el sheet no existe o la columna no existe.
    """
    suffix = input_path.suffix.lower()

    if suffix in (".xlsx", ".xls"):
        try:
            import openpyxl  # noqa: PLC0415
        except ImportError:
            log.error("openpyxl no esta instalado. Ejecuta: pip install openpyxl")
            raise SystemExit(1)

        wb = openpyxl.load_workbook(str(input_path), read_only=True, data_only=True)
        sheet_names = wb.sheetnames

        # Resolver sheet
        if sheet in ("", None):
            ws = wb.active
            resolved_sheet = ws.title
        elif sheet in sheet_names:
            ws = wb[sheet]
            resolved_sheet = sheet
        else:
            # Intentar como indice numerico
            try:
                idx = int(sheet)
                resolved_sheet = sheet_names[idx]
                ws = wb[resolved_sheet]
            except (ValueError, IndexError):
                log.error(
                    f"Sheet '{sheet}' no encontrado. Sheets disponibles: {sheet_names}"
                )
                raise SystemExit(1)

        log.info(f"Leyendo Excel: sheet='{resolved_sheet}'")
        rows_raw = list(ws.iter_rows(values_only=True))
        wb.close()

        if not rows_raw:
            log.error("El archivo Excel esta vacio.")
            raise SystemExit(1)

        headers = [str(h) if h is not None else "" for h in rows_raw[0]]
        rows = []
        for row_vals in rows_raw[1:]:
            row_dict = {}
            for col_name, val in zip(headers, row_vals):
                row_dict[col_name] = "" if val is None else str(val)
            rows.append(row_dict)
        fieldnames = headers

    elif suffix == ".csv":
        with open(input_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames or [])
            rows = list(reader)
    else:
        log.error(f"Formato de archivo no soportado: '{suffix}'. Use .csv, .xlsx o .xls.")
        raise SystemExit(1)

    if text_column not in fieldnames:
        log.error(
            f"La columna '{text_column}' no existe en el dataset. "
            f"Columnas disponibles: {fieldnames}"
        )
        raise SystemExit(1)

    log.info(f"Dataset cargado: {len(rows)} filas, columna de texto='{text_column}'")
    return rows, fieldnames


def load_jsonl_latest(path: Path, key_fields: tuple) -> dict:
    latest = {}
    if not path.exists():
        return latest
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = tuple(str(item.get(k, "")) for k in key_fields)
            latest[key] = item
    return latest


def append_claim_result(row_index: int, record: dict, status: str = "ok"):
    append_jsonl_durable(CLAIM_RESULTS_PATH, {
        "row_index": row_index,
        "status": status,
        "updated_at": now_iso(),
        "record": record,
    })


def load_claim_results() -> dict:
    rows = {}
    for key, item in load_jsonl_latest(CLAIM_RESULTS_PATH, ("row_index",)).items():
        rows[key[0]] = item.get("record", {})
    return rows


def append_arbiter_result(row_index: int, arbiter_id: str, result: dict):
    append_jsonl_durable(ARBITER_RESULTS_PATH, {
        "row_index": row_index,
        "arbiter_id": arbiter_id,
        "updated_at": now_iso(),
        "result": result,
    })


def load_arbiter_results() -> dict:
    partial = {}
    for key, item in load_jsonl_latest(ARBITER_RESULTS_PATH, ("row_index", "arbiter_id")).items():
        row_index, arbiter_id = key
        partial.setdefault(row_index, {})[arbiter_id] = item.get("result", {})
    return partial


# ---------------------------------------------------------------------------
# Carga y validacion de configuracion
# ---------------------------------------------------------------------------
def load_config(config_path: Path) -> dict:
    if not config_path.exists():
        raise FileNotFoundError(f"Config no encontrado: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    for key in ["task", "arbiters", "processing"]:
        if key not in cfg:
            raise ValueError(f"El config JSON debe tener la clave '{key}'")
    if not cfg["arbiters"]:
        raise ValueError("Debe haber al menos un arbitro en 'arbiters'")
    for arb in cfg["arbiters"]:
        for key in ["id", "provider", "system_prompt"]:
            if key not in arb:
                raise ValueError(f"Arbitro '{arb.get('id','?')}' debe tener '{key}'")
        p = arb["provider"]
        if "type" not in p:
            raise ValueError(f"Arbitro '{arb['id']}': provider debe tener 'type'")
        if not p.get("model"):
            raise ValueError(
                f"Arbitro '{arb['id']}': provider.model esta vacio. "
                f"Configura el modelo desde la webapp antes de ejecutar."
            )
    if "user_prompt_template" not in cfg["task"]:
        raise ValueError("El config JSON debe tener 'task.user_prompt_template'")
    if "principles" not in cfg["task"]:
        raise ValueError("El config JSON debe tener 'task.principles'")

    log.info(f"Config cargado: {len(cfg['arbiters'])} arbitros, "
             f"{len(cfg['task']['principles'])} principios")
    return cfg


def is_local_base_url(base_url: str) -> bool:
    if not base_url:
        return False
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1", "host.docker.internal"}


def validate_safety_policy(cfg: dict):
    safety = cfg.get("safety", {})
    if not bool(safety.get("free_only_mode", False)):
        return

    allowed = set(safety.get("allowed_provider_types", ["gemini", "vertex_gemini", "ollama", "openai_compat"]))
    allow_remote_compat = bool(safety.get("allow_remote_openai_compat", False))
    violations = []

    for arb in cfg.get("arbiters", []):
        provider = arb.get("provider", {})
        ptype = provider.get("type", "")
        if ptype not in allowed:
            violations.append(
                f"{arb['id']} usa proveedor '{ptype}', no permitido en free_only_mode"
            )
            continue
        if ptype == "openai_compat" and not allow_remote_compat:
            base_url = provider.get("base_url", "")
            if not is_local_base_url(base_url):
                violations.append(
                    f"{arb['id']} usa openai_compat remoto/no local ({base_url or 'sin base_url'})"
                )

    if violations:
        msg = (
            "Politica de seguridad free_only_mode bloqueo la ejecucion para evitar "
            "tokens de paga: " + "; ".join(violations)
        )
        raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# Resolucion de API key
# ---------------------------------------------------------------------------
def resolve_api_key(provider: dict) -> str:
    """
    Devuelve la API key para un proveedor.
    Prioridad: api_key_override > variable de entorno especificada en api_key_env.
    """
    override = provider.get("api_key_override", "").strip()
    if override:
        return override
    env_var = provider.get("api_key_env", "").strip()
    if env_var:
        val = os.environ.get(env_var, "").strip()
        if val:
            return val
    return ""


# ---------------------------------------------------------------------------
# Rate limiter dinamico
# ---------------------------------------------------------------------------
class DynamicRateLimiter:
    def __init__(self, proc_cfg: dict):
        self.delay        = float(proc_cfg.get("base_delay_seconds", 1.5))
        self.min_delay    = float(proc_cfg.get("min_delay_seconds", 0.5))
        self.max_delay    = float(proc_cfg.get("max_delay_seconds", 120.0))
        self.rl_mult      = float(proc_cfg.get("rate_limit_backoff_multiplier", 2.0))
        self.srv_mult     = float(proc_cfg.get("server_error_backoff_multiplier", 1.5))
        self.rec_thresh   = int(proc_cfg.get("recovery_threshold_successes", 5))
        self.rec_mult     = float(proc_cfg.get("recovery_delay_multiplier", 0.8))
        self.consec_ok    = 0

    def wait(self):
        if self.delay > 0:
            time.sleep(self.delay)

    def on_success(self):
        self.consec_ok += 1
        if self.consec_ok >= self.rec_thresh:
            self.delay     = max(self.min_delay, self.delay * self.rec_mult)
            self.consec_ok = 0
            log.debug(f"Rate limiter: delay reducido a {self.delay:.2f}s")

    def on_rate_limit(self):
        self.consec_ok = 0
        self.delay     = min(self.max_delay, self.delay * self.rl_mult)
        log.warning(f"Rate limiter: 429 detectado, delay → {self.delay:.2f}s")

    def on_server_error(self):
        self.consec_ok = 0
        self.delay     = min(self.max_delay, self.delay * self.srv_mult)
        log.warning(f"Rate limiter: error servidor, delay → {self.delay:.2f}s")


def classify_llm_error(exc: Exception) -> dict:
    if isinstance(exc, json.JSONDecodeError):
        return {
            "kind": "invalid_response",
            "pause": False,
            "retryable": True,
            "message": "El LLM devolvio una respuesta que no es JSON valido.",
        }

    text = f"{type(exc).__name__}: {exc}".lower()
    quota_terms = [
        "429", "quota", "resource_exhausted", "rate_limit", "rate limit",
        "too many requests", "insufficient_quota", "billing", "credit",
        "credits", "exceeded",
    ]
    auth_terms = [
        "401", "403", "unauthorized", "forbidden", "permission denied",
        "api key", "apikey", "authentication", "auth",
    ]
    network_terms = [
        "timeout", "timed out", "connection", "network", "dns",
        "name resolution", "unreachable", "temporary failure", "ssl",
        "connection reset", "connection aborted",
    ]
    config_terms = [
        "400", "bad request", "invalid model", "model not found",
        "not found", "unsupported", "invalid argument",
    ]
    server_terms = ["500", "502", "503", "504", "overloaded", "unavailable", "server error"]

    if any(term in text for term in quota_terms):
        return {"kind": "quota_or_rate_limit", "pause": True, "retryable": True,
                "message": "Cuota/rate limit/billing detectado."}
    if any(term in text for term in auth_terms):
        return {"kind": "auth", "pause": True, "retryable": False,
                "message": "Credenciales o permisos invalidos."}
    if any(term in text for term in network_terms):
        return {"kind": "network", "pause": True, "retryable": True,
                "message": "Problema de red o comunicacion con proveedor."}
    if any(term in text for term in config_terms):
        return {"kind": "config", "pause": True, "retryable": False,
                "message": "Error de configuracion o solicitud invalida."}
    if any(term in text for term in server_terms):
        return {"kind": "provider_server", "pause": True, "retryable": True,
                "message": "Error temporal del proveedor LLM."}

    return {"kind": "unknown", "pause": True, "retryable": True,
            "message": "Error no clasificado; se pausa por seguridad."}


# ---------------------------------------------------------------------------
# Backends de LLM
# ---------------------------------------------------------------------------

def _call_gemini(arbiter: dict, prompt_user: str) -> tuple:
    from google import genai
    from google.genai import types

    provider = arbiter["provider"]
    api_key  = resolve_api_key(provider)
    if not api_key:
        raise ValueError(f"API key de Gemini no encontrada para arbitro '{arbiter['id']}'")

    client   = genai.Client(api_key=api_key)
    gen_cfg  = arbiter.get("generation_config", {})

    response = client.models.generate_content(
        model    = provider["model"],
        contents = prompt_user,
        config   = types.GenerateContentConfig(
            system_instruction = arbiter["system_prompt"],
            temperature        = float(gen_cfg.get("temperature", 0.1)),
            max_output_tokens  = int(gen_cfg.get("max_output_tokens", 600)),
        ),
    )
    usage = getattr(response, "usage_metadata", None)
    usage_dict = {
        "prompt_tokens": usage.prompt_token_count if usage else 0,
        "completion_tokens": usage.candidates_token_count if usage else 0
    }
    return response.text, usage_dict


def _call_vertex_gemini(arbiter: dict, prompt_user: str) -> tuple:
    import vertexai
    from vertexai.generative_models import GenerativeModel

    provider = arbiter["provider"]
    project = (provider.get("vertex_project") or os.environ.get("GOOGLE_CLOUD_PROJECT", "")).strip()
    location = (provider.get("vertex_location") or os.environ.get("VERTEX_LOCATION", "us-central1")).strip()
    if not project:
        raise ValueError(
            f"Google Cloud project no configurado para arbitro '{arbiter['id']}'. "
            "Define GOOGLE_CLOUD_PROJECT o provider.vertex_project."
        )

    vertexai.init(project=project, location=location or "us-central1")
    gen_cfg = arbiter.get("generation_config", {})
    model = GenerativeModel(
        provider["model"],
        system_instruction=arbiter["system_prompt"],
    )

    response = model.generate_content(
        prompt_user,
        generation_config={
            "temperature": float(gen_cfg.get("temperature", 0.1)),
            "max_output_tokens": int(gen_cfg.get("max_output_tokens", 600)),
        },
    )
    usage = getattr(response, "usage_metadata", None)
    usage_dict = {
        "prompt_tokens": usage.prompt_token_count if usage else 0,
        "completion_tokens": usage.candidates_token_count if usage else 0
    }
    return response.text, usage_dict


def _call_anthropic(arbiter: dict, prompt_user: str) -> tuple:
    import anthropic

    provider = arbiter["provider"]
    api_key  = resolve_api_key(provider)
    if not api_key:
        raise ValueError(f"API key de Anthropic no encontrada para arbitro '{arbiter['id']}'")

    gen_cfg = arbiter.get("generation_config", {})
    client  = anthropic.Anthropic(api_key=api_key)

    message = client.messages.create(
        model      = provider["model"],
        max_tokens = int(gen_cfg.get("max_output_tokens", 600)),
        system     = arbiter["system_prompt"],
        messages   = [{"role": "user", "content": prompt_user}],
        temperature= float(gen_cfg.get("temperature", 0.1)),
    )
    usage = getattr(message, "usage", None)
    usage_dict = {
        "prompt_tokens": usage.input_tokens if usage else 0,
        "completion_tokens": usage.output_tokens if usage else 0
    }
    return message.content[0].text, usage_dict


def _call_openai_compat(arbiter: dict, prompt_user: str) -> tuple:
    """
    Llamada compatible con OpenAI API.
    Soporta: openai (ChatGPT), ollama, openai_compat (LM Studio, vLLM, etc.)
    """
    from openai import OpenAI

    provider = arbiter["provider"]
    api_key  = resolve_api_key(provider) or "ollama"  # Ollama no requiere key real
    base_url = provider.get("base_url", "").strip() or None
    gen_cfg  = arbiter.get("generation_config", {})

    client = OpenAI(api_key=api_key, base_url=base_url)

    response = client.chat.completions.create(
        model       = provider["model"],
        messages    = [
            {"role": "system",  "content": arbiter["system_prompt"]},
            {"role": "user",    "content": prompt_user},
        ],
        temperature = float(gen_cfg.get("temperature", 0.1)),
        max_tokens  = int(gen_cfg.get("max_output_tokens", 600)),
    )
    usage = getattr(response, "usage", None)
    usage_dict = {
        "prompt_tokens": usage.prompt_tokens if usage else 0,
        "completion_tokens": usage.completion_tokens if usage else 0
    }
    return response.choices[0].message.content, usage_dict


PROVIDER_CALLERS = {
    "gemini":        _call_gemini,
    "vertex_gemini": _call_vertex_gemini,
    "anthropic":     _call_anthropic,
    "openai":        _call_openai_compat,
    "ollama":        _call_openai_compat,
    "openai_compat": _call_openai_compat,
}


def build_output_instruction(task_cfg: dict) -> str:
    principles = list(task_cfg["principles"].keys())
    include_why = bool(task_cfg.get("include_justifications", False))

    if include_why:
        fields = []
        for p in principles:
            fields.append(f'"{p}":0')
            fields.append(f'"{p}_why":"..."')
        schema = "{" + ",".join(fields) + "}"
        return (
            f"Return JSON only: {schema}\n"
            "Each *_why <=8 words. If absent, use \"no cue\"."
        )

    schema = "{" + ",".join(f'"{p}":0' for p in principles) + "}"
    return f"Return JSON only: {schema}"


def build_principles_catalog(task_cfg: dict) -> str:
    lines = ["Persuasion principles to label:"]
    for key, description in task_cfg["principles"].items():
        lines.append(f"- {key}: {description}")
    return "\n".join(lines)


def build_user_prompt(task_cfg: dict, claim: str, max_chars: int) -> str:
    template = task_cfg["user_prompt_template"]
    output_instruction = build_output_instruction(task_cfg)
    principles_catalog = build_principles_catalog(task_cfg)
    prompt = template.format(
        claim=claim[:max_chars],
        principles_catalog=principles_catalog,
        output_instruction=output_instruction,
        output_schema=output_instruction,
    )
    if "{principles_catalog}" not in template:
        prompt = f"{principles_catalog}\n\n{prompt}"
    if "{output_instruction}" not in template and "{output_schema}" not in template:
        prompt = f"{prompt}\n\n{output_instruction}"
    return prompt


# ---------------------------------------------------------------------------
# Llamada a un arbitro (cualquier proveedor)
# ---------------------------------------------------------------------------
def call_arbiter(
    arbiter: dict,
    claim: str,
    rate_limiter: DynamicRateLimiter,
    task_cfg: dict,
    proc_cfg: dict,
) -> Optional[dict]:
    arb_id     = arbiter["id"]
    principles = list(task_cfg["principles"].keys())
    max_chars  = int(proc_cfg.get("claim_max_chars", 3000))
    prov_type  = arbiter["provider"]["type"]

    caller = PROVIDER_CALLERS.get(prov_type)
    if caller is None:
        log.error(f"[{arb_id}] Proveedor desconocido: '{prov_type}'")
        return None

    prompt_user = build_user_prompt(task_cfg, claim, max_chars)
    rate_limiter.wait()

    try:
        t0 = time.perf_counter()
        raw, usage = caller(arbiter, prompt_user)
        latency_s = round(time.perf_counter() - t0, 3)
        raw = raw.strip()
        arbiter.pop("_last_error", None)

        # Limpiar bloques markdown ```json ... ```
        if raw.startswith("```"):
            parts = raw.split("```")
            raw   = parts[1] if len(parts) > 1 else raw
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        result = json.loads(raw)
        result["_usage"] = usage
        result["_latency_s"] = latency_s
        log.debug(f"[{arb_id}] Latencia: {latency_s:.3f}s")

        # Validar y normalizar
        for p in principles:
            if p not in result:
                raise ValueError(f"Falta campo '{p}' en respuesta de {arb_id}")
            val = result[p]
            if val not in (0, 1):
                try:
                    result[p] = 1 if int(val) >= 1 else 0
                except (TypeError, ValueError):
                    raise ValueError(f"Valor invalido para '{p}': {val}")
            if f"{p}_why" not in result:
                result[f"{p}_why"] = ""

        rate_limiter.on_success()
        return result

    except Exception as e:
        err = str(e)
        classification = classify_llm_error(e)
        arbiter["_last_error"] = f"{type(e).__name__}: {err[:300]}"
        arbiter["_last_error_kind"] = classification["kind"]
        arbiter["_last_pause_recommended"] = classification["pause"]
        arbiter["_last_retryable"] = classification["retryable"]
        if any(k in err.lower() for k in ["429", "quota", "resource_exhausted", "rate_limit", "overloaded"]):
            rate_limiter.on_rate_limit()
        elif any(k in err.lower() for k in ["500", "503", "timeout", "unavailable", "connection"]):
            rate_limiter.on_server_error()
        elif any(k in err.lower() for k in ["400", "invalid", "bad request"]):
            log.error(f"[{arb_id}] Error permanente: {err[:200]}")
        else:
            rate_limiter.on_server_error()

        log.warning(f"[{arb_id}] [{classification['kind']}] {err[:200]}")
        return None


# ---------------------------------------------------------------------------
# Consenso por mayoria
# ---------------------------------------------------------------------------
def compute_consensus(arb_results: list, principles: list) -> dict:
    n        = len(arb_results)
    majority = (n // 2) + 1
    return {
        p: 1 if sum(r.get(p, 0) for r in arb_results) >= majority else 0
        for p in principles
    }


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------
def load_checkpoint() -> dict:
    results = {}
    last_index = -1
    if CHECKPOINT_PATH.exists():
        with open(CHECKPOINT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        results.update(data.get("results", {}))
        last_index = int(data.get("last_index", -1))

    incremental_results = load_claim_results()
    if incremental_results:
        results.update(incremental_results)
        last_index = max(last_index, max(int(k) for k in incremental_results))

    if last_index >= 0:
        log.info(f"Checkpoint: hasta indice {last_index}, {len(results)} registros persistidos")
    return {"last_index": last_index, "results": results}


def save_checkpoint(last_index: int, results: dict):
    write_json_atomic(CHECKPOINT_PATH, {
        "last_index": last_index,
        "updated_at": now_iso(),
        "results": results,
    })
    log.debug(f"Checkpoint guardado en indice {last_index}")


# ---------------------------------------------------------------------------
# Cola de fallidos
# ---------------------------------------------------------------------------
def append_to_failed_queue(row_index: int, row: dict, reason: str = ""):
    with open(FAILED_QUEUE_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({"index": row_index, "row": row, "reason": reason}, ensure_ascii=False) + "\n")


def load_failed_queue() -> list:
    if not FAILED_QUEUE_PATH.exists():
        return []
    seen = {}
    with open(FAILED_QUEUE_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                item = json.loads(line)
                seen[item["index"]] = item
    return list(seen.values())


def clear_failed_queue():
    if FAILED_QUEUE_PATH.exists():
        FAILED_QUEUE_PATH.unlink()


def make_failed_record(arbiters: list, principles: list) -> dict:
    record = {}
    for arb in arbiters:
        for p in principles:
            record[f"{p}_{arb['id']}"]       = -1
            record[f"{p}_{arb['id']}_why"]   = "FAILED"
        record[f"prompt_tokens_{arb['id']}"] = -1
        record[f"completion_tokens_{arb['id']}"] = -1
        record[f"total_tokens_{arb['id']}"] = -1
        record[f"latency_s_{arb['id']}"] = -1
    for p in principles:
        record[f"{p}_consensus"] = -1
    return record


# ---------------------------------------------------------------------------
# Construccion de columnas extra del CSV
# ---------------------------------------------------------------------------
def build_extra_fieldnames(arbiters: list, principles: list) -> list:
    extra = []
    for p in principles:
        for arb in arbiters:
            extra += [f"{p}_{arb['id']}", f"{p}_{arb['id']}_why"]
        extra.append(f"{p}_consensus")
    for arb in arbiters:
        extra += [f"prompt_tokens_{arb['id']}", f"completion_tokens_{arb['id']}", f"total_tokens_{arb['id']}", f"latency_s_{arb['id']}"]
    return extra



def _record_status(record: dict, principles: list) -> str:
    values = [_number(record.get(f"{p}_consensus"), None) for p in principles]
    values = [v for v in values if v is not None]
    if not values:
        return "skipped"
    if any(v < 0 for v in values):
        return "failed"
    return "ok"


def summarize_records(records: dict, arbiters: list, principles: list) -> dict:
    summary = {
        "processed_rows": 0,
        "successful_rows": 0,
        "failed_rows": 0,
        "skipped_rows": 0,
        "total_tokens_global": 0,
        "total_latency_s": 0.0,
        "arbiters": {},
    }
    for arb in arbiters:
        summary["arbiters"][arb["id"]] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "calls": 0,
            "latency_total_s": 0.0,
            "latency_min_s": None,
            "latency_max_s": None,
        }

    for record in records.values():
        status = _record_status(record, principles)
        summary["processed_rows"] += 1
        if status == "ok":
            summary["successful_rows"] += 1
        elif status == "failed":
            summary["failed_rows"] += 1
        else:
            summary["skipped_rows"] += 1

        for arb in arbiters:
            arb_id = arb["id"]
            arb_stats = summary["arbiters"][arb_id]
            p_tok = _number(record.get(f"prompt_tokens_{arb_id}"), 0)
            c_tok = _number(record.get(f"completion_tokens_{arb_id}"), 0)
            t_tok = _number(record.get(f"total_tokens_{arb_id}"), p_tok + c_tok)
            lat = _number(record.get(f"latency_s_{arb_id}"), -1.0)
            if p_tok > 0:
                arb_stats["prompt_tokens"] += p_tok
            if c_tok > 0:
                arb_stats["completion_tokens"] += c_tok
            if t_tok > 0:
                arb_stats["total_tokens"] += t_tok
                summary["total_tokens_global"] += t_tok
            if lat >= 0:
                arb_stats["calls"] += 1
                arb_stats["latency_total_s"] += lat
                summary["total_latency_s"] += lat
                arb_stats["latency_min_s"] = lat if arb_stats["latency_min_s"] is None else min(arb_stats["latency_min_s"], lat)
                arb_stats["latency_max_s"] = lat if arb_stats["latency_max_s"] is None else max(arb_stats["latency_max_s"], lat)

    summary["total_latency_s"] = round(summary["total_latency_s"], 3)
    for arb_stats in summary["arbiters"].values():
        arb_stats["latency_total_s"] = round(arb_stats["latency_total_s"], 3)
        arb_stats["latency_avg_s"] = (
            round(arb_stats["latency_total_s"] / arb_stats["calls"], 3)
            if arb_stats["calls"] else 0
        )
        arb_stats["latency_min_s"] = round(arb_stats["latency_min_s"], 3) if arb_stats["latency_min_s"] is not None else 0
        arb_stats["latency_max_s"] = round(arb_stats["latency_max_s"], 3) if arb_stats["latency_max_s"] is not None else 0

    return summary


def build_claim_metric(row_index: int, row: dict, record: dict, arbiters: list, principles: list, stage: str) -> dict:
    arbiter_metrics = []
    total_tokens = 0
    total_latency = 0.0
    has_latency = False

    for arb in arbiters:
        arb_id = arb["id"]
        p_tok = _number(record.get(f"prompt_tokens_{arb_id}"), 0)
        c_tok = _number(record.get(f"completion_tokens_{arb_id}"), 0)
        t_tok = _number(record.get(f"total_tokens_{arb_id}"), p_tok + c_tok)
        lat = _number(record.get(f"latency_s_{arb_id}"), -1.0)
        if t_tok > 0:
            total_tokens += t_tok
        if lat >= 0:
            total_latency += lat
            has_latency = True

        arbiter_metrics.append({
            "id": arb_id,
            "name": arb.get("name", arb_id),
            "prompt_tokens": p_tok,
            "completion_tokens": c_tok,
            "total_tokens": t_tok,
            "latency_s": round(lat, 3) if lat >= 0 else None,
            "labels": {p: record.get(f"{p}_{arb_id}") for p in principles},
        })

    return {
        "row_index": row_index,
        "stage": stage,
        "claim": row.get("claim", ""),
        "claim_es": row.get("claim_es", ""),
        "dataset": row.get("dataset", ""),
        "class": row.get("class", ""),
        "consensus": {p: record.get(f"{p}_consensus") for p in principles},
        "total_tokens": total_tokens,
        "total_latency_s": round(total_latency, 3) if has_latency else None,
        "arbiters": arbiter_metrics,
    }


def load_run_state() -> dict:
    if not RUN_STATE_PATH.exists():
        return {}
    try:
        with open(RUN_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_progress_state(
    state: dict,
    results: dict,
    arbiters: list,
    principles: list,
    status: str,
    pause_kind: str = "",
    pause_reason: str = "",
):
    summary = summarize_records(results, arbiters, principles)
    state.update({
        "status": status,
        "updated_at": now_iso(),
        "pause_kind": pause_kind,
        "pause_reason": pause_reason,
        "summary": summary,
    })
    write_json_atomic(RUN_STATE_PATH, state)


def should_reuse_run_state(state: dict, args, total: int) -> bool:
    if not state:
        return False
    if state.get("status") not in {"running", "paused"}:
        return False
    return (
        state.get("input_file") == str(args.input)
        and state.get("config_file") == str(args.config)
        and int(state.get("total_rows", -1)) == int(total)
    )


def is_record_final(record: dict, principles: list) -> bool:
    return _record_status(record, principles) in {"ok", "failed", "skipped"}


def max_persisted_index(results: dict) -> int:
    indices = [int(k) for k in results.keys() if str(k).isdigit()]
    return max(indices) if indices else -1


def total_token_budget(cfg: dict) -> int:
    try:
        return int(cfg.get("safety", {}).get("max_total_tokens", 0) or 0)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Procesamiento de una entrada
# ---------------------------------------------------------------------------
def process_claim(
    row_index: int,
    claim: str,
    cfg: dict,
    rate_limiters: dict,
    partial_arbiter_results: dict = None,
) -> Optional[dict]:
    arbiters   = cfg["arbiters"]
    task_cfg   = cfg["task"]
    proc_cfg   = cfg["processing"]
    principles = list(task_cfg["principles"].keys())

    row_key = str(row_index)
    partial_arbiter_results = partial_arbiter_results if partial_arbiter_results is not None else {}
    arb_results = dict(partial_arbiter_results.get(row_key, {}))
    for arb in arbiters:
        if arb["id"] in arb_results:
            log.info(f"[entrada {row_index}] Arbitro {arb['id']} ya completado — reutilizando checkpoint")
            continue
        result = call_arbiter(arb, claim, rate_limiters[arb["id"]], task_cfg, proc_cfg)
        if result is None:
            reason = arb.get("_last_error", "sin detalle")
            cfg["_last_failure_reason"] = reason
            cfg["_last_failure_kind"] = arb.get("_last_error_kind", "unknown")
            cfg["_last_failure_retryable"] = arb.get("_last_retryable", True)
            cfg["_pause_requested"] = bool(arb.get("_last_pause_recommended", True))
            log.warning(f"[entrada {row_index}] Arbitro {arb['id']} fallo — entrada encolada — {reason[:240]}")
            return None
        arb_results[arb["id"]] = result
        partial_arbiter_results.setdefault(row_key, {})[arb["id"]] = result
        append_arbiter_result(row_index, arb["id"], result)

    consensus = compute_consensus(list(arb_results.values()), principles)

    record = {}
    for p in principles:
        for arb in arbiters:
            record[f"{p}_{arb['id']}"]     = arb_results[arb["id"]][p]
            record[f"{p}_{arb['id']}_why"] = arb_results[arb["id"]].get(f"{p}_why", "")
        record[f"{p}_consensus"] = consensus[p]

    for arb in arbiters:
        usage   = arb_results[arb["id"]].get("_usage", {"prompt_tokens": 0, "completion_tokens": 0})
        p_tok   = usage.get("prompt_tokens", 0)
        c_tok   = usage.get("completion_tokens", 0)
        latency = arb_results[arb["id"]].get("_latency_s", -1)
        record[f"prompt_tokens_{arb['id']}"]     = p_tok
        record[f"completion_tokens_{arb['id']}"] = c_tok
        record[f"total_tokens_{arb['id']}"]      = p_tok + c_tok
        record[f"latency_s_{arb['id']}"]         = latency

    log.info(f"[entrada {row_index}] OK — " + " ".join(f"{p}={consensus[p]}" for p in principles))
    return record


# ---------------------------------------------------------------------------
# Escritura del CSV final
# ---------------------------------------------------------------------------
def write_output_csv(
    rows: list,
    results: dict,
    original_fieldnames: list,
    arbiters: list,
    principles: list,
    output_path: Path,
):
    extra = build_extra_fieldnames(arbiters, principles)
    consensus_metric_fields = build_consensus_metric_fieldnames(principles)
    output_fieldnames = list(original_fieldnames) + extra + consensus_metric_fields
    empty_record = {f: "" for f in extra}
    consensus_metrics = flatten_consensus_metrics(
        compute_consensus_metrics(results, principles, arbiters),
        principles,
    )

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=output_fieldnames, extrasaction="ignore")
        writer.writeheader()
        for i, row in enumerate(rows):
            record = results.get(str(i), empty_record.copy())
            writer.writerow({**row, **record, **consensus_metrics})

    log.info(f"CSV final: {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Etiqueta entradas con principios de persuasion usando arbitros multi-proveedor."
    )
    parser.add_argument("--config", default=str(SCRIPTS_DIR / "arbiters_config.json"),
                        help="Ruta al JSON de configuracion")
    parser.add_argument("--input",  default=None,
                        help="Ruta al archivo de entrada (CSV o Excel). Si no se indica, se usa el valor del config.")
    parser.add_argument("--sheet",  default=None,
                        help="Nombre o indice del worksheet (solo para Excel). Vacio = primera hoja.")
    parser.add_argument("--column", default=None,
                        help="Nombre de la columna con el texto a etiquetar. Si no se indica, se usa el valor del config.")
    parser.add_argument("--limit",  type=int, default=None,
                        help="Limitar el numero de entradas (para pruebas)")
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    validate_safety_policy(cfg)
    arbiters   = cfg["arbiters"]
    principles = list(cfg["task"]["principles"].keys())
    proc_cfg   = cfg["processing"]
    dataset_cfg = cfg.get("dataset", {})

    # Jerarquia: CLI args > config > defaults
    input_file  = args.input  or dataset_cfg.get("input_file") or str(
        BASE_DIR / "data" / "all_datasets_en_es" / "all_datasets_en_es.csv"
    )
    sheet       = args.sheet  if args.sheet is not None else dataset_cfg.get("sheet", "")
    text_column = args.column or dataset_cfg.get("text_column") or "claim"

    log.info("Leyendo dataset...")
    rows, original_fieldnames = load_dataset(Path(input_file), sheet=sheet, text_column=text_column)

    total = len(rows) if args.limit is None else min(args.limit, len(rows))
    log.info(f"Total de entradas a procesar: {total}")

    previous_state = load_run_state()
    if should_reuse_run_state(previous_state, args, total):
        run_id = previous_state.get("run_id", RUN_ID)
        output_path = Path(previous_state.get("output_path", OUTPUT_DIR / f"labeled_{run_id}.csv"))
        state = previous_state
        # Recuperar text_column del estado anterior para garantizar coherencia en reanudacion
        text_column = previous_state.get("text_column", text_column)
        log.info(f"Reanudando run incompleto: {run_id}")
    else:
        run_id = RUN_ID
        output_path = OUTPUT_DIR / f"labeled_{run_id}.csv"
        state = {
            "run_id": run_id,
            "status": "running",
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "input_file": str(input_file),
            "sheet": sheet,
            "text_column": text_column,
            "config_file": str(args.config),
            "total_rows": total,
            "output_file": output_path.name,
            "output_path": str(output_path),
            "log_path": str(LOG_PATH),
            "arbiters": [
                {
                    "id": arb["id"],
                    "name": arb.get("name", arb["id"]),
                    "provider": arb.get("provider", {}).get("type", ""),
                    "model": arb.get("provider", {}).get("model", ""),
                }
                for arb in arbiters
            ],
            "principles": principles,
            "safety": cfg.get("safety", {}),
        }

    log.info(f"=== RUN_ID={run_id} ===")
    log.info(f"Config    : {args.config}")
    log.info(f"Input     : {input_file}")
    log.info(f"Sheet     : {sheet or '(primera hoja / N/A)'}")
    log.info(f"Columna   : {text_column}")
    log.info(f"Output    : {output_path}")
    for arb in arbiters:
        p = arb["provider"]
        log.info(f"  {arb['id']} ({arb['name']}): {p['type']} / {p['model']}")

    rate_limiters = {arb["id"]: DynamicRateLimiter(proc_cfg) for arb in arbiters}

    checkpoint = load_checkpoint()
    last_done  = checkpoint["last_index"]
    results    = checkpoint["results"]
    partial_arbiter_results = load_arbiter_results()
    if results:
        log.info(f"Reanudacion: {len(results)} entradas con resultado final persistido")
    if partial_arbiter_results:
        partial_count = sum(len(v) for v in partial_arbiter_results.values())
        log.info(f"Reanudacion: {partial_count} respuestas de arbitros persistidas")

    initial_summary = summarize_records(results, arbiters, principles)
    save_progress_state(state, results, arbiters, principles, "running")
    emit_run_metric(
        "start",
        run_id=run_id,
        total_rows=total,
        output_file=output_path.name,
        output_path=str(output_path),
        input_file=str(input_file),
        sheet=sheet,
        text_column=text_column,
        config_file=str(args.config),
        completed_before_start=initial_summary["processed_rows"],
        principles=principles,
        arbiters=[
            {
                "id": arb["id"],
                "name": arb.get("name", arb["id"]),
                "provider": arb.get("provider", {}).get("type", ""),
                "model": arb.get("provider", {}).get("model", ""),
            }
            for arb in arbiters
        ],
        initial_summary=initial_summary,
    )

    checkpoint_every = int(proc_cfg.get("checkpoint_every", 10))
    paused = False
    pause_kind = ""
    pause_reason = ""

    # Loop principal
    log.info("=== Loop principal ===")
    for i in range(total):
        existing = results.get(str(i))
        if existing and is_record_final(existing, principles):
            continue

        row   = rows[i]
        claim = row.get(text_column, "").strip()

        if not claim:
            log.warning(f"[entrada {i}] Vacia — saltando")
            results[str(i)] = {f: "" for f in build_extra_fieldnames(arbiters, principles)}
            append_claim_result(i, results[str(i)], "skipped")
            save_checkpoint(max_persisted_index(results), results)
            save_progress_state(state, results, arbiters, principles, "running")
            emit_run_metric("claim_skipped", row_index=i, stage="main", reason="entrada vacia")
            continue

        cfg.pop("_pause_requested", None)
        record = process_claim(i, claim, cfg, rate_limiters, partial_arbiter_results)
        if record is None:
            append_to_failed_queue(i, row, cfg.get("_last_failure_reason", "sin detalle"))
            pause_kind = cfg.get("_last_failure_kind", "unknown")
            pause_reason = cfg.get("_last_failure_reason", "sin detalle")
            emit_run_metric(
                "claim_failed",
                run_id=run_id,
                row_index=i,
                stage="main",
                reason=pause_reason,
                queued=True,
                definitive=False,
                claim=row.get(text_column, ""),
                claim_es=row.get("claim_es", ""),
            )
            save_checkpoint(max_persisted_index(results), results)
            if cfg.get("_pause_requested", True):
                paused = True
                log.error(f"=== RUN PAUSADO [{pause_kind}] === {pause_reason}")
                save_progress_state(state, results, arbiters, principles, "paused", pause_kind, pause_reason)
                emit_run_metric("paused", run_id=run_id, pause_kind=pause_kind, pause_reason=pause_reason)
                break
        else:
            results[str(i)] = record
            append_claim_result(i, record, "ok")
            save_checkpoint(max_persisted_index(results), results)
            save_progress_state(state, results, arbiters, principles, "running")
            emit_run_metric("claim_ok", run_id=run_id, **build_claim_metric(i, row, record, arbiters, principles, "main"))

            max_tokens = total_token_budget(cfg)
            if max_tokens > 0:
                current_total = summarize_records(results, arbiters, principles)["total_tokens_global"]
                if current_total >= max_tokens:
                    paused = True
                    pause_kind = "token_budget"
                    pause_reason = f"Limite local de tokens alcanzado: {current_total}/{max_tokens}"
                    log.error(f"=== RUN PAUSADO [token_budget] === {pause_reason}")
                    save_progress_state(state, results, arbiters, principles, "paused", pause_kind, pause_reason)
                    emit_run_metric("paused", run_id=run_id, pause_kind=pause_kind, pause_reason=pause_reason)
                    break

        if (i + 1) % checkpoint_every == 0:
            log.info(f"--- Progreso: {i + 1}/{total} ---")

    if paused:
        return

    save_checkpoint(max_persisted_index(results), results)
    log.info("=== Loop principal completado ===")

    # Cola de fallidos
    failed_items = load_failed_queue()
    if failed_items:
        log.info(f"=== Reprocesando {len(failed_items)} fallidos ===")
        for rl in rate_limiters.values():
            rl.delay = min(rl.max_delay, rl.delay * rl.srv_mult)

        still_failed = []
        for item in failed_items:
            idx    = item["index"]
            existing = results.get(str(idx))
            if existing and is_record_final(existing, principles):
                log.info(f"[entrada {idx}] Ya tiene resultado final — saltando cola de fallidos")
                continue
            claim  = item["row"].get(text_column, "").strip()
            cfg.pop("_pause_requested", None)
            record = process_claim(idx, claim, cfg, rate_limiters, partial_arbiter_results)
            if record is None:
                reason = cfg.get("_last_failure_reason", "sin detalle")
                kind = cfg.get("_last_failure_kind", "unknown")
                emit_run_metric(
                    "claim_failed",
                    run_id=run_id,
                    row_index=idx,
                    stage="retry",
                    reason=reason,
                    queued=False,
                    definitive=not cfg.get("_last_failure_retryable", True),
                    claim=item["row"].get(text_column, ""),
                    claim_es=item["row"].get("claim_es", ""),
                )
                if cfg.get("_pause_requested", True):
                    log.error(f"=== RUN PAUSADO [{kind}] === {reason}")
                    save_checkpoint(max_persisted_index(results), results)
                    save_progress_state(state, results, arbiters, principles, "paused", kind, reason)
                    emit_run_metric("paused", run_id=run_id, pause_kind=kind, pause_reason=reason)
                    return

                log.error(f"[entrada {idx}] Fallo definitivo")
                still_failed.append(item)
                results[str(idx)] = make_failed_record(arbiters, principles)
                append_claim_result(idx, results[str(idx)], "failed")
                save_checkpoint(max_persisted_index(results), results)
                save_progress_state(state, results, arbiters, principles, "running")
            else:
                results[str(idx)] = record
                append_claim_result(idx, record, "ok")
                save_checkpoint(max_persisted_index(results), results)
                save_progress_state(state, results, arbiters, principles, "running")
                emit_run_metric("claim_ok", run_id=run_id, **build_claim_metric(idx, item["row"], record, arbiters, principles, "retry"))

        if still_failed:
            log.warning(f"{len(still_failed)} entradas fallaron definitivamente:")
            for item in still_failed:
                log.warning(f"  - entrada {item['index']}")

        clear_failed_queue()
        save_checkpoint(total - 1, results)

    log.info("=== Escribiendo CSV final ===")
    write_output_csv(rows[:total], results, original_fieldnames, arbiters, principles, output_path)
    emit_run_metric("output_written", run_id=run_id, output_file=output_path.name, output_path=str(output_path))

    # Contabilidad de tokens acumulada
    tokens_by_arbiter = {arb["id"]: {"prompt": 0, "completion": 0} for arb in arbiters}
    for record in results.values():
        for arb in arbiters:
            p_tok = record.get(f"prompt_tokens_{arb['id']}", 0)
            c_tok = record.get(f"completion_tokens_{arb['id']}", 0)
            if isinstance(p_tok, int) and p_tok > 0:
                tokens_by_arbiter[arb["id"]]["prompt"] += p_tok
            if isinstance(c_tok, int) and c_tok > 0:
                tokens_by_arbiter[arb["id"]]["completion"] += c_tok

    # Latencia acumulada por arbitro
    latency_by_arbiter = {arb["id"]: [] for arb in arbiters}
    for record in results.values():
        for arb in arbiters:
            lat = record.get(f"latency_s_{arb['id']}", -1)
            if isinstance(lat, (int, float)) and lat >= 0:
                latency_by_arbiter[arb["id"]].append(lat)

    log.info("=== REPORTE DE CONSUMO DE TOKENS Y LATENCIA ===")
    global_total = 0
    for arb in arbiters:
        a_prompt     = tokens_by_arbiter[arb["id"]]["prompt"]
        a_completion = tokens_by_arbiter[arb["id"]]["completion"]
        a_total      = a_prompt + a_completion
        global_total += a_total
        lats = latency_by_arbiter[arb["id"]]
        avg_lat  = round(sum(lats) / len(lats), 3) if lats else 0
        min_lat  = round(min(lats), 3) if lats else 0
        max_lat  = round(max(lats), 3) if lats else 0
        total_lat = round(sum(lats), 3) if lats else 0
        log.info(f"  Arbitro {arb['id']} ({arb['name']}):")
        log.info(f"    Tokens Entrada (Prompt)     : {a_prompt}")
        log.info(f"    Tokens Salida (Completion)  : {a_completion}")
        log.info(f"    Tokens Totales              : {a_total}")
        log.info(f"    Latencia Promedio           : {avg_lat:.3f}s")
        log.info(f"    Latencia Min / Max          : {min_lat:.3f}s / {max_lat:.3f}s")
        log.info(f"    Latencia Total Acumulada    : {total_lat:.3f}s ({len(lats)} llamadas)")
    log.info(f"  CONSUMO GLOBAL TOTAL DEL RUN  : {global_total} tokens")

    successful = sum(
        1 for k in results
        if isinstance(results[k].get(f"{principles[0]}_consensus"), int)
        and results[k][f"{principles[0]}_consensus"] >= 0
    )
    log.info("=== Completado ===")
    log.info(f"  Exitosos : {successful}/{total}")
    log.info(f"  Salida   : {output_path}")
    log.info(f"  Log      : {LOG_PATH}")
    save_progress_state(state, results, arbiters, principles, "completed")
    emit_run_metric(
        "complete",
        run_id=run_id,
        output_file=output_path.name,
        output_path=str(output_path),
        successful_rows=successful,
        total_rows=total,
        final_summary=summarize_records(results, arbiters, principles),
    )


if __name__ == "__main__":
    main()

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
import getpass
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

# Silenciar warnings de deprecacion del SDK de Vertex AI
warnings.filterwarnings("ignore", category=UserWarning, module="vertexai")

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Modulo compartido de metricas de acuerdo
# ---------------------------------------------------------------------------
from labelforge_core.agreement_metrics import (
    build_entry_consensus_metric_fieldnames,
    build_consensus_metric_fieldnames,
    compute_entry_consensus_metrics,
    compute_consensus_metrics,
    compute_majority_consensus,
    flatten_entry_consensus_metrics,
    flatten_consensus_metrics,
    format_entry_consensus_metrics,
)
from labelforge_core.arbiters_io import (
    export_arbiters_to_file,
    import_arbiters_from_file,
    merge_arbiters,
)
from labelforge_core.checkpoint_io import (
    append_arbiter_result,
    append_claim_result,
    append_to_failed_queue,
    clear_failed_queue,
    clear_runtime_checkpoints,
    load_arbiter_results,
    load_checkpoint,
    load_failed_queue,
    load_run_state,
    save_checkpoint,
    save_run_state,
)
from labelforge_core.dataset_io import DatasetError, load_dataset
from labelforge_core.llm_json import parse_llm_json
from labelforge_core.prompting import build_user_prompt
from labelforge_core.secrets_io import provider_api_key_env, secrets_env_path, set_secret_value
from labelforge_core.provider_preflight import require_provider_preflight
from labelforge_core.usage_metrics import (
    add_entry_usage_metrics,
    build_entry_usage_metric_fieldnames,
)
from labelforge_core.utils import number as _number, arb_label as _arb_label
from labelforge_core.paths import (
    ARBITERS_CONFIG_PATH,
    BASE_DIR,
    CHECKPOINTS_DIR,
    LOGS_DIR,
    OUTPUT_DIR,
    resolve_config_file_path,
)

# ---------------------------------------------------------------------------
# Rutas base
# ---------------------------------------------------------------------------
SECRETS_ENV_PATH = secrets_env_path(BASE_DIR)
load_dotenv(SECRETS_ENV_PATH)

OUTPUT_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
CHECKPOINTS_DIR.mkdir(exist_ok=True)

RUN_ID             = datetime.now().strftime("%Y%m%d_%H%M%S")
LOG_PATH           = LOGS_DIR / f"run_{RUN_ID}.log"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', line_buffering=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)
for noisy_logger in ("httpx", "httpcore", "anthropic", "openai", "google"):
    logging.getLogger(noisy_logger).setLevel(logging.WARNING)

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


def claim_preview(text: str, limit: int = 180) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 3)].rstrip() + "..."


def log_claim_start(row_index: int, claim: str, stage: str, position: int, total: int, run_id: str):
    stage_label = "reintento" if stage == "retry" else "principal"
    preview = claim_preview(claim)
    log.info(
        "[entrada %s] Procesando %s/%s - etapa=%s - texto=\"%s\"",
        row_index,
        position,
        total,
        stage_label,
        preview,
    )
    emit_run_metric(
        "claim_start",
        run_id=run_id,
        row_index=row_index,
        stage=stage,
        position=position,
        total_rows=total,
        claim_preview=preview,
    )


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def read_config_file(config_path: Path) -> dict:
    config_path = resolve_config_file_path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config no encontrado: {config_path}")
    with open(config_path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def write_config_file(config_path: Path, cfg: dict):
    config_path = resolve_config_file_path(config_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = config_path.with_suffix(config_path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(config_path)


def resolve_arbiters_password(args) -> str:
    if args.arbiters_password:
        return args.arbiters_password
    if args.arbiters_password_file:
        return Path(args.arbiters_password_file).read_text(encoding="utf-8").strip()
    return getpass.getpass("Contrasena para arbitros cifrados: ")


def verify_provider_key(provider: str, api_key: str, base_url: str = "") -> int:
    if provider == "gemini":
        from google import genai as gai
        client = gai.Client(api_key=api_key)
        return len(list(client.models.list()))
    if provider == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        return len(client.models.list(limit=50).data)
    if provider == "openai":
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        return len(client.models.list().data)
    if provider == "openai_compat":
        from openai import OpenAI
        client = OpenAI(api_key=api_key or "local", base_url=base_url)
        return len(client.models.list().data)
    raise ValueError(f"Proveedor sin verificacion de API key: {provider}")


# ---------------------------------------------------------------------------
# Carga y validacion de configuracion
# ---------------------------------------------------------------------------
def load_config(config_path: Path) -> dict:
    if not config_path.exists():
        raise FileNotFoundError(f"Config no encontrado: {config_path}")
    with open(config_path, "r", encoding="utf-8-sig") as f:
        cfg = json.load(f)

    for key in ["task", "arbiters", "processing"]:
        if key not in cfg:
            raise ValueError(f"El config JSON debe tener la clave '{key}'")
    if not cfg["arbiters"]:
        raise ValueError("Sin arbitros no se puede hacer nada. Configura o importa al menos un arbitro antes de ejecutar.")
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
        log.warning(f"Rate limiter: 429 detectado, delay  ->  {self.delay:.2f}s")

    def on_server_error(self):
        self.consec_ok = 0
        self.delay     = min(self.max_delay, self.delay * self.srv_mult)
        log.warning(f"Rate limiter: error servidor, delay  ->  {self.delay:.2f}s")


def classify_llm_error(exc: Exception) -> dict:
    if isinstance(exc, json.JSONDecodeError):
        return {
            "kind": "invalid_response",
            "pause": False,
            "retryable": True,
            "message": "El LLM devolvio una respuesta que no es JSON valido.",
        }

    if isinstance(exc, (ValueError, KeyError, TypeError)):
        return {
            "kind": "invalid_response",
            "pause": False,
            "retryable": True,
            "message": "Campo faltante o valor invalido en respuesta del LLM.",
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
            max_output_tokens  = int(gen_cfg.get("max_output_tokens", 2048)),
            response_mime_type = "application/json",
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

    generation_config = {
        "temperature": float(gen_cfg.get("temperature", 0.1)),
        "max_output_tokens": int(gen_cfg.get("max_output_tokens", 2048)),
        "response_mime_type": "application/json",
    }
    # Disable thinking for gemini-2.5-flash models (pro doesn't support thinking_budget=0)
    if "gemini-2.5-flash" in provider["model"]:
        generation_config["thinking_config"] = {"thinking_budget": 0}

    response = model.generate_content(
        prompt_user,
        generation_config=generation_config,
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
        max_tokens = int(gen_cfg.get("max_output_tokens", 2048)),
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

    request_payload = {
        "model": provider["model"],
        "messages": [
            {"role": "system", "content": arbiter["system_prompt"]},
            {"role": "user", "content": prompt_user},
        ],
        "temperature": float(gen_cfg.get("temperature", 0.1)),
        "max_tokens": int(gen_cfg.get("max_output_tokens", 2048)),
        "response_format": {"type": "json_object"},
    }
    try:
        response = client.chat.completions.create(**request_payload)
    except Exception as exc:
        if "response_format" not in str(exc).lower() and "json_object" not in str(exc).lower():
            raise
        request_payload.pop("response_format", None)
        response = client.chat.completions.create(**request_payload)
    usage = getattr(response, "usage", None)
    usage_dict = {
        "prompt_tokens": usage.prompt_tokens if usage else 0,
        "completion_tokens": usage.completion_tokens if usage else 0
    }
    return response.choices[0].message.content, usage_dict


def _call_llama_cpp(arbiter: dict, prompt_user: str) -> tuple:
    from labelforge_core.local_models import get_or_load_llama

    provider = arbiter["provider"]
    model_path = provider.get("model_path") or provider.get("model", "")
    n_ctx = int(provider.get("n_ctx", 4096))
    gen_cfg = arbiter.get("generation_config", {})

    llama = get_or_load_llama(model_path, n_ctx=n_ctx)

    response = llama.create_chat_completion(
        messages=[
            {"role": "system", "content": arbiter["system_prompt"]},
            {"role": "user", "content": prompt_user},
        ],
        temperature=float(gen_cfg.get("temperature", 0.1)),
        max_tokens=int(gen_cfg.get("max_output_tokens", 2048)),
        response_format={"type": "json_object"},
    )
    content = response["choices"][0]["message"]["content"]
    usage = response.get("usage", {})
    usage_dict = {
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
    }
    return content, usage_dict


def _call_openvino(arbiter: dict, prompt_user: str) -> tuple:
    try:
        from optimum.intel import OVModelForCausalLM
        from transformers import AutoTokenizer, pipeline
    except ImportError as exc:
        raise RuntimeError(
            f"OpenVINO/optimum no instalado para inferencia NPU: {exc}. "
            "Ejecuta 'python setup_env.py' en un sistema con Intel AI Boost."
        ) from exc

    provider = arbiter["provider"]
    model_path = provider.get("model_path") or provider.get("model", "")
    gen_cfg = arbiter.get("generation_config", {})

    full_path = model_path if Path(model_path).is_absolute() else str(BASE_DIR / model_path)

    tokenizer = AutoTokenizer.from_pretrained(full_path)
    model = OVModelForCausalLM.from_pretrained(full_path, device="NPU")
    pipe = pipeline("text-generation", model=model, tokenizer=tokenizer)

    messages = [
        {"role": "system", "content": arbiter["system_prompt"]},
        {"role": "user", "content": prompt_user},
    ]
    prompt_text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    result = pipe(
        prompt_text,
        max_new_tokens=int(gen_cfg.get("max_output_tokens", 2048)),
        temperature=float(gen_cfg.get("temperature", 0.1)),
        do_sample=True,
    )
    generated = result[0]["generated_text"][len(prompt_text):]
    return generated, {"prompt_tokens": 0, "completion_tokens": 0}


def _call_local(arbiter: dict, prompt_user: str) -> tuple:
    """Dispatcher: NPU (OpenVINO) si disponible, sino llama-cpp-python."""
    try:
        from labelforge_core.local_models import load_hw_profile
        hw = load_hw_profile()
        best = hw.get("best_backend", "cpu")
    except FileNotFoundError:
        best = "cpu"

    if best == "npu":
        try:
            return _call_openvino(arbiter, prompt_user)
        except Exception as exc:
            log.warning("[local] NPU fallo (%s)  -  recayendo en llama-cpp-python", exc)

    return _call_llama_cpp(arbiter, prompt_user)


PROVIDER_CALLERS = {
    "gemini":        _call_gemini,
    "vertex_gemini": _call_vertex_gemini,
    "anthropic":     _call_anthropic,
    "openai":        _call_openai_compat,
    "ollama":        _call_openai_compat,
    "openai_compat": _call_openai_compat,
    "llama_cpp":     _call_local,
}


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
    if prov_type != "llama_cpp":
        rate_limiter.wait()

    try:
        t0 = time.perf_counter()
        raw, usage = caller(arbiter, prompt_user)
        latency_s = round(time.perf_counter() - t0, 3)
        raw = raw.strip()
        arbiter.pop("_last_error", None)

        result, parse_mode = parse_llm_json(raw, principles)
        if parse_mode != "direct":
            log.warning(f"[{arb_id}] JSON recuperado con parser '{parse_mode}'")
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
        total_tokens = int(usage.get("prompt_tokens", 0) or 0) + int(usage.get("completion_tokens", 0) or 0)
        log.info(f"[{arb_id}] llamada OK  -  latencia={latency_s:.3f}s tokens={total_tokens}")
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


def add_entry_consensus_metrics(record: dict, arbiters: list, principles: list) -> dict:
    metrics = compute_entry_consensus_metrics(record, arbiters, principles)
    record.update(flatten_entry_consensus_metrics(metrics, principles))
    return metrics


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
    for field in build_entry_consensus_metric_fieldnames(principles):
        record[field] = -1
    for field in build_entry_usage_metric_fieldnames():
        record[field] = -1
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
    extra.extend(build_entry_usage_metric_fieldnames())
    extra.extend(build_entry_consensus_metric_fieldnames(principles))
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

        entry_usage = add_entry_usage_metrics(record, arbiters)
        if entry_usage["entry_total_tokens"] > 0:
            summary["total_tokens_global"] += entry_usage["entry_total_tokens"]
        if entry_usage["entry_latency_s"] is not None and entry_usage["entry_latency_s"] >= 0:
            summary["total_latency_s"] += entry_usage["entry_latency_s"]

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
            if lat >= 0:
                arb_stats["calls"] += 1
                arb_stats["latency_total_s"] += lat
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
    usage_metrics = add_entry_usage_metrics(record, arbiters)

    for arb in arbiters:
        arb_id = arb["id"]
        p_tok = _number(record.get(f"prompt_tokens_{arb_id}"), 0)
        c_tok = _number(record.get(f"completion_tokens_{arb_id}"), 0)
        t_tok = _number(record.get(f"total_tokens_{arb_id}"), p_tok + c_tok)
        lat = _number(record.get(f"latency_s_{arb_id}"), -1.0)

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
        "consensus_metrics": add_entry_consensus_metrics(record, arbiters, principles),
        "usage_metrics": usage_metrics,
        "total_tokens": usage_metrics["entry_total_tokens"],
        "total_latency_s": usage_metrics["entry_latency_s"],
        "latency_avg_s": usage_metrics["entry_latency_avg_s"],
        "arbiter_calls": usage_metrics["entry_arbiter_calls"],
        "arbiters": arbiter_metrics,
    }


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
    save_run_state(state)


def should_reuse_run_state(state: dict, args, total: int, resolved_input: str = "") -> bool:
    if getattr(args, "fresh", False):
        return False
    if not state:
        return False
    if state.get("status") not in {"running", "paused"}:
        return False
    return (
        state.get("input_file") == str(resolved_input or args.input or "")
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
            log.info(f"[entrada {row_index}] Arbitro {arb['id']} ya completado  -  reutilizando checkpoint")
            continue
        result = call_arbiter(arb, claim, rate_limiters[arb["id"]], task_cfg, proc_cfg)
        if result is None:
            reason = arb.get("_last_error", "sin detalle")
            cfg["_last_failure_reason"] = reason
            cfg["_last_failure_kind"] = arb.get("_last_error_kind", "unknown")
            cfg["_last_failure_retryable"] = arb.get("_last_retryable", True)
            cfg["_pause_requested"] = bool(arb.get("_last_pause_recommended", True))
            log.warning(f"[entrada {row_index}] Arbitro {arb['id']} fallo  -  entrada encolada  -  {reason[:240]}")
            return None
        arb_results[arb["id"]] = result
        partial_arbiter_results.setdefault(row_key, {})[arb["id"]] = result
        append_arbiter_result(row_index, arb["id"], result)

    consensus = compute_majority_consensus(list(arb_results.values()), principles)

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

    add_entry_usage_metrics(record, arbiters)
    entry_consensus_metrics = add_entry_consensus_metrics(record, arbiters, principles)
    log.info(
        f"[entrada {row_index}] OK  -  "
        + " ".join(f"{p}={consensus[p]}" for p in principles)
        + "  -  "
        + format_entry_consensus_metrics(entry_consensus_metrics, principles)
    )
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
            if record:
                add_entry_usage_metrics(record, arbiters)
            if record and _record_status(record, principles) == "ok":
                add_entry_consensus_metrics(record, arbiters, principles)
            writer.writerow({**row, **record, **consensus_metrics})

    log.info(f"CSV final: {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Etiqueta entradas con principios de persuasion usando arbitros multi-proveedor."
    )
    parser.add_argument("--config", default=str(ARBITERS_CONFIG_PATH),
                        help="Ruta al JSON de configuracion")
    parser.add_argument("--input",  default=None,
                        help="Ruta al archivo de entrada (CSV o Excel). Si no se indica, se usa el valor del config.")
    parser.add_argument("--sheet",  default=None,
                        help="Nombre o indice del worksheet (solo para Excel). Vacio = primera hoja.")
    parser.add_argument("--column", default=None,
                        help="Nombre de la columna con el texto a etiquetar. Si no se indica, se usa el valor del config.")
    parser.add_argument("--limit",  type=int, default=None,
                        help="Limitar el numero de entradas (para pruebas)")
    parser.add_argument("--fresh", action="store_true",
                        help="Arrancar una corrida limpia ignorando checkpoints reanudables previos")
    parser.add_argument("--export-arbiters", default=None,
                        help="Exportar los arbitros a un archivo .lfarb cifrado y salir")
    parser.add_argument("--import-arbiters", default=None,
                        help="Importar arbitros desde un archivo .lfarb cifrado y salir")
    parser.add_argument("--replace-arbiters", action="store_true",
                        help="Al importar, reemplazar todos los arbitros actuales en vez de fusionar por ID")
    parser.add_argument("--arbiters-password", default=None,
                        help="Contrasena para importar/exportar arbitros cifrados")
    parser.add_argument("--arbiters-password-file", default=None,
                        help="Archivo que contiene la contrasena para importar/exportar arbitros cifrados")
    parser.add_argument("--set-provider-key", default=None,
                        help="Verificar y guardar API key para proveedor: gemini, anthropic, openai u openai_compat")
    parser.add_argument("--api-key", default=None,
                        help="API key para --set-provider-key")
    parser.add_argument("--api-key-env", default=None,
                        help="Variable donde guardar la API key en secrets/secrets.env")
    parser.add_argument("--base-url", default="",
                        help="Base URL para openai_compat al verificar API key")
    args = parser.parse_args()

    if args.export_arbiters and args.import_arbiters:
        raise SystemExit("Usa solo una operacion: --export-arbiters o --import-arbiters.")

    if args.set_provider_key:
        provider = args.set_provider_key.strip()
        api_key = args.api_key or getpass.getpass(f"API key para {provider}: ")
        env_var = args.api_key_env or provider_api_key_env(provider)
        if not env_var:
            raise SystemExit(f"Proveedor '{provider}' no usa API key en secrets/secrets.env.")
        try:
            count = verify_provider_key(provider, api_key, args.base_url)
        except Exception as exc:
            raise SystemExit(f"No se pudo verificar la clave: {exc}") from exc
        set_secret_value(SECRETS_ENV_PATH, env_var, api_key)
        print(f"Clave verificada y guardada en {env_var}. Modelos detectados: {count}.")
        return

    if args.export_arbiters:
        cfg = read_config_file(Path(args.config))
        password = resolve_arbiters_password(args)
        out_path = export_arbiters_to_file(
            cfg.get("arbiters", []),
            Path(args.export_arbiters),
            password,
        )
        print(f"Arbitros exportados en archivo cifrado: {out_path}")
        return

    if args.import_arbiters:
        cfg = read_config_file(Path(args.config))
        password = resolve_arbiters_password(args)
        imported = import_arbiters_from_file(Path(args.import_arbiters), password)
        mode = "replace" if args.replace_arbiters else "merge"
        cfg["arbiters"], stats = merge_arbiters(cfg.get("arbiters", []), imported, mode)
        write_config_file(Path(args.config), cfg)
        print(
            "Importacion lista: "
            f"{stats['added']} agregados, {stats['updated']} actualizados, "
            f"{stats['kept']} conservados, {stats['total']} total."
        )
        return

    cfg = load_config(Path(args.config))
    validate_safety_policy(cfg)
    require_provider_preflight(cfg)
    arbiters   = cfg["arbiters"]
    principles = list(cfg["task"]["principles"].keys())
    proc_cfg   = cfg["processing"]
    dataset_cfg = cfg.get("dataset", {})

    # Jerarquia: CLI args > config (sin fallback a archivos hardcodeados)
    input_file  = args.input  or dataset_cfg.get("input_file", "")
    sheet       = args.sheet  if args.sheet is not None else dataset_cfg.get("sheet", "")
    text_column = args.column or dataset_cfg.get("text_column", "")
    if not input_file:
        log.error(
            "No se especifico archivo de dataset. "
            "Usa --input <archivo> o configura dataset.input_file en el config."
        )
        raise SystemExit(1)
    if not text_column:
        log.error(
            "No se especifico columna de texto. "
            "Usa --column <columna> o configura dataset.text_column en el config."
        )
        raise SystemExit(1)

    log.info("Leyendo dataset...")
    try:
        rows, original_fieldnames = load_dataset(Path(input_file), sheet=sheet, text_column=text_column)
    except DatasetError as exc:
        log.error("%s", exc)
        raise SystemExit(1) from exc
    log.info(f"Dataset cargado: {len(rows)} filas, columna de texto='{text_column}'")

    total = len(rows) if args.limit is None else min(args.limit, len(rows))
    log.info(f"Total de entradas a procesar: {total}")

    if args.fresh:
        clear_runtime_checkpoints(include_run_state=True, include_claim_results=True)
        log.info("Modo --fresh: checkpoints reanudables previos limpiados")

    previous_state = load_run_state()
    if should_reuse_run_state(previous_state, args, total, resolved_input=str(input_file)):
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
        log.info(f"  {_arb_label(arb)}: {p['type']} / {p['model']}")

    rate_limiters = {arb["id"]: DynamicRateLimiter(proc_cfg) for arb in arbiters}

    checkpoint = load_checkpoint()
    # If checkpoint data exists but belongs to a different input/config, discard it.
    # Only reuse when dataset and config match; a higher limit on the same run is fine.
    if checkpoint["results"] and previous_state:
        prev_input  = previous_state.get("input_file", "")
        prev_config = previous_state.get("config_file", "")
        mismatch = (
            (prev_input  and prev_input  != str(input_file))
            or (prev_config and prev_config != str(args.config))
        )
        if mismatch:
            log.warning(
                "Checkpoint ignorado: corresponde a otro input o config. "
                "Use --fresh para iniciar completamente desde cero."
            )
            checkpoint = {"last_index": -1, "results": {}}

    last_done  = checkpoint["last_index"]
    results    = checkpoint["results"]
    partial_arbiter_results = load_arbiter_results()
    results = {
        str(k): v
        for k, v in results.items()
        if str(k).isdigit() and int(k) < total
    }
    partial_arbiter_results = {
        str(k): v
        for k, v in partial_arbiter_results.items()
        if str(k).isdigit() and int(k) < total
    }
    last_done = max_persisted_index(results)
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
        dataset_total_rows=len(rows),
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
            log.warning(f"[entrada {i}] Vacia  -  saltando")
            results[str(i)] = {f: "" for f in build_extra_fieldnames(arbiters, principles)}
            append_claim_result(i, results[str(i)], "skipped")
            save_checkpoint(max_persisted_index(results), results)
            save_progress_state(state, results, arbiters, principles, "running")
            emit_run_metric("claim_skipped", row_index=i, stage="main", reason="entrada vacia")
            continue

        cfg.pop("_pause_requested", None)
        log_claim_start(i, claim, "main", i + 1, total, run_id)
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
        for retry_pos, item in enumerate(failed_items, start=1):
            idx    = item["index"]
            existing = results.get(str(idx))
            if existing and is_record_final(existing, principles):
                log.info(f"[entrada {idx}] Ya tiene resultado final  -  saltando cola de fallidos")
                continue
            claim  = item["row"].get(text_column, "").strip()
            cfg.pop("_pause_requested", None)
            log_claim_start(idx, claim, "retry", retry_pos, len(failed_items), run_id)
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
    clear_runtime_checkpoints()
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

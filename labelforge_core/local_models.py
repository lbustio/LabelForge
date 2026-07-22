"""
local_models.py
---------------
Gestion de modelos GGUF locales para LabelForge.

Responsabilidades:
  - Leer hw_profile.json para parametros de runtime
  - Listar GGUFs cacheados en models/
  - Calcular n_gpu_layers optimo segun tamano del modelo vs VRAM disponible
  - Cache de modelos Llama cargados (evita recargar en cada llamada)
  - Catalogo de modelos recomendados por tier de hardware
  - Descarga desde Hugging Face con verificacion de integridad
"""

from __future__ import annotations

import concurrent.futures
import gc
import hashlib
import json
import logging
import os
import re
import struct
import threading
import time
from pathlib import Path
from typing import Optional

from labelforge_core.paths import (
    BASE_DIR,
    HW_PROFILE_PATH,
    MODELS_DIR,
    resolve_local_models_config_path,
)

log = logging.getLogger(__name__)

MODELS_JSON   = MODELS_DIR / "models.json"
HW_PROFILE    = HW_PROFILE_PATH
CATALOG_CACHE      = MODELS_DIR / "catalog_cache.json"
LOCAL_MODELS_CONFIG = resolve_local_models_config_path()

_catalog_cfg: dict = {}

def _load_catalog_cfg() -> dict:
    global _catalog_cfg
    if _catalog_cfg:
        return _catalog_cfg
    try:
        with open(LOCAL_MODELS_CONFIG, encoding="utf-8") as f:
            _catalog_cfg = json.load(f).get("catalog", {})
    except Exception as exc:
        log.warning("[catalog] No se pudo leer local_models_config.json: %s", exc)
        _catalog_cfg = {}
    return _catalog_cfg

def _catalog_ttl_s() -> int:
    return int(_load_catalog_cfg().get("cache_ttl_days", 7)) * 24 * 3600

def _catalog_system_prompt() -> str:
    return _load_catalog_cfg().get(
        "system_prompt",
        "You are an expert in local LLM deployment. Respond ONLY with valid JSON.",
    )

def _catalog_user_prompt(hw_summary: str) -> str:
    template = _load_catalog_cfg().get("user_prompt_template", "Hardware:\n{hw_summary}\n\nReturn a JSON array of GGUF model recommendations.")
    return template.format(hw_summary=hw_summary)

def _catalog_llm_model(provider: str) -> str:
    defaults = {"gemini": "gemini-2.5-flash", "anthropic": "claude-haiku-4-5", "openai": "gpt-4o-mini"}
    return _load_catalog_cfg().get("llm_models", {}).get(provider, defaults.get(provider, ""))

# ---------------------------------------------------------------------------
# Cache de modelos Llama cargados  -  evita recargar cuando el mismo arbitro
# local se usa de forma consecutiva. En GPU, varios GGUF medianos suelen no caber
# juntos, asi que se libera el modelo anterior antes de cargar otro distinto.
# Clave: (model_path_str, n_ctx)  Valor: (instancia Llama, ultimo acceso float)
# ---------------------------------------------------------------------------
_LLAMA_MODEL_CACHE: dict = {}   # {key: (Llama, last_access_time)}
_LLAMA_NEGOTIATED_CONTEXTS: dict[str, int] = {}
_cache_lock = threading.Lock()
_LLAMA_MAX_LOADED_MODELS = 1


def _release_llama_instance(instance) -> None:
    """Libera de forma determinista el contexto nativo y la memoria GPU/CPU."""
    close = getattr(instance, "close", None)
    if callable(close):
        try:
            close()
        except Exception as exc:
            log.warning("[local_models] El cierre explicito del modelo fallo: %s", exc)
    # llama-cpp-python conserva recursos nativos fuera del heap de Python. El
    # recolector debe ejecutarse antes de intentar reservar VRAM para otro GGUF.
    del instance
    gc.collect()


# ---------------------------------------------------------------------------
# hw_profile
# ---------------------------------------------------------------------------

def load_hw_profile() -> dict:
    if not HW_PROFILE.exists():
        raise FileNotFoundError(
            f"No se encontro {HW_PROFILE}. "
            "Ejecuta 'python setup/setup_env.py' antes de usar modelos locales."
        )
    with open(HW_PROFILE, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# GGUF metadata  -  leer numero de capas sin cargar el modelo
# ---------------------------------------------------------------------------

def _read_gguf_n_layers(model_path: Path) -> Optional[int]:
    """
    Lee el numero de capas del modelo desde los metadatos GGUF.
    Formato GGUF: magic(4) + version(4) + n_tensors(8) + n_kv(8) + kv_pairs...
    Busca la clave 'llama.block_count' o 'general.architecture' + '.block_count'.
    """
    try:
        with open(model_path, "rb") as f:
            magic = f.read(4)
            if magic != b"GGUF":
                return None
            version = struct.unpack("<I", f.read(4))[0]
            if version not in (2, 3):
                return None
            n_tensors = struct.unpack("<Q", f.read(8))[0]
            n_kv      = struct.unpack("<Q", f.read(8))[0]

            # Tipos de valor GGUF
            TYPE_SIZES = {0: 1, 1: 1, 2: 2, 3: 4, 4: 4, 5: 4, 6: 8, 7: 8, 8: 0, 10: 2, 11: 0, 12: 0}

            def read_string():
                slen = struct.unpack("<Q", f.read(8))[0]
                return f.read(slen).decode("utf-8", errors="replace")

            def skip_value(vtype: int):
                if vtype == 8:  # string
                    slen = struct.unpack("<Q", f.read(8))[0]
                    f.read(slen)
                elif vtype == 9:  # array
                    elem_type = struct.unpack("<I", f.read(4))[0]
                    count     = struct.unpack("<Q", f.read(8))[0]
                    for _ in range(count):
                        skip_value(elem_type)
                else:
                    size = TYPE_SIZES.get(vtype, 4)
                    f.read(size)

            for _ in range(n_kv):
                key   = read_string()
                vtype = struct.unpack("<I", f.read(4))[0]
                if key in ("llama.block_count", "mistral.block_count",
                           "phi3.block_count", "gemma.block_count",
                           "qwen2.block_count", "falcon.block_count"):
                    val = struct.unpack("<I", f.read(4))[0]
                    return int(val)
                skip_value(vtype)
    except Exception:
        pass
    return None


def _read_gguf_context_length(model_path: Path) -> Optional[int]:
    """Lee ``<arquitectura>.context_length`` sin crear un llama_context."""
    scalar_formats = {
        0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i",
        6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d",
    }
    try:
        with open(model_path, "rb") as metadata_file:
            if metadata_file.read(4) != b"GGUF":
                return None
            version = struct.unpack("<I", metadata_file.read(4))[0]
            if version not in (2, 3):
                return None
            metadata_file.read(8)  # tensor count
            metadata_count = struct.unpack("<Q", metadata_file.read(8))[0]

            def read_string() -> str:
                length = struct.unpack("<Q", metadata_file.read(8))[0]
                return metadata_file.read(length).decode("utf-8", errors="replace")

            def read_value(value_type: int):
                if value_type == 8:
                    return read_string()
                if value_type == 9:
                    element_type = struct.unpack("<I", metadata_file.read(4))[0]
                    count = struct.unpack("<Q", metadata_file.read(8))[0]
                    return [read_value(element_type) for _ in range(count)]
                value_format = scalar_formats.get(value_type)
                if not value_format:
                    raise ValueError(f"Tipo GGUF no soportado: {value_type}")
                return struct.unpack(value_format, metadata_file.read(struct.calcsize(value_format)))[0]

            for _ in range(metadata_count):
                key = read_string()
                value_type = struct.unpack("<I", metadata_file.read(4))[0]
                value = read_value(value_type)
                if key.endswith(".context_length") and isinstance(value, int) and value > 0:
                    return value
    except Exception as exc:
        log.debug("[local_models] No se pudo leer context_length de %s: %s", model_path.name, exc)
    return None


# ---------------------------------------------------------------------------
# Calculo de parametros optimos
# ---------------------------------------------------------------------------

def get_optimal_params(model_path: str, n_ctx: int = 0, hw_profile: Optional[dict] = None) -> dict:
    """
    Calcula {n_gpu_layers, n_threads, n_batch} optimos para un modelo GGUF
    segun el hardware disponible en hw_profile.json.
    """
    path = Path(model_path)
    if not path.is_absolute():
        path = BASE_DIR / path

    if hw_profile is None:
        try:
            hw_profile = load_hw_profile()
        except FileNotFoundError:
            hw_profile = {"gpu": {"detected": False}, "cpu": {"cores_physical": 4}, "best_backend": "cpu"}

    gpu   = hw_profile.get("gpu", {})
    cpu   = hw_profile.get("cpu", {})
    best  = hw_profile.get("best_backend", "cpu")

    cores = cpu.get("cores_physical", 4) or 4
    n_threads = max(1, min(cores, 8))  # Limitar para dejar nucleos al sistema

    # Sin GPU o backend CPU: todo en CPU
    if not gpu.get("detected") or best == "cpu":
        return {"n_gpu_layers": 0, "n_threads": n_threads, "n_batch": 512}

    vram_gb      = gpu.get("vram_gb")
    shared       = gpu.get("shared_memory", False)
    model_size_b = path.stat().st_size if path.exists() else 0
    model_size_g = model_size_b / (1024 ** 3)

    # Memoria efectiva disponible (factor conservador para memoria compartida)
    if shared and vram_gb:
        effective_vram = vram_gb * 0.75
    elif vram_gb:
        effective_vram = vram_gb * 0.90
    else:
        # VRAM desconocida  -  modo conservador: usar 4 GB estimado
        effective_vram = 4.0

    if model_size_g == 0 or model_size_g <= effective_vram * 0.9:
        # Todo el modelo cabe en VRAM (o no sabemos el tamano)
        n_gpu_layers = -1
    else:
        # Carga parcial: calcular cuantas capas caben
        n_layers = _read_gguf_n_layers(path)
        if n_layers and n_layers > 0:
            ratio = effective_vram / model_size_g
            n_gpu_layers = max(1, int(n_layers * ratio))
            log.info("[local_models] Offload parcial: %d/%d capas en GPU (VRAM %.1f GB, modelo %.1f GB)",
                     n_gpu_layers, n_layers, effective_vram, model_size_g)
        else:
            # No pudimos leer capas: modo seguro, solo CPU
            n_gpu_layers = 0
            log.warning("[local_models] No se pudo leer n_layers de %s  -  modo CPU", path.name)

    return {"n_gpu_layers": n_gpu_layers, "n_threads": n_threads, "n_batch": 512}


# ---------------------------------------------------------------------------
# Catalogo fallback (usado cuando los LLMs no estan disponibles)
# ---------------------------------------------------------------------------

_FALLBACK_CATALOG = [
    {
        "repo_id": "bartowski/Llama-3.2-1B-Instruct-GGUF",
        "filename": "Llama-3.2-1B-Instruct-Q4_K_M.gguf",
        "size_gb": 0.8, "ram_min_gb": 4, "tier": "micro",
        "description": "Llama 3.2 1B  -  muy rapido, ideal para CPU o sistemas con poca RAM",
    },
    {
        "repo_id": "bartowski/Llama-3.2-3B-Instruct-GGUF",
        "filename": "Llama-3.2-3B-Instruct-Q4_K_M.gguf",
        "size_gb": 2.0, "ram_min_gb": 6, "tier": "small",
        "description": "Llama 3.2 3B  -  balance calidad/velocidad para sistemas medianos",
    },
    {
        "repo_id": "bartowski/Meta-Llama-3.1-8B-Instruct-GGUF",
        "filename": "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
        "size_gb": 4.9, "ram_min_gb": 8, "tier": "medium",
        "description": "Llama 3.1 8B  -  buena calidad, requiere al menos 8 GB RAM/VRAM",
    },
    {
        "repo_id": "bartowski/Mistral-7B-Instruct-v0.3-GGUF",
        "filename": "Mistral-7B-Instruct-v0.3-Q4_K_M.gguf",
        "size_gb": 4.1, "ram_min_gb": 8, "tier": "medium",
        "description": "Mistral 7B  -  excelente para clasificacion estructurada",
    },
    {
        "repo_id": "bartowski/Qwen2.5-7B-Instruct-GGUF",
        "filename": "Qwen2.5-7B-Instruct-Q4_K_M.gguf",
        "size_gb": 4.7, "ram_min_gb": 8, "tier": "medium",
        "description": "Qwen 2.5 7B  -  buena alternativa a Mistral 7B",
    },
    {
        "repo_id": "bartowski/Llama-3.3-70B-Instruct-GGUF",
        "filename": "Llama-3.3-70B-Instruct-Q4_K_M.gguf",
        "size_gb": 43.0, "ram_min_gb": 48, "tier": "large",
        "description": "Llama 3.3 70B  -  maxima calidad, requiere GPU con 40+ GB VRAM",
    },
]


# ---------------------------------------------------------------------------
# Catalogo dinamico  -  consultado a los LLMs configurados
# ---------------------------------------------------------------------------


def _hw_summary(hw: dict) -> str:
    gpu = hw.get("gpu", {})
    npu = hw.get("npu", {})
    cpu = hw.get("cpu", {})
    lines = [
        f"OS: {hw.get('os', '?')}",
        f"CPU: {cpu.get('vendor', '?')} {cpu.get('model', '?')} ({cpu.get('cores_physical', '?')} cores)",
        f"RAM: {hw.get('ram_gb', '?')} GB",
        f"GPU: {gpu.get('name', 'none')}  -  VRAM: {gpu.get('vram_gb') or 'shared/unknown'} GB"
        + (" (shared memory)" if gpu.get("shared_memory") else ""),
        f"NPU: {npu.get('vendor', '') + ' ' + npu.get('type', '') if npu.get('detected') else 'none'}",
        f"Best backend: {hw.get('best_backend', 'cpu')}",
    ]
    return "\n".join(lines)


def _read_api_key(env_var: str) -> str:
    return os.environ.get(env_var, "").strip()


def _query_gemini(hw: dict) -> list[dict]:
    api_key = _read_api_key("GEMINI_API_KEY")
    if not api_key:
        return []
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=_catalog_llm_model("gemini"),
            contents=_catalog_user_prompt(_hw_summary(hw)),
            config=types.GenerateContentConfig(
                system_instruction=_catalog_system_prompt(),
                response_mime_type="application/json",
                temperature=0.2,
            ),
        )
        return _parse_llm_catalog_response(response.text)
    except Exception as exc:
        log.info("[catalog] Gemini no disponible: %s", exc)
        return []


def _query_anthropic(hw: dict) -> list[dict]:
    api_key = _read_api_key("ANTHROPIC_API_KEY")
    if not api_key:
        return []
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=_catalog_llm_model("anthropic"),
            max_tokens=2048,
            system=_catalog_system_prompt(),
            messages=[{"role": "user", "content": _catalog_user_prompt(_hw_summary(hw))}],
            temperature=0.2,
        )
        return _parse_llm_catalog_response(message.content[0].text)
    except Exception as exc:
        log.info("[catalog] Anthropic no disponible: %s", exc)
        return []


def _query_openai(hw: dict) -> list[dict]:
    api_key = _read_api_key("OPENAI_API_KEY")
    if not api_key:
        return []
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=_catalog_llm_model("openai"),
            messages=[
                {"role": "system", "content": _catalog_system_prompt()},
                {"role": "user",   "content": _catalog_user_prompt(_hw_summary(hw))},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=2048,
        )
        return _parse_llm_catalog_response(response.choices[0].message.content)
    except Exception as exc:
        log.info("[catalog] OpenAI no disponible: %s", exc)
        return []


_HF_REPO_RE = re.compile(r"^[\w][\w.\-]{0,38}/[\w][\w.\-]{0,95}$")
_GGUF_FILE_RE = re.compile(r"^[\w][\w.\-]{2,120}\.gguf$", re.IGNORECASE)


def _is_valid_hf_repo_id(repo_id: str) -> bool:
    """Comprueba que repo_id tiene formato owner/repo sin espacios ni rutas."""
    return bool(repo_id) and bool(_HF_REPO_RE.match(repo_id)) and "/" in repo_id


def _is_valid_gguf_filename(filename: str) -> bool:
    """Comprueba que filename parece un GGUF real (sin rutas, extension correcta)."""
    return (
        bool(filename)
        and bool(_GGUF_FILE_RE.match(filename))
        and "/" not in filename
        and "\\" not in filename
        and ".." not in filename
    )


def _parse_llm_catalog_response(raw: str) -> list[dict]:
    """Extrae una lista de dicts del JSON devuelto por el LLM."""
    if not raw:
        return []
    raw = raw.strip()
    # Quitar markdown fences si las hay
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)
    raw = raw.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Intentar extraer el primer array JSON del texto
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []
    # Puede venir como {"models": [...]} o directamente [...]
    if isinstance(data, dict):
        for key in ("models", "recommendations", "results"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list):
        return []

    required = {"repo_id", "filename", "size_gb", "ram_min_gb"}
    result = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if not required.issubset(item.keys()):
            continue
        # Normalizar tipos
        try:
            repo_id  = str(item["repo_id"]).strip()
            filename = str(item["filename"]).strip()
            size_gb  = float(item["size_gb"])
            ram_gb   = int(item["ram_min_gb"])
        except (ValueError, TypeError):
            continue
        if not _is_valid_hf_repo_id(repo_id):
            log.warning("[catalog] repo_id descartado (formato invalido): %r", repo_id)
            continue
        if not _is_valid_gguf_filename(filename):
            log.warning("[catalog] filename descartado (formato invalido): %r", filename)
            continue
        if size_gb <= 0 or ram_gb <= 0:
            log.warning("[catalog] modelo descartado (size/ram invalidos): %r %r", repo_id, filename)
            continue
        result.append({
            "repo_id":     repo_id,
            "filename":    filename,
            "size_gb":     size_gb,
            "ram_min_gb":  ram_gb,
            "tier":        str(item.get("tier", "medium")).strip(),
            "description": str(item.get("description", "")).strip(),
        })
    return result


def _synthesize_catalog(responses: list[list[dict]]) -> list[dict]:
    """
    Fusiona las respuestas de multiples LLMs.
    Los modelos recomendados por mas proveedores aparecen primero.
    Desduplicacion por filename (case-insensitive).
    """
    counts: dict[str, int] = {}
    by_filename: dict[str, dict] = {}

    for response in responses:
        seen_in_this = set()
        for item in response:
            if "tinyllama-1.1b-chat-v1.0" in item.get("filename", "").lower():
                item = {
                    **item,
                    "repo_id": "TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF",
                    "filename": "tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf",
                }
            key = item["filename"].lower()
            if key in seen_in_this:
                continue
            seen_in_this.add(key)
            counts[key] = counts.get(key, 0) + 1
            if key not in by_filename:
                by_filename[key] = item
            else:
                # Si la descripcion es mas larga, usarla
                if len(item.get("description", "")) > len(by_filename[key].get("description", "")):
                    by_filename[key]["description"] = item["description"]

    # Ordenar: mayor votos primero, luego por tamano
    ranked = sorted(
        by_filename.values(),
        key=lambda m: (-counts[m["filename"].lower()], m["size_gb"]),
    )
    # Anadir campo de consenso
    for m in ranked:
        m["consensus_votes"] = counts[m["filename"].lower()]

    return ranked


def _load_catalog_cache() -> Optional[list[dict]]:
    if not CATALOG_CACHE.exists():
        return None
    try:
        data = json.loads(CATALOG_CACHE.read_text(encoding="utf-8"))
        if data.get("source") != "huggingface":
            return None
        if time.time() - data.get("timestamp", 0) > _catalog_ttl_s():
            return None
        return data.get("catalog")
    except Exception:
        return None


def _save_catalog_cache(catalog: list[dict]):
    MODELS_DIR.mkdir(exist_ok=True)
    with open(CATALOG_CACHE, "w", encoding="utf-8") as f:
        json.dump({"timestamp": time.time(), "source": "huggingface", "catalog": catalog}, f, ensure_ascii=False, indent=2)


def _catalog_from_huggingface() -> list[dict]:
    """Obtiene repositorios y archivos exactos con una unica consulta al Hub."""
    from huggingface_hub import HfApi

    cfg = _load_catalog_cfg()
    quantization = str(cfg.get("quantization", "Q4_K_M")).lower()
    api = HfApi(token=_hf_token())
    models = api.list_models(
        filter=cfg.get("filter", "gguf"),
        search=cfg.get("search", "instruct"),
        sort=cfg.get("sort", "downloads"),
        limit=int(cfg.get("limit", 100)),
        expand=["gguf", "siblings", "downloads", "likes", "gated", "tags"],
    )
    catalog = []
    seen_files = set()
    for info in models:
        if getattr(info, "private", False) or getattr(info, "gated", False):
            continue
        repo_id = str(info.id)
        gguf_meta = getattr(info, "gguf", None) or {}
        total_params = int(gguf_meta.get("total") or 0)
        if not total_params:
            continue
        context_length = int(gguf_meta.get("context_length") or 0)
        siblings = getattr(info, "siblings", None) or []
        filenames = [
            s.rfilename for s in siblings
            if s.rfilename.lower().endswith(".gguf")
            and quantization in s.rfilename.lower()
            and "/" not in s.rfilename
        ]
        for filename in filenames[:1]:
            key = (repo_id.lower(), filename.lower())
            if key in seen_files:
                continue
            seen_files.add(key)
            # Q4_K_M usa aproximadamente 4.8 bits/peso mas metadatos. Esto se
            # marca como estimacion; la ruta nunca se deriva ni se modifica.
            size_gb = round(total_params * 0.62 / (1024 ** 3), 2) if total_params else 0
            params_b = total_params / 1_000_000_000 if total_params else 0
            if params_b >= 30:
                tier = "large"
            elif params_b >= 6:
                tier = "medium"
            elif params_b >= 2:
                tier = "small"
            else:
                tier = "micro"
            catalog.append({
                "repo_id": repo_id,
                "filename": filename,
                "size_gb": size_gb,
                "size_estimated": True,
                "ram_min_gb": max(2, int(size_gb * 1.35 + 0.99)) if size_gb else 0,
                "tier": tier,
                "description": f"GGUF Q4_K_M listado directamente por Hugging Face ({params_b:.1f}B parametros).",
                "parameters": total_params,
                "context_length": context_length,
                "downloads": int(getattr(info, "downloads", 0) or 0),
                "source": "huggingface",
            })
    return catalog


def build_dynamic_catalog(hw_profile: dict, force: bool = False) -> list[dict]:
    """
    Consulta Hugging Face una vez y conserva exactamente sus repo_id/filename.
    Usa cache local para no repetir la consulta en cada apertura del modal.
    """
    if not force:
        cached = _load_catalog_cache()
        if cached:
            log.info("[catalog] Usando catalogo en cache (%d modelos)", len(cached))
            return cached

    log.info("[catalog] Consultando catalogo GGUF directamente en Hugging Face...")
    catalog = _catalog_from_huggingface()
    log.info("[catalog] Hugging Face devolvio %d archivos Q4_K_M exactos", len(catalog))
    _save_catalog_cache(catalog)
    return catalog


def recommended_models(hw_profile: Optional[dict] = None, force_refresh: bool = False) -> list[dict]:
    """
    Retorna modelos recomendados para el hardware disponible.
    Consulta el catalogo de Hugging Face y filtra por el hardware local.
    """
    if hw_profile is None:
        try:
            hw_profile = load_hw_profile()
        except FileNotFoundError:
            hw_profile = {}

    catalog = build_dynamic_catalog(hw_profile, force=force_refresh)

    ram_gb  = hw_profile.get("ram_gb", 0) or 0
    gpu     = hw_profile.get("gpu", {})
    vram_gb = gpu.get("vram_gb") or 0
    shared  = gpu.get("shared_memory", False)

    # Si no hay hw_profile.json, intentar obtener RAM del sistema via psutil
    if not ram_gb and not vram_gb:
        try:
            import psutil
            ram_gb = round(psutil.virtual_memory().total / (1024 ** 3), 1)
            log.debug("[local_models] hw_profile vacio  -  usando RAM del sistema: %.1f GB", ram_gb)
        except Exception:
            pass

    hw_known = bool(ram_gb or vram_gb)

    if vram_gb and not shared:
        effective_mem = vram_gb * 0.90
    elif vram_gb and shared:
        effective_mem = vram_gb * 0.75
    else:
        effective_mem = ram_gb * 0.6

    result = []
    for model in catalog:
        fits = (model["size_gb"] <= effective_mem) if hw_known else None
        result.append({**model, "fits_in_memory": fits})

    result.sort(key=lambda m: (m["fits_in_memory"] is False, -m.get("consensus_votes", 0), -m["size_gb"]))
    return result


# ---------------------------------------------------------------------------
# Directorio de archivo de modelos
# ---------------------------------------------------------------------------

ARCHIVE_DIR = MODELS_DIR / "archive"


class DownloadCancelled(RuntimeError):
    """Senal interna para cancelar una descarga GGUF en curso."""

# Familias conocidas de modelos GGUF para parseo de nombre
_GGUF_FAMILIES = [
    ("codellama",  "Code Llama"),
    ("deepseek",   "DeepSeek"),
    ("starcoder",  "StarCoder"),
    ("openchat",   "OpenChat"),
    ("wizardlm",   "WizardLM"),
    ("wizard",     "WizardLM"),
    ("mixtral",    "Mixtral"),
    ("mistral",    "Mistral"),
    ("llama",      "Llama"),
    ("qwen",       "Qwen"),
    ("gemma",      "Gemma"),
    ("falcon",     "Falcon"),
    ("phi",        "Phi"),
    ("solar",      "SOLAR"),
    ("hermes",     "Hermes"),
    ("vicuna",     "Vicuna"),
    ("orca",       "Orca"),
    ("zephyr",     "Zephyr"),
    ("neural",     "NeuralChat"),
    ("tinyllama",  "TinyLlama"),
]


def _parse_gguf_metadata(filename: str) -> dict:
    """Extrae familia, parametros, cuantizacion y variante de un nombre de archivo GGUF."""
    stem = Path(filename).stem

    # Cuantizacion: al final del stem (Q4_K_M, Q8_0, F16, IQ3_XS, etc.)
    m = re.search(
        r'[-_]((?:IQ\d+_\w+|Q\d+(?:_K(?:_[SML])?)?|F16|F32|BF16))$',
        stem, re.IGNORECASE,
    )
    quant = m.group(1).upper() if m else ""

    # Recuento de parametros: ej. 3B, 7B, 13B, 0.5B, 1.5B
    m = re.search(r'[-_](\d+(?:\.\d+)?[Bb])', stem)
    params = m.group(1).upper() if m else ""

    # Familia: primera coincidencia en tabla de familias conocidas
    sl = stem.lower()
    family = next((name for key, name in _GGUF_FAMILIES if key in sl), "")
    if not family:
        family = re.split(r'[-_]', stem)[0].capitalize()

    # Variante: Instruct, Chat, Code, RLHF, Base
    variant = next(
        (v for v in ("Instruct", "Chat", "Code", "RLHF") if v.lower() in sl),
        "",
    )

    return {"family": family, "params": params, "quant": quant, "variant": variant}


# ---------------------------------------------------------------------------
# Listado de modelos cacheados
# ---------------------------------------------------------------------------

def list_cached_models() -> list[dict]:
    """Lista todos los archivos .gguf en models/ con metadata enriquecida."""
    MODELS_DIR.mkdir(exist_ok=True)

    registry: dict[str, dict] = {}
    if MODELS_JSON.exists():
        try:
            for entry in json.loads(MODELS_JSON.read_text(encoding="utf-8")):
                registry[entry["filename"]] = entry
        except Exception:
            pass

    models = []
    for gguf in sorted(MODELS_DIR.glob("*.gguf")):
        stat     = gguf.stat()
        size_b   = stat.st_size
        n_layers = _read_gguf_n_layers(gguf)
        meta     = registry.get(gguf.name, {})
        parsed   = _parse_gguf_metadata(gguf.name)
        models.append({
            "filename":    gguf.name,
            "path":        str(gguf.relative_to(BASE_DIR)),
            "abs_path":    str(gguf),
            "size_gb":     round(size_b / (1024 ** 3), 2),
            "size_bytes":  size_b,
            "n_layers":    n_layers,
            "repo_id":     meta.get("repo_id", ""),
            "creator":     (meta.get("repo_id", "").split("/", 1)[0] if meta.get("repo_id") else ""),
            "description": meta.get("description", ""),
            "modified_at": int(stat.st_mtime),
            **parsed,
        })
    return models


# ---------------------------------------------------------------------------
# Gestion del archivo de modelos (models/archive/)
# ---------------------------------------------------------------------------

def list_archived_models() -> list[dict]:
    """Lista los archivos .gguf en models/archive/."""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    registry: dict[str, dict] = {}
    if MODELS_JSON.exists():
        try:
            for entry in json.loads(MODELS_JSON.read_text(encoding="utf-8")):
                registry[entry["filename"]] = entry
        except Exception:
            pass

    models = []
    for gguf in sorted(ARCHIVE_DIR.glob("*.gguf")):
        stat   = gguf.stat()
        meta   = registry.get(gguf.name, {})
        parsed = _parse_gguf_metadata(gguf.name)
        models.append({
            "filename":    gguf.name,
            "path":        str(gguf.relative_to(BASE_DIR)),
            "abs_path":    str(gguf),
            "size_gb":     round(stat.st_size / (1024 ** 3), 2),
            "size_bytes":  stat.st_size,
            "n_layers":    _read_gguf_n_layers(gguf),
            "repo_id":     meta.get("repo_id", ""),
            "creator":     (meta.get("repo_id", "").split("/", 1)[0] if meta.get("repo_id") else ""),
            "description": meta.get("description", ""),
            "modified_at": int(stat.st_mtime),
            **parsed,
        })
    return models


def archive_model(filename: str) -> None:
    """Mueve un GGUF de models/ a models/archive/."""
    src = MODELS_DIR / filename
    if not src.exists():
        raise FileNotFoundError(f"Modelo no encontrado: {filename}")
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    dest = ARCHIVE_DIR / filename
    if dest.exists():
        raise FileExistsError(f"Ya existe un modelo archivado con ese nombre: {filename}")
    src.rename(dest)
    log.info("[local_models] Modelo archivado: %s", filename)


def restore_model(filename: str) -> None:
    """Mueve un GGUF de models/archive/ de vuelta a models/."""
    src = ARCHIVE_DIR / filename
    if not src.exists():
        raise FileNotFoundError(f"Modelo no encontrado en archivo: {filename}")
    MODELS_DIR.mkdir(exist_ok=True)
    dest = MODELS_DIR / filename
    if dest.exists():
        raise FileExistsError(f"Ya existe un modelo activo con ese nombre: {filename}")
    src.rename(dest)
    log.info("[local_models] Modelo restaurado: %s", filename)


def delete_archived_model(filename: str) -> None:
    """Elimina permanentemente un GGUF de models/archive/."""
    target = ARCHIVE_DIR / filename
    if not target.exists():
        raise FileNotFoundError(f"Modelo no encontrado en archivo: {filename}")
    target.unlink()
    log.info("[local_models] Modelo archivado eliminado: %s", filename)


def list_external_models() -> list[dict]:
    """Lista los modelos registrados como movidos fuera del cache (location: external)."""
    if not MODELS_JSON.exists():
        return []
    try:
        registry = json.loads(MODELS_JSON.read_text(encoding="utf-8"))
    except Exception:
        return []
    results = []
    for entry in registry:
        if entry.get("location") == "external" and entry.get("filename"):
            parsed = _parse_gguf_metadata(entry["filename"])
            path = entry.get("path", "")
            results.append({
                "filename":    entry["filename"],
                "path":        path,
                "abs_path":    path,
                "size_gb":     entry.get("size_gb", 0),
                "repo_id":     entry.get("repo_id", ""),
                "creator":     (entry.get("repo_id", "").split("/", 1)[0] if entry.get("repo_id") else ""),
                "description": entry.get("description", ""),
                "exists":      Path(path).exists() if path else False,
                **parsed,
            })
    return results


def restore_external_model(filename: str) -> None:
    """Mueve un GGUF externo de vuelta a models/ y actualiza el registro."""
    if not MODELS_JSON.exists():
        raise FileNotFoundError(f"No hay registro de modelos externos.")
    try:
        registry = json.loads(MODELS_JSON.read_text(encoding="utf-8"))
    except Exception:
        raise RuntimeError("No se pudo leer models.json.")

    entry = next((r for r in registry if r.get("filename") == filename and r.get("location") == "external"), None)
    if not entry:
        raise FileNotFoundError(f"Modelo externo no encontrado en el registro: {filename}")

    src = Path(entry.get("path", ""))
    if not src.exists():
        raise FileNotFoundError(f"El archivo ya no esta en la ruta registrada: {src}")

    MODELS_DIR.mkdir(exist_ok=True)
    dest = MODELS_DIR / filename
    if dest.exists():
        raise FileExistsError(f"Ya existe un modelo activo con ese nombre: {filename}")

    src.rename(dest)
    registry = [r for r in registry if r.get("filename") != filename]
    with open(MODELS_JSON, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)
    log.info("[local_models] Modelo externo restaurado: %s", filename)


def unregister_external_model(filename: str) -> None:
    """Elimina la entrada de un modelo externo del registro sin tocar el archivo."""
    if not MODELS_JSON.exists():
        raise FileNotFoundError(f"No hay registro de modelos externos.")
    try:
        registry = json.loads(MODELS_JSON.read_text(encoding="utf-8"))
    except Exception:
        raise RuntimeError("No se pudo leer models.json.")

    original = len(registry)
    registry = [r for r in registry if not (r.get("filename") == filename and r.get("location") == "external")]
    if len(registry) == original:
        raise FileNotFoundError(f"Modelo externo no encontrado en el registro: {filename}")

    with open(MODELS_JSON, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)
    log.info("[local_models] Modelo externo desregistrado: %s", filename)


def list_known_model_filenames() -> set[str]:
    """Devuelve modelos ya disponibles o movidos fuera del cache desde la app."""
    names = {m["filename"] for m in list_cached_models()}
    names.update(m["filename"] for m in list_archived_models())

    if MODELS_JSON.exists():
        try:
            registry = json.loads(MODELS_JSON.read_text(encoding="utf-8"))
        except Exception:
            registry = []
        for entry in registry:
            if entry.get("location") == "external" and entry.get("filename"):
                names.add(entry["filename"])
    return names


def move_model_to_directory(filename: str, destination_dir: str | Path) -> Path:
    """Mueve un GGUF activo desde models/ a una carpeta elegida por el usuario."""
    src = MODELS_DIR / filename
    if not src.exists():
        raise FileNotFoundError(f"Modelo no encontrado: {filename}")

    dest_dir = Path(destination_dir).expanduser()
    if not dest_dir.is_absolute():
        raise ValueError("La carpeta destino debe ser una ruta absoluta.")
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest = dest_dir / filename
    if dest.exists():
        raise FileExistsError(f"Ya existe un archivo con ese nombre en: {dest}")
    src.rename(dest)
    _mark_model_moved(filename, dest)
    log.info("[local_models] Modelo movido fuera del cache: %s -> %s", filename, dest)
    return dest


# ---------------------------------------------------------------------------
# Descarga desde Hugging Face
# ---------------------------------------------------------------------------

def _sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _resolve_gguf_filename(repo_id: str, filename: str) -> str:
    """
    Busca el nombre real de un archivo GGUF en el repo cuando el nombre exacto
    no existe. Estrategias en orden de preferencia:
      1. Coincidencia exacta ignorando mayusculas/minusculas
      2. Mismo sufijo de cuantizacion (ej. Q4_K_M.gguf) — cubre el caso donde
         el LLM omite el prefijo "Meta-" u otro prefijo del nombre real
      3. Mas palabras en comun con el nombre solicitado
    """
    try:
        from huggingface_hub import HfApi
        api = HfApi(token=_hf_token())
        files = list(api.list_repo_files(repo_id))
        gguf_files = [f for f in files if f.endswith(".gguf")]
        if not gguf_files:
            return filename

        filename_lower = filename.lower()

        # 1. Exacto case-insensitive
        exact = [f for f in gguf_files if f.lower() == filename_lower]
        if exact:
            log.warning("[local_models] Nombre corregido (case) %s: %s -> %s", repo_id, filename, exact[0])
            return exact[0]

        # 2. Mismo sufijo de cuantizacion (todo lo que hay tras el ultimo guion antes de .gguf)
        # ej. "Q4_K_M.gguf" de "Llama-3.1-8B-Instruct-Q4_K_M.gguf"
        import re
        quant_match = re.search(r'(Q\d[^.]*\.gguf|IQ\d[^.]*\.gguf|f16\.gguf|f32\.gguf)$', filename, re.I)
        if quant_match:
            quant_suffix = quant_match.group(1).lower()
            quant_matches = [f for f in gguf_files if f.lower().endswith(quant_suffix)]
            if len(quant_matches) == 1:
                log.warning("[local_models] Nombre corregido (quant) %s: %s -> %s", repo_id, filename, quant_matches[0])
                return quant_matches[0]
            if quant_matches:
                # Elegir el que tenga mas palabras en comun con el filename pedido
                stem = re.sub(r'\.gguf$', '', filename_lower)
                words = set(re.split(r'[-_.]', stem))
                best = max(quant_matches, key=lambda f: len(words & set(re.split(r'[-_.]', f.lower()))))
                log.warning("[local_models] Nombre corregido (quant+sim) %s: %s -> %s", repo_id, filename, best)
                return best

        log.warning("[local_models] '%s' no encontrado en %s. Disponibles: %s",
                    filename, repo_id, ", ".join(gguf_files[:10]))
    except Exception as exc:
        exc_str = str(exc)
        # Propagar errores de acceso para que el usuario sepa que hacer
        if any(k in exc_str.lower() for k in ("gated", "access", "restricted", "401", "403", "forbidden", "unauthorized")):
            if "meta-llama" in repo_id.lower():
                detail = "Acepta la licencia del modelo original de Meta y verifica HF_TOKEN."
            else:
                detail = "El repositorio puede no existir, ser privado o requerir HF_TOKEN; verifica el repo_id y el archivo exactos."
            raise RuntimeError(f"No se pudo acceder al repositorio '{repo_id}'. {detail}") from exc
        log.debug("[local_models] No se pudo listar archivos de %s: %s", repo_id, exc)
    return filename


def _hf_download_url(repo_id: str, filename: str) -> str:
    return f"https://huggingface.co/{repo_id}/resolve/main/{filename}"


def _hf_token() -> Optional[str]:
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_HUB_TOKEN")
    if token:
        return token
    try:
        from huggingface_hub import HfApi
        return HfApi().token
    except Exception:
        return None


def _stream_download(url: str, dest: Path, progress_callback=None) -> None:
    """
    Descarga con streaming, progreso en tiempo real y soporte de reanudacion.

    - Si existe un archivo .part de una descarga anterior, envia Range: bytes=N-
      para continuar desde donde quedo (el servidor debe soportar HTTP 206).
    - El .part NO se borra en caso de error, permitiendo reintentar y reanudar.
    - timeout=(15, 120): 15 s para conectar, 120 s entre chunks (evita stalls).
    """
    import requests as req_lib

    token = _hf_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    tmp = dest.parent / f"{dest.name}.part"

    # ------------------------------------------------------------------ resume
    resumed = tmp.stat().st_size if tmp.exists() else 0
    if resumed > 0:
        headers["Range"] = f"bytes={resumed}-"
        log.info("[local_models] Reanudando %s desde %.1f MB",
                 dest.name, resumed / 1_048_576)

    with req_lib.get(url, headers=headers, stream=True,
                     timeout=(15, 120), allow_redirects=True) as r:

        # 416 = rango ya satisfecho (archivo completo en .part)
        if r.status_code == 416:
            log.info("[local_models] Archivo ya completo en .part, renombrando")
            tmp.rename(dest)
            return

        if r.status_code == 401:
            if token:
                raise RuntimeError(
                    "Acceso denegado (401). El token HF_TOKEN es valido pero no tienes permiso "
                    "para este modelo. Acepta la licencia en huggingface.co (busca el repo "
                    "original de Meta y haz clic en 'Agree and access repository')."
                )
            else:
                raise RuntimeError(
                    "Acceso denegado (401). Este modelo es privado o de acceso restringido. "
                    "Agrega tu token de HuggingFace como HF_TOKEN en secrets/secrets.env "
                    "y reinicia la aplicacion."
                )

        r.raise_for_status()

        # Si el servidor ignora el Range y devuelve 200 en vez de 206,
        # empezar de cero para evitar datos duplicados/corruptos
        if resumed and r.status_code == 200:
            log.warning("[local_models] El servidor no soporta Range  -  reiniciando desde 0")
            resumed = 0
            headers.pop("Range", None)

        content_length = int(r.headers.get("content-length", 0))
        total      = resumed + content_length  # tamano completo del archivo
        downloaded = resumed
        last_logged_pct = (resumed * 100 // total) if total else -1

        # Notificar el progreso inicial (util cuando se reanuda)
        if progress_callback and total and resumed:
            progress_callback(downloaded, total)

        mode = "ab" if resumed else "wb"
        with open(tmp, mode) as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):  # 1 MB
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = int(downloaded * 100 / total)
                    if progress_callback:
                        progress_callback(downloaded, total)
                    if pct // 10 != last_logged_pct // 10:
                        log.info("[local_models] %s: %d%% (%.0f / %.0f MB)",
                                 dest.name, pct,
                                 downloaded / 1_048_576, total / 1_048_576)
                        last_logged_pct = pct
                else:
                    mb = int(downloaded / 1_048_576)
                    if mb % 100 == 0 and mb != last_logged_pct:
                        log.info("[local_models] %s: %.0f MB descargados...",
                                 dest.name, downloaded / 1_048_576)
                        last_logged_pct = mb

    tmp.rename(dest)


def download_model(
    repo_id: str,
    filename: str,
    expected_sha256: Optional[str] = None,
    progress_callback=None,
    description: str = "",
) -> Path:
    """
    Descarga un modelo GGUF desde Hugging Face Hub a models/ con progreso en tiempo real.
    Intenta corregir automaticamente el nombre del archivo si hay un 404.
    progress_callback(downloaded_bytes, total_bytes) se llama durante la descarga.
    Lanza RuntimeError si falla o el SHA256 no coincide.
    """
    try:
        import requests as req_lib  # noqa: F401
    except ImportError:
        raise RuntimeError("requests no disponible. Ejecuta 'python setup/setup_env.py' primero.")

    MODELS_DIR.mkdir(exist_ok=True)
    log.info("[local_models] Descargando %s/%s", repo_id, filename)

    dest = MODELS_DIR / filename
    url  = _hf_download_url(repo_id, filename)

    if dest.exists():
        log.info("[local_models] Modelo ya existe en cache: %s", dest.name)
        _update_models_json(dest, repo_id=repo_id, description=description)
        return dest

    try:
        _stream_download(url, dest, progress_callback=progress_callback)
    except DownloadCancelled:
        raise
    except Exception as exc:
        # repo_id y filename provienen del catalogo de Hugging Face. No intentar
        # adivinar aliases, cambiar mayusculas ni consultar de nuevo el repo.
        raise RuntimeError(f"Descarga fallida para {repo_id}/{filename}: {exc}") from exc

    log.info("[local_models] Descarga completada: %s (%.2f GB)",
             dest.name, dest.stat().st_size / (1024 ** 3))

    if expected_sha256:
        log.info("[local_models] Verificando integridad SHA256...")
        actual = _sha256_file(dest)
        if actual != expected_sha256:
            dest.unlink(missing_ok=True)
            raise RuntimeError(
                f"SHA256 no coincide para {filename}. "
                f"Esperado: {expected_sha256[:12]}... | Obtenido: {actual[:12]}... "
                "El archivo fue eliminado."
            )
        log.info("[local_models] Integridad OK")

    _update_models_json(dest, repo_id=repo_id, description=description)
    return dest


def _update_models_json(model_path: Path, repo_id: str = "", description: str = ""):
    """Actualiza models/models.json con el modelo recien descargado."""
    registry: list = []
    if MODELS_JSON.exists():
        try:
            registry = json.loads(MODELS_JSON.read_text(encoding="utf-8"))
        except Exception:
            registry = []

    entry = {
        "filename": model_path.name,
        "path": str(model_path.relative_to(BASE_DIR)),
        "size_gb": round(model_path.stat().st_size / (1024 ** 3), 2),
        "repo_id": repo_id,
        "description": description,
    }
    registry = [r for r in registry if r.get("filename") != model_path.name]
    registry.append(entry)

    with open(MODELS_JSON, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)


def _mark_model_moved(filename: str, destination: Path) -> None:
    """Marca un modelo como movido a una ruta externa para no recomendarlo otra vez."""
    registry: list = []
    if MODELS_JSON.exists():
        try:
            registry = json.loads(MODELS_JSON.read_text(encoding="utf-8"))
        except Exception:
            registry = []

    previous = next((r for r in registry if r.get("filename") == filename), {})
    entry = {
        **previous,
        "filename": filename,
        "path": str(destination),
        "size_gb": round(destination.stat().st_size / (1024 ** 3), 2),
        "location": "external",
    }
    registry = [r for r in registry if r.get("filename") != filename]
    registry.append(entry)

    MODELS_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(MODELS_JSON, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Cache de instancias Llama
# ---------------------------------------------------------------------------

def get_or_load_llama(model_path: str, n_ctx: int = 0, **llama_kwargs):
    """
    Retorna una instancia Llama del cache o la carga desde disco.
    Thread-safe mediante _cache_lock.
    Mantiene un solo GGUF cargado por defecto para evitar thrashing de VRAM.
    Si la carga falla, evicta el modelo menos usado y reintenta.
    """
    # Preparar las DLL solo cuando realmente se solicita el runtime GGUF.
    from labelforge_core.cuda_init import ensure_cuda_ready
    ensure_cuda_ready()
    try:
        from llama_cpp import Llama
    except ImportError:
        raise RuntimeError(
            "llama-cpp-python no instalado. Ejecuta 'python setup/setup_env.py' primero."
        )

    key = (model_path, n_ctx)
    with _cache_lock:
        if key in _LLAMA_MODEL_CACHE:
            instance, _ = _LLAMA_MODEL_CACHE[key]
            _LLAMA_MODEL_CACHE[key] = (instance, time.monotonic())
            return instance

        path = Path(model_path)
        if not path.is_absolute():
            path = BASE_DIR / path
        if not path.exists():
            raise FileNotFoundError(
                f"Modelo GGUF no encontrado: {path}. "
                "Descargalo desde la webapp (Arbitros  ->  Descargar modelos)."
            )
        optimal = get_optimal_params(str(path), n_ctx)
        trained_context = _read_gguf_context_length(path) if n_ctx == 0 else None
        negotiated_context = _LLAMA_NEGOTIATED_CONTEXTS.get(str(path.resolve())) if n_ctx == 0 else None
        if n_ctx > 0:
            context_candidates = [n_ctx]
        elif negotiated_context:
            context_candidates = []
            candidate = negotiated_context
            while candidate >= 4096:
                context_candidates.append(candidate)
                candidate //= 2
            if 4096 not in context_candidates:
                context_candidates.append(4096)
        elif trained_context:
            context_candidates = []
            candidate = trained_context
            while candidate >= 4096:
                context_candidates.append(candidate)
                candidate //= 2
            if 4096 not in context_candidates:
                context_candidates.append(4096)
        else:
            # 0 pide primero la capacidad nativa. Si la reserva falla, se
            # negocia hacia abajo sin perder texto: el pipeline fragmenta.
            context_candidates = [0, 65536, 32768, 16384, 8192, 4096]

        while True:
            while len(_LLAMA_MODEL_CACHE) >= _LLAMA_MAX_LOADED_MODELS:
                lru_key = min(_LLAMA_MODEL_CACHE, key=lambda k: _LLAMA_MODEL_CACHE[k][1])
                lru_name = Path(lru_key[0]).name
                evicted_instance, _ = _LLAMA_MODEL_CACHE.pop(lru_key)
                log.info(
                    "[local_models] Liberando '%s' antes de cargar '%s'",
                    lru_name, path.name,
                )
                _release_llama_instance(evicted_instance)
                del evicted_instance

            last_context_error = None
            for candidate_ctx in context_candidates:
                ctx_label = "nativo del modelo" if candidate_ctx == 0 else str(candidate_ctx)
                log.info("[local_models] Cargando modelo: %s (n_gpu_layers=%s, contexto=%s)",
                         path.name, optimal["n_gpu_layers"], ctx_label)
                try:
                    instance = Llama(
                        model_path=str(path),
                        n_ctx=candidate_ctx,
                        n_gpu_layers=llama_kwargs.get("n_gpu_layers", optimal["n_gpu_layers"]),
                        n_threads=llama_kwargs.get("n_threads", optimal["n_threads"]),
                        n_batch=llama_kwargs.get("n_batch", optimal["n_batch"]),
                        verbose=False,
                        **{k: v for k, v in llama_kwargs.items()
                           if k not in ("n_gpu_layers", "n_threads", "n_batch")},
                    )
                except Exception as exc:
                    if n_ctx == 0 and "create llama_context" in str(exc).lower():
                        last_context_error = exc
                        log.warning(
                            "[local_models] Contexto %s no cabe para %s; probando una ventana menor",
                            ctx_label, path.name,
                        )
                        continue
                    raise

                _LLAMA_MODEL_CACHE[key] = (instance, time.monotonic())
                actual_ctx = instance.n_ctx() if callable(getattr(instance, "n_ctx", None)) else candidate_ctx
                if n_ctx == 0 and actual_ctx:
                    _LLAMA_NEGOTIATED_CONTEXTS[str(path.resolve())] = int(actual_ctx)
                log.info(
                    "[local_models] Modelo cargado y en cache: %s (contexto efectivo=%s tokens%s)",
                    path.name, actual_ctx,
                    ", ajustado al hardware" if n_ctx == 0 and trained_context and actual_ctx < trained_context else "",
                )
                return instance
            if last_context_error:
                raise RuntimeError(
                    f"No se pudo crear un contexto local ni siquiera con {context_candidates[-1]} tokens"
                ) from last_context_error
            raise RuntimeError(f"No se pudo cargar el modelo local: {path.name}")


def clear_model_cache():
    """Libera todos los modelos cargados en memoria."""
    with _cache_lock:
        instances = [entry[0] for entry in _LLAMA_MODEL_CACHE.values()]
        _LLAMA_MODEL_CACHE.clear()
        for instance in instances:
            _release_llama_instance(instance)
        instances.clear()
        gc.collect()
    log.info("[local_models] Cache de modelos Llama vaciada")

"""
Provider/model rate-limit profiles used by the WebApp.

Limits are stored in config/rate_limits.json and loaded at runtime.
If the file does not exist, built-in defaults are written there automatically.
Use load_config() / save_config() / get_defaults() to read and update the config.
"""

from __future__ import annotations

import copy
import json
import threading

from labelforge_core.paths import RATE_LIMITS_CONFIG_PATH


# ---------------------------------------------------------------------------
# Built-in defaults  -  written to config/rate_limits.json on first run
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG: dict = {
    "gemini": {
        # Per-model limits for Gemini API / AI Studio (free and paid tiers).
        # Source: https://ai.google.dev/gemini-api/docs/rate-limits
        "gemini-2.5-pro":        {"free": {"rpm": 5,   "tpm": 250_000,   "rpd": 25},   "paid": {"rpm": 150,  "tpm": 2_000_000,  "rpd": None}},
        "gemini-2.5-flash":      {"free": {"rpm": 10,  "tpm": 250_000,   "rpd": 500},  "paid": {"rpm": 1000, "tpm": 4_000_000,  "rpd": None}},
        "gemini-2.5-flash-lite": {"free": {"rpm": 30,  "tpm": 1_000_000, "rpd": 1500}, "paid": {"rpm": 2000, "tpm": 4_000_000,  "rpd": None}},
        "gemini-2.0-flash":      {"free": {"rpm": 15,  "tpm": 1_000_000, "rpd": 1500}, "paid": {"rpm": 2000, "tpm": 4_000_000,  "rpd": None}},
        "gemini-2.0-flash-lite": {"free": {"rpm": 30,  "tpm": 1_000_000, "rpd": 1500}, "paid": {"rpm": 4000, "tpm": 4_000_000,  "rpd": None}},
        "gemini-1.5-pro":        {"free": {"rpm": 2,   "tpm": 32_000,    "rpd": 50},   "paid": {"rpm": 360,  "tpm": 4_000_000,  "rpd": None}},
        "gemini-1.5-flash":      {"free": {"rpm": 15,  "tpm": 1_000_000, "rpd": 1500}, "paid": {"rpm": 1000, "tpm": 4_000_000,  "rpd": None}},
        "gemini-3":              {"free": {"rpm": 5,   "tpm": 250_000,   "rpd": 25},   "paid": {"rpm": 150,  "tpm": 2_000_000,  "rpd": None}},
        "gemma":                 {"free": {"rpm": 30,  "tpm": 1_000_000, "rpd": 1500}, "paid": {"rpm": 1000, "tpm": 4_000_000,  "rpd": None}},
        "_default":              {"free": {"rpm": 10,  "tpm": 250_000,   "rpd": 500},  "paid": {"rpm": 500,  "tpm": 2_000_000,  "rpd": None}},
    },
    "openai": {
        # Per-model limits for OpenAI Tier 1.
        # Source: https://platform.openai.com/settings/organization/limits
        "gpt-5.5-pro":   {"rpm": 3,   "tpm": 10_000,    "rpd": 50},
        "gpt-5.5":       {"rpm": 3,   "tpm": 10_000,    "rpd": 50},
        "gpt-5.4-pro":   {"rpm": 3,   "tpm": 10_000,    "rpd": 50},
        "gpt-5.4-nano":  {"rpm": 10,  "tpm": 60_000,    "rpd": 50},
        "gpt-5.4-mini":  {"rpm": 10,  "tpm": 100_000,   "rpd": 50},
        "gpt-5.4":       {"rpm": 3,   "tpm": 10_000,    "rpd": 50},
        "gpt-5.3":       {"rpm": 3,   "tpm": 10_000,    "rpd": 50},
        "gpt-5.2-pro":   {"rpm": 3,   "tpm": 10_000,    "rpd": 50},
        "gpt-5.2":       {"rpm": 3,   "tpm": 10_000,    "rpd": 50},
        "gpt-5.1":       {"rpm": 3,   "tpm": 10_000,    "rpd": 50},
        "gpt-5-pro":     {"rpm": 10,  "tpm": 30_000,    "rpd": 50},
        "gpt-5-nano":    {"rpm": 10,  "tpm": 40_000,    "rpd": 50},
        "gpt-5-mini":    {"rpm": 10,  "tpm": 60_000,    "rpd": 50},
        "gpt-5":         {"rpm": 3,   "tpm": 10_000,    "rpd": 50},
        "gpt-4.1-nano":  {"rpm": 10,  "tpm": 60_000,    "rpd": 50},
        "gpt-4.1-mini":  {"rpm": 10,  "tpm": 60_000,    "rpd": 50},
        "gpt-4.1":       {"rpm": 3,   "tpm": 10_000,    "rpd": 50},
        "gpt-4o-mini":   {"rpm": 10,  "tpm": 60_000,    "rpd": 50},
        "gpt-4o":        {"rpm": 3,   "tpm": 10_000,    "rpd": 50},
        "gpt-3.5-turbo": {"rpm": 3,   "tpm": 40_000,    "rpd": 50},
        "o3-mini":       {"rpm": 150, "tpm": 1_000_000, "rpd": None},
        "o4-mini":       {"rpm": 3,   "tpm": 100_000,   "rpd": 50},
        "o3":            {"rpm": 3,   "tpm": 100_000,   "rpd": 50},
        "o1":            {"rpm": 10,  "tpm": 100_000,   "rpd": 50},
        "_default":      {"rpm": 10,  "tpm": 100_000,   "rpd": 50},
    },
    "account_defaults": {
        # Conservative fallback per model-class for account-dependent providers.
        # Users should override these with their actual console quotas.
        "vertex_gemini": {
            "pro":   {"rpm": 60,  "tpm": None, "rpd": None},
            "mid":   {"rpm": 120, "tpm": None, "rpd": None},
            "lite":  {"rpm": 240, "tpm": None, "rpd": None},
            "other": {"rpm": 60,  "tpm": None, "rpd": None},
        },
        "anthropic": {
            "pro":   {"rpm": 20, "itpm": None, "otpm": None, "rpd": None},
            "mid":   {"rpm": 40, "itpm": None, "otpm": None, "rpd": None},
            "lite":  {"rpm": 60, "itpm": None, "otpm": None, "rpd": None},
            "other": {"rpm": 30, "itpm": None, "otpm": None, "rpd": None},
        },
        "openai": {
            "pro":   {"rpm": 60,  "tpm": None, "rpd": None},
            "mid":   {"rpm": 120, "tpm": None, "rpd": None},
            "lite":  {"rpm": 240, "tpm": None, "rpd": None},
            "other": {"rpm": 60,  "tpm": None, "rpd": None},
        },
        "openai_compat": {
            "pro":   {"rpm": 60,  "tpm": None, "rpd": None},
            "mid":   {"rpm": 120, "tpm": None, "rpd": None},
            "lite":  {"rpm": 240, "tpm": None, "rpd": None},
            "other": {"rpm": 60,  "tpm": None, "rpd": None},
        },
        "ollama": {
            "pro":   {"rpm": 30,  "tpm": None, "rpd": None},
            "mid":   {"rpm": 60,  "tpm": None, "rpd": None},
            "lite":  {"rpm": 120, "tpm": None, "rpd": None},
            "other": {"rpm": 60,  "tpm": None, "rpd": None},
        },
    },
    "account_model_limits": {
        # Per-model overrides for account-dependent providers.
        "vertex_gemini": {
            "gemini-3.1-pro-preview":   {"rpm": 30,  "tpm": None, "rpd": None},
            "gemini-3-pro-preview":     {"rpm": 30,  "tpm": None, "rpd": None},
            "gemini-2.5-pro":           {"rpm": 60,  "tpm": None, "rpd": None},
            "gemini-1.5-pro":           {"rpm": 60,  "tpm": None, "rpd": None},
            "gemini-pro-latest":        {"rpm": 60,  "tpm": None, "rpd": None},
            "gemini-3.5-flash":         {"rpm": 120, "tpm": None, "rpd": None},
            "gemini-3-flash-preview":   {"rpm": 120, "tpm": None, "rpd": None},
            "gemini-2.5-flash":         {"rpm": 120, "tpm": None, "rpd": None},
            "gemini-2.0-flash":         {"rpm": 120, "tpm": None, "rpd": None},
            "gemini-1.5-flash":         {"rpm": 120, "tpm": None, "rpd": None},
            "gemini-flash-latest":      {"rpm": 120, "tpm": None, "rpd": None},
            "gemini-3.1-flash-lite":    {"rpm": 240, "tpm": None, "rpd": None},
            "gemini-2.5-flash-lite":    {"rpm": 240, "tpm": None, "rpd": None},
            "gemini-2.0-flash-lite":    {"rpm": 240, "tpm": None, "rpd": None},
            "gemini-flash-lite-latest": {"rpm": 240, "tpm": None, "rpd": None},
        },
        "anthropic": {
            "claude-opus-4-5":   {"rpm": 20, "itpm": None, "otpm": None, "rpd": None},
            "claude-opus-4-0":   {"rpm": 20, "itpm": None, "otpm": None, "rpd": None},
            "claude-3-opus":     {"rpm": 20, "itpm": None, "otpm": None, "rpd": None},
            "claude-sonnet-4-5": {"rpm": 40, "itpm": None, "otpm": None, "rpd": None},
            "claude-sonnet-4-0": {"rpm": 40, "itpm": None, "otpm": None, "rpd": None},
            "claude-3-5-sonnet": {"rpm": 40, "itpm": None, "otpm": None, "rpd": None},
            "claude-haiku-4-5":  {"rpm": 60, "itpm": None, "otpm": None, "rpd": None},
            "claude-3-5-haiku":  {"rpm": 60, "itpm": None, "otpm": None, "rpd": None},
        },
        "openai": {
            "o1-mini":       {"rpm": 120, "tpm": None, "rpd": None},
            "o3-mini":       {"rpm": 120, "tpm": None, "rpd": None},
            "o1":            {"rpm": 30,  "tpm": None, "rpd": None},
            "o3":            {"rpm": 30,  "tpm": None, "rpd": None},
            "o4":            {"rpm": 30,  "tpm": None, "rpd": None},
            "gpt-4o-mini":   {"rpm": 240, "tpm": None, "rpd": None},
            "gpt-4o":        {"rpm": 60,  "tpm": None, "rpd": None},
            "gpt-4-turbo":   {"rpm": 60,  "tpm": None, "rpd": None},
            "gpt-4":         {"rpm": 60,  "tpm": None, "rpd": None},
            "gpt-3.5-turbo": {"rpm": 240, "tpm": None, "rpd": None},
        },
        "openai_compat": {},
        "ollama": {},
    },
}


# ---------------------------------------------------------------------------
# Config loading / saving
# ---------------------------------------------------------------------------

_cache_lock:   threading.Lock = threading.Lock()
_config_cache: dict | None    = None


def _write_defaults() -> dict:
    cfg = copy.deepcopy(_DEFAULT_CONFIG)
    RATE_LIMITS_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RATE_LIMITS_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return cfg


def load_config(force: bool = False) -> dict:
    """Load rate-limits config from JSON. Writes built-in defaults on first run."""
    global _config_cache
    with _cache_lock:
        if _config_cache is not None and not force:
            return _config_cache
        if not RATE_LIMITS_CONFIG_PATH.exists():
            _config_cache = _write_defaults()
        else:
            try:
                with open(RATE_LIMITS_CONFIG_PATH, encoding="utf-8") as f:
                    _config_cache = json.load(f)
            except Exception:
                _config_cache = _write_defaults()
        return _config_cache


def save_config(data: dict) -> None:
    """Validate and save rate-limits config, then invalidate in-memory cache."""
    global _config_cache
    for required in ("gemini", "openai", "account_defaults", "account_model_limits"):
        if required not in data:
            raise ValueError(f"Seccion requerida faltante: '{required}'")
    RATE_LIMITS_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _cache_lock:
        with open(RATE_LIMITS_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        _config_cache = data


def get_defaults() -> dict:
    """Return a fresh deep copy of the built-in defaults (does not touch disk)."""
    return copy.deepcopy(_DEFAULT_CONFIG)


# ---------------------------------------------------------------------------
# Model-tier classification
# ---------------------------------------------------------------------------

def model_tier(model_id: str) -> str:
    model_id = (model_id or "").lower()
    if any(k in model_id for k in ("lite", "haiku", "nano")) or "-mini" in model_id or model_id.endswith("mini"):
        return "lite"
    if (
        "opus" in model_id
        or ("pro" in model_id and "lite" not in model_id)
        or model_id.startswith(("gpt-4", "gpt-5", "o1", "o3"))
    ):
        return "pro"
    if any(k in model_id for k in ("flash", "sonnet", "gpt-")):
        return "mid"
    return "other"


# ---------------------------------------------------------------------------
# Limit matching (reads from JSON config via load_config())
# ---------------------------------------------------------------------------

def _match_gemini_limits(model_id: str, tier: str) -> tuple[dict, str]:
    model_id  = (model_id or "").lower()
    tier      = tier if tier in {"free", "paid"} else "free"
    gemini_cfg = load_config().get("gemini", {})
    for pattern in sorted(gemini_cfg.keys(), key=len, reverse=True):
        if pattern == "_default":
            continue
        if model_id.startswith(pattern) or pattern in model_id:
            entry = gemini_cfg[pattern]
            return dict(entry.get(tier, entry.get("free", {}))), pattern
    default = gemini_cfg.get("_default", _DEFAULT_CONFIG["gemini"]["_default"])
    return dict(default.get(tier, default.get("free", {}))), "_default"


def _match_openai_limits(model_id: str) -> tuple[dict, str]:
    model_id   = (model_id or "").lower()
    openai_cfg = load_config().get("openai", {})
    for pattern in sorted(openai_cfg.keys(), key=len, reverse=True):
        if pattern == "_default":
            continue
        if model_id.startswith(pattern) or pattern in model_id:
            return dict(openai_cfg[pattern]), pattern
    return dict(openai_cfg.get("_default", _DEFAULT_CONFIG["openai"]["_default"])), "_default"


def _match_account_limits(provider: str, model_id: str) -> tuple[dict, str]:
    provider  = provider or "openai_compat"
    model_id  = (model_id or "").lower()
    cfg       = load_config()

    model_limits = cfg.get("account_model_limits", {}).get(provider, {})
    for pattern in sorted(model_limits.keys(), key=len, reverse=True):
        if model_id.startswith(pattern) or pattern in model_id:
            return dict(model_limits[pattern]), pattern

    model_class = model_tier(model_id)
    acc_defaults = cfg.get("account_defaults", {})
    provider_defaults = acc_defaults.get(provider, acc_defaults.get("openai_compat", {}))
    fallback = provider_defaults.get(model_class) or provider_defaults.get("other") or {"rpm": 60}
    return dict(fallback), model_class


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_rate_limit_profile(provider: str, model_id: str, tier: str = "free") -> dict:
    provider = provider or "gemini"

    if provider == "llama_cpp":
        return {
            "provider": provider,
            "model": model_id,
            "tier": "local",
            "rpm": None,
            "rpd": None,
            "tpm": None,
            "limit_kind": "local",
            "source_label": "Modelo local GGUF",
            "source_url": "",
            "note": "Modelo local  -  sin limites de API.",
        }

    if provider == "gemini":
        limits, matched = _match_gemini_limits(model_id, tier)
        return {
            **limits,
            "provider": provider,
            "model": model_id,
            "tier": tier if tier in {"free", "paid"} else "free",
            "pattern_matched": matched,
            "limit_kind": "published",
            "source_label": "Gemini API / AI Studio",
            "source_url": "https://ai.google.dev/gemini-api/docs/rate-limits",
            "note": "Limites publicados por Google para Gemini API; pueden variar por proyecto/tier.",
        }

    if provider == "openai":
        limits, matched = _match_openai_limits(model_id)
        return {
            **limits,
            "provider": provider,
            "model": model_id,
            "tier": "tier1",
            "pattern_matched": matched,
            "limit_kind": "published",
            "source_label": "OpenAI Tier 1 (platform dashboard)",
            "source_url": "https://platform.openai.com/settings/organization/limits",
            "note": "Limites publicados de OpenAI Tier 1; pueden diferir en cuentas de otro tier.",
        }

    model_class = model_tier(model_id)
    limits, matched = _match_account_limits(provider, model_id)
    source_label = {
        "vertex_gemini": "Google Cloud Vertex AI quotas",
        "anthropic":     "Claude Console / Anthropic rate limits",
        "openai":        "OpenAI dashboard rate limits",
        "openai_compat": "Proveedor OpenAI-compatible",
        "saptiva":       "Saptiva InferNode (plan de la llave; consulta GET /v1/me)",
        "ollama":        "Servidor local",
    }.get(provider, "Proveedor")
    source_url = {
        "vertex_gemini": "https://cloud.google.com/vertex-ai/generative-ai/docs/quotas",
        "anthropic":     "https://docs.anthropic.com/en/api/rate-limits",
        "openai":        "https://platform.openai.com/docs/guides/rate-limits",
        "saptiva":       "https://docs.saptiva.com/usuario/modelos/api-desde-codigo",
    }.get(provider, "")
    return {
        **limits,
        "provider": provider,
        "model": model_id,
        "tier": "account",
        "model_class": model_class,
        "pattern_matched": matched,
        "limit_kind": "account_dependent",
        "source_label": source_label,
        "source_url": source_url,
        "note": "Limite real dependiente de cuenta/proyecto/region/tier; se usa fallback conservador para recomendar delays.",
    }


def compute_min_delay(provider: str, model_id: str, tier: str = "free", n_arbiters: int = 1) -> dict:
    profile    = get_rate_limit_profile(provider, model_id, tier)

    if profile.get("limit_kind") == "local":
        return {
            **profile,
            "n_arbiters":  n_arbiters,
            "min_delay_s": 0,
            "safe_delay_s": 0,
            "alert_level": "info",
            "alert_msg":   "Modelo local GGUF  -  sin limites de API, sin delay necesario.",
        }

    rpm        = profile.get("rpm") or 10
    n_arbiters = max(int(n_arbiters or 1), 1)
    delay_min  = (60.0 / rpm) * n_arbiters * 1.2

    if profile["limit_kind"] == "account_dependent":
        alert_level = "warning"
        if provider == "ollama":
            alert_msg = (
                f"{profile['source_label']}: throughput dependiente de hardware/modelo local. "
                f"Fallback conservador: {rpm} RPM; ajusta segun tu servidor."
            )
        else:
            alert_msg = (
                f"{profile['source_label']}: limites reales dependientes de cuenta/proyecto. "
                f"Fallback conservador: {rpm} RPM; ajusta si tu consola indica otra cuota."
            )
    elif rpm <= 5:
        alert_level = "danger"
        alert_msg = (
            f"Modelo restrictivo ({rpm} RPM). Con {n_arbiters} arbitro(s), "
            f"usa al menos {delay_min:.1f}s entre entradas."
        )
    elif rpm <= 15:
        alert_level = "warning"
        alert_msg = f"Limite moderado ({rpm} RPM). Espera minima recomendada: {delay_min:.1f}s."
    else:
        alert_level = "info"
        alert_msg = f"Limite adecuado ({rpm} RPM). Espera minima recomendada: {delay_min:.1f}s."

    return {
        **profile,
        "n_arbiters":   n_arbiters,
        "min_delay_s":  round(delay_min, 1),
        "safe_delay_s": round(delay_min * 1.5, 1),
        "alert_level":  alert_level,
        "alert_msg":    alert_msg,
    }

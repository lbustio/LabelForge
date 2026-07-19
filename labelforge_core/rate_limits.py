"""Cuotas por proveedor/modelo cargadas exclusivamente desde JSON."""

from __future__ import annotations

import json
import threading

from labelforge_core.paths import RATE_LIMITS_CONFIG_PATH


_cache_lock = threading.Lock()
_config_cache: dict | None = None
_REQUIRED_SECTIONS = ("gemini", "openai", "account_defaults", "account_model_limits")


def validate_config(data: dict) -> None:
    if not isinstance(data, dict):
        raise ValueError("La configuracion de cuotas debe ser un objeto JSON.")
    for section in _REQUIRED_SECTIONS:
        if not isinstance(data.get(section), dict):
            raise ValueError(f"Seccion requerida faltante o invalida: '{section}'")
    for provider in ("gemini", "openai"):
        if "_default" not in data[provider]:
            raise ValueError(f"config/rate_limits.json requiere '{provider}._default'")


def load_config(force: bool = False) -> dict:
    """Carga la unica fuente de verdad; nunca genera ni sustituye cuotas."""
    global _config_cache
    with _cache_lock:
        if _config_cache is not None and not force:
            return _config_cache
        if not RATE_LIMITS_CONFIG_PATH.is_file():
            raise FileNotFoundError(
                f"No existe la configuracion de cuotas: {RATE_LIMITS_CONFIG_PATH}"
            )
        try:
            with open(RATE_LIMITS_CONFIG_PATH, encoding="utf-8") as config_file:
                loaded = json.load(config_file)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"JSON invalido en {RATE_LIMITS_CONFIG_PATH}: linea {exc.lineno}, columna {exc.colno}"
            ) from exc
        validate_config(loaded)
        _config_cache = loaded
        return _config_cache


def save_config(data: dict) -> None:
    """Valida y persiste config/rate_limits.json."""
    global _config_cache
    validate_config(data)
    RATE_LIMITS_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _cache_lock:
        with open(RATE_LIMITS_CONFIG_PATH, "w", encoding="utf-8") as config_file:
            json.dump(data, config_file, ensure_ascii=False, indent=2)
            config_file.write("\n")
        _config_cache = data


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


def _match_gemini_limits(model_id: str, tier: str) -> tuple[dict, str]:
    model_id = (model_id or "").lower()
    tier = tier if tier in {"free", "paid"} else "free"
    provider_config = load_config()["gemini"]
    for pattern in sorted(provider_config, key=len, reverse=True):
        if pattern != "_default" and (model_id.startswith(pattern) or pattern in model_id):
            entry = provider_config[pattern]
            return dict(entry.get(tier, entry.get("free", {}))), pattern
    default = provider_config["_default"]
    return dict(default.get(tier, default.get("free", {}))), "_default"


def _match_openai_limits(model_id: str) -> tuple[dict, str]:
    model_id = (model_id or "").lower()
    provider_config = load_config()["openai"]
    for pattern in sorted(provider_config, key=len, reverse=True):
        if pattern != "_default" and (model_id.startswith(pattern) or pattern in model_id):
            return dict(provider_config[pattern]), pattern
    return dict(provider_config["_default"]), "_default"


def _match_account_limits(provider: str, model_id: str) -> tuple[dict, str]:
    provider = provider or "openai_compat"
    model_id = (model_id or "").lower()
    config = load_config()
    model_limits = config["account_model_limits"].get(provider, {})
    for pattern in sorted(model_limits, key=len, reverse=True):
        if model_id.startswith(pattern) or pattern in model_id:
            return dict(model_limits[pattern]), pattern

    model_class = model_tier(model_id)
    account_defaults = config["account_defaults"]
    provider_defaults = account_defaults.get(provider, account_defaults.get("openai_compat", {}))
    fallback = provider_defaults.get(model_class) or provider_defaults.get("other")
    return (dict(fallback), model_class) if fallback else ({}, "unconfigured")


def get_rate_limit_profile(provider: str, model_id: str, tier: str = "free") -> dict:
    provider = provider or "gemini"
    if provider == "llama_cpp":
        return {
            "provider": provider, "model": model_id, "tier": "local",
            "rpm": None, "rpd": None, "tpm": None, "limit_kind": "local",
            "source_label": "Modelo local GGUF", "source_url": "",
            "note": "Modelo local: no usa una API remota.",
        }
    if provider == "gemini":
        limits, matched = _match_gemini_limits(model_id, tier)
        return {
            **limits, "provider": provider, "model": model_id,
            "tier": tier if tier in {"free", "paid"} else "free",
            "pattern_matched": matched, "limit_kind": "configured",
            "source_label": "config/rate_limits.json", "source_url": "",
            "note": "Valor configurable; la cuota efectiva pertenece al proyecto activo.",
        }
    if provider == "openai":
        limits, matched = _match_openai_limits(model_id)
        return {
            **limits, "provider": provider, "model": model_id, "tier": "account",
            "pattern_matched": matched, "limit_kind": "configured",
            "source_label": "config/rate_limits.json", "source_url": "",
            "note": "Valor configurable; la cuota efectiva pertenece a la cuenta activa.",
        }

    limits, matched = _match_account_limits(provider, model_id)
    return {
        **limits, "provider": provider, "model": model_id, "tier": "account",
        "model_class": model_tier(model_id), "pattern_matched": matched,
        "limit_kind": "configured" if limits else "unconfigured",
        "source_label": "config/rate_limits.json", "source_url": "",
        "note": "Valor configurable; la cuota efectiva depende de cuenta, proyecto, region y proveedor.",
    }


def compute_min_delay(provider: str, model_id: str, tier: str = "free", n_arbiters: int = 1) -> dict:
    profile = get_rate_limit_profile(provider, model_id, tier)
    if profile["limit_kind"] == "local":
        return {
            **profile, "n_arbiters": n_arbiters, "min_delay_s": 0,
            "safe_delay_s": 0, "alert_level": "info",
            "alert_msg": "Modelo local GGUF: sin cuota de API remota.",
        }

    rpm = profile.get("rpm")
    if not rpm:
        return {
            **profile, "n_arbiters": n_arbiters, "min_delay_s": None,
            "safe_delay_s": None, "alert_level": "info",
            "alert_msg": "Sin RPM configurado; se aplicara ajuste reactivo ante respuestas reales.",
        }
    n_arbiters = max(int(n_arbiters or 1), 1)
    delay_min = (60.0 / float(rpm)) * n_arbiters * 1.2
    return {
        **profile, "n_arbiters": n_arbiters,
        "min_delay_s": round(delay_min, 1), "safe_delay_s": round(delay_min * 1.5, 1),
        "alert_level": "warning" if rpm <= 15 else "info",
        "alert_msg": f"Referencia configurada: {rpm} RPM; espera orientativa {delay_min:.1f}s.",
    }

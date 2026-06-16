"""
Provider runtime preflight checks.

These checks are intentionally local/lightweight: they catch missing credentials
or incomplete provider configuration before a run starts, without sending model
requests or consuming tokens.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def _provider_key(provider: dict) -> str:
    override = (provider.get("api_key_override") or "").strip()
    if override:
        return override
    env_var = (provider.get("api_key_env") or "").strip()
    return os.environ.get(env_var, "").strip() if env_var else ""


def _check_vertex_adc() -> str:
    cred_file = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if cred_file and not Path(cred_file).exists():
        return (
            "GOOGLE_APPLICATION_CREDENTIALS apunta a un archivo que no existe: "
            f"{cred_file}. Corrige esa ruta en secrets/secrets.env o en el entorno."
        )

    gcloud = shutil.which("gcloud")
    try:
        import google.auth
        from google.auth.exceptions import DefaultCredentialsError

        google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    except DefaultCredentialsError as exc:
        gcloud_hint = (
            "Opcion con Google Cloud CLI ya instalada: ejecuta "
            "`gcloud auth application-default login` y luego "
            "`gcloud auth application-default set-quota-project TU_PROJECT_ID`."
            if gcloud
            else
            "No se detecto `gcloud` en PATH. Opcion sin instalar CLI en esta app: "
            "usa una service account JSON creada en Google Cloud Console y define "
            "`GOOGLE_APPLICATION_CREDENTIALS=secrets\\service-account.json` en secrets/secrets.env. "
            "Opcion minima si prefieres login interactivo: instala Google Cloud CLI y "
            "ejecuta `gcloud auth application-default login`."
        )
        return (
            "No se encontraron credenciales ADC de Google Cloud para Vertex AI. "
            "Vertex no usa API key directa. "
            f"{gcloud_hint} "
            f"Detalle: {exc}"
        )
    except Exception as exc:
        return f"No se pudieron validar credenciales ADC de Vertex AI: {exc}"

    return ""


def _missing_api_key_message(arb_id: str, ptype: str, env_var: str) -> str:
    label = {
        "gemini": "Gemini AI Studio",
        "anthropic": "Anthropic Claude",
        "openai": "OpenAI",
    }.get(ptype, ptype)
    cli = {
        "gemini": "python main_label.py --set-provider-key gemini",
        "anthropic": "python main_label.py --set-provider-key anthropic",
        "openai": "python main_label.py --set-provider-key openai",
    }.get(ptype, "python main_label.py --set-provider-key PROVEEDOR")
    return (
        f"{arb_id}: falta API key para {label} ({env_var}). "
        "Soluciones: en WebApp ve a Arbitros, pega la clave en Clave de API y pulsa verificar; "
        f"o usa CLI `{cli}`; o define `{env_var}=...` en secrets/secrets.env."
    )


def validate_provider_preflight(cfg: dict) -> list[str]:
    """Return human-readable issues that should block a labeling run."""
    issues: list[str] = []
    vertex_arb_ids: list[str] = []

    for arb in cfg.get("arbiters", []):
        arb_id = arb.get("id", "?")
        provider = arb.get("provider", {})
        ptype = provider.get("type", "")
        model = (provider.get("model") or "").strip()

        if not ptype:
            issues.append(f"{arb_id}: proveedor no configurado.")
            continue
        if not model:
            issues.append(f"{arb_id}: modelo no configurado para proveedor '{ptype}'.")

        if ptype == "vertex_gemini":
            vertex_arb_ids.append(arb_id)
            project = (
                provider.get("vertex_project")
                or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
            ).strip()
            if not project:
                issues.append(
                    f"{arb_id}: Vertex AI necesita provider.vertex_project o GOOGLE_CLOUD_PROJECT."
                )

        elif ptype in {"gemini", "anthropic", "openai"}:
            if not _provider_key(provider):
                env_var = provider.get("api_key_env") or "variable de entorno"
                issues.append(_missing_api_key_message(arb_id, ptype, env_var))

        elif ptype == "openai_compat":
            base_url = (provider.get("base_url") or "").strip()
            if not base_url:
                issues.append(
                    f"{arb_id}: openai_compat necesita provider.base_url. "
                    "Ejemplos: Ollama `http://localhost:11434/v1`, LM Studio/vLLM segun tu servidor. "
                    "Si el servidor requiere key, guardala como OPENAI_API_KEY desde Arbitros o secrets/secrets.env."
                )

        elif ptype == "ollama":
            base_url = (provider.get("base_url") or "").strip()
            if not base_url:
                issues.append(
                    f"{arb_id}: Ollama no tiene base_url. Usa `http://localhost:11434/v1` "
                    "si tienes Ollama local con API compatible OpenAI, o configura la direccion "
                    "del servidor en el arbitro."
                )

    if vertex_arb_ids:
        vertex_adc_issue = _check_vertex_adc()
        if vertex_adc_issue:
            joined = ", ".join(vertex_arb_ids)
            issues.append(f"Vertex AI ({joined}): {vertex_adc_issue}")

    return issues


def require_provider_preflight(cfg: dict):
    issues = validate_provider_preflight(cfg)
    if issues:
        raise RuntimeError("Preflight de proveedores fallo: " + "; ".join(issues))

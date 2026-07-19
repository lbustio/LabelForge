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

from labelforge_core.paths import BASE_DIR, HW_PROFILE_PATH


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
        "gemini": "python main_cli.py --set-provider-key gemini",
        "anthropic": "python main_cli.py --set-provider-key anthropic",
        "openai": "python main_cli.py --set-provider-key openai",
    }.get(ptype, "python main_cli.py --set-provider-key PROVEEDOR")
    return (
        f"{arb_id}: falta API key para {label} ({env_var}). "
        "Soluciones: en WebApp ve a Arbitros, pega la clave en Clave de API y pulsa verificar; "
        f"o usa CLI `{cli}`; o define `{env_var}=...` en secrets/secrets.env."
    )


def _check_llama_cpp(arb_id: str, provider: dict) -> list[str]:
    issues: list[str] = []

    # 1. hw_profile.json existe y setup_ok
    if not HW_PROFILE_PATH.exists():
        issues.append(
            f"{arb_id}: falta models/hw_profile.json. "
            "Ejecuta 'python setup/setup_env.py' antes de usar modelos locales."
        )
    else:
        try:
            import json
            profile = json.loads(HW_PROFILE_PATH.read_text(encoding="utf-8"))
            if not profile.get("setup_ok"):
                issues.append(
                    f"{arb_id}: setup_ok=false en hw_profile.json. "
                    "Revisa logs/setup_*.log y vuelve a ejecutar 'python setup_env.py'."
                )
        except Exception as exc:
            issues.append(f"{arb_id}: hw_profile.json no se puede leer: {exc}")

    # 2. llama-cpp-python importable
    try:
        import llama_cpp  # noqa: F401
    except ImportError:
        issues.append(
            f"{arb_id}: llama-cpp-python no esta instalado. "
            "Ejecuta 'python setup/setup_env.py' para instalarlo con soporte GPU correcto."
        )

    # 3. model_path existe y es .gguf
    model_path = (provider.get("model_path") or "").strip()
    if not model_path:
        issues.append(
            f"{arb_id}: llama_cpp requiere provider.model_path con la ruta al archivo .gguf. "
            "Descarga un modelo desde la webapp (Arbitros  ->  Descargar modelos)."
        )
    else:
        full_path = Path(model_path) if Path(model_path).is_absolute() else BASE_DIR / model_path
        if not full_path.exists():
            issues.append(
                f"{arb_id}: modelo GGUF no encontrado: {model_path}. "
                "Descargalo desde la webapp (Arbitros  ->  Descargar modelos)."
            )
        elif full_path.suffix.lower() != ".gguf":
            issues.append(
                f"{arb_id}: model_path no apunta a un archivo .gguf: {model_path}"
            )

    return issues


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

        elif ptype == "llama_cpp":
            issues += _check_llama_cpp(arb_id, provider)

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

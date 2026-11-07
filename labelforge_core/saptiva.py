"""
saptiva.py
----------
Proveedor Saptiva InferNode (API de modelos de Saptiva Studio).

InferNode es compatible con el SDK de OpenAI: basta con cambiar la URL base y
usar la llave de InferNode (``sk-saptiva-...``). Por defecto apunta a la
instalacion del Laboratorio de IA IBERO:

    https://api.laboratorioia.ibero.mx/infernode/v1

Documentacion:
  - https://docs.saptiva.com/usuario/modelos/api-desde-codigo
  - https://docs.saptiva.com/usuario/modelos/lotes

Decisiones relevantes para el etiquetador:

* Los modelos se nombran por alias (``Saptiva Turbo``, ``Saptiva Omni``,
  ``Saptiva Cortex``). El nombre exacto del catalogo incluye la version y deja
  de resolver cuando el modelo se actualiza.
* Los modelos del lab razonan por defecto. El razonamiento consume
  ``max_tokens`` y, con topes bajos, ``content`` llega vacio. Para etiquetar
  (salida JSON corta) se apaga con ``chat_template_kwargs.enable_thinking=false``.
  Verificado el 2026-11-07 contra el lab: ``reasoning.effort=none`` NO lo apaga.
  Se puede reactivar por arbitro con ``generation_config.reasoning=true`` y
  acotar con ``generation_config.reasoning_budget`` (tokens de razonamiento,
  enviado como ``thinking_token_budget``).
* Este modulo construye el mismo ``body`` para la llamada en linea y para las
  lineas del archivo de lotes, asi ambos modos etiquetan igual.
"""

from __future__ import annotations

import os

PROVIDER_TYPE = "saptiva"
API_KEY_ENV = "SAPTIVA_API_KEY"
BASE_URL_ENV = "SAPTIVA_BASE_URL"
DEFAULT_BASE_URL = "https://api.laboratorioia.ibero.mx/infernode/v1"
CHAT_ENDPOINT = "/v1/chat/completions"
DEFAULT_ALIASES = ("Saptiva Turbo", "Saptiva Omni", "Saptiva Cortex")


class EmptyCompletionError(ValueError):
    """El modelo no devolvio ``content`` (tipicamente: razonamiento agoto max_tokens)."""


def resolve_base_url(provider: dict | None = None) -> str:
    """Prioridad: provider.base_url > SAPTIVA_BASE_URL > instalacion del lab IBERO."""
    provider = provider or {}
    url = (provider.get("base_url") or "").strip() or os.environ.get(BASE_URL_ENV, "").strip()
    return (url or DEFAULT_BASE_URL).rstrip("/")


def make_client(api_key: str, base_url: str = "", max_retries: int = 2, timeout: float = 300.0):
    """Cliente OpenAI apuntando a InferNode. Una peticion no debe pasar de ~300 s."""
    from openai import OpenAI

    return OpenAI(
        api_key=api_key,
        base_url=(base_url or DEFAULT_BASE_URL).rstrip("/"),
        max_retries=max_retries,
        timeout=timeout,
    )


def build_chat_body(arbiter: dict, prompt_user: str, json_mode: bool = True) -> dict:
    """Cuerpo de /v1/chat/completions para un arbitro (en linea o en lote)."""
    provider = arbiter["provider"]
    gen_cfg = arbiter.get("generation_config", {}) or {}
    body = {
        "model": provider["model"],
        "messages": [
            {"role": "system", "content": arbiter["system_prompt"]},
            {"role": "user", "content": prompt_user},
        ],
        "temperature": float(gen_cfg.get("temperature", 0.1)),
        "max_tokens": int(gen_cfg.get("max_output_tokens", 2048)),
        # Etiquetas de consumo: no se envian al modelo, aparecen en /v1/billing/usage.
        "metadata": {"app": "labelforge", "arbiter": str(arbiter.get("id", ""))},
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if gen_cfg.get("seed") is not None:
        body["seed"] = int(gen_cfg["seed"])
    if not bool(gen_cfg.get("reasoning", False)):
        body["chat_template_kwargs"] = {"enable_thinking": False}
    else:
        budget = int(gen_cfg.get("reasoning_budget", 0) or 0)
        if budget > 0:
            # Verificado en el lab (2026-11-07): thinking_token_budget si acota el
            # razonamiento; chat_template_kwargs.reasoning_budget se ignora.
            body["thinking_token_budget"] = budget
    return body


def extract_content(message: dict | None, finish_reason: str | None = None) -> str:
    """Devuelve ``content`` o lanza un error explicito si vino vacio."""
    content = (message or {}).get("content")
    if content:
        return content
    has_reasoning = bool((message or {}).get("reasoning"))
    if finish_reason == "length" or has_reasoning:
        raise EmptyCompletionError(
            "Saptiva devolvio content vacio (finish_reason="
            f"{finish_reason or '?'}): el razonamiento consumio max_tokens. "
            "Deja generation_config.reasoning=false o sube max_output_tokens."
        )
    raise EmptyCompletionError(f"Saptiva devolvio content vacio (finish_reason={finish_reason or '?'}).")


def usage_dict(usage) -> dict:
    if usage is None:
        return {"prompt_tokens": 0, "completion_tokens": 0}
    if isinstance(usage, dict):
        return {
            "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
        }
    return {
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
    }


def call_chat(arbiter: dict, prompt_user: str, api_key: str) -> tuple[str, dict]:
    """Llamada en linea. Devuelve (texto, usage) como el resto de backends."""
    provider = arbiter["provider"]
    client = make_client(api_key, resolve_base_url(provider))
    body = build_chat_body(arbiter, prompt_user)
    standard = {k: body[k] for k in ("model", "messages", "temperature", "max_tokens", "response_format") if k in body}
    extra = {k: v for k, v in body.items() if k not in standard}
    response = client.chat.completions.create(**standard, extra_body=extra or None)
    choice = response.choices[0]
    message = choice.message.model_dump() if hasattr(choice.message, "model_dump") else dict(choice.message)
    return extract_content(message, choice.finish_reason), usage_dict(getattr(response, "usage", None))


def list_models(api_key: str, base_url: str = "") -> list[dict]:
    """Alias primero (recomendados) y despues nombres exactos del catalogo."""
    client = make_client(api_key, base_url, max_retries=1, timeout=30.0)
    aliases: list[str] = []
    try:
        raw = client.get("/models/aliases", cast_to=object)
        data = raw.get("data", raw) if isinstance(raw, dict) else raw
        for item in data or []:
            name = item.get("id") or item.get("alias") or item.get("name") if isinstance(item, dict) else item
            if name:
                aliases.append(str(name))
    except Exception:
        aliases = []
    result = [
        {"id": a, "display_name": a, "task_description": "Alias recomendado de Saptiva (sigue al modelo vigente)."}
        for a in sorted(set(aliases))
    ]
    for m in client.models.list().data:
        if m.id not in aliases:
            result.append({"id": m.id, "display_name": m.id, "task_description": "Nombre exacto del catalogo (fijo a una version)."})
    return result

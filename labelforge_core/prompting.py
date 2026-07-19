"""
prompting.py
------------
Construccion pura de prompts para el pipeline de etiquetado.
"""

from __future__ import annotations


DEFAULT_CONTEXT_WINDOW = 32768
DEFAULT_CONTEXT_SAFETY_MARGIN = 512
DEFAULT_CHUNK_OVERLAP_CHARS = 400


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
    # Un limite de 0 significa que la entrada debe enviarse completa. Evita que
    # el valor falsy termine convertido accidentalmente en [:0].
    item = claim if max_chars <= 0 else claim[:max_chars]
    prompt = template.format(
        item=item,
        principles_catalog=principles_catalog,
        output_instruction=output_instruction,
        output_schema=output_instruction,
    )
    if "{principles_catalog}" not in template:
        prompt = f"{principles_catalog}\n\n{prompt}"
    if "{output_instruction}" not in template and "{output_schema}" not in template:
        prompt = f"{prompt}\n\n{output_instruction}"
    return prompt


def arbiter_context_window(arbiter: dict, proc_cfg: dict) -> int:
    """Ventana efectiva declarada por cualquier proveedor/modelo.

    ``context_window`` es el contrato comun. ``n_ctx`` se conserva como el
    limite de ejecucion especifico de llama-cpp-python. Si un proveedor no publica su
    capacidad se usa un fallback conservador configurable, nunca un recorte.
    """
    provider = arbiter.get("provider", {})
    explicit = provider.get("context_window")
    if explicit:
        return max(512, int(explicit))
    if provider.get("type") == "llama_cpp" and provider.get("n_ctx"):
        return max(512, int(provider["n_ctx"]))
    return max(512, int(proc_cfg.get("context_window_fallback", DEFAULT_CONTEXT_WINDOW)))


def split_claim_for_arbiter(arbiter: dict, task_cfg: dict, proc_cfg: dict, claim: str) -> list[str]:
    """Divide sin perder contenido cuando la entrada no cabe en el arbitro.

    La estimacion de 3 caracteres/token es deliberadamente conservadora para
    texto multilingue. Se reserva espacio para instrucciones, plantilla,
    respuesta y un margen operativo. Los fragmentos se solapan para no perder
    una senal de persuasion situada justo en un limite.
    """
    if not claim:
        return [claim]
    arbiter_window = arbiter_context_window(arbiter, proc_cfg)
    output_tokens = int(arbiter.get("generation_config", {}).get("max_output_tokens", 600) or 600)
    safety = int(proc_cfg.get("context_safety_margin_tokens", DEFAULT_CONTEXT_SAFETY_MARGIN) or 0)
    fixed_prompt = build_user_prompt(task_cfg, "", 0)
    fixed_chars = len(fixed_prompt) + len(arbiter.get("system_prompt", ""))
    available_tokens = arbiter_window - output_tokens - safety - max(1, (fixed_chars + 2) // 3)
    if available_tokens <= 0:
        raise ValueError(
            f"El contexto de {arbiter_window} tokens no deja espacio para la entrada "
            f"despues de reservar instrucciones y {output_tokens} tokens de respuesta."
        )
    max_chunk_chars = available_tokens * 3
    if len(claim) <= max_chunk_chars:
        return [claim]

    overlap = max(0, int(proc_cfg.get("chunk_overlap_chars", DEFAULT_CHUNK_OVERLAP_CHARS) or 0))
    overlap = min(overlap, max_chunk_chars // 4)
    chunks = []
    start = 0
    while start < len(claim):
        hard_end = min(len(claim), start + max_chunk_chars)
        end = hard_end
        if hard_end < len(claim):
            candidates = [claim.rfind(sep, start + max_chunk_chars // 2, hard_end) for sep in ("\n\n", "\n", ". ", " ")]
            boundary = max(candidates)
            if boundary > start:
                end = boundary + 1
        chunks.append(claim[start:end])
        if end >= len(claim):
            break
        start = max(start + 1, end - overlap)
    return chunks

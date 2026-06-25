"""
prompting.py
------------
Construccion pura de prompts para el pipeline de etiquetado.
"""

from __future__ import annotations


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
        item=claim[:max_chars],
        principles_catalog=principles_catalog,
        output_instruction=output_instruction,
        output_schema=output_instruction,
    )
    if "{principles_catalog}" not in template:
        prompt = f"{principles_catalog}\n\n{prompt}"
    if "{output_instruction}" not in template and "{output_schema}" not in template:
        prompt = f"{prompt}\n\n{output_instruction}"
    return prompt

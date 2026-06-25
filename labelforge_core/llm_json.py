"""
Helpers for parsing JSON-like responses returned by LLM arbiters.
"""

import ast
import json
import re
from typing import Optional


def strip_markdown_json(raw: str) -> str:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.strip().lower().startswith("json"):
            raw = raw.strip()[4:]
    return raw.strip()


def extract_first_json_object(raw: str) -> str:
    start = raw.find("{")
    if start < 0:
        return raw
    depth = 0
    in_string = False
    escape = False
    for pos in range(start, len(raw)):
        ch = raw[pos]
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start:pos + 1]
    return raw[start:]


def _coerce_label_value(value) -> Optional[int]:
    if value in (0, 1):
        return int(value)
    text = str(value).strip().lower()
    if text in {"0", "false", "no", "absent", "none"}:
        return 0
    if text in {"1", "true", "yes", "present"}:
        return 1
    try:
        return 1 if int(float(text)) >= 1 else 0
    except (TypeError, ValueError):
        return None


def _salvage_principle_json(raw: str, principles: list[str]) -> Optional[dict]:
    """
    Last-resort parser for malformed LLM JSON. It only accepts a response if every
    required principle label can be recovered; justifications are best-effort.
    """
    if not principles:
        return None

    text = extract_first_json_object(strip_markdown_json(raw))
    result = {}

    for p in principles:
        label_re = re.compile(
            rf'["\']?{re.escape(p)}["\']?\s*:\s*["\']?(0|1|true|false|yes|no)["\']?',
            re.IGNORECASE,
        )
        match = label_re.search(text)
        if not match:
            return None
        value = _coerce_label_value(match.group(1))
        if value is None:
            return None
        result[p] = value

        why_key = f"{p}_why"
        why_re = re.compile(
            rf'["\']?{re.escape(why_key)}["\']?\s*:\s*["\']?'
            rf'(.*?)'
            rf'(?=(?:["\']?\s*,\s*["\']?[A-Za-z_][A-Za-z0-9_]*["\']?\s*:)|(?:\s*[}}]\s*$))',
            re.IGNORECASE | re.DOTALL,
        )
        why_match = why_re.search(text)
        why = why_match.group(1).strip() if why_match else "no cue"
        why = why.strip().strip('",\'').replace("\r", " ").replace("\n", " ")
        result[why_key] = why[:240] if why else "no cue"

    return result


def parse_llm_json(raw: str, principles: Optional[list[str]] = None) -> tuple[dict, str]:
    """Parse LLM JSON with conservative repairs for common response drift."""
    cleaned = extract_first_json_object(strip_markdown_json(raw))
    no_trailing = re.sub(r",\s*([}\]])", r"\1", cleaned)
    quoted_keys = re.sub(r'([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)', r'\1"\2"\3', cleaned)
    quoted_no_trailing = re.sub(r",\s*([}\]])", r"\1", quoted_keys)
    attempts = [
        ("direct", cleaned),
        ("trim_trailing_commas", no_trailing),
        ("quote_unquoted_keys", quoted_keys),
        ("quote_keys_trim_commas", quoted_no_trailing),
    ]

    last_error = None
    for mode, candidate in attempts:
        try:
            return json.loads(candidate), mode
        except json.JSONDecodeError as exc:
            last_error = exc

    try:
        value = ast.literal_eval(cleaned)
        if isinstance(value, dict):
            return value, "python_literal"
    except (ValueError, SyntaxError) as exc:
        last_error = exc

    salvaged = _salvage_principle_json(raw, principles or [])
    if salvaged is not None:
        return salvaged, "salvage_fields"

    raise last_error or ValueError("No se pudo parsear JSON del LLM")

"""
utils.py
--------
Utilidades comunes para el proyecto LabelForge.
Evita la duplicación de helpers de conversión numérica y formateo.
"""

from pathlib import Path
from typing import Optional, Any


def ensure_project_dirs(root: Path) -> list[Path]:
    """
    Verifica y crea todas las carpetas necesarias para el funcionamiento del proyecto.
    Retorna la lista de directorios creados (los que no existían).
    """
    required = [
        root / "logs",
        root / "output",
        root / "checkpoints",
        root / "models",
        root / "secrets",
        root / "config",
        root / "scripts",
        root / "data",
        root / "archive",
    ]
    created = []
    for d in required:
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            created.append(d)
    return created


def safe_int(value: Any, default: Any = None) -> Any:
    """Intenta convertir un valor a entero de forma segura."""
    if value in (None, ""):
        return default
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return default


def safe_float(value: Any, default: Any = None) -> Any:
    """Intenta convertir un valor a flotante de forma segura."""
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def safe_binary_int(value: Any) -> Optional[int]:
    """
    Intenta convertir un valor a 0 o 1 de forma segura.
    Devuelve None si no es un valor binario convertible.
    """
    if value in (0, 1):
        return value
    if value in ("0", "1"):
        return int(value)
    try:
        val = int(float(value))
        if val in (0, 1):
            return val
    except (TypeError, ValueError):
        pass
    return None


def number(value: Any, default: Any = 0) -> Any:
    """
    Convierte un valor al tipo de dato del default.
    Si falla, retorna el default.
    """
    if value in (None, ""):
        return default
    try:
        if default is None:
            return int(float(value))
        return type(default)(value)
    except (TypeError, ValueError):
        return default

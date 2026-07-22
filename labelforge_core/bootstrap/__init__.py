"""Composicion e inicializacion explicita de LabelForge.

Las exportaciones son perezosas para que importar un submodulo de paths no
construya ni importe adaptadores de infraestructura.
"""

from __future__ import annotations

from importlib import import_module


__all__ = [
    "AppContainer",
    "AppSettings",
    "SettingsError",
    "WorkspacePaths",
    "build_container",
    "default_workspace_root",
]

_EXPORT_MODULES = {
    "AppContainer": "container",
    "build_container": "container",
    "AppSettings": "settings",
    "SettingsError": "settings",
    "WorkspacePaths": "paths",
    "default_workspace_root": "paths",
}


def __getattr__(name: str):
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(name)
    module = import_module(f"{__name__}.{module_name}")
    return getattr(module, name)

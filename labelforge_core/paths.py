"""
Rutas canonicas del proyecto LabelForge.

Politica:
- `labelforge_core/` contiene codigo Python importable.
- `config/` contiene configuracion editable y versionable.
- `secrets/` contiene credenciales locales privadas.
- `models/`, `logs/`, `output/`, `checkpoints/` y `archive/` contienen estado,
  cache o artefactos generados localmente.
"""

from __future__ import annotations

from pathlib import Path

from labelforge_core.bootstrap.paths import WorkspacePaths, default_workspace_root

_DEFAULT_PATHS = WorkspacePaths.from_root(default_workspace_root())

# Alias de compatibilidad para modulos aun no migrados a inyeccion de paths.
# La autoridad de calculo es WorkspacePaths; estos nombres se retiraran cuando
# sus consumidores reciban el workspace desde el composition root.
BASE_DIR = _DEFAULT_PATHS.root
CORE_DIR = _DEFAULT_PATHS.core
SCRIPTS_DIR = CORE_DIR
CONFIG_DIR = _DEFAULT_PATHS.config
MODELS_DIR = _DEFAULT_PATHS.models
HW_PROFILE_PATH = _DEFAULT_PATHS.hardware_profile
SECRETS_DIR = _DEFAULT_PATHS.secrets
LOGS_DIR = _DEFAULT_PATHS.logs
OUTPUT_DIR = _DEFAULT_PATHS.output
RUN_RECORDS_DIR = _DEFAULT_PATHS.run_records
CHECKPOINTS_DIR = _DEFAULT_PATHS.checkpoints
ARCHIVE_DIR = _DEFAULT_PATHS.archive
DATA_DIR = _DEFAULT_PATHS.data

ARBITERS_CONFIG_PATH = _DEFAULT_PATHS.arbiters_config
LOCAL_MODELS_CONFIG_PATH = _DEFAULT_PATHS.local_models_config
RATE_LIMITS_CONFIG_PATH = _DEFAULT_PATHS.rate_limits_config

def resolve_config_file_path(path: str | Path | None) -> Path:
    """Resuelve la ruta de configuracion sin redirigir ubicaciones antiguas."""
    if path is None:
        return ARBITERS_CONFIG_PATH
    return Path(path)


def resolve_local_models_config_path() -> Path:
    """Ruta canonica del config del catalogo GGUF."""
    return LOCAL_MODELS_CONFIG_PATH

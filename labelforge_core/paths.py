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


BASE_DIR = Path(__file__).resolve().parent.parent
CORE_DIR = BASE_DIR / "labelforge_core"
SCRIPTS_DIR = CORE_DIR
CONFIG_DIR = BASE_DIR / "config"
MODELS_DIR = BASE_DIR / "models"
HW_PROFILE_PATH = MODELS_DIR / "hw_profile.json"
SECRETS_DIR = BASE_DIR / "secrets"
LOGS_DIR = BASE_DIR / "logs"
OUTPUT_DIR = BASE_DIR / "output"
CHECKPOINTS_DIR = BASE_DIR / "checkpoints"
ARCHIVE_DIR = BASE_DIR / "archive"
DATA_DIR = BASE_DIR / "data"

ARBITERS_CONFIG_PATH = CONFIG_DIR / "arbiters_config.json"
LOCAL_MODELS_CONFIG_PATH = CONFIG_DIR / "local_models_config.json"
RATE_LIMITS_CONFIG_PATH = CONFIG_DIR / "rate_limits.json"

def resolve_config_file_path(path: str | Path | None) -> Path:
    """Resuelve la ruta de configuracion sin redirigir ubicaciones antiguas."""
    if path is None:
        return ARBITERS_CONFIG_PATH
    return Path(path)


def resolve_local_models_config_path() -> Path:
    """Ruta canonica del config del catalogo GGUF."""
    return LOCAL_MODELS_CONFIG_PATH


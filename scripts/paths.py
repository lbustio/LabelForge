"""
Rutas canonicas del proyecto LabelForge.

Politica:
- `scripts/` contiene codigo Python importable.
- `config/` contiene configuracion editable y versionable.
- `secrets/` contiene credenciales locales privadas.
- `models/`, `logs/`, `output/`, `checkpoints/` y `archive/` contienen estado,
  cache o artefactos generados localmente.
"""

from __future__ import annotations

from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BASE_DIR / "scripts"
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

LEGACY_ARBITERS_CONFIG_PATH = SCRIPTS_DIR / "arbiters_config.json"
LEGACY_LOCAL_MODELS_CONFIG_PATH = SCRIPTS_DIR / "local_models_config.json"


def resolve_config_file_path(path: str | Path | None, *, legacy_path: Path | None = None) -> Path:
    """
    Resuelve una ruta de configuracion manteniendo compatibilidad con ubicaciones
    antiguas dentro de `scripts/`.

    Si la ruta pedida no existe y coincide con una ruta legacy ya movida, retorna
    la ruta canonica en `config/`.
    """
    if path is None:
        return ARBITERS_CONFIG_PATH if ARBITERS_CONFIG_PATH.exists() else LEGACY_ARBITERS_CONFIG_PATH

    requested = Path(path)
    if requested.exists():
        return requested

    legacy_path = legacy_path or LEGACY_ARBITERS_CONFIG_PATH
    try:
        same_legacy = requested.resolve(strict=False) == legacy_path.resolve(strict=False)
    except OSError:
        same_legacy = False
    if same_legacy and ARBITERS_CONFIG_PATH.exists():
        return ARBITERS_CONFIG_PATH

    return requested


def resolve_local_models_config_path() -> Path:
    """Ruta del config del catalogo GGUF, con fallback a la ubicacion legacy."""
    if LOCAL_MODELS_CONFIG_PATH.exists():
        return LOCAL_MODELS_CONFIG_PATH
    return LEGACY_LOCAL_MODELS_CONFIG_PATH

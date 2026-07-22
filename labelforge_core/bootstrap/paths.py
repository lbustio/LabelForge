"""Rutas inmutables de una instalacion local de LabelForge."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


def default_workspace_root() -> Path:
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class WorkspacePaths:
    root: Path

    @classmethod
    def from_root(cls, root: str | Path) -> "WorkspacePaths":
        resolved = Path(root).expanduser().resolve(strict=False)
        if resolved == Path(resolved.anchor):
            raise ValueError("La raiz del sistema no puede ser un workspace de LabelForge")
        return cls(root=resolved)

    @property
    def core(self) -> Path:
        return self.root / "labelforge_core"

    @property
    def config(self) -> Path:
        return self.root / "config"

    @property
    def secrets(self) -> Path:
        return self.root / "secrets"

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def models(self) -> Path:
        return self.root / "models"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def output(self) -> Path:
        return self.root / "output"

    @property
    def checkpoints(self) -> Path:
        return self.root / "checkpoints"

    @property
    def archive(self) -> Path:
        return self.root / "archive"

    @property
    def app_state(self) -> Path:
        return self.root / "app_state"

    @property
    def avatars(self) -> Path:
        return self.app_state / "avatars"

    @property
    def legacy_avatars(self) -> Path:
        return self.data / "avatars"

    @property
    def secrets_env(self) -> Path:
        return self.secrets / "secrets.env"

    @property
    def auth_db(self) -> Path:
        return self.secrets / "auth.db"

    @property
    def arbiters_config(self) -> Path:
        return self.config / "arbiters_config.json"

    @property
    def local_models_config(self) -> Path:
        return self.config / "local_models_config.json"

    @property
    def rate_limits_config(self) -> Path:
        return self.config / "rate_limits.json"

    @property
    def hardware_profile(self) -> Path:
        return self.models / "hw_profile.json"

    @property
    def run_records(self) -> Path:
        return self.output / ".manifests"

    def runtime_directories(self) -> tuple[Path, ...]:
        """Directorios requeridos; declararlos no los crea."""
        return (
            self.config,
            self.secrets,
            self.data,
            self.models,
            self.logs,
            self.output,
            self.checkpoints,
            self.archive,
            self.app_state,
        )

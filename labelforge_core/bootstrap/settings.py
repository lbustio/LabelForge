"""Configuracion tipada, pura y sin lectura implicita de archivos `.env`."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .paths import WorkspacePaths, default_workspace_root


class SettingsError(ValueError):
    pass


def _as_bool(value: object, *, name: str) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise SettingsError(f"{name} debe ser booleano")


def _as_int(value: object, *, name: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SettingsError(f"{name} debe ser entero") from exc
    if not minimum <= parsed <= maximum:
        raise SettingsError(f"{name} debe estar entre {minimum} y {maximum}")
    return parsed


@dataclass(frozen=True, slots=True)
class AppSettings:
    paths: WorkspacePaths
    host: str = "127.0.0.1"
    port: int = 7860
    debug: bool = False
    reload: bool = False
    secret_key: str | None = field(default=None, repr=False)
    max_upload_bytes: int = 300 * 1024 * 1024
    session_idle_seconds: int = 3600
    session_absolute_seconds: int = 28800
    cookie_secure: bool = True
    cookie_samesite: str = "Lax"
    trusted_proxy_hops: int = 0
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        if self.session_absolute_seconds < self.session_idle_seconds:
            raise SettingsError(
                "La sesion absoluta no puede vencer antes que la sesion inactiva"
            )
        if self.debug and self.host not in {"127.0.0.1", "localhost", "::1"}:
            raise SettingsError("El modo debug solo puede escuchar en localhost")

    @classmethod
    def from_sources(
        cls,
        *,
        overrides: Mapping[str, object] | None = None,
        environ: Mapping[str, str] | None = None,
        default_root: str | Path | None = None,
    ) -> "AppSettings":
        overrides = dict(overrides or {})
        environ = environ if environ is not None else os.environ

        def value(key: str, env_name: str, default: object) -> object:
            if key in overrides and overrides[key] is not None:
                return overrides[key]
            return environ.get(env_name, default)

        workspace_value = value(
            "workspace_root",
            "LABELFORGE_WORKSPACE",
            default_root or default_workspace_root(),
        )
        host = str(value("host", "LABELFORGE_HOST", "127.0.0.1")).strip()
        if not host:
            raise SettingsError("LABELFORGE_HOST no puede estar vacio")
        same_site = str(value("cookie_samesite", "LABELFORGE_COOKIE_SAMESITE", "Lax")).title()
        if same_site not in {"Lax", "Strict", "None"}:
            raise SettingsError("LABELFORGE_COOKIE_SAMESITE debe ser Lax, Strict o None")
        log_level = str(value("log_level", "LABELFORGE_LOG_LEVEL", "INFO")).upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise SettingsError("LABELFORGE_LOG_LEVEL no es valido")

        secret = str(value("secret_key", "LABELFORGE_SECRET_KEY", "")).strip() or None
        return cls(
            paths=WorkspacePaths.from_root(workspace_value),
            host=host,
            port=_as_int(value("port", "LABELFORGE_PORT", 7860), name="LABELFORGE_PORT", minimum=1, maximum=65535),
            debug=_as_bool(value("debug", "LABELFORGE_DEBUG", False), name="LABELFORGE_DEBUG"),
            reload=_as_bool(value("reload", "LABELFORGE_RELOAD", False), name="LABELFORGE_RELOAD"),
            secret_key=secret,
            max_upload_bytes=_as_int(value("max_upload_bytes", "LABELFORGE_MAX_UPLOAD_BYTES", 300 * 1024 * 1024), name="LABELFORGE_MAX_UPLOAD_BYTES", minimum=1, maximum=10 * 1024**3),
            session_idle_seconds=_as_int(value("session_idle_seconds", "LABELFORGE_SESSION_IDLE_SECONDS", 3600), name="LABELFORGE_SESSION_IDLE_SECONDS", minimum=60, maximum=7 * 86400),
            session_absolute_seconds=_as_int(value("session_absolute_seconds", "LABELFORGE_SESSION_ABSOLUTE_SECONDS", 28800), name="LABELFORGE_SESSION_ABSOLUTE_SECONDS", minimum=60, maximum=30 * 86400),
            cookie_secure=_as_bool(value("cookie_secure", "LABELFORGE_COOKIE_SECURE", True), name="LABELFORGE_COOKIE_SECURE"),
            cookie_samesite=same_site,
            trusted_proxy_hops=_as_int(value("trusted_proxy_hops", "LABELFORGE_TRUSTED_PROXY_HOPS", 0), name="LABELFORGE_TRUSTED_PROXY_HOPS", minimum=0, maximum=10),
            log_level=log_level,
        )

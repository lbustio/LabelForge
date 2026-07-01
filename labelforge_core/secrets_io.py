import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional


def _restrict_file_permissions(path: Path) -> None:
    """Restringe el acceso al archivo solo al propietario actual (cross-platform)."""
    if sys.platform == "win32":
        try:
            username = os.environ.get("USERNAME") or os.getlogin()
            subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"{username}:(F)"],
                check=True, capture_output=True,
            )
        except Exception:
            pass  # No crítico; icacls falla silenciosamente en algunos entornos
    else:
        path.chmod(0o600)


PROVIDER_API_KEY_ENV = {
    "gemini": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openai_compat": "OPENAI_API_KEY",
    "ollama": "",
    "vertex_gemini": "",
}


def secrets_dir(base_dir: Path) -> Path:
    return Path(base_dir) / "secrets"


def secrets_env_path(base_dir: Path) -> Path:
    """Canonical local dotenv path."""
    return secrets_dir(Path(base_dir)) / "secrets.env"


def provider_api_key_env(provider: str) -> str:
    return PROVIDER_API_KEY_ENV.get(provider, "")


def read_dotenv(path: Path) -> tuple[list[str], dict]:
    lines = []
    values = {}
    path = Path(path)
    if path.exists():
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return lines, values


def set_secret_value(path: Path, key: str, value: str) -> None:
    key = (key or "").strip()
    value = (value or "").strip()
    if not key:
        raise ValueError("Variable de entorno requerida.")
    if not value:
        raise ValueError("Clave de API requerida.")

    path = Path(path)
    lines, _ = read_dotenv(path)
    rendered = f"{key}={value}"
    replaced = False
    new_lines = []

    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            current_key = stripped.split("=", 1)[0].strip()
            if current_key == key:
                new_lines.append(rendered)
                replaced = True
                continue
        new_lines.append(line)

    if not replaced:
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.append(rendered)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    _restrict_file_permissions(path)
    os.environ[key] = value


def resolve_gcp_credentials(base_dir: Path) -> Optional[str]:
    """
    Garantiza que GOOGLE_APPLICATION_CREDENTIALS apunte a un archivo valido,
    sin depender de rutas absolutas en secrets.env.

    Orden de resolucion:
      1. Si ya esta seteada y el archivo existe  ->  se normaliza a absoluta.
      2. Si esta seteada con ruta relativa  ->  se resuelve contra base_dir.
      3. Si no esta seteada o la ruta no existe  ->  auto-detecta el primer
         JSON de tipo service_account en secrets/.

    Retorna la ruta absoluta resuelta, o None si no se encontro nada.
    """
    base_dir = Path(base_dir).resolve()
    secrets_d = base_dir / "secrets"

    current = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if current:
        p = Path(current)
        if not p.is_absolute():
            p = base_dir / p
        if p.exists():
            resolved = str(p.resolve())
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = resolved
            return resolved
        # Ruta configurada pero no existe  ->  caer a auto-deteccion

    # Auto-deteccion: primer JSON de service_account en secrets/
    if secrets_d.is_dir():
        for candidate in sorted(secrets_d.glob("*.json")):
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if data.get("type") == "service_account":
                    resolved = str(candidate.resolve())
                    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = resolved
                    return resolved
            except Exception:
                continue

    return None


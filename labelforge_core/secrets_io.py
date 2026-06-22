import json
import os
from pathlib import Path
from typing import Optional


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
    """Canonical local dotenv path. Falls back to legacy root file if present."""
    base_dir = Path(base_dir)
    canonical = secrets_dir(base_dir) / "secrets.env"
    legacy = base_dir / "secrets.env"
    if canonical.exists() or not legacy.exists():
        return canonical
    return legacy


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
    os.environ[key] = value


def resolve_gcp_credentials(base_dir: Path) -> Optional[str]:
    """
    Garantiza que GOOGLE_APPLICATION_CREDENTIALS apunte a un archivo vÃ¡lido,
    sin depender de rutas absolutas en secrets.env.

    Orden de resoluciÃ³n:
      1. Si ya estÃ¡ seteada y el archivo existe â†’ se normaliza a absoluta.
      2. Si estÃ¡ seteada con ruta relativa â†’ se resuelve contra base_dir.
      3. Si no estÃ¡ seteada o la ruta no existe â†’ auto-detecta el primer
         JSON de tipo service_account en secrets/.

    Retorna la ruta absoluta resuelta, o None si no se encontrÃ³ nada.
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
        # Ruta configurada pero no existe â†’ caer a auto-detecciÃ³n

    # Auto-detecciÃ³n: primer JSON de service_account en secrets/
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


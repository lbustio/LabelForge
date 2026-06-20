"""
main_webapp.py
--------------
Entrypoint principal para la webapp de configuracion y lanzamiento del etiquetado.

Puerto por defecto: 7860 (compatible con macOS, Windows y Linux sin conflictos).
  - macOS: el puerto 5000 esta reservado por AirPlay Receiver desde Monterey.
  - 7860 es libre en los tres sistemas operativos y no requiere privilegios.

Uso:
    python main_webapp.py [--host HOST] [--port PORT] [--debug]

Ejemplos:
    # Arrancar con los valores por defecto (127.0.0.1:7860)
    python main_webapp.py

    # Cambiar el puerto
    python main_webapp.py --port 8080

    # Accesible desde la red local (ej: para probar desde otro dispositivo)
    python main_webapp.py --host 0.0.0.0 --port 7860

Variables de entorno (se pueden poner en secrets/secrets.env):
    LABELFORGE_HOST  - host por defecto (ej: 0.0.0.0)
    LABELFORGE_PORT  - puerto por defecto (ej: 8080)
    Los argumentos --host y --port tienen precedencia sobre las variables de entorno.
"""

import os
import sys
import argparse
from pathlib import Path

# Asegurar que el root del proyecto este en el path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Cargar variables de entorno antes de parsear argumentos
from dotenv import load_dotenv
from scripts.secrets_io import resolve_gcp_credentials, secrets_env_path
load_dotenv(secrets_env_path(ROOT))
resolve_gcp_credentials(ROOT)

# Valores por defecto: CLI > env var > hardcoded
_DEFAULT_HOST = os.environ.get("LABELFORGE_HOST", "127.0.0.1")
_DEFAULT_PORT = int(os.environ.get("LABELFORGE_PORT", "7860"))

from scripts.utils import ensure_project_dirs
_created = ensure_project_dirs(ROOT)
if _created:
    for _d in _created:
        print(f"  [setup] Directorio creado: {_d.relative_to(ROOT)}")
else:
    print("  [setup] Directorios del proyecto verificados OK")

from webapp.app import app


def main():
    parser = argparse.ArgumentParser(
        description="Webapp de configuracion y lanzamiento del etiquetado de principios de persuasion."
    )
    parser.add_argument(
        "--host", default=_DEFAULT_HOST,
        help=f"Host en el que escucha la webapp (default: {_DEFAULT_HOST}, override con LABELFORGE_HOST)"
    )
    parser.add_argument(
        "--port", type=int, default=_DEFAULT_PORT,
        help=f"Puerto en el que escucha la webapp (default: {_DEFAULT_PORT}, override con LABELFORGE_PORT)"
    )
    parser.add_argument(
        "--debug", action="store_true", default=False,
        help="Activar modo debug de Flask (no usar en produccion)"
    )
    args = parser.parse_args()

    print(f"  LabelForge  →  http://{args.host}:{args.port}")
    print("  Presiona Ctrl+C para detener.")

    app.run(
        host=args.host,
        port=args.port,
        debug=args.debug,
        threaded=True,
        use_reloader=args.debug,
    )


if __name__ == "__main__":
    main()

"""
main_webapp.py
--------------
Entrypoint principal para la webapp de configuracion y lanzamiento del etiquetado.

Uso:
    python main_webapp.py [--host HOST] [--port PORT] [--debug]

Ejemplos:
    # Arrancar en modo desarrollo (localhost:5000)
    python main_webapp.py

    # Arrancar en un puerto diferente
    python main_webapp.py --port 8080

    # Arrancar accesible desde la red local
    python main_webapp.py --host 0.0.0.0 --port 5000
"""

import sys
import argparse
from pathlib import Path

# Asegurar que el root del proyecto este en el path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp.app import app


def main():
    parser = argparse.ArgumentParser(
        description="Webapp de configuracion y lanzamiento del etiquetado de principios de persuasion."
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="Host en el que escucha la webapp (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--port", type=int, default=5000,
        help="Puerto en el que escucha la webapp (default: 5000)"
    )
    parser.add_argument(
        "--debug", action="store_true", default=False,
        help="Activar modo debug de Flask (no usar en produccion)"
    )
    args = parser.parse_args()

    print(f"Iniciando webapp en http://{args.host}:{args.port}")
    print("Presiona Ctrl+C para detener.")

    app.run(
        host=args.host,
        port=args.port,
        debug=args.debug,
        threaded=True,
        use_reloader=args.debug,
    )


if __name__ == "__main__":
    main()

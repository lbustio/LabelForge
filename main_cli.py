"""
main_cli.py
-----------
Entrypoint principal para el proceso de etiquetado de principios de persuasion.

Uso:
    python main_cli.py [--limit N] [--config PATH] [--input PATH]

Ejemplos:
    # Procesar 10 entradas para prueba
    python main_cli.py --limit 10

    # Procesar todo el dataset
    python main_cli.py

    # Usar una config personalizada
    python main_cli.py --config config/my_config.json --limit 50
"""

import sys
from pathlib import Path

# Asegurar que el root del proyecto este en el path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from labelforge_core.secrets_io import resolve_gcp_credentials, secrets_env_path
load_dotenv(secrets_env_path(ROOT))
resolve_gcp_credentials(ROOT)

from labelforge_core.utils import ensure_project_dirs
_created = ensure_project_dirs(ROOT)
if _created:
    for _d in _created:
        print(f"  [setup] Directorio creado: {_d.relative_to(ROOT)}")
else:
    print("  [setup] Directorios del proyecto verificados OK")

from labelforge_core.label_persuasion import main

if __name__ == "__main__":
    main()

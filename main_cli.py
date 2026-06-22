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

import json
import sys
from pathlib import Path

# Asegurar que el root del proyecto este en el path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _check_hw_profile_if_needed():
    """
    Verifica hw_profile.json solo si hay árbitros llama_cpp configurados.
    Si hay proveedores locales y el setup no se ha completado, aborta con
    un mensaje accionable.
    """
    hw_profile  = ROOT / "models" / "hw_profile.json"

    try:
        from labelforge_core.paths import ARBITERS_CONFIG_PATH, resolve_config_file_path
        config_path = resolve_config_file_path(ARBITERS_CONFIG_PATH)
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return  # Sin config, no hay nada que verificar

    uses_local = any(
        arb.get("provider", {}).get("type") == "llama_cpp"
        for arb in cfg.get("arbiters", [])
    )
    if not uses_local:
        return

    if not hw_profile.exists():
        print(
            "Error: hay árbitros con proveedor 'llama_cpp' pero falta models/hw_profile.json.\n"
            "Ejecuta primero: python setup_env.py\n"
            "Luego vuelve a ejecutar este comando."
        )
        sys.exit(1)

    try:
        profile = json.loads(hw_profile.read_text(encoding="utf-8"))
        if not profile.get("setup_ok"):
            print(
                "Error: setup_ok=false en models/hw_profile.json.\n"
                "El setup tuvo errores. Revisa logs/setup_*.log y vuelve a ejecutar: python setup_env.py"
            )
            sys.exit(1)
    except Exception as exc:
        print(f"Error leyendo models/hw_profile.json: {exc}")
        sys.exit(1)


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

_check_hw_profile_if_needed()

from labelforge_core.label_persuasion import main

if __name__ == "__main__":
    main()


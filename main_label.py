"""
main_label.py
-------------
Entrypoint principal para el proceso de etiquetado de principios de persuasion.

Uso:
    python main_label.py [--limit N] [--config PATH] [--input PATH]

Ejemplos:
    # Procesar 10 entradas para prueba
    python main_label.py --limit 10

    # Procesar todo el dataset
    python main_label.py

    # Usar una config personalizada
    python main_label.py --config scripts/my_config.json --limit 50
"""

import sys
from pathlib import Path

# Asegurar que el root del proyecto este en el path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.label_persuasion import main

if __name__ == "__main__":
    main()

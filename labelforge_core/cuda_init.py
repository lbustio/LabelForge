"""
cuda_init.py
------------
Pre-carga las DLLs de CUDA en Windows ANTES de que cualquier modulo importe llama_cpp.

En CUDA 13.x las DLLs (cublas64_13.dll, etc.) estan en bin\\x64, no en bin.

Dos mecanismos complementarios:
1. os.add_dll_directory()  -  funciona para cargas con LOAD_LIBRARY_SEARCH_DEFAULT_DIRS
2. PATH  -  necesario para LoadLibrary con winmode=0 (que usa llama_cpp en Windows)
   Las dependencias de llama.dll/ggml-cuda.dll se resuelven via PATH.

Los handles de os.add_dll_directory() DEBEN guardarse: si el GC los destruye,
el directorio se elimina del search path. Por eso mantenemos _handles a nivel modulo.
"""

from __future__ import annotations

import ctypes
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

_handles: list = []
_initialized = False


def _ensure_cuda_ready() -> bool:
    """Prepara el entorno de DLLs CUDA para que llama_cpp pueda cargarse.

    Idempotente  -  safe to call multiple times.
    Returns True si se encontraron DLLs CUDA, False si no aplica o no se encontraron.
    """
    global _initialized
    if _initialized:
        return bool(_handles)
    _initialized = True

    if os.name != "nt":
        return False

    cuda_root = Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA")
    if not cuda_root.exists():
        return False

    for cv in sorted(cuda_root.glob("v*"), reverse=True):
        os.environ.setdefault("CUDA_PATH", str(cv))
        for sub in ("bin\\x64", "bin"):
            d = cv / sub
            if not d.exists():
                continue
            cublas_dlls = list(d.glob("cublas*.dll"))
            if not cublas_dlls:
                continue

            # Mecanismo 1: AddDllDirectory (para LOAD_LIBRARY_SEARCH_DEFAULT_DIRS)
            try:
                _handles.append(os.add_dll_directory(str(d)))
            except Exception as exc:
                log.debug("[cuda_init] add_dll_directory fallo para %s: %s", d, exc)

            # Mecanismo 2: PATH (para LoadLibrary winmode=0, que usa llama_cpp)
            current_path = os.environ.get("PATH", "")
            d_str = str(d)
            if d_str not in current_path:
                os.environ["PATH"] = d_str + os.pathsep + current_path
                log.info("[cuda_init] Agregado a PATH: %s", d_str)

            # Mecanismo 3: pre-cargar DLLs clave en memoria  -  resuelve la cadena
            # de dependencias antes de que llama.dll las necesite
            for dll in d.glob("*.dll"):
                try:
                    ctypes.CDLL(str(dll))
                except Exception:
                    pass

            log.info("[cuda_init] CUDA listo desde: %s", d)
            return True
        break

    return False


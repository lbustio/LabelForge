"""
setup_env.py
------------
Script de setup único para LabelForge con soporte de modelos locales GGUF.

Ejecutar UNA VEZ por máquina, ANTES de main_webapp.py o main_cli.py:
    python setup_env.py          # instalacion normal
    python setup_env.py --force  # reinstalar aunque ya este hecho

Flujo:
  1. Verifica Python >= 3.10 y entorno virtual activo
  2. Detecta hardware (OS / CPU / RAM / NPU / GPU)
  3. Instala requirements.txt (base)
  4. Instala backend NPU (OpenVINO) si se detecta Intel AI Boost
  5. Instala llama-cpp-python con soporte para el backend GPU detectado
  6. Verifica imports criticos
  7. Crea models/ si no existe
  8. Escribe models/hw_profile.json
  9. Imprime resumen final

Logging: identico al pipeline — %(asctime)s [%(levelname)s] %(message)s
         FileHandler (logs/setup_YYYYMMDD_HHMMSS.log) + StreamHandler
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------
BASE_DIR   = Path(__file__).resolve().parent
LOG_DIR    = BASE_DIR / "logs"
MODELS_DIR = BASE_DIR / "models"
HW_PROFILE = MODELS_DIR / "hw_profile.json"

LOG_DIR.mkdir(exist_ok=True)
MODELS_DIR.mkdir(exist_ok=True)

RUN_TS   = datetime.now().strftime("%Y%m%d_%H%M%S")
LOG_PATH = LOG_DIR / f"setup_{RUN_TS}.log"


# ---------------------------------------------------------------------------
# Logging — mismo formato que el pipeline
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)
for _noisy in ("pip", "urllib3", "httpx", "httpcore", "filelock"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _section(name: str):
    log.info("=== %s ===", name)


def _run_pip(args: list[str]) -> bool:
    """Ejecuta pip capturando salida línea a línea. Retorna True si exitoso."""
    cmd = [sys.executable, "-m", "pip"] + args
    log.info("Ejecutando: %s", " ".join(cmd))
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env={**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1"},
        )
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                log.info("  %s", line)
        proc.wait()
        if proc.returncode != 0:
            log.error("pip salió con código %d", proc.returncode)
            return False
        return True
    except Exception as exc:
        log.error("Error al ejecutar pip: %s", exc)
        return False


def _abort(msg: str):
    log.error(msg)
    log.error("Setup fallido. Revisa el log en: %s", LOG_PATH)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Verificaciones previas
# ---------------------------------------------------------------------------

def check_python_version():
    _section("VERIFICANDO PYTHON")
    ver = sys.version_info
    log.info("Python %d.%d.%d detectado", ver.major, ver.minor, ver.micro)
    if (ver.major, ver.minor) < (3, 10):
        _abort(f"Se requiere Python >= 3.10. Versión actual: {ver.major}.{ver.minor}.{ver.micro}")
    log.info("Version de Python OK")


def check_virtual_env():
    _section("VERIFICANDO ENTORNO VIRTUAL")
    in_venv   = hasattr(sys, "real_prefix") or (
        hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix
    )
    in_conda  = os.environ.get("CONDA_DEFAULT_ENV") not in (None, "base", "")

    if not in_venv and not in_conda:
        _abort(
            "No se detectó entorno virtual activo (venv o conda). "
            "Crea uno con: python -m venv .venv && source .venv/bin/activate  "
            "(Windows: .venv\\Scripts\\activate)"
        )
    env_name = os.environ.get("CONDA_DEFAULT_ENV") or sys.prefix
    log.info("Entorno virtual activo: %s", env_name)


# ---------------------------------------------------------------------------
# Detección de hardware
# ---------------------------------------------------------------------------

def detect_hardware() -> dict:
    _section("DETECTANDO HARDWARE")
    # Import dinámico: el módulo puede no estar disponible si psutil no está aún
    try:
        from scripts.hw_detection import detect_all
        hw = detect_all()
    except ImportError:
        # psutil todavía no está instalado — usar detección mínima
        import platform
        hw = {
            "os": platform.system(),
            "python_version": sys.version.split()[0],
            "cpu": {"vendor": "unknown", "model": platform.processor() or "unknown",
                    "cores_physical": 1, "avx2": False, "avx512": False},
            "ram_gb": 0.0,
            "npu": {"detected": False},
            "gpu": {"detected": False},
            "best_backend": "cpu",
        }
        log.warning("psutil no disponible — deteccion de HW parcial. Se reinstalará tras instalar requirements.")

    log.info("Sistema operativo : %s", hw["os"])
    log.info("Python            : %s", hw["python_version"])

    cpu = hw["cpu"]
    log.info("CPU               : %s — %s (%d núcleos físicos)",
             cpu.get("vendor", "?"), cpu.get("model", "?"), cpu.get("cores_physical", 0))
    log.info("CPU AVX2          : %s | AVX512: %s", cpu.get("avx2"), cpu.get("avx512"))
    log.info("RAM               : %.1f GB", hw["ram_gb"])

    npu = hw["npu"]
    if npu.get("detected"):
        log.info("NPU               : %s %s — DETECTADA",
                 npu.get("vendor", "?"), npu.get("type", "?"))
    else:
        log.info("NPU               : no detectada")

    gpu = hw["gpu"]
    if gpu.get("detected"):
        vram_str = f"{gpu['vram_gb']} GB" if gpu.get("vram_gb") else "compartida/desconocida"
        log.info("GPU               : %s — %s | VRAM: %s | Backend: %s",
                 gpu.get("vendor", "?"), gpu.get("name", "?"), vram_str, gpu.get("backend", "?"))
        if gpu.get("shared_memory"):
            log.info("                  (memoria de GPU compartida con sistema — factor conservador 0.75)")
    else:
        log.info("GPU               : no detectada — se usará CPU")

    log.info("Backend óptimo    : %s", hw["best_backend"])
    return hw


# ---------------------------------------------------------------------------
# Instalacion de paquetes base
# ---------------------------------------------------------------------------

def install_base_requirements() -> bool:
    _section("INSTALANDO REQUIREMENTS BASE")
    import tempfile
    req_path = BASE_DIR / "requirements.txt"
    if not req_path.exists():
        _abort(f"No se encontró requirements.txt en {BASE_DIR}")

    # Filtrar llama-cpp-python: lo instala setup_env.py con flags GPU específicos
    lines = req_path.read_text(encoding="utf-8").splitlines()
    filtered = [
        l for l in lines
        if not (l.strip().startswith("llama-cpp-python") and not l.strip().startswith("#"))
    ]
    log.info("Instalando requirements.txt (excluyendo llama-cpp-python — se instala por separado)")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as tmp:
        tmp.write("\n".join(filtered))
        tmp_path = tmp.name
    try:
        ok = _run_pip(["install", "-r", tmp_path, "--upgrade"])
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    if not ok:
        log.error("Falló la instalación de requirements.txt — se continuará con hardware ya detectado")
        return False
    log.info("Requirements base instalados OK")
    return True


# ---------------------------------------------------------------------------
# Backend NPU — OpenVINO
# ---------------------------------------------------------------------------

def install_npu_backend():
    _section("INSTALANDO BACKEND NPU (OpenVINO)")
    packages = [
        "openvino>=2024.5.0",
        "optimum[openvino]",
        "nncf",
    ]
    log.info("Paquetes NPU: %s", ", ".join(packages))
    ok = _run_pip(["install"] + packages)
    if not ok:
        log.warning("Falló la instalación del backend NPU (OpenVINO). Se continuará sin NPU.")
        return False

    try:
        import openvino
        log.info("OpenVINO importado OK — versión: %s", openvino.__version__)
    except ImportError as exc:
        log.warning("OpenVINO instalado pero no importable: %s", exc)
        return False

    log.info("Backend NPU (OpenVINO) instalado y verificado")
    return True


# ---------------------------------------------------------------------------
# Backend GPU/CPU — llama-cpp-python
# ---------------------------------------------------------------------------

_ABETLEN_BASE = "https://abetlen.github.io/llama-cpp-python/whl"
_WIN_VSDEVCMD = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\Tools\VsDevCmd.bat")
_WIN_CUDA_PATH = Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.3")


def _cuda_source_env() -> dict:
    cuda_path = os.environ.get("CUDA_PATH") or str(_WIN_CUDA_PATH)
    env = {
        "CMAKE_ARGS": "-DGGML_CUDA=on",
        "FORCE_CMAKE": "1",
        "CMAKE_CUDA_ARCHITECTURES": "native",
        "CMAKE_BUILD_PARALLEL_LEVEL": str(max((os.cpu_count() or 4) - 1, 1)),
        "CUDA_PATH": cuda_path,
        "CudaToolkitDir": cuda_path + "\\",
        "CUDA_PATH_V13_3": cuda_path,
    }
    cuda_bin = str(Path(cuda_path) / "bin")
    env["PATH"] = cuda_bin + os.pathsep + os.environ.get("PATH", "")
    return env

_LLAMA_INSTALL_MAP = {
    # backend  → list of (env_vars_dict, pip_args_extra) intentos en orden
    "cuda":   [
        # Intentos con wheels precompiladas — del más nuevo al más antiguo
        # cu130/cu128 necesarios para Blackwell (RTX 5xxx, sm_120+, CUDA 12.8+)
        ({}, ["--prefer-binary", "--extra-index-url", f"{_ABETLEN_BASE}/cu130"]),
        ({}, ["--prefer-binary", "--extra-index-url", f"{_ABETLEN_BASE}/cu128"]),
        ({}, ["--prefer-binary", "--extra-index-url", f"{_ABETLEN_BASE}/cu126"]),
        ({}, ["--prefer-binary", "--extra-index-url", f"{_ABETLEN_BASE}/cu124"]),
        ({}, ["--prefer-binary", "--extra-index-url", f"{_ABETLEN_BASE}/cu121"]),
        # Último recurso: compilar desde fuente con MSVC
        ({"CMAKE_ARGS": "-DGGML_CUDA=on"}, ["--force-reinstall", "--no-cache-dir"]),
        # Fallback funcional si CUDA no carga en runtime
        ({}, ["--prefer-binary", "--extra-index-url", f"{_ABETLEN_BASE}/cpu"]),
    ],
    "rocm":   [({"CMAKE_ARGS": "-DGGML_HIPBLAS=on"}, ["--force-reinstall", "--no-cache-dir"])],
    "metal":  [({"CMAKE_ARGS": "-DGGML_METAL=on"},   ["--force-reinstall", "--no-cache-dir"])],
    "vulkan": [({}, ["--extra-index-url", f"{_ABETLEN_BASE}/vulkan"])],
    "cpu":    [
        # 1er intento: wheel precompilada CPU (no requiere MSVC en Windows)
        ({}, ["--prefer-binary", "--extra-index-url", f"{_ABETLEN_BASE}/cpu"]),
        # Último recurso: instalar genérico (intentará compilar si no hay wheel)
        ({}, []),
    ],
}


def _has_windows_cuda_build_tools() -> bool:
    return (
        os.name == "nt"
        and _WIN_VSDEVCMD.exists()
        and (_WIN_CUDA_PATH / "bin" / "nvcc.exe").exists()
    )


def _uninstall_llama_cpp():
    subprocess.run(
        [sys.executable, "-m", "pip", "uninstall", "llama-cpp-python", "-y"],
        capture_output=True, text=True
    )


def _detect_cuda_version() -> str:
    """Retorna versión CUDA como '12.6' o '' si no se puede detectar."""
    try:
        out = subprocess.check_output(["nvidia-smi"], text=True, stderr=subprocess.DEVNULL, timeout=10)
        m = re.search(r"CUDA Version:\s*(\d+\.\d+)", out)
        if m:
            return m.group(1)
    except Exception:
        pass
    return ""


def _run_pip_attempt(env_vars: dict, extra_args: list) -> bool:
    env = {**os.environ, **env_vars}
    cmd = [sys.executable, "-m", "pip", "install", "llama-cpp-python>=0.3.0",
           "--upgrade"] + extra_args
    log.info("  Intentando: %s", " ".join(cmd))
    if env_vars:
        log.info("  Variables: %s", " ".join(f"{k}={v}" for k, v in env_vars.items()))
    try:
        run_env = {**env, "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
        if os.name == "nt" and env_vars.get("CMAKE_ARGS") == "-DGGML_CUDA=on" and _WIN_VSDEVCMD.exists():
            # subprocess.list2cmdline escapa correctamente ">=" evitando que cmd.exe
            # lo interprete como redirección de I/O
            cmd_text = subprocess.list2cmdline(cmd)
            shell_cmd = f'call "{_WIN_VSDEVCMD}" -arch=x64 && {cmd_text}'
            proc = subprocess.Popen(
                ["cmd.exe", "/c", shell_cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=run_env,
            )
        else:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                env=run_env,
            )
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                log.info("  %s", line)
        proc.wait()
        return proc.returncode == 0
    except Exception as exc:
        log.warning("  Error en intento: %s", exc)
        return False


def _probe_llama_cpp() -> tuple[bool, bool, str]:
    import tempfile
    # Escribir el probe como script temporal para evitar problemas de escaping y GC.
    # IMPORTANTE: os.add_dll_directory() devuelve un handle — si se descarta sin asignar,
    # el GC lo destruye inmediatamente y elimina el directorio del search path.
    # En CUDA 13.x las DLLs están en bin\x64, no en bin.
    script = textwrap.dedent("""\
        import os, ctypes
        from pathlib import Path
        cuda_root = Path(r"C:\\Program Files\\NVIDIA GPU Computing Toolkit\\CUDA")
        _handles = []
        for cv in sorted(cuda_root.glob("v*"), reverse=True):
            os.environ.setdefault("CUDA_PATH", str(cv))
            for sub in ("bin\\\\x64", "bin"):
                d = cv / sub
                if d.exists() and list(d.glob("cublas*.dll")):
                    _handles.append(os.add_dll_directory(str(d)))
                    for dll in ("cublas64_13.dll", "cublasLt64_13.dll", "cudart64_13.dll",
                                "cublas64_12.dll", "cublasLt64_12.dll", "cudart64_12.dll"):
                        dp = d / dll
                        if dp.exists():
                            try:
                                ctypes.CDLL(str(dp))
                            except Exception:
                                pass
            break
        import llama_cpp
        print("gpu=" + str(bool(llama_cpp.llama_supports_gpu_offload())))
    """)

    probe_env = dict(os.environ)
    if "CUDA_PATH" not in probe_env and os.name == "nt" and _WIN_CUDA_PATH.exists():
        probe_env["CUDA_PATH"] = str(_WIN_CUDA_PATH)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False,
                                     encoding="utf-8") as tmp:
        tmp.write(script)
        tmp_path = tmp.name
    try:
        proc = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            env=probe_env,
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    output = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        return False, False, output
    return True, "gpu=True" in output, output


def install_llama_cpp(backend: str) -> bool:
    _section("INSTALANDO llama-cpp-python")
    backend = backend if backend in _LLAMA_INSTALL_MAP else "cpu"
    attempts = _LLAMA_INSTALL_MAP[backend]

    log.info("Backend detectado para llama-cpp-python: %s", backend)

    if backend == "cuda":
        cuda_ver = _detect_cuda_version()
        if _has_windows_cuda_build_tools():
            log.info("MSVC + CUDA Toolkit detectados — se prioriza compilación local CUDA desde fuente")
            source_attempt = (_cuda_source_env(), ["--force-reinstall", "--no-cache-dir"])
            attempts = [source_attempt] + [a for a in attempts if a != source_attempt]
        if cuda_ver:
            log.info("CUDA detectada: versión %s", cuda_ver)
            major, minor = cuda_ver.split(".", 1)
            cu_tag = f"cu{major}{minor.split('.')[0]}"
            preferred = ({}, ["--prefer-binary", "--extra-index-url", f"{_ABETLEN_BASE}/{cu_tag}"])
            # Insertar el intento con versión exacta como primera opción
            if not _has_windows_cuda_build_tools():
                attempts = [preferred] + [a for a in attempts if a != preferred]
        else:
            log.warning("No se pudo detectar versión de CUDA — se intentarán múltiples índices")

    # Desinstalar versión previa para evitar incompatibilidades
    log.info("Desinstalando versión previa de llama-cpp-python (si existe)...")
    _uninstall_llama_cpp()

    for i, (env_vars, extra_args) in enumerate(attempts, 1):
        log.info("Intento %d/%d...", i, len(attempts))
        if _run_pip_attempt(env_vars, extra_args):
            import_ok, gpu_ok, probe_output = _probe_llama_cpp()
            if import_ok:
                if backend != "cpu" and not gpu_ok:
                    log.warning(
                        "llama-cpp-python importó, pero sin offload GPU en intento %d. "
                        "Se probará otra opción si queda disponible.",
                        i,
                    )
                    if i < len(attempts):
                        _uninstall_llama_cpp()
                        continue
                log.info("llama-cpp-python instalado y verificado OK (intento %d)", i)
                return True
            log.warning("llama-cpp-python instalado pero no importable: %s", probe_output)
            if i < len(attempts):
                _uninstall_llama_cpp()
        log.warning("Intento %d falló — probando siguiente opción...", i)

    log.error("Todos los intentos de instalar llama-cpp-python fallaron")
    return False


# ---------------------------------------------------------------------------
# Verificacion de imports
# ---------------------------------------------------------------------------

def verify_imports(has_npu: bool, gpu_backend: str) -> dict:
    _section("VERIFICANDO IMPORTS CRITICOS")
    results: dict = {}

    # Pre-cargar DLLs de CUDA antes de importar llama_cpp (necesario en Windows/CUDA 13.x)
    try:
        from scripts.cuda_init import _ensure_cuda_ready
        _ensure_cuda_ready()
    except ImportError:
        pass
    try:
        import llama_cpp
        gpu_ok = llama_cpp.llama_supports_gpu_offload()
        log.info("llama_cpp importado OK — GPU offload soportado: %s", gpu_ok)
        if gpu_backend != "cpu" and not gpu_ok:
            log.warning(
                "llama_cpp es importable, pero no reporta soporte GPU para backend=%s. "
                "Se usará CPU como fallback.",
                gpu_backend,
            )
        results["llama_cpp"] = True
        results["llama_cpp_gpu"] = gpu_ok
    except (ImportError, RuntimeError, OSError) as exc:
        log.error("llama_cpp NO importable: %s", exc)
        if "Could not find module" in str(exc) or "Failed to load shared library" in str(exc):
            log.error(
                "En Windows esto suele significar que falta una dependencia DLL de llama.dll "
                "(por ejemplo runtime CUDA/MSVC), no necesariamente que falte llama.dll."
            )
        results["llama_cpp"] = False
        results["llama_cpp_gpu"] = False

    # openvino (solo si hubo intento de instalación NPU)
    if has_npu:
        try:
            import openvino
            log.info("openvino importado OK — versión: %s", openvino.__version__)
            results["openvino"] = True
            results["openvino_version"] = openvino.__version__
        except (ImportError, RuntimeError, OSError) as exc:
            log.warning("openvino NO importable (NPU no disponible en runtime): %s", exc)
            results["openvino"] = False

    # huggingface_hub
    try:
        import huggingface_hub
        log.info("huggingface_hub importado OK — versión: %s", huggingface_hub.__version__)
        results["huggingface_hub"] = True
    except (ImportError, RuntimeError, OSError) as exc:
        log.error("huggingface_hub NO importable: %s", exc)
        results["huggingface_hub"] = False

    return results


# ---------------------------------------------------------------------------
# Escritura de hw_profile.json
# ---------------------------------------------------------------------------

def write_hw_profile(hw: dict, verify_results: dict, npu_ok: bool, llama_ok: bool):
    _section("ESCRIBIENDO hw_profile.json")

    npu = dict(hw.get("npu", {}))
    gpu = dict(hw.get("gpu", {}))

    npu["backend"] = "openvino" if npu.get("detected") else None
    npu["ok"] = npu_ok
    if npu.get("ok") and verify_results.get("openvino"):
        npu["version"] = verify_results.get("openvino_version")

    gpu["ok"] = llama_ok and verify_results.get("llama_cpp_gpu", False)

    # Si el NPU falló en runtime, el mejor backend baja al GPU/CPU
    best_backend = hw["best_backend"]
    if best_backend == "npu" and not npu_ok:
        best_backend = gpu.get("backend") or "cpu"
        log.warning("NPU detectada pero backend OpenVINO no OK — recayendo en: %s", best_backend)
    if best_backend not in ("cpu", "npu") and not gpu.get("ok"):
        log.warning(
            "GPU detectada con backend=%s, pero llama_cpp no confirmó offload GPU — recayendo en CPU",
            best_backend,
        )
        best_backend = "cpu"

    profile = {
        "setup_date": datetime.now().isoformat(timespec="seconds"),
        "setup_log": str(LOG_PATH.relative_to(BASE_DIR)),
        "os": hw["os"],
        "python_version": hw["python_version"],
        "cpu": hw["cpu"],
        "ram_gb": hw["ram_gb"],
        "npu": npu,
        "gpu": gpu,
        "best_backend": best_backend,
        "setup_ok": llama_ok,
    }

    with open(HW_PROFILE, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)

    log.info("hw_profile.json escrito en: %s", HW_PROFILE.relative_to(BASE_DIR))
    log.info("setup_ok = %s | best_backend = %s", profile["setup_ok"], profile["best_backend"])
    return profile


# ---------------------------------------------------------------------------
# Resumen final
# ---------------------------------------------------------------------------

def print_summary(profile: dict):
    _section("RESUMEN FINAL")
    ok = profile.get("setup_ok", False)
    status = "EXITOSO" if ok else "CON ERRORES"
    log.info("Setup %s", status)
    log.info("Sistema       : %s | Python %s", profile["os"], profile["python_version"])
    log.info("CPU           : %s — %s", profile["cpu"].get("vendor"), profile["cpu"].get("model"))
    log.info("RAM           : %.1f GB", profile["ram_gb"])

    npu = profile["npu"]
    if npu.get("detected"):
        log.info("NPU           : %s %s — ok=%s", npu.get("vendor"), npu.get("type"), npu.get("ok"))

    gpu = profile["gpu"]
    if gpu.get("detected"):
        log.info("GPU           : %s — ok=%s | backend=%s",
                 gpu.get("name"), gpu.get("ok"), gpu.get("backend"))

    log.info("Backend activo: %s", profile["best_backend"])
    log.info("Log de setup  : %s", profile["setup_log"])

    if ok:
        log.info("")
        log.info("Siguiente paso: python main_webapp.py  o  python main_cli.py")
    else:
        log.error("")
        log.error("El setup tuvo errores. Revisa el log completo en: %s", profile["setup_log"])
        log.error("Corrige los errores y vuelve a ejecutar: python setup_env.py --force")


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="LabelForge — setup de hardware y backends locales"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Reinstalar aunque el setup ya se haya completado previamente"
    )
    args = parser.parse_args()

    _section("LABELFORGE SETUP")
    log.info("Inicio de setup: %s", datetime.now().isoformat(timespec="seconds"))
    log.info("Directorio base: %s", BASE_DIR)
    log.info("Log de sesión  : %s", LOG_PATH.relative_to(BASE_DIR))

    # Si ya existe hw_profile y setup_ok, salir salvo --force
    if HW_PROFILE.exists() and not args.force:
        try:
            prev = json.loads(HW_PROFILE.read_text(encoding="utf-8"))
            if prev.get("setup_ok"):
                log.info("Setup previo OK encontrado (%s). Usa --force para reinstalar.",
                         prev.get("setup_date", "?"))
                sys.exit(0)
        except Exception:
            pass

    # 1. Verificaciones previas
    check_python_version()
    check_virtual_env()

    # 2. Detectar hardware (con psutil si ya está disponible)
    hw = detect_hardware()

    # 3. Requirements base (nunca aborta — continúa para escribir hw_profile.json)
    req_ok = install_base_requirements()
    if not req_ok:
        log.warning("Requirements base instalados con errores — se continuará el setup")

    # Re-importar hw_detection ahora que psutil está disponible
    if hw["ram_gb"] == 0.0:
        log.info("Re-detectando hardware con psutil disponible...")
        hw = detect_hardware()

    # 4. Backend NPU
    npu_ok = False
    if hw["npu"].get("detected"):
        npu_ok = install_npu_backend()
    else:
        log.info("NPU no detectada — se omite instalación de OpenVINO")

    # 5. llama-cpp-python con el backend GPU correcto
    # Si best_backend es "npu", llama-cpp-python igual se instala para el fallback GPU/CPU
    gpu_backend = hw["gpu"].get("backend") or "cpu"
    llama_ok = install_llama_cpp(gpu_backend)
    if not llama_ok:
        log.error("Falló la instalación de llama-cpp-python")

    # 6. Verificar imports
    verify_results = verify_imports(has_npu=hw["npu"].get("detected", False),
                                    gpu_backend=gpu_backend)

    llama_ok = verify_results.get("llama_cpp", False)

    # 7. Crear todos los directorios requeridos del proyecto
    _section("VERIFICANDO DIRECTORIOS DEL PROYECTO")
    try:
        from scripts.utils import ensure_project_dirs
        created = ensure_project_dirs(BASE_DIR)
        if created:
            for d in created:
                log.info("Directorio creado: %s", d.relative_to(BASE_DIR))
        else:
            log.info("Todos los directorios del proyecto existen")
    except Exception as exc:
        log.warning("No se pudo verificar directorios del proyecto: %s", exc)
        MODELS_DIR.mkdir(exist_ok=True)
    log.info("Directorio models/ listo: %s", MODELS_DIR.relative_to(BASE_DIR))

    # 8. Escribir hw_profile.json
    profile = write_hw_profile(hw, verify_results, npu_ok, llama_ok)

    # 9. Resumen
    print_summary(profile)

    sys.exit(0 if profile["setup_ok"] else 1)


if __name__ == "__main__":
    main()

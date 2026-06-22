"""
hw_detection.py
---------------
Deteccion multiplataforma de hardware.

Usado por setup_env.py (setup time) y por local_models.py (runtime)
para construir o validar hw_profile.json.

Retorna un dict compatible con el esquema de hw_profile.json.
"""

from __future__ import annotations

import platform
import subprocess
import re
import sys
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# OS
# ---------------------------------------------------------------------------

def detect_os() -> str:
    return platform.system()  # "Windows" | "Linux" | "Darwin"


# ---------------------------------------------------------------------------
# CPU
# ---------------------------------------------------------------------------

def _cpu_flags_linux() -> set[str]:
    try:
        text = Path("/proc/cpuinfo").read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("flags"):
                return set(line.split(":")[1].split())
    except Exception:
        pass
    return set()


def _cpu_flags_macos() -> set[str]:
    flags = set()
    try:
        out = subprocess.check_output(["sysctl", "-a"], text=True, stderr=subprocess.DEVNULL)
        for line in out.splitlines():
            if "hw.optional.avx2_0" in line and "1" in line:
                flags.add("avx2")
            if "hw.optional.avx512f" in line and "1" in line:
                flags.add("avx512f")
    except Exception:
        pass
    return flags


def _cpu_flags_windows() -> set[str]:
    flags = set()
    try:
        import ctypes
        # IsProcessorFeaturePresent: 20 = PF_AVX2_INSTRUCTIONS_AVAILABLE
        avx2 = ctypes.windll.kernel32.IsProcessorFeaturePresent(20)
        if avx2:
            flags.add("avx2")
    except Exception:
        pass
    return flags


def detect_cpu() -> dict:
    info: dict = {
        "vendor": "unknown",
        "model": platform.processor() or "unknown",
        "cores_physical": 1,
        "avx2": False,
        "avx512": False,
    }

    try:
        import psutil
        info["cores_physical"] = psutil.cpu_count(logical=False) or 1
    except Exception:
        pass

    os_name = detect_os()
    if os_name == "Linux":
        flags = _cpu_flags_linux()
        info["avx2"] = "avx2" in flags
        info["avx512"] = "avx512f" in flags
        try:
            cpuinfo = Path("/proc/cpuinfo").read_text(encoding="utf-8")
            for line in cpuinfo.splitlines():
                if "vendor_id" in line:
                    v = line.split(":")[1].strip()
                    info["vendor"] = "Intel" if "Intel" in v else ("AMD" if "AMD" in v else v)
                    break
        except Exception:
            pass
    elif os_name == "Darwin":
        flags = _cpu_flags_macos()
        info["avx2"] = "avx2" in flags
        info["avx512"] = "avx512f" in flags
        # Apple Silicon detection
        try:
            out = subprocess.check_output(["sysctl", "hw.optional.arm64"], text=True, stderr=subprocess.DEVNULL)
            if "1" in out:
                info["vendor"] = "Apple"
        except Exception:
            pass
        if info["vendor"] == "unknown":
            brand = (platform.processor() or "").lower()
            info["vendor"] = "Intel" if "intel" in brand else ("AMD" if "amd" in brand else "Apple")
    elif os_name == "Windows":
        flags = _cpu_flags_windows()
        info["avx2"] = "avx2" in flags
        try:
            out = subprocess.check_output(
                ["wmic", "cpu", "get", "Name,Manufacturer", "/value"],
                text=True, stderr=subprocess.DEVNULL
            )
            for line in out.splitlines():
                if "Manufacturer=" in line:
                    v = line.split("=")[1].strip()
                    info["vendor"] = "Intel" if "Intel" in v else ("AMD" if "AMD" in v else v)
                if "Name=" in line and line.split("=")[1].strip():
                    info["model"] = line.split("=")[1].strip()
        except Exception:
            pass

    return info


# ---------------------------------------------------------------------------
# RAM
# ---------------------------------------------------------------------------

def detect_ram_gb() -> float:
    try:
        import psutil
        return round(psutil.virtual_memory().total / (1024 ** 3), 1)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# NPU (Intel AI Boost / Neural Processing Unit)
# ---------------------------------------------------------------------------

def _detect_npu_windows() -> dict:
    """Busca dispositivo NPU en WMI (Intel AI Boost aparece como 'Neural')."""
    npu: dict = {"detected": False, "vendor": None, "type": None}
    try:
        out = subprocess.check_output(
            ["wmic", "path", "Win32_PnPEntity", "where",
             "(Name like '%Neural%' or Name like '%NPU%' or Name like '%AI Boost%')",
             "get", "Name", "/value"],
            text=True, stderr=subprocess.DEVNULL, timeout=15
        )
        names = [line.split("=")[1].strip() for line in out.splitlines()
                 if line.startswith("Name=") and line.split("=")[1].strip()]
        if names:
            npu["detected"] = True
            npu["vendor"] = "Intel"
            npu["type"] = "AI Boost"
            npu["name"] = names[0]
    except Exception:
        pass
    return npu


def _detect_npu_linux() -> dict:
    """Busca NPU en sysfs PCI o lspci."""
    npu: dict = {"detected": False, "vendor": None, "type": None}
    try:
        out = subprocess.check_output(
            ["lspci"], text=True, stderr=subprocess.DEVNULL, timeout=10
        )
        for line in out.splitlines():
            lower = line.lower()
            if "neural" in lower or "npu" in lower or "ai boost" in lower:
                npu["detected"] = True
                npu["vendor"] = "Intel"
                npu["type"] = "AI Boost"
                npu["name"] = line.strip()
                break
    except Exception:
        pass
    return npu


def detect_npu() -> dict:
    os_name = detect_os()
    if os_name == "Windows":
        return _detect_npu_windows()
    if os_name == "Linux":
        return _detect_npu_linux()
    # macOS: sin NPU externo, Apple Neural Engine no expuesto como acelerador genÃ©rico
    return {"detected": False, "vendor": None, "type": None}


# ---------------------------------------------------------------------------
# GPU
# ---------------------------------------------------------------------------

def _detect_nvidia() -> Optional[dict]:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            text=True, stderr=subprocess.DEVNULL, timeout=10
        )
        lines = [l.strip() for l in out.strip().splitlines() if l.strip()]
        if not lines:
            return None
        name, vram_mb = lines[0].split(",", 1)
        vram_gb = round(float(vram_mb.strip()) / 1024, 1)
        return {
            "detected": True,
            "vendor": "NVIDIA",
            "name": name.strip(),
            "vram_gb": vram_gb,
            "shared_memory": False,
            "backend": "cuda",
        }
    except Exception:
        return None


def _detect_amd_linux() -> Optional[dict]:
    try:
        out = subprocess.check_output(
            ["rocm-smi", "--showmeminfo", "vram", "--json"],
            text=True, stderr=subprocess.DEVNULL, timeout=10
        )
        import json
        data = json.loads(out)
        # rocm-smi JSON: {"card0": {"VRAM Total Memory (B)": "..."}, ...}
        for card, info in data.items():
            vram_bytes = int(info.get("VRAM Total Memory (B)", 0))
            vram_gb = round(vram_bytes / (1024 ** 3), 1)
            return {
                "detected": True,
                "vendor": "AMD",
                "name": card,
                "vram_gb": vram_gb,
                "shared_memory": False,
                "backend": "rocm",
            }
    except Exception:
        pass
    # Fallback: lspci
    try:
        out = subprocess.check_output(["lspci"], text=True, stderr=subprocess.DEVNULL, timeout=10)
        for line in out.splitlines():
            if "amd" in line.lower() and ("radeon" in line.lower() or "vega" in line.lower() or "navi" in line.lower()):
                return {
                    "detected": True,
                    "vendor": "AMD",
                    "name": line.strip(),
                    "vram_gb": None,
                    "shared_memory": False,
                    "backend": "rocm",
                }
    except Exception:
        pass
    return None


def _detect_amd_windows() -> Optional[dict]:
    try:
        out = subprocess.check_output(
            ["wmic", "path", "Win32_VideoController",
             "where", "(AdapterCompatibility like '%AMD%' or AdapterCompatibility like '%ATI%')",
             "get", "Name,AdapterRAM", "/value"],
            text=True, stderr=subprocess.DEVNULL, timeout=15
        )
        name = None
        ram_bytes = 0
        for line in out.splitlines():
            if line.startswith("Name=") and line.split("=")[1].strip():
                name = line.split("=")[1].strip()
            if line.startswith("AdapterRAM=") and line.split("=")[1].strip():
                try:
                    ram_bytes = int(line.split("=")[1].strip())
                except ValueError:
                    pass
        if name:
            return {
                "detected": True,
                "vendor": "AMD",
                "name": name,
                "vram_gb": round(ram_bytes / (1024 ** 3), 1) if ram_bytes else None,
                "shared_memory": False,
                "backend": "vulkan",  # AMD en Windows: Vulkan (ROCm solo Linux)
            }
    except Exception:
        pass
    return None


def _detect_intel_arc_windows() -> Optional[dict]:
    try:
        out = subprocess.check_output(
            ["wmic", "path", "Win32_VideoController",
             "where", "(Name like '%Intel%Arc%' or Name like '%Intel%Xe%')",
             "get", "Name,AdapterRAM", "/value"],
            text=True, stderr=subprocess.DEVNULL, timeout=15
        )
        name = None
        ram_bytes = 0
        for line in out.splitlines():
            if line.startswith("Name=") and line.split("=")[1].strip():
                name = line.split("=")[1].strip()
            if line.startswith("AdapterRAM=") and line.split("=")[1].strip():
                try:
                    ram_bytes = int(line.split("=")[1].strip())
                except ValueError:
                    pass
        if name:
            # Intel Arc usa memoria compartida (iGPU integrada o Arc con LPDDR)
            return {
                "detected": True,
                "vendor": "Intel",
                "name": name,
                "vram_gb": round(ram_bytes / (1024 ** 3), 1) if ram_bytes else None,
                "shared_memory": True,
                "backend": "vulkan",
            }
    except Exception:
        pass
    return None


def _detect_intel_arc_linux() -> Optional[dict]:
    try:
        out = subprocess.check_output(["lspci"], text=True, stderr=subprocess.DEVNULL, timeout=10)
        for line in out.splitlines():
            lower = line.lower()
            if "intel" in lower and ("arc" in lower or "xe" in lower):
                return {
                    "detected": True,
                    "vendor": "Intel",
                    "name": line.strip(),
                    "vram_gb": None,
                    "shared_memory": True,
                    "backend": "vulkan",
                }
    except Exception:
        pass
    return None


def _detect_apple_silicon() -> Optional[dict]:
    try:
        out = subprocess.check_output(
            ["sysctl", "hw.optional.arm64"],
            text=True, stderr=subprocess.DEVNULL, timeout=5
        )
        if "1" not in out:
            return None
        # En Apple Silicon la RAM es unificada (shared GPU/CPU)
        import psutil
        total_ram_gb = round(psutil.virtual_memory().total / (1024 ** 3), 1)
        model_out = subprocess.check_output(
            ["sysctl", "hw.model"], text=True, stderr=subprocess.DEVNULL
        ).strip()
        return {
            "detected": True,
            "vendor": "Apple",
            "name": model_out.split(":")[1].strip() if ":" in model_out else "Apple Silicon GPU",
            "vram_gb": total_ram_gb,  # Unificada: toda la RAM es accesible al GPU
            "shared_memory": True,
            "backend": "metal",
        }
    except Exception:
        return None


def _detect_ati_macos() -> Optional[dict]:
    """iMac con AMD/ATI Radeon en macOS Intel."""
    try:
        out = subprocess.check_output(
            ["system_profiler", "SPDisplaysDataType"],
            text=True, stderr=subprocess.DEVNULL, timeout=15
        )
        name = None
        vram_mb = None
        for line in out.splitlines():
            stripped = line.strip()
            if "Chipset Model:" in stripped:
                name = stripped.split(":", 1)[1].strip()
            if "VRAM" in stripped and ":" in stripped:
                vram_str = stripped.split(":", 1)[1].strip()
                m = re.search(r"(\d+)", vram_str)
                if m:
                    val = int(m.group(1))
                    vram_mb = val * 1024 if "GB" in vram_str else val
        if name and ("AMD" in name or "ATI" in name or "Radeon" in name):
            return {
                "detected": True,
                "vendor": "AMD",
                "name": name,
                "vram_gb": round(vram_mb / 1024, 1) if vram_mb else None,
                "shared_memory": False,
                "backend": "vulkan",  # Metal no funciona con llama-cpp-python en iMac Intel
            }
    except Exception:
        pass
    return None


def detect_gpu() -> dict:
    os_name = detect_os()
    null_gpu = {"detected": False, "vendor": None, "name": None, "vram_gb": None, "shared_memory": False, "backend": None}

    # NVIDIA: multiplataforma con nvidia-smi
    nvidia = _detect_nvidia()
    if nvidia:
        return nvidia

    if os_name == "Linux":
        amd = _detect_amd_linux()
        if amd:
            return amd
        intel = _detect_intel_arc_linux()
        if intel:
            return intel

    elif os_name == "Darwin":
        apple = _detect_apple_silicon()
        if apple:
            return apple
        ati = _detect_ati_macos()
        if ati:
            return ati

    elif os_name == "Windows":
        intel = _detect_intel_arc_windows()
        if intel:
            return intel
        amd = _detect_amd_windows()
        if amd:
            return amd

    return null_gpu


# ---------------------------------------------------------------------------
# Backend decision
# ---------------------------------------------------------------------------

def decide_best_backend(npu: dict, gpu: dict) -> str:
    """Prioridad: NPU â†’ GPU â†’ CPU."""
    if npu.get("detected"):
        return "npu"
    if gpu.get("detected"):
        return gpu.get("backend") or "cpu"
    return "cpu"


# ---------------------------------------------------------------------------
# Punto de entrada principal
# ---------------------------------------------------------------------------

def detect_all() -> dict:
    """
    Retorna el inventario completo de hardware.
    Compatible con el esquema de hw_profile.json (sin setup_date ni setup_log).
    """
    os_name = detect_os()
    cpu = detect_cpu()
    ram_gb = detect_ram_gb()
    npu = detect_npu()
    gpu = detect_gpu()
    best_backend = decide_best_backend(npu, gpu)

    return {
        "os": os_name,
        "python_version": sys.version.split()[0],
        "cpu": cpu,
        "ram_gb": ram_gb,
        "npu": npu,
        "gpu": gpu,
        "best_backend": best_backend,
    }


if __name__ == "__main__":
    import json
    result = detect_all()
    print(json.dumps(result, indent=2, ensure_ascii=False))


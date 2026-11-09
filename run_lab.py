#!/usr/bin/env python3
"""
run_lab.py
----------
Script de entrada para correr LabelForge en el Laboratorio de IA IBERO.

Solo usa la biblioteca estandar para poder ejecutarse antes de instalar nada.

    python3 run_lab.py setup             # crea .venv e instala dependencias
    python3 run_lab.py key               # pide la llave sin mostrarla y la guarda
    python3 run_lab.py check             # valida llave, modelos y config
    python3 run_lab.py run data/lab/*.csv --bg
    python3 run_lab.py status
    python3 run_lab.py stop

`run` procesa los archivos en serie, en modo lotes, y deja cada resultado en
output/<dataset>_etiquetado.csv. Con --bg sigue corriendo aunque se cierre el
navegador. Si se interrumpe, volver a lanzar el mismo comando retoma donde iba y
salta los archivos ya terminados.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
VENV_PYTHON = VENV_DIR / "bin" / "python"
REQUIREMENTS = ROOT / "requirements-lab.txt"
DEFAULT_CONFIG = ROOT / "config" / "arbiters_config.saptiva.json"
SECRETS_ENV = ROOT / "secrets" / "secrets.env"
OUTPUT_DIR = ROOT / "output"
LOGS_DIR = ROOT / "logs"
RUN_STATE = ROOT / "checkpoints" / "run_state.json"
BATCH_STATE = ROOT / "checkpoints" / "saptiva_batch_state.json"
MANIFEST = OUTPUT_DIR / "lab_runs.json"
PID_FILE = LOGS_DIR / "lab_run.pid"
CHAIN_LOG = LOGS_DIR / "lab_run.log"
KEY_ENV = "SAPTIVA_API_KEY"


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def run_key(dataset: Path, config: Path, column: str, limit: int | None) -> str:
    """Identifica una corrida: contenido del dataset y de la config, columna y limite."""
    h = hashlib.sha256()
    for path in (dataset, config):
        h.update(Path(path).read_bytes())
    h.update(f"{column}|{limit}".encode("utf-8"))
    return h.hexdigest()[:16]


def should_resume(state: dict, dataset: Path) -> bool:
    """True si hay una corrida a medias de este mismo archivo; si no, se arranca limpio."""
    return state.get("status") in {"running", "paused"} and state.get("input_file") == str(dataset)


def build_cli_command(python: str, dataset: Path, config: Path, column: str, limit: int | None, resume: bool) -> list[str]:
    cmd = [python, str(ROOT / "main_cli.py"), "--config", str(config), "--input", str(dataset),
           "--column", column, "--batch"]
    if limit is not None:
        cmd += ["--limit", str(limit)]
    if not resume:
        cmd.append("--fresh")
    return cmd


def _python() -> str:
    if not VENV_PYTHON.exists():
        raise SystemExit("Falta el entorno. Corre primero: python3 run_lab.py setup")
    return str(VENV_PYTHON)


def _load_key() -> str:
    value = os.environ.get(KEY_ENV, "").strip()
    if value:
        return value
    try:
        lines = SECRETS_ENV.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return ""
    for line in lines:
        if line.strip().startswith(f"{KEY_ENV}="):
            return line.split("=", 1)[1].strip().strip("\"'")
    return ""


def cmd_setup(_args) -> int:
    if not VENV_PYTHON.exists():
        print(f"Creando entorno en {VENV_DIR} ...")
        subprocess.check_call([sys.executable, "-m", "venv", str(VENV_DIR)])
    subprocess.check_call([str(VENV_PYTHON), "-m", "pip", "install", "-q", "-r", str(REQUIREMENTS)])
    print("Entorno listo. Siguiente paso: python3 run_lab.py key")
    return 0


def cmd_key(_args) -> int:
    key = getpass.getpass("Llave de InferNode (sk-saptiva-..., no se muestra): ").strip()
    if not key:
        raise SystemExit("No se recibio ninguna llave.")
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "from pathlib import Path; from labelforge_core import saptiva;"
        "from labelforge_core.secrets_io import set_secret_value;"
        "key = sys.stdin.read().strip();"
        "n = len(saptiva.list_models(key, saptiva.resolve_base_url()));"
        "set_secret_value(Path(sys.argv[2]), sys.argv[3], key);"
        "print(f'Llave verificada y guardada. Modelos disponibles: {n}')"
    )
    # La llave viaja por stdin: no aparece en la lista de procesos ni en el historial.
    return subprocess.run([_python(), "-c", code, str(ROOT), str(SECRETS_ENV), KEY_ENV], input=key, text=True).returncode


def cmd_check(args) -> int:
    key = _load_key()
    if not key:
        raise SystemExit("Falta la llave. Corre: python3 run_lab.py key")
    code = (
        "import json, sys; sys.path.insert(0, sys.argv[1]);"
        "from labelforge_core import saptiva;"
        "from labelforge_core.provider_preflight import validate_provider_preflight as v;"
        "cfg = json.load(open(sys.argv[2], encoding='utf-8'));"
        "issues = v(cfg);"
        "ids = {m['id'] for m in saptiva.list_models(__import__('os').environ['SAPTIVA_API_KEY'], saptiva.resolve_base_url())};"
        "models = [a['provider']['model'] for a in cfg['arbiters']];"
        "issues += [f'Modelo no disponible: {m}' for m in models if m not in ids];"
        "print('Arbitros:', ', '.join(f\"{a['id']}={a['provider']['model']}\" for a in cfg['arbiters']));"
        "print('\\n'.join(issues) if issues else 'Todo listo para correr.');"
        "sys.exit(1 if issues else 0)"
    )
    env = {**os.environ, KEY_ENV: key}
    return subprocess.run([_python(), "-c", code, str(ROOT), str(args.config)], env=env).returncode


def _running_pid() -> int | None:
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def _run_one(dataset: Path, args, manifest: dict) -> bool:
    key = run_key(dataset, args.config, args.column, args.limit)
    done = manifest.get(key)
    if done and not args.force and Path(done["output"]).exists():
        print(f"[{dataset.name}] ya terminado: {done['output']}", flush=True)
        return True

    resume = should_resume(_read_json(RUN_STATE, {}), dataset) and not args.force
    print(f"[{dataset.name}] {'retomando' if resume else 'iniciando'} ...", flush=True)
    started = time.time()
    code = subprocess.call(build_cli_command(_python(), dataset, args.config, args.column, args.limit, resume), cwd=ROOT)
    state = _read_json(RUN_STATE, {})
    if code != 0 or state.get("status") != "completed":
        print(f"[{dataset.name}] NO termino (codigo {code}, estado {state.get('status')}): "
              f"{state.get('pause_kind', '')} {state.get('pause_reason', '')}", flush=True)
        print("Corrige la causa y lanza el mismo comando: retoma donde iba.", flush=True)
        return False

    final = OUTPUT_DIR / f"{dataset.stem}_etiquetado.csv"
    shutil.copy2(state["output_path"], final)
    summary = state.get("summary", {})
    manifest[key] = {"dataset": dataset.name, "output": str(final), "summary": {
        k: summary.get(k) for k in ("processed_rows", "successful_rows", "failed_rows", "skipped_rows", "total_tokens_global")
    }, "minutes": round((time.time() - started) / 60, 1)}
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[{dataset.name}] listo: {final} {manifest[key]['summary']}", flush=True)
    return True


def _chain(args) -> int:
    manifest = _read_json(MANIFEST, {})
    for dataset in args.datasets:
        if not _run_one(dataset, args, manifest):
            print("CADENA DETENIDA", flush=True)
            return 1
    print("CADENA COMPLETA", flush=True)
    return 0


def cmd_run(args) -> int:
    args.datasets = [Path(d).resolve() for d in args.datasets]
    args.config = Path(args.config).resolve()
    missing = [str(d) for d in args.datasets if not d.is_file()]
    if missing:
        raise SystemExit(f"No existen: {', '.join(missing)}")
    if not _load_key():
        raise SystemExit("Falta la llave. Corre: python3 run_lab.py key")
    LOGS_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)

    running = _running_pid()
    if running and running != os.getpid():
        raise SystemExit(f"Ya hay una corrida activa (pid {running}). Usa status o stop: comparten checkpoints.")

    if args.bg:
        forward = [a for a in sys.argv[1:] if a != "--bg"]
        with open(CHAIN_LOG, "a", encoding="utf-8") as log:
            proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), *forward], cwd=ROOT,
                                    stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    start_new_session=True)
        PID_FILE.write_text(str(proc.pid))
        print(f"Corriendo en segundo plano (pid {proc.pid}). Avance: python3 run_lab.py status")
        return 0

    PID_FILE.write_text(str(os.getpid()))
    try:
        return _chain(args)
    finally:
        PID_FILE.unlink(missing_ok=True)


def cmd_status(_args) -> int:
    pid = _running_pid()
    print(f"Proceso: {'activo, pid ' + str(pid) if pid else 'sin corrida activa'}")
    state = _read_json(RUN_STATE, {})
    if state:
        print(f"Ultima corrida: {Path(state.get('input_file', '?')).name} | estado {state.get('status')} "
              f"| filas {state.get('total_rows')} | {state.get('pause_kind', '')} {state.get('pause_reason', '')}".rstrip(" |"))
    for key, job in _read_json(BATCH_STATE, {}).get("jobs", {}).items():
        print(f"  Lote {key}: {job.get('status', 'por crear')} {job.get('completed') or 0}/{job.get('lines')} "
              f"(fallidas {job.get('failed') or 0})")
    for item in _read_json(MANIFEST, {}).values():
        print(f"Terminado: {item['dataset']} -> {item['output']} ({item['minutes']} min) {item['summary']}")
    if CHAIN_LOG.exists():
        print("--- ultimas lineas ---")
        print("\n".join(CHAIN_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-8:]))
    return 0


def cmd_stop(_args) -> int:
    pid = _running_pid()
    if not pid:
        print("No hay corrida activa.")
        return 0
    os.killpg(os.getpgid(pid), signal.SIGTERM)
    PID_FILE.unlink(missing_ok=True)
    print(f"Detenida la corrida {pid}. Los lotes siguen en el servidor; relanza el mismo comando para retomarlos.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LabelForge en el Laboratorio de IA IBERO")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup", help="Crear .venv e instalar dependencias").set_defaults(func=cmd_setup)
    sub.add_parser("key", help="Guardar la llave de InferNode (prompt oculto)").set_defaults(func=cmd_key)
    check = sub.add_parser("check", help="Validar llave, modelos y config")
    check.add_argument("--config", default=str(DEFAULT_CONFIG))
    check.set_defaults(func=cmd_check)
    run = sub.add_parser("run", help="Etiquetar uno o varios archivos con la API de lotes")
    run.add_argument("datasets", nargs="+", help="Archivos CSV o Excel, se procesan en serie")
    run.add_argument("--column", default="txt", help="Columna con el texto (por defecto: txt)")
    run.add_argument("--config", default=str(DEFAULT_CONFIG))
    run.add_argument("--limit", type=int, default=None, help="Procesar solo las primeras N filas")
    run.add_argument("--force", action="store_true", help="Repetir aunque ya este terminado")
    run.add_argument("--bg", action="store_true", help="Seguir corriendo aunque se cierre el navegador")
    run.set_defaults(func=cmd_run)
    sub.add_parser("status", help="Ver avance y resultados").set_defaults(func=cmd_status)
    sub.add_parser("stop", help="Detener la corrida local").set_defaults(func=cmd_stop)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

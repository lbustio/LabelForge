"""
main_tui.py  —  Interfaz interactiva estilo TUI para LabelForge.

Navegación por menús numerados, sin flags de línea de comandos.
Requiere: pip install rich  (incluido en requirements.txt)

Uso:
    python main_tui.py
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── Bootstrap de entorno (igual que main_cli.py) ──────────────────────────
from dotenv import load_dotenv
from labelforge_core.secrets_io import resolve_gcp_credentials, secrets_env_path
from labelforge_core.utils import ensure_project_dirs
from labelforge_core.paths import (
    ARBITERS_CONFIG_PATH,
    CHECKPOINTS_DIR,
    OUTPUT_DIR,
    LOGS_DIR,
    MODELS_DIR,
)
from labelforge_core.checkpoint_io import (
    CHECKPOINT_PATH,
    clear_runtime_checkpoints,
    load_run_state,
)

load_dotenv(secrets_env_path(ROOT))
resolve_gcp_credentials(ROOT)
ensure_project_dirs(ROOT)
logging.basicConfig(level=logging.WARNING)

# ── Rich ──────────────────────────────────────────────────────────────────
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.rule import Rule
    from rich.prompt import Prompt, Confirm
    from rich.progress import (
        Progress, BarColumn, DownloadColumn,
        TransferSpeedColumn, TimeRemainingColumn,
    )
    from rich import box as rbox
except ImportError as _e:
    sys.exit(
        "ERROR: 'rich' no está instalado.\n"
        "Ejecuta:  pip install rich\n"
        f"Detalle: {_e}"
    )

console = Console()
VERSION = "1.0"


# ─── utilidades ──────────────────────────────────────────────────────────

def _clear():
    os.system("cls" if os.name == "nt" else "clear")


def _header():
    console.print(Panel(
        f"[bold cyan]LabelForge[/]  [dim]v{VERSION}  ·  Plataforma de etiquetado LLM[/]",
        box=rbox.DOUBLE_EDGE,
    ))


def _pause():
    try:
        console.print()
        console.input("  [dim][Presiona Enter para continuar…][/]")
    except (EOFError, KeyboardInterrupt):
        pass


def _ask_option(valid: set[str]) -> str:
    ordered = sorted(valid, key=lambda x: (x == "0", x))
    hint = "/".join(ordered)
    while True:
        try:
            sel = Prompt.ask(f"[bold yellow]  Opción[/] [dim]({hint})[/]").strip()
        except (EOFError, KeyboardInterrupt):
            return "0"
        if sel in valid:
            return sel
        console.print(f"  [red]Opción no válida. Elige: {hint}[/]")


def _menu(title: str, options: list[tuple[str, str]]) -> str:
    """Muestra menú numerado y devuelve la clave elegida."""
    _clear()
    _header()
    console.print()
    console.print(f"  [bold]{title}[/]")
    console.print()
    for key, label in options:
        if key == "0":
            console.print(f"    [dim][0]  {label}[/]")
        else:
            console.print(f"    [bold cyan][{key}][/]  {label}")
    console.print()
    return _ask_option({k for k, _ in options})


# ─── helpers de catálogo ─────────────────────────────────────────────────

_CATALOG_CACHE_PATH = MODELS_DIR / "catalog_cache.json"
_CATALOG_TTL_S = 7 * 24 * 3600


def _catalog_status() -> str:
    """Devuelve una nota sobre el origen del catálogo de modelos."""
    if not _CATALOG_CACHE_PATH.exists():
        return "[dim]Sin caché — se intentará consultar LLMs (puede tardar)[/]"
    try:
        data = json.loads(_CATALOG_CACHE_PATH.read_text(encoding="utf-8"))
        age_s = time.time() - data.get("timestamp", 0)
        if age_s <= _CATALOG_TTL_S:
            age_h = int(age_s / 3600)
            return f"[dim]Catálogo en caché (hace {age_h}h)[/]"
        return "[dim]Caché vencido — se intentará consultar LLMs (puede tardar)[/]"
    except Exception:
        return "[dim]Sin caché — se intentará consultar LLMs (puede tardar)[/]"


# ─── sección: hardware y modelos ─────────────────────────────────────────

def _show_hw_report():
    _clear()
    _header()
    console.print(Rule("[bold]Informe de hardware[/]"))
    console.print("\n  [dim]Detectando hardware…[/]")

    from labelforge_core.hw_detection import detect_all
    p = detect_all()
    cpu = p.get("cpu", {})
    gpu = p.get("gpu", {})

    t = Table(box=rbox.SIMPLE, show_header=False, padding=(0, 2))
    t.add_column("Campo", style="cyan", min_width=24, no_wrap=True)
    t.add_column("Valor")

    t.add_row("Sistema operativo", p.get("os", "?"))
    t.add_row("Python", p.get("python_version", "?"))
    t.add_row("CPU", f"{cpu.get('vendor', '?')}  –  {cpu.get('model', '?')}")
    t.add_row("Núcleos físicos", str(cpu.get("cores_physical", "?")))
    t.add_row("AVX2", "[green]✓[/]" if cpu.get("avx2") else "[dim]✗[/]")
    t.add_row("AVX-512", "[green]✓[/]" if cpu.get("avx512") else "[dim]✗[/]")
    t.add_row("RAM", f"{p.get('ram_gb', '?')} GB")

    if gpu.get("detected"):
        vram = f"{gpu['vram_gb']} GB" if gpu.get("vram_gb") else "compartida"
        shared = "  [dim](memoria compartida)[/]" if gpu.get("shared_memory") else ""
        t.add_row("GPU", f"{gpu.get('name', '?')}  –  VRAM: {vram}{shared}")
    else:
        t.add_row("GPU", "[dim]No detectada[/]")

    t.add_row("Backend recomendado", f"[bold]{p.get('best_backend', '?')}[/]")

    console.print()
    console.print(t)
    _pause()


def _models_status_table(models: list[dict], cached_names: set[str]) -> Table:
    t = Table(box=rbox.SIMPLE_HEAD, show_header=True, padding=(0, 1))
    t.add_column("#", justify="right", style="dim", width=3)
    t.add_column("Archivo", style="cyan", no_wrap=True)
    t.add_column("Tamaño", justify="right", width=9)
    t.add_column("Estado")
    for i, m in enumerate(models, 1):
        fn = m.get("filename", "?")
        size = f"{m.get('size_gb', '?')} GB"
        if fn in cached_names:
            estado = "[green]✓ descargado[/]"
        elif m.get("fits_in_memory") is False:
            estado = "[red]no cabe en memoria[/]"
        else:
            estado = "[yellow]pendiente[/]"
        t.add_row(str(i), fn, size, estado)
    return t


def _show_recommended(hw_profile: dict | None):
    _clear()
    _header()
    console.print(Rule("[bold]Modelos recomendados para tu hardware[/]"))
    console.print(f"\n  {_catalog_status()}")

    from labelforge_core.local_models import recommended_models, list_cached_models
    models = recommended_models(hw_profile)
    cached = {m["filename"] for m in list_cached_models()}

    console.print()
    console.print(_models_status_table(models, cached))
    _pause()


def _download_models(hw_profile: dict | None):
    from labelforge_core.local_models import recommended_models, list_cached_models, download_model

    while True:
        _clear()
        _header()
        console.print(Rule("[bold]Descargar modelos[/]"))
        console.print(f"\n  {_catalog_status()}\n")

        models = recommended_models(hw_profile)
        cached = {m["filename"] for m in list_cached_models()}
        pending = [m for m in models if m.get("filename") not in cached]

        if not pending:
            console.print("  [green]Todos los modelos recomendados ya están descargados.[/]")
            _pause()
            return

        console.print(f"  Modelos pendientes: [bold]{len(pending)}[/]\n")
        for i, m in enumerate(pending, 1):
            fn = m.get("filename", "?")
            size = m.get("size_gb", "?")
            fits = m.get("fits_in_memory")
            fits_tag = (
                "  [dim](cabe en memoria)[/]" if fits is True
                else "  [red](puede no caber)[/]" if fits is False
                else ""
            )
            console.print(f"    [bold cyan][{i}][/]  {fn}  [dim]{size} GB[/]{fits_tag}")
            if desc := m.get("description"):
                console.print(f"         [dim]{desc}[/]")

        console.print()
        console.print("    [dim][0]  Volver sin descargar[/]")
        console.print()

        valid = {str(i) for i in range(1, len(pending) + 1)} | {"0"}
        sel = _ask_option(valid)
        if sel == "0":
            return

        m = pending[int(sel) - 1]
        repo_id = m["repo_id"]
        filename = m["filename"]
        size_gb = m.get("size_gb", "?")

        if not Confirm.ask(f"\n  ¿Descargar [cyan]{filename}[/] ([dim]{size_gb} GB[/])?"):
            continue

        _clear()
        _header()
        console.print(Rule(f"[bold]Descargando: {filename}[/]"))
        console.print()

        _last_total: list[int] = [0]

        with Progress(
            "[progress.description]{task.description}",
            BarColumn(),
            "[progress.percentage]{task.percentage:>3.0f}%",
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(filename[:60], total=None)

            def _cb(downloaded: int, total: int):
                if total and total != _last_total[0]:
                    progress.update(task, total=total)
                    _last_total[0] = total
                progress.update(task, completed=downloaded)

            try:
                dest = download_model(
                    repo_id=repo_id,
                    filename=filename,
                    progress_callback=_cb,
                    description=m.get("description", ""),
                )
                console.print(f"\n  [green]✓ Guardado en {dest}[/]")
            except KeyboardInterrupt:
                console.print("\n  [yellow]Descarga cancelada por el usuario.[/]")
            except Exception as exc:
                console.print(f"\n  [red]Error durante la descarga: {exc}[/]")

        _pause()


def _show_cached():
    _clear()
    _header()
    console.print(Rule("[bold]Modelos descargados[/]"))

    from labelforge_core.local_models import list_cached_models
    models = list_cached_models()

    if not models:
        console.print(f"\n  [dim]No hay archivos .gguf en {ROOT / 'models'}[/]")
    else:
        t = Table(box=rbox.SIMPLE_HEAD, padding=(0, 1))
        t.add_column("#", justify="right", style="dim", width=3)
        t.add_column("Archivo", style="cyan", no_wrap=True)
        t.add_column("Tamaño", justify="right")
        t.add_column("Capas", justify="right")
        for i, m in enumerate(models, 1):
            t.add_row(str(i), m["filename"], f"{m['size_gb']} GB", str(m.get("n_layers") or "?"))
        console.print()
        console.print(t)

    _pause()


def _section_models(hw_profile: dict | None):
    while True:
        sel = _menu("Hardware y modelos", [
            ("1", "Ver informe de hardware"),
            ("2", "Ver modelos recomendados"),
            ("3", "Descargar modelos pendientes"),
            ("4", "Ver modelos descargados"),
            ("0", "Volver al menú principal"),
        ])
        if sel == "0":
            return
        elif sel == "1":
            _show_hw_report()
        elif sel == "2":
            _show_recommended(hw_profile)
        elif sel == "3":
            _download_models(hw_profile)
        elif sel == "4":
            _show_cached()


# ─── sección: árbitros ────────────────────────────────────────────────────

def _show_arbiters():
    _clear()
    _header()
    console.print(Rule("[bold]Árbitros configurados[/]"))

    try:
        cfg = json.loads(ARBITERS_CONFIG_PATH.read_text(encoding="utf-8"))
        arbiters = cfg.get("arbiters", [])
    except Exception as exc:
        console.print(f"\n  [red]Error leyendo config: {exc}[/]")
        _pause()
        return

    if not arbiters:
        console.print("\n  [yellow]No hay árbitros configurados.[/]")
    else:
        t = Table(box=rbox.SIMPLE_HEAD, padding=(0, 1))
        t.add_column("#", justify="right", style="dim", width=3)
        t.add_column("Nombre · Persona", style="cyan")
        t.add_column("ID", style="dim")
        t.add_column("Proveedor")
        t.add_column("Modelo / Config")

        for i, arb in enumerate(arbiters, 1):
            name = arb.get("name", "?")
            persona = arb.get("persona", "")
            label = f"{name} · {persona}" if persona else name
            prov = arb.get("provider", {})
            model = prov.get("model") or prov.get("base_url") or "—"
            t.add_row(str(i), label, arb.get("id", "?"), prov.get("type", "?"), model)

        console.print()
        console.print(t)

    console.print()
    console.print(f"  [dim]Archivo de configuración:[/]  [cyan]{ARBITERS_CONFIG_PATH}[/]")
    console.print("  [dim]Edita el archivo JSON manualmente para modificar los árbitros.[/]")
    _pause()


def _open_arbiters_editor():
    _clear()
    _header()
    console.print(Rule("[bold]Abrir config de árbitros[/]"))
    path = str(ARBITERS_CONFIG_PATH)
    try:
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.run(["open", path], check=False)
        else:
            editor = os.environ.get("EDITOR", "nano")
            subprocess.run([editor, path], check=False)
        console.print(f"\n  [green]Abriendo:[/]  {path}")
    except Exception as exc:
        console.print(f"\n  [red]No se pudo abrir el editor: {exc}[/]")
        console.print(f"  Edita manualmente:  [cyan]{path}[/]")
    _pause()


def _section_arbiters():
    while True:
        sel = _menu("Árbitros", [
            ("1", "Ver árbitros configurados"),
            ("2", "Abrir config en editor del sistema"),
            ("0", "Volver al menú principal"),
        ])
        if sel == "0":
            return
        elif sel == "1":
            _show_arbiters()
        elif sel == "2":
            _open_arbiters_editor()


# ─── sección: etiquetado ─────────────────────────────────────────────────

def _has_checkpoint() -> bool:
    return CHECKPOINT_PATH.exists()


def _checkpoint_summary() -> str:
    state = load_run_state()
    if not state:
        return ""
    done = state.get("completed_rows", "?")
    total = state.get("total_rows", "?")
    run_id = state.get("run_id", "")
    return f"{done}/{total} entradas — run {run_id}"


def _run_labeling(limit: int | None = None, fresh: bool = False):
    _clear()
    _header()
    parts: list[str] = []
    if fresh:
        parts.append("inicio limpio")
    elif _has_checkpoint():
        parts.append("reanudando checkpoint")
    if limit:
        parts.append(f"límite: {limit}")
    subtitle = " · ".join(parts) if parts else "dataset completo"
    console.print(Rule(f"[bold]Etiquetado  —  {subtitle}[/]"))
    console.print()

    cmd = [sys.executable, str(ROOT / "main_cli.py")]
    if limit:
        cmd += ["--limit", str(limit)]
    if fresh:
        cmd += ["--fresh"]

    console.print(f"  [dim]$ {' '.join(cmd)}[/]\n")
    console.print(Rule(style="dim"))
    console.print()

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="", flush=True)
        proc.wait()
        console.print()
        console.print(Rule(style="dim"))
        if proc.returncode == 0:
            console.print("\n  [green]✓ Proceso completado.[/]")
        else:
            console.print(f"\n  [red]Proceso terminó con código {proc.returncode}.[/]")
    except KeyboardInterrupt:
        try:
            proc.terminate()
        except Exception:
            pass
        console.print("\n  [yellow]Proceso interrumpido por el usuario.[/]")

    _pause()


def _clear_checkpoint_confirm():
    _clear()
    _header()
    console.print(Rule("[bold]Limpiar checkpoint[/]"))
    summary = _checkpoint_summary()
    if summary:
        console.print(f"\n  Checkpoint activo: [cyan]{summary}[/]")
    console.print(
        "\n  [dim]Esto elimina los archivos de checkpoint, cola de fallos y "
        "resultados parciales.\n  La próxima ejecución empezará desde el principio.[/]\n"
    )
    if Confirm.ask("  ¿Confirmar limpieza?"):
        cleared = clear_runtime_checkpoints(include_claim_results=True, include_run_state=True)
        if cleared:
            console.print(f"\n  [green]Eliminados: {', '.join(cleared)}[/]")
        else:
            console.print("\n  [dim]No había archivos de checkpoint.[/]")
    _pause()


def _ask_limit() -> int | None:
    try:
        raw = Prompt.ask(
            "\n  [bold]Número de entradas a procesar[/]  [dim](Enter = todas)[/]"
        ).strip()
        if not raw:
            return None
        n = int(raw)
        return n if n > 0 else None
    except (ValueError, EOFError, KeyboardInterrupt):
        return None


def _section_labeling():
    while True:
        has_cp = _has_checkpoint()
        cp_hint = _checkpoint_summary()

        opts: list[tuple[str, str]] = [
            ("1", "Iniciar — dataset completo"),
            ("2", "Iniciar con límite de entradas"),
        ]
        if has_cp:
            resume_label = (
                f"Reanudar checkpoint  [dim]({cp_hint})[/]" if cp_hint
                else "Reanudar checkpoint"
            )
            opts.append(("3", resume_label))
            opts.append(("4", "Limpiar checkpoint"))
        opts.append(("0", "Volver al menú principal"))

        sel = _menu("Etiquetado", opts)
        if sel == "0":
            return
        elif sel == "1":
            if Confirm.ask("\n  ¿Iniciar etiquetado del dataset completo?"):
                _run_labeling()
        elif sel == "2":
            lim = _ask_limit()
            if lim is not None:
                _run_labeling(limit=lim)
        elif sel == "3":
            _run_labeling()
        elif sel == "4":
            _clear_checkpoint_confirm()


# ─── sección: resultados ─────────────────────────────────────────────────

def _show_outputs():
    _clear()
    _header()
    console.print(Rule("[bold]Outputs disponibles[/]"))

    outputs = sorted(OUTPUT_DIR.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)

    if not outputs:
        console.print(f"\n  [dim]No hay archivos CSV en {OUTPUT_DIR}[/]")
    else:
        t = Table(box=rbox.SIMPLE_HEAD, padding=(0, 1))
        t.add_column("#", justify="right", style="dim", width=3)
        t.add_column("Archivo", style="cyan")
        t.add_column("Tamaño", justify="right")
        t.add_column("Fecha")
        for i, p in enumerate(outputs, 1):
            stat = p.stat()
            size = f"{stat.st_size / 1024:.0f} KB"
            mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d  %H:%M")
            t.add_row(str(i), p.name, size, mtime)
        console.print()
        console.print(t)
        console.print(f"\n  [dim]Directorio: {OUTPUT_DIR}[/]")

    _pause()


def _show_last_log():
    _clear()
    _header()
    console.print(Rule("[bold]Últimas líneas del log[/]"))

    logs = sorted(LOGS_DIR.glob("run_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not logs:
        console.print("\n  [dim]No hay logs disponibles.[/]")
        _pause()
        return

    last = logs[0]
    console.print(f"\n  [dim]{last}[/]\n")
    try:
        text = last.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        tail = lines[-60:] if len(lines) > 60 else lines
        for line in tail:
            console.print(f"  [dim]{line}[/]")
    except Exception as exc:
        console.print(f"  [red]Error leyendo log: {exc}[/]")

    _pause()


def _section_results():
    while True:
        sel = _menu("Resultados", [
            ("1", "Listar outputs CSV"),
            ("2", "Ver últimas líneas del log"),
            ("0", "Volver al menú principal"),
        ])
        if sel == "0":
            return
        elif sel == "1":
            _show_outputs()
        elif sel == "2":
            _show_last_log()


# ─── main loop ────────────────────────────────────────────────────────────

def main():
    hw_profile: dict | None = None

    while True:
        has_cp = _has_checkpoint()
        cp_label = (
            "Etiquetado  [dim](hay checkpoint activo)[/]" if has_cp
            else "Etiquetado"
        )
        sel = _menu("Menú principal", [
            ("1", "Hardware y modelos"),
            ("2", cp_label),
            ("3", "Árbitros"),
            ("4", "Resultados"),
            ("5", "Salir"),
        ])

        if sel == "1":
            if hw_profile is None:
                from labelforge_core.hw_detection import detect_all
                hw_profile = detect_all()
            _section_models(hw_profile)
        elif sel == "2":
            _section_labeling()
        elif sel == "3":
            _section_arbiters()
        elif sel == "4":
            _section_results()
        elif sel == "5":
            _clear()
            console.print("\n  [dim]Hasta luego.[/]\n")
            sys.exit(0)


if __name__ == "__main__":
    main()

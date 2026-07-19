"""
dataset_io.py
-------------
Autoridad compartida para cargar e inspeccionar datasets CSV/Excel.

La webapp conserva su politica de permisos HTTP/uploads; este modulo solo
centraliza formatos, sheets, columnas y conversion de filas.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from labelforge_core.paths import BASE_DIR


CSV_SUFFIXES = {".csv"}
EXCEL_SUFFIXES = {".xlsx", ".xls", ".xlsm"}
SUPPORTED_DATASET_SUFFIXES = CSV_SUFFIXES | EXCEL_SUFFIXES


class DatasetError(ValueError):
    """Error recuperable de dataset para CLI/webapp."""


def is_supported_dataset_path(path: Path, suffixes: Iterable[str] | None = None) -> bool:
    allowed = set(suffixes or SUPPORTED_DATASET_SUFFIXES)
    return path.suffix.lower() in allowed


def _format_suffixes(suffixes: Iterable[str] | None = None) -> str:
    return ", ".join(sorted(set(suffixes or SUPPORTED_DATASET_SUFFIXES)))


def resolve_dataset_path(
    raw_path: str | Path | None,
    *,
    base_dir: Path = BASE_DIR,
    allowed_root: Path | None = None,
    suffixes: Iterable[str] | None = None,
) -> Path:
    raw_path = str(raw_path or "").strip()
    if not raw_path:
        raise DatasetError("El archivo de entrada es obligatorio")

    path = Path(raw_path)
    if not path.is_absolute():
        path = base_dir / path
    path = path.resolve()

    if allowed_root is not None:
        try:
            path.relative_to(allowed_root.resolve())
        except ValueError as exc:
            raise PermissionError("La ruta del dataset debe estar dentro de data/") from exc

    if not is_supported_dataset_path(path, suffixes):
        raise DatasetError(f"Formato no soportado. Usa {_format_suffixes(suffixes)}.")
    return path


def _open_workbook(path: Path):
    try:
        import openpyxl  # noqa: PLC0415
    except ImportError as exc:
        raise DatasetError("openpyxl no esta instalado. Ejecuta: pip install openpyxl") from exc
    return openpyxl.load_workbook(str(path), read_only=True, data_only=True)


def _resolve_excel_sheet(workbook, sheet: str | int | None):
    sheet_names = workbook.sheetnames
    if sheet in ("", None):
        ws = workbook.active
        return ws, ws.title

    sheet_value = str(sheet)
    if sheet_value in sheet_names:
        return workbook[sheet_value], sheet_value

    try:
        idx = int(sheet_value)
        resolved = sheet_names[idx]
        return workbook[resolved], resolved
    except (ValueError, IndexError) as exc:
        raise DatasetError(
            f"Sheet '{sheet_value}' no encontrado. Sheets disponibles: {sheet_names}"
        ) from exc


def _normalize_headers(values) -> list[str]:
    return ["" if value is None else str(value).strip() for value in values]


def _excel_rows(path: Path, sheet: str | int | None = "") -> tuple[list[dict], list[str], list[str], str]:
    wb = _open_workbook(path)
    try:
        ws, resolved_sheet = _resolve_excel_sheet(wb, sheet)
        iterator = ws.iter_rows(values_only=True)
        try:
            headers = _normalize_headers(next(iterator))
        except StopIteration as exc:
            raise DatasetError("El archivo Excel esta vacio.") from exc

        rows = []
        for values in iterator:
            row = {}
            for idx, header in enumerate(headers):
                if not header:
                    continue
                value = values[idx] if idx < len(values) else None
                row[header] = "" if value is None else str(value)
            rows.append(row)
        return rows, [header for header in headers if header], list(wb.sheetnames), resolved_sheet
    finally:
        wb.close()


def _csv_rows(path: Path) -> tuple[list[dict], list[str]]:
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames or [])


def load_dataset(
    input_path: str | Path,
    sheet: str | int | None = "",
    text_column: str | None = "claim",
) -> tuple[list[dict], list[str]]:
    path = Path(input_path)
    suffix = path.suffix.lower()

    if suffix in EXCEL_SUFFIXES:
        rows, fieldnames, _sheets, _resolved_sheet = _excel_rows(path, sheet)
    elif suffix in CSV_SUFFIXES:
        rows, fieldnames = _csv_rows(path)
    else:
        raise DatasetError(f"Formato de archivo no soportado: '{suffix}'. Use {_format_suffixes()}.")

    if text_column and text_column not in fieldnames:
        raise DatasetError(
            f"La columna '{text_column}' no existe en el dataset. "
            f"Columnas disponibles: {fieldnames}"
        )
    return rows, fieldnames


def profile_dataset(
    input_path: str | Path,
    sheet: str | int | None = "",
    max_preview_rows: int = 8,
    max_stats_rows: int = 10_000,
) -> dict:
    """
    Devuelve un perfil rico del dataset: forma, tipos de columna, tasa de relleno,
    valores de muestra, estadisticas numericas y una vista previa de las primeras filas.
    Equivalente a combinar df.info(), df.describe() y df.head().
    """
    path = Path(input_path)
    suffix = path.suffix.lower()

    if suffix in EXCEL_SUFFIXES:
        rows, fieldnames, sheets, resolved_sheet = _excel_rows(path, sheet)
        is_excel = True
    elif suffix in CSV_SUFFIXES:
        rows, fieldnames = _csv_rows(path)
        sheets, resolved_sheet = [], ""
        is_excel = False
    else:
        raise DatasetError(f"Formato no soportado: '{suffix}'. Usa {_format_suffixes()}.")

    n_rows = len(rows)
    stat_rows = rows[:max_stats_rows]

    columns: list[dict] = []
    for col in fieldnames:
        values = [r.get(col) for r in stat_rows]
        non_null = sum(1 for v in values if v not in (None, ""))
        non_empty = sum(1 for v in values if str(v or "").strip())

        numeric_vals: list[float] = []
        for v in values:
            if v not in (None, ""):
                try:
                    numeric_vals.append(float(v))
                except (ValueError, TypeError):
                    pass

        is_numeric = bool(numeric_vals) and (len(numeric_vals) / max(non_null, 1)) >= 0.8
        col_type = "number" if is_numeric else "text"

        stats: dict | None = None
        if is_numeric:
            nv = sorted(numeric_vals)
            n = len(nv)
            mean = sum(nv) / n
            std = (sum((x - mean) ** 2 for x in nv) / n) ** 0.5 if n > 1 else 0.0

            def _pct(lst: list[float], p: float) -> float:
                i = min(int(p * len(lst)), len(lst) - 1)
                return round(lst[i], 4)

            stats = {
                "count": n,
                "mean": round(mean, 4),
                "std": round(std, 4),
                "min": round(nv[0], 4),
                "q25": _pct(nv, 0.25),
                "q50": _pct(nv, 0.50),
                "q75": _pct(nv, 0.75),
                "max": round(nv[-1], 4),
            }

        sample: list[str] = []
        seen: set[str] = set()
        for v in values:
            sv = str(v).strip() if v not in (None, "") else ""
            if sv and sv not in seen:
                seen.add(sv)
                sample.append(sv[:120])
            if len(sample) >= 3:
                break

        columns.append({
            "name": col,
            "type": col_type,
            "non_null": non_null,
            "non_empty": non_empty,
            "null_count": len(stat_rows) - non_null,
            "empty_count": len(stat_rows) - non_empty,
            "fill_pct": round(non_empty / len(stat_rows) * 100, 1) if stat_rows else 0,
            "sample": sample,
            "stats": stats,
        })

    preview = [
        {col: str(row.get(col) or "")[:200] for col in fieldnames}
        for row in rows[:max_preview_rows]
    ]

    return {
        "n_rows": n_rows,
        "n_cols": len(fieldnames),
        "fieldnames": fieldnames,
        "is_excel": is_excel,
        "sheets": sheets,
        "resolved_sheet": resolved_sheet,
        "columns": columns,
        "preview": preview,
        "truncated_stats": n_rows > max_stats_rows,
    }


def inspect_dataset(input_path: str | Path, sheet: str | int | None = "") -> dict:
    path = Path(input_path)
    suffix = path.suffix.lower()

    if suffix in EXCEL_SUFFIXES:
        wb = _open_workbook(path)
        try:
            ws, _resolved_sheet = _resolve_excel_sheet(wb, sheet)
            iterator = ws.iter_rows(values_only=True)
            try:
                fieldnames = [header for header in _normalize_headers(next(iterator)) if header]
            except StopIteration:
                fieldnames = []
            return {"is_excel": True, "sheets": list(wb.sheetnames), "columns": fieldnames}
        finally:
            wb.close()
    if suffix in CSV_SUFFIXES:
        with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
            reader = csv.DictReader(f)
            return {"is_excel": False, "sheets": [], "columns": list(reader.fieldnames or [])}
    raise DatasetError(f"Formato no soportado: '{suffix}'. Usa {_format_suffixes()}.")

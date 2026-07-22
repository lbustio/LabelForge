"""Registro durable y tipado de una ejecucion terminada."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def validate_run_id(run_id: str) -> str:
    value = str(run_id or "")
    safe = "".join(ch for ch in value if ch.isalnum() or ch in {"-", "_"})
    if not safe or safe != value:
        raise ValueError("run_id invalido")
    return value


_FORBIDDEN_SECRET_KEYS = ("api_key", "password", "secret", "credential", "access_token", "refresh_token")


def _contains_secret_field(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(marker in normalized for marker in _FORBIDDEN_SECRET_KEYS):
                return True
            if _contains_secret_field(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_secret_field(item) for item in value)
    return False


@dataclass(frozen=True, slots=True)
class RunOwner:
    user_id: int
    username: str

    def __post_init__(self) -> None:
        if self.user_id <= 0:
            raise ValueError("user_id invalido")
        if not self.username:
            raise ValueError("username requerido")


@dataclass(frozen=True, slots=True)
class RunRecord:
    run_id: str
    status: str
    schema_version: int = 1
    created_at: str | None = None
    completed_at: str | None = None
    updated_at: str | None = None
    input_file: str | None = None
    sheet: str = ""
    text_column: str = ""
    output_file: str | None = None
    total_rows: int = 0
    dataset_total_rows: int = 0
    eligible_rows: int = 0
    execution_order: str = "row_major"
    arbiters: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    principles: tuple[str, ...] = field(default_factory=tuple)
    summary: dict[str, Any] = field(default_factory=dict)
    configuration: dict[str, Any] = field(default_factory=dict)
    owner: RunOwner | None = None
    legacy: bool = False

    def __post_init__(self) -> None:
        validate_run_id(self.run_id)
        if not self.status:
            raise ValueError("status requerido")
        if self.output_file and Path(self.output_file).name != self.output_file:
            raise ValueError("output_file debe ser un nombre, no una ruta")
        for field_name in ("total_rows", "dataset_total_rows", "eligible_rows"):
            if getattr(self, field_name) < 0:
                raise ValueError(f"{field_name} no puede ser negativo")
        if _contains_secret_field((self.configuration, self.arbiters, asdict(self.owner) if self.owner else None)):
            raise ValueError("el registro durable no puede contener credenciales")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunRecord":
        return cls(
            run_id=str(data.get("run_id", "") or ""),
            status=str(data.get("status", "") or ""),
            schema_version=int(data.get("schema_version", 1)),
            created_at=data.get("created_at"),
            completed_at=data.get("completed_at"),
            updated_at=data.get("updated_at"),
            input_file=data.get("input_file"),
            sheet=str(data.get("sheet", "") or ""),
            text_column=str(data.get("text_column", "") or ""),
            output_file=data.get("output_file"),
            total_rows=int(data.get("total_rows", 0) or 0),
            dataset_total_rows=int(data.get("dataset_total_rows", data.get("total_rows", 0)) or 0),
            eligible_rows=int(data.get("eligible_rows", 0) or 0),
            execution_order=str(data.get("execution_order", "row_major") or "row_major"),
            arbiters=tuple(dict(item) for item in data.get("arbiters", []) if isinstance(item, dict)),
            principles=tuple(str(item) for item in data.get("principles", [])),
            summary=dict(data.get("summary") or {}),
            configuration=dict(data.get("configuration") or {}),
            owner=(
                RunOwner(
                    user_id=int(data["owner"].get("user_id") or 0),
                    username=str(data["owner"].get("username") or ""),
                )
                if isinstance(data.get("owner"), dict) and data["owner"].get("user_id")
                else None
            ),
            legacy=bool(data.get("legacy", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["arbiters"] = [dict(item) for item in self.arbiters]
        data["principles"] = list(self.principles)
        data["owner"] = asdict(self.owner) if self.owner else None
        return data

"""Almacen thread-safe para la proyeccion mutable de metricas de una ejecucion."""

from __future__ import annotations

import copy
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator


@dataclass(slots=True)
class MetricsEditor:
    stats: dict

    def replace(self, stats: dict) -> dict:
        self.stats = stats
        return stats


class InMemoryRunMetrics:
    """Encapsula sincronizacion, reemplazo y lectura segura de la proyeccion."""

    def __init__(self, empty_factory: Callable[[], dict]) -> None:
        self._empty_factory = empty_factory
        self._lock = threading.RLock()
        self._stats = empty_factory()

    @contextmanager
    def edit(self) -> Iterator[MetricsEditor]:
        with self._lock:
            editor = MetricsEditor(self._stats)
            yield editor
            self._stats = editor.stats

    def reset(self) -> None:
        with self._lock:
            self._stats = self._empty_factory()

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._stats)

    def has_run(self) -> bool:
        with self._lock:
            return bool(self._stats.get("run_id"))

    def replace_if_empty(self, stats: dict) -> bool:
        with self._lock:
            if self._stats.get("run_id"):
                return False
            self._stats = stats
            return True

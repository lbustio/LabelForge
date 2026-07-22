"""Supervisor thread-safe para el proceso local iniciado por la web."""

from __future__ import annotations

import threading
from typing import Any

from labelforge_core.application.ports import RunRuntimeSnapshot, StopRequest


class InMemoryRunSupervisor:
    def __init__(self, *, max_log_lines: int = 500) -> None:
        self._lock = threading.RLock()
        self._max_log_lines = max_log_lines
        self._reset_unlocked()

    def _reset_unlocked(self) -> None:
        self._running = False
        self._stop_requested = False
        self._process: Any | None = None
        self._pid: int | None = None
        self._exit_code: int | str | None = None
        self._last_error = ""
        self._run_log_path: str | None = None
        self._log_lines: list[str] = []
        self._log_seq = 0

    def claim_start(self) -> bool:
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._stop_requested = False
            return True

    def abort_start(self) -> None:
        with self._lock:
            self._running = False
            self._process = None
            self._pid = None

    def reset_for_run(self, log_path: str) -> None:
        with self._lock:
            self._run_log_path = log_path
            self._log_lines = []
            self._log_seq = 0
            self._exit_code = None
            self._last_error = ""

    def attach_process(self, process: Any) -> bool:
        with self._lock:
            self._process = process
            self._pid = process.pid
            return self._stop_requested

    def request_stop(self) -> StopRequest:
        with self._lock:
            if not self._running:
                return StopRequest(accepted=False)
            self._stop_requested = True
            return StopRequest(accepted=True, process=self._process)

    def record_exit(self, exit_code: int) -> None:
        with self._lock:
            self._exit_code = "STOPPED" if self._stop_requested else exit_code

    def record_error(self, public_message: str) -> None:
        with self._lock:
            self._exit_code = "ERROR"
            self._last_error = public_message

    def finish(self) -> bool:
        with self._lock:
            was_stopped = self._stop_requested
            self._running = False
            self._process = None
            return was_stopped

    def append_log(self, line: str) -> None:
        with self._lock:
            self._log_lines.append(line)
            self._log_seq += 1
            if len(self._log_lines) > self._max_log_lines:
                self._log_lines = self._log_lines[-self._max_log_lines:]

    def snapshot(self, since: int = 0) -> RunRuntimeSnapshot:
        with self._lock:
            base_seq = max(0, self._log_seq - len(self._log_lines))
            start = max(0, int(since or 0) - base_seq)
            lines = tuple(self._log_lines[start:])
            first_seq = base_seq + start + 1
            entries = tuple((first_seq + offset, line) for offset, line in enumerate(lines))
            return RunRuntimeSnapshot(
                running=self._running,
                stop_requested=self._stop_requested,
                pid=self._pid,
                exit_code=self._exit_code,
                last_error=self._last_error,
                run_log_path=self._run_log_path,
                seq=self._log_seq,
                lines=lines,
                log_entries=entries,
            )

    def is_active(self) -> bool:
        with self._lock:
            return self._running

    def reset(self) -> None:
        with self._lock:
            if self._running:
                raise RuntimeError("No se puede reiniciar el supervisor durante una ejecucion")
            self._reset_unlocked()

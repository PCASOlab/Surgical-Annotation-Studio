"""
widgets/worker.py

Generic background-thread runner so ffmpeg calls (standardize/cut, which
can take tens of seconds per clip) never freeze the UI. Any tab can do:

    self.worker = FnWorker(some_function, arg1, arg2)
    self.worker.log.connect(self.append_log)
    self.worker.finished_ok.connect(self.on_done)
    self.worker.failed.connect(self.on_error)
    self.worker.start()
"""
from __future__ import annotations
import inspect
from typing import Callable

from PySide6.QtCore import QThread, Signal


class FnWorker(QThread):
    log = Signal(str)
    progress = Signal(int, int)   # (current, total)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable, *args, **kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        try:
            params = inspect.signature(fn).parameters
        except (TypeError, ValueError):
            params = {}
        self._accepts_on_log = "on_log" in params
        self._accepts_on_progress = "on_progress" in params

    def run(self) -> None:
        try:
            kwargs = dict(self._kwargs)
            if self._accepts_on_log:
                kwargs["on_log"] = self.log.emit
            if self._accepts_on_progress:
                kwargs["on_progress"] = lambda c, t: self.progress.emit(c, t)
            result = self._fn(*self._args, **kwargs)
            self.finished_ok.emit(result)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))

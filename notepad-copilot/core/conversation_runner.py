"""Stable Qt interface for live SDK sessions and legacy print-mode requests."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from core.copilot_runner import CopilotRunner


class ConversationRunner(QObject):
    output_received = Signal(str)
    process_started = Signal()
    process_finished = Signal(int)
    error_occurred = Signal(str)
    message_accepted = Signal(str)
    message_rejected = Signal(str, str)
    info_received = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._legacy = CopilotRunner(self)
        self._live = None
        self._live_mode = True
        self._closing = False
        self._model: str | None = None
        self._effort: str | None = None
        self._wire(self._legacy)

    @property
    def live_mode(self) -> bool:
        return self._live_mode

    def _selected(self):
        return self._live if self._live_mode else self._legacy

    def _wire(self, backend):
        for name in (
            "output_received", "process_started", "process_finished",
            "error_occurred", "message_accepted", "message_rejected",
            "info_received",
        ):
            source = getattr(backend, name, None)
            if source is not None:
                destination = getattr(self, name)
                source.connect(
                    lambda *args, b=backend, signal=destination:
                    self._forward(b, signal, args)
                )

    def _forward(self, backend, signal, args):
        if not self._closing and backend is self._selected():
            signal.emit(*args)

    def set_live_mode(self, enabled: bool) -> bool:
        if enabled == self._live_mode:
            return True
        if self.is_running():
            self.error_occurred.emit("请先停止当前任务，再切换会话模式。")
            return False
        self.reset_session()
        self._live_mode = enabled
        return True

    def set_model(self, model: str | None,
                  reasoning_effort: str | None = None):
        self._model = model or None
        self._effort = reasoning_effort or None
        self._legacy.set_model(self._model, self._effort)

    def submit(self, request_id: str, prompt: str,
               attachments: list | None = None) -> bool:
        if self._closing:
            self.message_rejected.emit(request_id, "窗口正在关闭。")
            return False
        if self._live_mode:
            if self._live is None:
                from core.live_runner import LiveCopilotRunner
                self._live = LiveCopilotRunner(self)
                self._wire(self._live)
            return self._live.submit(
                request_id, prompt,
                [{"type": "file", "path": str(Path(path).resolve()),
                  "displayName": Path(path).name}
                 for path in (attachments or [])],
                self._model, self._effort,
            )
        if self._legacy.send(prompt, attachments):
            self.message_accepted.emit(request_id)
            return True
        self.message_rejected.emit(
            request_id,
            "传统模式未能发送；运行中追加要求需要开启「运行中插嘴」。",
        )
        return False

    def is_running(self) -> bool:
        backend = self._selected()
        return backend is not None and backend.is_running()

    def has_session_history(self) -> bool:
        backend = self._selected()
        return backend is not None and backend.has_session_history()

    def submissions_allowed(self) -> bool:
        if self._closing:
            return False
        backend = self._selected()
        if backend is None:
            return True
        return (backend.submissions_allowed() if self._live_mode
                else not backend.is_running())

    def last_output(self) -> str:
        backend = self._selected()
        return backend.last_output() if backend is not None else ""

    def stop(self):
        backend = self._selected()
        if backend is None or not backend.is_running():
            return
        backend.stop()
        if backend is self._legacy:
            self.process_finished.emit(-2)

    def reset_session(self):
        backend = self._selected()
        if backend is not None:
            backend.reset_session()

    def shutdown(self):
        self._closing = True
        if self._live is not None:
            self._live.shutdown()
        self._legacy.stop()

from __future__ import annotations

import logging
import logging.handlers
import msvcrt
from pathlib import Path

from .attachments import AttachmentProcessor
from .config import Settings
from .copilot import CopilotRunner
from .feishu import FeishuGateway
from .service import ConversationService
from .store import Store
from .worker import JobWorker


class InstanceLock:
    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        if self.handle.tell() == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise RuntimeError("文曲星 v02 已在运行") from exc

    def release(self) -> None:
        if self.handle is None:
            return
        self.handle.seek(0)
        msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        self.handle.close()
        self.handle = None


def configure_logging(settings: Settings) -> None:
    handler = logging.handlers.RotatingFileHandler(
        settings.log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.log_level, logging.INFO))
    root.handlers.clear()
    root.addHandler(handler)


def run(settings: Settings) -> None:
    configure_logging(settings)
    lock = InstanceLock(settings.lock_path)
    lock.acquire()
    store = Store(settings.database_path)
    store.initialize()
    copilot = CopilotRunner(
        settings.copilot_cli,
        settings.copilot_work_dir,
        settings.copilot_timeout_seconds,
    )
    service = ConversationService(store, copilot)
    gateway = FeishuGateway(settings, service)
    attachment_processor = AttachmentProcessor(
        settings.attachment_dir,
        settings.attachment_max_bytes,
        settings.attachment_extract_max_bytes,
        settings.attachment_archive_max_files,
    )
    worker = JobWorker(
        store,
        copilot,
        gateway,
        settings.worker_count,
        attachment_processor,
    )
    worker.start()
    try:
        gateway.start()
    finally:
        worker.stop()
        lock.release()

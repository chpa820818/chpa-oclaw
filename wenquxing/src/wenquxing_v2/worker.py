from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor

from .copilot import CopilotError, CopilotRunner
from .feishu import FeishuGateway
from .store import Job, Store

logger = logging.getLogger(__name__)


class JobWorker:
    def __init__(
        self,
        store: Store,
        copilot: CopilotRunner,
        gateway: FeishuGateway,
        worker_count: int,
    ):
        self.store = store
        self.copilot = copilot
        self.gateway = gateway
        self.worker_count = worker_count
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=self._dispatch, name="job-dispatcher", daemon=True
        )
        self.session_locks: dict[int, threading.Lock] = {}
        self.session_locks_guard = threading.Lock()

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=10)

    def _dispatch(self) -> None:
        with ThreadPoolExecutor(
            max_workers=self.worker_count, thread_name_prefix="job"
        ) as executor:
            active: set[Future[None]] = set()
            while not self.stop_event.is_set():
                active = {future for future in active if not future.done()}
                capacity = self.worker_count - len(active)
                if capacity > 0:
                    for job in self.store.claim_jobs(capacity):
                        active.add(executor.submit(self._process, job))
                self.stop_event.wait(0.5)

    def _process(self, job: Job) -> None:
        lock = self._session_lock(job.session_id)
        with lock:
            try:
                response = job.response_content
                if response is None:
                    if job.session_id is None or job.prompt is None:
                        raise RuntimeError("任务缺少会话或提示词")
                    session = self.store.get_session(job.session_id)
                    try:
                        response = self.copilot.ask(
                            session.copilot_session_id, job.prompt
                        )
                    except CopilotError as exc:
                        logger.error("Copilot failed for job %s: %s", job.id, exc)
                        response = f"Copilot 处理失败：{exc}"
                    self.store.save_response(job.id, response)
                self.gateway.reply(
                    job.message_id, job.chat_id, job.response_type, response
                )
                self.store.complete_job(job.id)
            except Exception as exc:
                logger.exception("Job %s failed", job.id)
                self.store.retry_job(job.id, job.attempts, str(exc))

    def _session_lock(self, session_id: int | None) -> threading.Lock:
        key = session_id or 0
        with self.session_locks_guard:
            return self.session_locks.setdefault(key, threading.Lock())

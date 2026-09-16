from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from .attachments import AttachmentError, AttachmentProcessor
from .copilot import CopilotError, CopilotRunner
from .feishu import FeishuGateway
from .file_outputs import output_files
from .store import Job, Store

logger = logging.getLogger(__name__)


class JobWorker:
    def __init__(
        self,
        store: Store,
        copilot: CopilotRunner,
        gateway: FeishuGateway,
        worker_count: int,
        attachment_processor: AttachmentProcessor,
    ):
        self.store = store
        self.copilot = copilot
        self.gateway = gateway
        self.worker_count = worker_count
        self.attachment_processor = attachment_processor
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=self._dispatch, name="job-dispatcher", daemon=True
        )
        self.workspace_lock = threading.Lock()

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
        with self.workspace_lock:
            try:
                response = job.response_content
                outputs: list[Path] = []
                wants_files = _wants_files_returned(job.prompt or "")
                if response is None:
                    if job.session_id is None or job.prompt is None:
                        raise RuntimeError("任务缺少会话或提示词")
                    session = self.store.get_session(job.session_id)
                    attachments: tuple[Path, ...] = ()
                    prompt = job.prompt
                    session_dir = self.attachment_processor.session_directory(
                        session.public_id
                    )
                    output_dir = session_dir / "outbox" / f"job-{job.id}"
                    if wants_files:
                        output_dir.mkdir(parents=True, exist_ok=True)
                        prompt = (
                            f"{prompt}\n\n用户要求把结果文件发回飞书。必须将每个最终文件保存"
                            f"到受管根目录内的 "
                            f"{output_dir.relative_to(self.attachment_processor.root)}；"
                            "不要把临时文件放入该目录。文曲星会自动上传其中的普通文件。"
                        )
                    try:
                        if job.attachment_key and job.attachment_type:
                            path = self.attachment_processor.existing(
                                job.attachment_path
                            )
                            if path is None:
                                resource_type = (
                                    "image"
                                    if job.attachment_type == "image"
                                    else "file"
                                )
                                content, response_name = (
                                    self.gateway.download_resource(
                                        job.message_id,
                                        job.attachment_key,
                                        resource_type,
                                    )
                                )
                                path = self.attachment_processor.save(
                                    session.public_id,
                                    job.message_id,
                                    job.attachment_type,
                                    job.attachment_name,
                                    response_name,
                                    content,
                                )
                                self.store.save_attachment(
                                    job.id, path, len(content)
                                )
                            prepared = self.attachment_processor.prepare(path)
                            attachments = prepared.attachments
                            relative_files = [
                                str(file.relative_to(session_dir))
                                for file in prepared.files
                            ]
                            inventory = "\n".join(
                                f"- {file}" for file in relative_files
                            )
                            prompt = (
                                f"{prompt}\n\n新增到当前会话目录的文件：\n{inventory}"
                                f"\n\n处理说明：{prepared.context}"
                                "\n请自行检查或按用户要求处理这些文件及本会话已有文件。"
                            )
                        response = self.copilot.ask(
                            session.copilot_session_id,
                            prompt,
                            attachments,
                            session_dir,
                        )
                    except AttachmentError as exc:
                        logger.error("Attachment failed for job %s: %s", job.id, exc)
                        response = f"文件已保存到当前会话目录，但自动分析失败：{exc}"
                    except CopilotError as exc:
                        logger.error("Copilot failed for job %s: %s", job.id, exc)
                        response = f"Copilot 处理失败：{exc}"
                    self.store.save_response(job.id, response)
                if wants_files:
                    session = self.store.get_session(job.session_id)
                    session_dir = self.attachment_processor.session_directory(
                        session.public_id
                    )
                    outputs = output_files(
                        session_dir / "outbox" / f"job-{job.id}"
                    )
                self.gateway.reply(
                    job.message_id, job.chat_id, job.response_type, response
                )
                for index, output in enumerate(outputs):
                    self.gateway.send_file(
                        job.chat_id,
                        output,
                        f"wenquxing-output:{job.message_id}:{index}:{output.name}",
                    )
                self.store.complete_job(job.id)
            except Exception as exc:
                logger.exception("Job %s failed", job.id)
                self.store.retry_job(job.id, job.attempts, str(exc))


def _wants_files_returned(prompt: str) -> bool:
    lowered = prompt.lower()
    return any(
        phrase in lowered
        for phrase in (
            "发给我",
            "发送给我",
            "发回来",
            "回传",
            "传给我",
            "send me",
            "send it",
            "return the file",
            "attach it",
        )
    )

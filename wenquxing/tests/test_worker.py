from __future__ import annotations

from pathlib import Path

from wenquxing_v2.attachments import AttachmentProcessor
from wenquxing_v2.service import ConversationService
from wenquxing_v2.store import Store
from wenquxing_v2.worker import JobWorker


class FakeGateway:
    def __init__(self) -> None:
        self.replies: list[str] = []

    def download_resource(
        self, message_id: str, resource_key: str, resource_type: str
    ) -> tuple[bytes, str]:
        assert (message_id, resource_key, resource_type) == (
            "m-file",
            "file-key",
            "file",
        )
        return b"session file content", "report.txt"

    def reply(
        self, message_id: str, chat_id: str, message_type: str, content: str
    ) -> None:
        self.replies.append(content)


class FakeCopilot:
    def __init__(self) -> None:
        self.work_dir: Path | None = None

    def ask(
        self,
        session_id: str,
        prompt: str,
        attachments=(),
        work_dir: Path | None = None,
    ) -> str:
        self.work_dir = work_dir
        assert work_dir is not None
        files = list(work_dir.glob("files/*/report.txt"))
        assert len(files) == 1
        assert files[0].read_text(encoding="utf-8") == "session file content"
        assert "files" in prompt
        assert attachments == ()
        return "analysis complete"


def test_worker_saves_file_in_current_session_workspace(tmp_path: Path) -> None:
    store = Store(tmp_path / "wenquxing.sqlite3")
    store.initialize()
    service = ConversationService(store)
    service.accept_attachment(
        "m-file", "owner", "chat", "file", "file-key", "report.txt"
    )
    job = store.claim_jobs(1)[0]
    session = store.get_session(job.session_id)  # type: ignore[arg-type]
    gateway = FakeGateway()
    copilot = FakeCopilot()
    processor = AttachmentProcessor(
        tmp_path / "sessions",
        max_bytes=1024 * 1024,
        max_extract_bytes=2 * 1024 * 1024,
        max_archive_files=12,
    )
    worker = JobWorker(  # type: ignore[arg-type]
        store, copilot, gateway, 1, processor
    )

    worker._process(job)

    assert copilot.work_dir == tmp_path / "sessions" / session.public_id
    assert gateway.replies == ["analysis complete"]
    assert store.status_counts() == {"done": 1}

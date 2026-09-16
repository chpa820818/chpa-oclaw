from __future__ import annotations

from pathlib import Path

from wenquxing_v2.service import ConversationService
from wenquxing_v2.store import Store


def make_store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "test.sqlite3")
    store.initialize()
    return store


def test_message_is_idempotent_and_bound_to_current_session(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    service = ConversationService(store)

    assert service.accept_message("m1", "owner", "chat", "你好")
    first = store.claim_jobs(1)[0]
    assert not service.accept_message("m1", "owner", "chat", "重复")

    created = store.create_session("owner", "chat", "项目")
    assert service.accept_message("m2", "owner", "chat", "继续")
    second = store.claim_jobs(1)[0]

    assert first.session_id != created.id
    assert second.session_id == created.id


def test_attachment_is_persisted_and_bound_to_current_session(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    service = ConversationService(store)

    assert service.accept_attachment(
        "m-file", "owner", "chat", "file", "file-key", "report.zip"
    )
    job = store.claim_jobs(1)[0]

    assert job.attachment_type == "file"
    assert job.attachment_key == "file-key"
    assert job.attachment_name == "report.zip"
    assert job.session_id == store.current_session("owner", "chat").id
    assert not service.accept_attachment(
        "m-file", "owner", "chat", "file", "other-key", "duplicate.zip"
    )


def test_each_session_has_a_stable_local_directory(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    first = store.current_session("owner", "chat")
    second = store.create_session("owner", "chat", "项目文件")

    assert (tmp_path / "sessions" / first.public_id).is_dir()
    assert (tmp_path / "sessions" / second.public_id).is_dir()

    store.rename_session("owner", "chat", second.public_id, "改名后")

    assert (tmp_path / "sessions" / second.public_id).is_dir()


def test_session_commands_and_memory_reset(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    service = ConversationService(store)

    assert service.accept_message("m1", "owner", "chat", "新会话 项目 A")
    current = store.current_session("owner", "chat")
    old_copilot_id = current.copilot_session_id
    assert current.name == "项目 A"

    assert service.accept_message("m2", "owner", "chat", "清除上下文")
    reset = store.current_session("owner", "chat")
    assert reset.id == current.id
    assert reset.copilot_session_id != old_copilot_id


def test_card_switches_session(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    service = ConversationService(store)
    original = store.current_session("owner", "chat")
    other = store.create_session("owner", "chat", "第二个")

    result = service.card_action(
        "owner", "chat", {"action": "switch_session", "session": original.public_id}
    )

    assert original.name in result.message
    assert result.card is not None
    assert store.current_session("owner", "chat").id == original.id
    assert other.id != original.id


def test_only_one_job_per_session_is_claimed_at_once(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    service = ConversationService(store)
    service.accept_message("m1", "owner", "chat", "第一条")
    service.accept_message("m2", "owner", "chat", "第二条")

    claimed = store.claim_jobs(4)

    assert [job.message_id for job in claimed] == ["m1"]


def test_unnamed_session_uses_first_prompt_as_name(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    service = ConversationService(store)
    created = store.create_session("owner", "chat")

    service.accept_message("m1", "owner", "chat", "制定十月项目发布计划")

    renamed = store.current_session("owner", "chat")
    assert renamed.id == created.id
    assert renamed.name == "制定十月项目发布计划"


def test_explicit_session_name_is_not_replaced(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    service = ConversationService(store)
    created = store.create_session("owner", "chat", "发布项目")

    service.accept_message("m1", "owner", "chat", "第一条普通消息")

    assert store.current_session("owner", "chat").name == created.name


def test_naming_alias_renames_current_session(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    service = ConversationService(store)

    service.accept_message("m1", "owner", "chat", "命名会话 财务分析")

    assert store.current_session("owner", "chat").name == "财务分析"


def test_card_creates_named_session_from_form(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    service = ConversationService(store)

    editor = service.card_action(
        "owner", "chat", {"action": "new_session_editor"}
    )
    created = service.card_action(
        "owner",
        "chat",
        {"action": "create_session"},
        {"session_name": "市场计划"},
    )

    assert editor.card is not None
    assert created.toast_type == "success"
    assert store.current_session("owner", "chat").name == "市场计划"


def test_card_renames_selected_session_from_form(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    service = ConversationService(store)
    target = store.create_session("owner", "chat", "旧名称")

    result = service.card_action(
        "owner",
        "chat",
        {"action": "rename_session", "session": target.public_id},
        {"session_name": "新名称"},
    )

    assert result.toast_type == "success"
    assert store.current_session("owner", "chat").name == "新名称"


class FakeCopilot:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete_session(self, session_id: str) -> None:
        self.deleted.append(session_id)


def test_card_hides_and_restores_session(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    service = ConversationService(store)
    hidden = store.create_session("owner", "chat", "稍后处理")

    archived = service.card_action(
        "owner", "chat", {"action": "archive_session", "session": hidden.public_id}
    )
    restored = service.card_action(
        "owner", "chat", {"action": "restore_session", "session": hidden.public_id}
    )

    assert archived.toast_type == "success"
    assert store.list_archived_sessions("owner", "chat") == []
    assert restored.toast_type == "success"
    assert store.current_session("owner", "chat").name == "稍后处理"


def test_permanent_delete_removes_messages_and_copilot_session(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    copilot = FakeCopilot()
    service = ConversationService(store, copilot)  # type: ignore[arg-type]
    target = store.create_session("owner", "chat", "待删除")
    service.accept_message("m1", "owner", "chat", "秘密消息")
    job = store.claim_jobs(1)[0]
    store.complete_job(job.id)

    result = service.card_action(
        "owner",
        "chat",
        {"action": "permanent_delete_session", "session": target.public_id},
    )

    assert result.toast_type == "success"
    assert store.find_session("owner", "chat", target.public_id) is None
    assert copilot.deleted == [target.copilot_session_id]
    assert not store.is_received("m1")


def test_permanent_delete_removes_managed_attachment_files(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    service = ConversationService(store, FakeCopilot())  # type: ignore[arg-type]
    target = store.current_session("owner", "chat")
    service.accept_attachment(
        "m-file", "owner", "chat", "file", "file-key", "report.txt"
    )
    directory = (
        tmp_path
        / "sessions"
        / target.public_id
    )
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "report.txt").write_text("content", encoding="utf-8")

    service.card_action(
        "owner",
        "chat",
        {"action": "permanent_delete_session", "session": target.public_id},
    )

    assert not directory.exists()


def test_session_card_does_not_display_public_ids(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    service = ConversationService(store)
    current = store.current_session("owner", "chat")

    card = service.card_action(
        "owner", "chat", {"action": "cancel_session_editor"}
    ).card

    assert card is not None
    visible_text: list[str] = []

    def collect(value) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "content" and isinstance(child, str):
                    visible_text.append(child)
                else:
                    collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(card)
    assert current.public_id not in "\n".join(visible_text)

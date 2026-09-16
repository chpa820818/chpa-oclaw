from __future__ import annotations

from dataclasses import dataclass

from .cards import (
    archived_sessions_card,
    session_card,
    session_card_data,
    session_editor_card,
    session_manage_card,
)
from .copilot import CopilotRunner
from .store import Store


HELP_TEXT = """文曲星 v02

直接发送内容：在当前会话中询问 Copilot
发送图片、文件、视频或 ZIP：下载后交给 Copilot 分析
会话：打开会话选择卡片
新会话 [名称]：创建并进入会话
进入会话 <名称>：切换会话
当前会话：显示当前选项
命名会话 <名称>：修改当前会话名称
清除上下文：清空当前会话的 Copilot 记忆
帮助：显示本说明"""


@dataclass(frozen=True)
class LocalResponse:
    message_type: str
    content: str


@dataclass(frozen=True)
class CardActionResult:
    toast_type: str
    message: str
    card: dict | None = None


class ConversationService:
    def __init__(self, store: Store, copilot: CopilotRunner | None = None):
        self.store = store
        self.copilot = copilot

    def accept_message(
        self, message_id: str, owner_open_id: str, chat_id: str, text: str
    ) -> bool:
        text = text.strip()
        response = self._command(owner_open_id, chat_id, text)
        if response is None:
            return self.store.accept_prompt(
                message_id, owner_open_id, chat_id, text
            )
        return self.store.accept_local_response(
            message_id,
            owner_open_id,
            chat_id,
            text,
            response.message_type,
            response.content,
        )

    def accept_notice(
        self,
        message_id: str,
        owner_open_id: str,
        chat_id: str,
        source_description: str,
        notice: str,
    ) -> bool:
        return self.store.accept_local_response(
            message_id,
            owner_open_id,
            chat_id,
            source_description,
            "text",
            notice,
        )

    def accept_attachment(
        self,
        message_id: str,
        owner_open_id: str,
        chat_id: str,
        message_type: str,
        resource_key: str,
        file_name: str,
    ) -> bool:
        if message_type == "image":
            prompt = "请分析这张图片，提取文字、关键事实和重要细节。"
        elif message_type == "media":
            prompt = "请根据视频视觉关键帧概括内容、场景变化和重要信息。"
        else:
            prompt = f"请分析附件“{file_name}”，概括内容并列出重要信息。"
        return self.store.accept_attachment(
            message_id,
            owner_open_id,
            chat_id,
            f"[{message_type} attachment] {file_name}",
            prompt,
            message_type,
            resource_key,
            file_name,
        )

    def card_action(
        self,
        owner_open_id: str,
        chat_id: str,
        value: dict[str, object],
        form_value: dict[str, object] | None = None,
    ) -> CardActionResult:
        action = str(value.get("action", ""))
        if action == "switch_session":
            selector = str(value.get("session", ""))
            session = self.store.switch_session(owner_open_id, chat_id, selector)
            if session is None:
                return CardActionResult("error", "会话不存在或已失效")
            return CardActionResult(
                "success",
                f"已进入 {session.name}",
                self._session_list_card(owner_open_id, chat_id),
            )
        if action == "new_session_editor":
            return CardActionResult("info", "请输入会话名称", session_editor_card())
        if action in {"manage_session", "edit_session"}:
            selector = str(value.get("session", ""))
            session = self.store.find_session(owner_open_id, chat_id, selector)
            if session is None:
                return CardActionResult("error", "会话不存在或已失效")
            if action == "manage_session":
                return CardActionResult(
                    "info", f"正在管理 {session.name}", session_manage_card(session)
                )
            return CardActionResult(
                "info", f"正在编辑 {session.name}", session_editor_card(session)
            )
        if action == "create_session":
            name = self._form_name(form_value)
            error = self._name_error(name)
            if error:
                return CardActionResult("error", error)
            try:
                session = self.store.create_session(owner_open_id, chat_id, name)
            except ValueError as exc:
                return CardActionResult("error", str(exc))
            return CardActionResult(
                "success",
                f"已创建并进入 {session.name}",
                self._session_list_card(owner_open_id, chat_id),
            )
        if action == "rename_session":
            selector = str(value.get("session", ""))
            name = self._form_name(form_value)
            error = self._name_error(name)
            if error:
                return CardActionResult("error", error)
            try:
                session = self.store.rename_session(
                    owner_open_id, chat_id, selector, name
                )
            except ValueError as exc:
                return CardActionResult("error", str(exc))
            if session is None:
                return CardActionResult("error", "会话不存在或已失效")
            return CardActionResult(
                "success",
                f"已更新 {session.name}",
                self._session_list_card(owner_open_id, chat_id),
            )
        if action == "cancel_session_editor":
            return CardActionResult(
                "info",
                "已返回会话列表",
                self._session_list_card(owner_open_id, chat_id),
            )
        if action == "archive_session":
            selector = str(value.get("session", ""))
            session = self.store.archive_session(owner_open_id, chat_id, selector)
            if session is None:
                return CardActionResult("error", "会话不存在或已隐藏")
            return CardActionResult(
                "success",
                f"已隐藏 {session.name}，可从“已隐藏会话”恢复",
                self._session_list_card(owner_open_id, chat_id),
            )
        if action == "show_archived_sessions":
            archived = self.store.list_archived_sessions(owner_open_id, chat_id)
            return CardActionResult(
                "info", "已显示隐藏会话", archived_sessions_card(archived)
            )
        if action == "restore_session":
            selector = str(value.get("session", ""))
            session = self.store.restore_session(owner_open_id, chat_id, selector)
            if session is None:
                return CardActionResult("error", "隐藏会话不存在")
            return CardActionResult(
                "success",
                f"已恢复并进入 {session.name}",
                self._session_list_card(owner_open_id, chat_id),
            )
        if action == "permanent_delete_session":
            if self.copilot is None:
                return CardActionResult("error", "永久删除服务当前不可用")
            selector = str(value.get("session", ""))
            target = self.store.find_session(owner_open_id, chat_id, selector)
            if target is None:
                return CardActionResult("error", "会话不存在或已删除")
            try:
                deleted = self.store.permanently_delete_session(
                    owner_open_id, chat_id, selector
                )
                if deleted is not None:
                    self.copilot.delete_session(deleted.copilot_session_id)
            except RuntimeError as exc:
                return CardActionResult("error", str(exc))
            if deleted is None:
                return CardActionResult("error", "会话不存在或已删除")
            return CardActionResult(
                "success",
                f"已永久删除 {deleted.name}",
                self._session_list_card(owner_open_id, chat_id),
            )
        return CardActionResult("error", "无法识别此操作")

    def _session_list_card(self, owner_open_id: str, chat_id: str) -> dict:
        current = self.store.current_session(owner_open_id, chat_id)
        sessions = self.store.list_sessions(owner_open_id, chat_id)
        return session_card_data(sessions, current)

    @staticmethod
    def _form_name(form_value: dict[str, object] | None) -> str:
        return str((form_value or {}).get("session_name", "")).strip()

    @staticmethod
    def _name_error(name: str) -> str | None:
        if not name:
            return "请输入会话名称"
        if len(name) > 40:
            return "会话名称不能超过 40 个字符"
        return None

    def _command(
        self, owner_open_id: str, chat_id: str, text: str
    ) -> LocalResponse | None:
        if text in {"帮助", "/help"}:
            return LocalResponse("text", HELP_TEXT)
        if text in {"会话", "会话列表"}:
            current = self.store.current_session(owner_open_id, chat_id)
            sessions = self.store.list_sessions(owner_open_id, chat_id)
            return LocalResponse("interactive", session_card(sessions, current))
        if text == "当前会话":
            session = self.store.current_session(owner_open_id, chat_id)
            return LocalResponse(
                "text", f"当前会话：{session.name}"
            )
        if text == "新会话" or text.startswith("新会话 "):
            name = text[3:].strip() or None
            if name is not None and len(name) > 40:
                return LocalResponse("text", "会话名称不能超过 40 个字符。")
            session = self.store.create_session(owner_open_id, chat_id, name)
            return LocalResponse(
                "text", f"已创建并进入：{session.name}"
            )
        if text.startswith("进入会话 "):
            selector = text[5:].strip()
            if not selector:
                return LocalResponse("text", "请提供会话名称。")
            session = self.store.switch_session(owner_open_id, chat_id, selector)
            if session is None:
                return LocalResponse("text", f"未找到会话：{selector}")
            return LocalResponse(
                "text", f"已进入：{session.name}"
            )
        rename_prefix = next(
            (
                prefix
                for prefix in ("命名会话 ", "重命名会话 ", "会话命名 ")
                if text.startswith(prefix)
            ),
            None,
        )
        if rename_prefix is not None:
            name = text[len(rename_prefix) :].strip()
            if not name:
                return LocalResponse("text", "请提供新的会话名称。")
            if len(name) > 40:
                return LocalResponse("text", "会话名称不能超过 40 个字符。")
            try:
                session = self.store.rename_current(owner_open_id, chat_id, name)
            except ValueError as exc:
                return LocalResponse("text", str(exc))
            return LocalResponse(
                "text", f"已重命名：{session.name}"
            )
        if text == "清除上下文":
            session = self.store.clear_current(owner_open_id, chat_id)
            return LocalResponse(
                "text", f"已清除 {session.name} 的上下文。"
            )
        return None

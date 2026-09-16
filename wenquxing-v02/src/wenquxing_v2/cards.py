from __future__ import annotations

import json

from .store import Session


def session_card(sessions: list[Session], current: Session) -> str:
    return json.dumps(session_card_data(sessions, current), ensure_ascii=False)


def session_card_data(sessions: list[Session], current: Session) -> dict:
    elements: list[dict] = [
        {
            "tag": "markdown",
            "content": (
                f"当前：**{current.name}**\n"
                "点击名称切换会话，点击“编辑”直接修改名称。"
            ),
        }
    ]
    for index, session in enumerate(sessions[:12]):
        elements.append(
            {
                "tag": "column_set",
                "horizontal_spacing": "8px",
                "columns": [
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 4,
                        "elements": [
                            _callback_button(
                                f"{'✓ ' if session.id == current.id else ''}"
                                f"{session.name}",
                                {
                                    "action": "switch_session",
                                    "session": session.public_id,
                                },
                                "primary" if session.id == current.id else "default",
                                f"switch_{index}",
                                "fill",
                            )
                        ],
                    },
                    {
                        "tag": "column",
                        "width": "auto",
                        "elements": [
                            _callback_button(
                                "管理",
                                {
                                    "action": "manage_session",
                                    "session": session.public_id,
                                },
                                "default",
                                f"edit_{index}",
                            )
                        ],
                    },
                ],
            }
        )
    elements.append(
        _callback_button(
            "＋ 新建并命名会话",
            {"action": "new_session_editor"},
            "primary_filled",
            "new_session",
            "fill",
        )
    )
    elements.append(
        _callback_button(
            "查看已隐藏会话",
            {"action": "show_archived_sessions"},
            "default",
            "show_archived",
            "fill",
        )
    )
    return _card("文曲星 · 会话管理", elements)


def session_editor_card(session: Session | None = None) -> dict:
    editing = session is not None
    action = "rename_session" if editing else "create_session"
    title = "编辑会话名称" if editing else "新建会话"
    form_elements = [
        {
            "tag": "input",
            "element_id": "session_name_input",
            "name": "session_name",
            "required": True,
            "max_length": 40,
            "width": "fill",
            "default_value": session.name if session else "",
            "label": {"tag": "plain_text", "content": "会话名称"},
            "placeholder": {
                "tag": "plain_text",
                "content": "例如：项目规划、财务分析",
            },
        },
        {
            "tag": "button",
            "element_id": "save_session_name",
            "name": "save_session_name",
            "form_action_type": "submit",
            "type": "primary_filled",
            "width": "fill",
            "text": {"tag": "plain_text", "content": "保存"},
            "behaviors": [
                {
                    "type": "callback",
                    "value": {
                        "action": action,
                        "session": session.public_id if session else "",
                    },
                }
            ],
        },
    ]
    elements = [
        {
            "tag": "markdown",
            "content": (
                f"正在编辑：**{session.name}**"
                if editing
                else "输入名称后创建并自动进入新会话。"
            ),
        },
        {
            "tag": "form",
            "element_id": "session_name_form",
            "name": "session_name_form",
            "elements": form_elements,
        },
        _callback_button(
            "返回会话列表",
            {"action": "cancel_session_editor"},
            "default",
            "cancel_editor",
            "fill",
        ),
    ]
    return _card(title, elements)


def session_manage_card(session: Session) -> dict:
    return _card(
        "管理会话",
        [
            {"tag": "markdown", "content": f"当前管理：**{session.name}**"},
            _callback_button(
                "编辑名称",
                {"action": "edit_session", "session": session.public_id},
                "primary",
                "edit_name",
                "fill",
            ),
            _callback_button(
                "从列表隐藏（可恢复）",
                {"action": "archive_session", "session": session.public_id},
                "default",
                "archive_session",
                "fill",
            ),
            _callback_button(
                "永久删除",
                {"action": "permanent_delete_session", "session": session.public_id},
                "danger_filled",
                "delete_session",
                "fill",
                confirm=(
                    "永久删除会话",
                    f"“{session.name}”的消息和 Copilot 记忆将永久删除，无法恢复。",
                ),
            ),
            _callback_button(
                "返回会话列表",
                {"action": "cancel_session_editor"},
                "default",
                "back_to_sessions",
                "fill",
            ),
        ],
    )


def archived_sessions_card(sessions: list[Session]) -> dict:
    elements: list[dict] = [
        {
            "tag": "markdown",
            "content": "隐藏会话仍保留消息和记忆，可恢复或永久删除。",
        }
    ]
    if not sessions:
        elements.append({"tag": "markdown", "content": "暂无已隐藏会话。"})
    for index, session in enumerate(sessions[:12]):
        elements.append(
            {
                "tag": "column_set",
                "horizontal_spacing": "8px",
                "columns": [
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 3,
                        "elements": [
                            {"tag": "markdown", "content": f"**{session.name}**"}
                        ],
                    },
                    {
                        "tag": "column",
                        "width": "auto",
                        "elements": [
                            _callback_button(
                                "恢复",
                                {
                                    "action": "restore_session",
                                    "session": session.public_id,
                                },
                                "primary",
                                f"restore_{index}",
                            )
                        ],
                    },
                    {
                        "tag": "column",
                        "width": "auto",
                        "elements": [
                            _callback_button(
                                "永久删除",
                                {
                                    "action": "permanent_delete_session",
                                    "session": session.public_id,
                                },
                                "danger",
                                f"purge_{index}",
                                confirm=(
                                    "永久删除会话",
                                    f"“{session.name}”的消息和 Copilot 记忆将永久删除，无法恢复。",
                                ),
                            )
                        ],
                    },
                ],
            }
        )
    elements.append(
        _callback_button(
            "返回会话列表",
            {"action": "cancel_session_editor"},
            "default",
            "back_to_active",
            "fill",
        )
    )
    return _card("已隐藏会话", elements)


def _callback_button(
    text: str,
    value: dict[str, str],
    button_type: str,
    element_id: str,
    width: str = "default",
    confirm: tuple[str, str] | None = None,
) -> dict:
    button = {
        "tag": "button",
        "element_id": element_id,
        "type": button_type,
        "width": width,
        "text": {"tag": "plain_text", "content": text[:100]},
        "behaviors": [{"type": "callback", "value": value}],
    }
    if confirm:
        button["confirm"] = {
            "title": {"tag": "plain_text", "content": confirm[0]},
            "text": {"tag": "plain_text", "content": confirm[1]},
        }
    return button


def _card(title: str, elements: list[dict]) -> dict:
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": title},
        },
        "body": {
            "direction": "vertical",
            "vertical_spacing": "8px",
            "padding": "12px 12px 12px 12px",
            "elements": elements,
        },
    }

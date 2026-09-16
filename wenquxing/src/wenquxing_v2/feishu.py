from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateFileRequest,
    CreateFileRequestBody,
    CreateImageRequest,
    CreateImageRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
    GetMessageResourceRequest,
    P2ImMessageReceiveV1,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)
from lark_oapi.event.callback.model.p2_card_action_trigger import (
    P2CardActionTrigger,
    P2CardActionTriggerResponse,
)

from .config import Settings
from .service import ConversationService

logger = logging.getLogger(__name__)


class FeishuError(RuntimeError):
    pass


class FeishuGateway:
    def __init__(self, settings: Settings, service: ConversationService):
        self.settings = settings
        self.service = service
        self.client = (
            lark.Client.builder()
            .app_id(settings.app_id)
            .app_secret(settings.app_secret)
            .log_level(lark.LogLevel.WARNING)
            .build()
        )
        self.event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_message)
            .register_p2_card_action_trigger(self._on_card_action)
            .build()
        )

    def start(self) -> None:
        logger.info("Starting Feishu WebSocket client")
        client = lark.ws.Client(
            self.settings.app_id,
            self.settings.app_secret,
            event_handler=self.event_handler,
            log_level=lark.LogLevel.WARNING,
        )
        client.start()

    def reply(
        self, message_id: str, chat_id: str, message_type: str, content: str
    ) -> None:
        chunks = [content] if message_type == "interactive" else _chunks(content, 18000)
        for index, chunk in enumerate(chunks):
            encoded = (
                chunk
                if message_type == "interactive"
                else json.dumps({"text": chunk}, ensure_ascii=False)
            )
            outgoing_type = message_type if index == 0 else "text"
            logical_id = str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"wenquxing-v02:{message_id}:{index}")
            )
            if index == 0:
                request = (
                    ReplyMessageRequest.builder()
                    .message_id(message_id)
                    .request_body(
                        ReplyMessageRequestBody.builder()
                        .content(encoded)
                        .msg_type(outgoing_type)
                        .reply_in_thread(False)
                        .uuid(logical_id)
                        .build()
                    )
                    .build()
                )
                response = self.client.im.v1.message.reply(request)
            else:
                request = (
                    CreateMessageRequest.builder()
                    .receive_id_type("chat_id")
                    .request_body(
                        CreateMessageRequestBody.builder()
                        .receive_id(chat_id)
                        .msg_type("text")
                        .content(encoded)
                        .uuid(logical_id)
                        .build()
                    )
                    .build()
                )
                response = self.client.im.v1.message.create(request)
            if not response.success():
                raise FeishuError(
                    f"飞书回复失败 code={response.code}, msg={response.msg}, "
                    f"log_id={response.get_log_id()}"
                )

    def send_text(self, open_id: str, text: str, logical_key: str) -> None:
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("open_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(open_id)
                .msg_type("text")
                .content(json.dumps({"text": text}, ensure_ascii=False))
                .uuid(str(uuid.uuid5(uuid.NAMESPACE_URL, logical_key)))
                .build()
            )
            .build()
        )
        response = self.client.im.v1.message.create(request)
        if not response.success():
            raise FeishuError(
                f"飞书发送失败 code={response.code}, msg={response.msg}, "
                f"log_id={response.get_log_id()}"
            )

    def download_resource(
        self, message_id: str, resource_key: str, resource_type: str
    ) -> tuple[bytes, str | None]:
        request = (
            GetMessageResourceRequest.builder()
            .message_id(message_id)
            .file_key(resource_key)
            .type(resource_type)
            .build()
        )
        response = self.client.im.v1.message_resource.get(request)
        if not response.success() or response.file is None:
            raise FeishuError(
                f"飞书附件下载失败 code={response.code}, msg={response.msg}, "
                f"log_id={response.get_log_id()}"
            )
        return response.file.read(), response.file_name

    def send_file(self, chat_id: str, path: Path, logical_key: str) -> None:
        suffix = path.suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
            with path.open("rb") as stream:
                upload = self.client.im.v1.image.create(
                    CreateImageRequest.builder()
                    .request_body(
                        CreateImageRequestBody.builder()
                        .image_type("message")
                        .image(stream)
                        .build()
                    )
                    .build()
                )
            if not upload.success() or upload.data is None:
                raise FeishuError(
                    f"飞书图片上传失败 code={upload.code}, msg={upload.msg}, "
                    f"log_id={upload.get_log_id()}"
                )
            message_type = "image"
            content = {"image_key": upload.data.image_key}
        else:
            with path.open("rb") as stream:
                upload = self.client.im.v1.file.create(
                    CreateFileRequest.builder()
                    .request_body(
                        CreateFileRequestBody.builder()
                        .file_type("stream")
                        .file_name(path.name)
                        .file(stream)
                        .build()
                    )
                    .build()
                )
            if not upload.success() or upload.data is None:
                raise FeishuError(
                    f"飞书文件上传失败 code={upload.code}, msg={upload.msg}, "
                    f"log_id={upload.get_log_id()}"
                )
            message_type = "file"
            content = {"file_key": upload.data.file_key}
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type(message_type)
                .content(json.dumps(content))
                .uuid(str(uuid.uuid5(uuid.NAMESPACE_URL, logical_key)))
                .build()
            )
            .build()
        )
        response = self.client.im.v1.message.create(request)
        if not response.success():
            raise FeishuError(
                f"飞书文件发送失败 code={response.code}, msg={response.msg}, "
                f"log_id={response.get_log_id()}"
            )

    def _on_message(self, data: P2ImMessageReceiveV1) -> None:
        try:
            event = data.event
            if event is None or event.sender is None or event.message is None:
                return
            sender = event.sender
            message = event.message
            open_id = sender.sender_id.open_id if sender.sender_id else None
            if (
                sender.tenant_key != self.settings.tenant_key
                or open_id != self.settings.owner_open_id
                or message.chat_type != "p2p"
            ):
                logger.warning("Rejected unauthorized or non-p2p message")
                return
            payload = json.loads(message.content or "{}")
            if not isinstance(payload, dict):
                raise ValueError("Feishu message content must be an object")
            if message.message_type in {"image", "file", "media"}:
                attachment = _attachment_details(message.message_type, payload)
                if attachment is None:
                    self.service.accept_notice(
                        message.message_id,
                        open_id,
                        message.chat_id,
                        f"[{message.message_type} message]",
                        "附件消息缺少资源标识，无法下载。",
                    )
                    return
                resource_key, file_name = attachment
                self.service.accept_attachment(
                    message.message_id,
                    open_id,
                    message.chat_id,
                    message.message_type,
                    resource_key,
                    file_name,
                )
                return
            if message.message_type != "text":
                self.service.accept_notice(
                    message.message_id,
                    open_id,
                    message.chat_id,
                    f"[{message.message_type or 'unknown'} message]",
                    "当前版本仅支持文本消息。",
                )
                return
            text = str(payload.get("text", "")).strip()
            if not text:
                return
            self.service.accept_message(
                message.message_id, open_id, message.chat_id, text
            )
        except Exception:
            logger.exception("Failed to accept Feishu message")

    def _on_card_action(
        self, data: P2CardActionTrigger
    ) -> P2CardActionTriggerResponse:
        try:
            event = data.event
            if event is None or event.operator is None or event.context is None:
                return _toast("error", "无效的卡片操作")
            if (
                event.operator.tenant_key != self.settings.tenant_key
                or event.operator.open_id != self.settings.owner_open_id
            ):
                return _toast("error", "无权执行此操作")
            chat_id = event.context.open_chat_id
            value = event.action.value if event.action and event.action.value else {}
            form_value = (
                event.action.form_value
                if event.action and event.action.form_value
                else {}
            )
            result = self.service.card_action(
                event.operator.open_id, chat_id, value, form_value
            )
            return _card_action_response(result)
        except Exception:
            logger.exception("Failed to process card action")
            return _toast("error", "会话切换失败，请重试")


def _toast(kind: str, content: str) -> P2CardActionTriggerResponse:
    return P2CardActionTriggerResponse(
        {"toast": {"type": kind, "content": content}}
    )


def _card_action_response(result) -> P2CardActionTriggerResponse:
    payload = {
        "toast": {"type": result.toast_type, "content": result.message}
    }
    if result.card is not None:
        payload["card"] = {"type": "raw", "data": result.card}
    return P2CardActionTriggerResponse(payload)


def _chunks(text: str, size: int) -> list[str]:
    return [text[index : index + size] for index in range(0, len(text), size)] or [""]


def _attachment_details(
    message_type: str, payload: dict[str, object]
) -> tuple[str, str] | None:
    if message_type == "image":
        key = str(payload.get("image_key", "")).strip()
        name = "image.jpg"
    else:
        key = str(payload.get("file_key", "")).strip()
        fallback = "video.mp4" if message_type == "media" else "attachment.bin"
        name = str(payload.get("file_name", fallback)).strip() or fallback
    return (key[:500], name[:200]) if key else None

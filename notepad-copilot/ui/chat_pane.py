"""Bottom pane: input + thinking/progress log (no final answer)."""
from __future__ import annotations

import hashlib
import re
import sys
import uuid
from dataclasses import dataclass

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.conversation_runner import ConversationRunner


# Footer pattern (token / request stats) — only matched at the END of output.
_FOOTER_LINE_RE = re.compile(
    r"^\s*(Changes\b|Requests\b|Tokens\b|AI Credits\b|↑|↓|Premium\b)"
)

# ANSI escape sequences — Copilot CLI emits color codes via stdout.
_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

# Tool-call markers in Copilot CLI output:
#   ● <tool name / action>            <- start of a successful tool call
#   ✗ <tool name>                      <- start of a failed tool call
#   │ <args / continuation>            <- arg / body line
#   └ <result summary>                 <- end of a tool block
# Narrative ("thinking") text between tool blocks has no marker.
_TOOL_START_RE = re.compile(r"[●✗]\s")
_TOOL_END_RE = re.compile(r"^\s*└", re.MULTILINE)

_FINAL_BEGIN = "<<<NOTEPAD_COPILOT_FINAL_ANSWER>>>"
_FINAL_END = "<<<END_NOTEPAD_COPILOT_FINAL_ANSWER>>>"
_FINAL_BLOCK_RE = re.compile(
    rf"{re.escape(_FINAL_BEGIN)}\s*(.*?)\s*{re.escape(_FINAL_END)}",
    re.DOTALL,
)
_CODELIKE_LINE_RE = re.compile(
    r"^\s*(?:"
    r"\d+\.\s*$|"
    r"\d+\.\s+\S{1,80}$|"
    r"[|`{}\[\]]|"
    r"(?:kubectl|crictl|az|curl|tail|cat|grep|where|summarize|"
    r"ContainerLogV2|namespaceFilteringMode|Exclude_Path)\b"
    r")",
    re.IGNORECASE,
)


def _with_final_answer_contract(prompt: str) -> str:
    """Ask the CLI to keep the user-facing answer complete and final."""
    return (
        f"{prompt.rstrip()}\n\n"
        "请在回答最后给出完整的用户可见结果，保留必要的分析、依据、"
        "查询语句、对比和结论；不要只给一句摘要。\n"
        f"请将这段完整最终结果严格包裹在 {_FINAL_BEGIN} 和 "
        f"{_FINAL_END} 之间；标记内不要输出工具调用、调试信息或 "
        "token 统计说明。"
    )


def _strip_footer(text: str) -> str:
    """Trim trailing footer lines (walk from the bottom)."""
    lines = text.rstrip().splitlines()
    while lines:
        last = lines[-1]
        if not last.strip() or _FOOTER_LINE_RE.match(last):
            lines.pop()
            continue
        break
    return "\n".join(lines).rstrip()


def _extract_marked_answer(text: str) -> str:
    matches = list(_FINAL_BLOCK_RE.finditer(text))
    if not matches:
        return ""
    return matches[-1].group(1).strip()


def _tail_answer_from_unmarked_output(text: str) -> str:
    """Best-effort fallback for older runs where the CLI did not emit markers."""
    return text.strip()


def _strip_tool_blocks(text: str) -> str:
    """Remove CLI tool-call chrome while preserving narrative answer text."""
    cleaned_lines: list[str] = []
    in_block = False
    skip_continuation = False
    for line in text.splitlines():
        if skip_continuation:
            if line.startswith((" ", "\t")) and line.strip():
                continue
            skip_continuation = False

        if not in_block and _TOOL_START_RE.search(line[:6]):
            in_block = True
            continue

        if in_block:
            if _TOOL_END_RE.match(line):
                in_block = False
                skip_continuation = True
            continue

        if line.lstrip().startswith("│"):
            continue
        cleaned_lines.append(line)

    return "\n".join(cleaned_lines).strip()


def _strip_thinking(text: str) -> str:
    """Remove Copilot CLI tool-call traces and interim narration.

    Strategy:
      1. Drop ANSI escapes.
      2. Strip footer (token stats etc.).
      3. If the output contains tool-call markers (`●` / `✗` / `└`),
         remove the tool-call chrome but keep the narrative answer text.
      4. If no markers exist, the whole stream is already the answer.
      5. As a safety net, keep the old "after last └" tail only when the
         cleaned narrative is not materially richer.
    """
    text = _ANSI_RE.sub("", text)
    marked = _extract_marked_answer(text)
    if marked:
        return _strip_footer(marked).strip()

    text = text.replace(_FINAL_BEGIN, "").replace(_FINAL_END, "")
    text = _strip_footer(text)
    if not text:
        return ""

    has_tool_blocks = bool(
        _TOOL_END_RE.search(text) or _TOOL_START_RE.search(text)
    )
    if not has_tool_blocks:
        return _tail_answer_from_unmarked_output(text)

    cleaned = _strip_tool_blocks(text)

    # Find the byte offset just past the last "└ ..." line.
    last_end = -1
    for m in _TOOL_END_RE.finditer(text):
        # Walk forward to end of that physical line, then skip any
        # contiguous wrap-continuation lines (Copilot wraps long
        # tool output by indenting follow-on lines with spaces).
        line_end = text.find("\n", m.end())
        if line_end == -1:
            line_end = len(text)
        # Continuation lines start with whitespace (indentation).
        i = line_end
        while i < len(text):
            nl = text.find("\n", i + 1)
            seg_end = nl if nl != -1 else len(text)
            seg = text[i + 1:seg_end]
            if seg.startswith((" ", "\t")) and seg.strip():
                i = seg_end
                continue
            break
        last_end = i

    tail = text[last_end + 1:].strip() if last_end >= 0 else ""

    # Older behavior returned only `tail`, which often collapses a detailed
    # final answer down to a last-line "总结". Prefer the full non-tool
    # narrative when it clearly contains more user-facing content.
    if cleaned and (not tail or len(cleaned) > len(tail) * 1.25):
        return cleaned

    if len(tail) >= 40:
        return tail

    return cleaned or tail or text.strip()


def _split_answer_and_footer(buf: str) -> tuple[str, str]:
    """Return (clean_answer, footer). Backwards-compatible wrapper."""
    answer = _strip_thinking(buf)
    # Footer extraction kept for callers that want it; we don't expose
    # it back to the UI any more.
    return answer, ""


@dataclass
class _PendingMessage:
    prompt: str
    note_hash: str | None
    image_keys: list[str]
    addition: bool


class ChatPane(QWidget):
    """Input row + thinking/progress log.

    Streams Copilot's live output here as "thinking". When a request
    finishes, the cleaned-up answer (footer stripped) is emitted via
    `answer_ready` so the right-side ResultPane can show it.
    """

    send_requested = Signal(str)        # prompt only; main injects note ctx
    answer_ready = Signal(str, str)     # (question, final_answer_markdown)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = QSettings("NotepadCopilot", "NotepadCopilot")
        self.runner = ConversationRunner(self)
        self._buffer: list[str] = []
        self._current_question: str = ""
        self._task_prompts: list[str] = []
        self._pending: dict[str, _PendingMessage] = {}
        self._failed_messages: list[str] = []
        self._stopping = False
        # Resolved-path strings of images already sent to Copilot during
        # the current session. Used to compute what's new on each send.
        self._sent_image_keys: set[str] = set()
        # Hash of the last note-text we sent; "" before any send.
        self._last_note_hash: str = ""
        self._build_ui()
        self._connect_runner()

    # --- UI -----------------------------------------------------------

    def _build_ui(self):
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header bar
        header = QWidget()
        header.setObjectName("PaneHeader")
        bar = QHBoxLayout(header)
        bar.setContentsMargins(12, 6, 8, 6)
        bar.setSpacing(6)
        title = QLabel("💬  Chat")
        title.setObjectName("PaneTitle")
        bar.addWidget(title)
        subtitle = QLabel("Progress · final answers on the right")
        subtitle.setWordWrap(True)
        subtitle.setObjectName("FieldLabel")
        bar.addWidget(subtitle)
        bar.addStretch(1)

        controls = QHBoxLayout()
        controls.setContentsMargins(12, 6, 8, 6)
        controls.setSpacing(6)
        model_label = QLabel("Model")
        model_label.setObjectName("FieldLabel")
        controls.addWidget(model_label)

        self.model_combo = QComboBox()
        self.model_combo.addItem("User default", "")
        self.model_combo.addItem(
            "GPT-5.6 Sol Fast (Internal only)", "gpt-5.6-sol-fast"
        )
        self.model_combo.addItem("GPT-5.6 Sol", "gpt-5.6-sol")
        self.model_combo.addItem("GPT-6 Astra", "gpt-6-astra")
        self.model_combo.setToolTip(
            "Select a model for future requests in this window. User default omits --model."
        )
        saved_model = self._settings.value("copilot/model", "", type=str)
        model_index = self.model_combo.findData(saved_model)
        self.model_combo.setCurrentIndex(max(0, model_index))
        self.model_combo.currentIndexChanged.connect(
            self._on_model_selection_changed
        )
        controls.addWidget(self.model_combo)

        self.effort_combo = QComboBox()
        for effort in ("medium", "low", "high", "xhigh", "max"):
            self.effort_combo.addItem(effort, effort)
        self.effort_combo.setToolTip(
            "Reasoning effort (Copilot CLI --reasoning-effort)."
        )
        saved_effort = self._settings.value(
            "copilot/reasoning_effort", "medium", type=str
        )
        effort_index = self.effort_combo.findData(saved_effort)
        self.effort_combo.setCurrentIndex(max(0, effort_index))
        self.effort_combo.currentIndexChanged.connect(
            self._on_model_selection_changed
        )
        controls.addWidget(self.effort_combo)

        self.auto_sync_chk = QCheckBox("Auto-sync notes")
        self.auto_sync_chk.setChecked(True)
        self.auto_sync_chk.setToolTip(
            "When enabled, include the latest notes whenever they change.\n"
            "When disabled, include notes only with the first request, then resume this window's session."
        )
        controls.addWidget(self.auto_sync_chk)

        self.live_chk = QCheckBox("Live input")
        self.live_chk.setToolTip(
            "Use the Copilot SDK to add requests before the next model step. "
            "Late messages continue in the next turn; completed tool actions are not undone.\n"
            "Switching modes resets this window's AI session. Disable for legacy -p mode."
        )
        live_enabled = self._settings.value(
            "copilot/live_input", True, type=bool
        ) and sys.version_info >= (3, 11)
        self.live_chk.setChecked(live_enabled)
        self.runner.set_live_mode(live_enabled)
        self.live_chk.setEnabled(sys.version_info >= (3, 11))
        self.live_chk.toggled.connect(self._on_live_mode_changed)
        controls.addWidget(self.live_chk)
        controls.addStretch(1)

        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("StatusPill")
        self.status_label.setProperty("state", "ok")
        bar.addWidget(self.status_label)

        self.reset_btn = QPushButton("Reset Session")
        self.reset_btn.setToolTip(
            "End this session and clear chat output. The next request starts a new Copilot session."
        )
        self.reset_btn.clicked.connect(self._on_reset_session)
        bar.addWidget(self.reset_btn)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setProperty("danger", True)
        self.stop_btn.clicked.connect(self._on_stop)
        bar.addWidget(self.stop_btn)

        layout.addWidget(header)
        layout.addLayout(controls)

        # Body wrapper (inset around transcript + input)
        body = QWidget()
        body_v = QVBoxLayout(body)
        body_v.setContentsMargins(8, 8, 8, 8)
        body_v.setSpacing(6)

        # Transcript
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        font = QFont("Cascadia Mono")
        font.setStyleHint(QFont.Monospace)
        font.setPointSize(10)
        self.output.setFont(font)
        body_v.addWidget(self.output, 1)

        delivery_row = QHBoxLayout()
        self.delivery_label = QLabel(
            "Add requests while a task runs. Completed actions are not automatically undone."
        )
        self.delivery_label.setWordWrap(True)
        self.delivery_label.setObjectName("FieldLabel")
        delivery_row.addWidget(self.delivery_label, 1)
        self.retry_btn = QPushButton("Restore Unsent")
        self.retry_btn.clicked.connect(self._restore_failed_message)
        self.retry_btn.hide()
        delivery_row.addWidget(self.retry_btn)
        body_v.addLayout(delivery_row)

        # Input row
        input_row = QHBoxLayout()
        input_row.setSpacing(6)
        self.input = QLineEdit()
        self.input.setPlaceholderText(
            "Ask Copilot… (Enter to send; the first request includes your notes)"
        )
        self.input.returnPressed.connect(self._on_send)
        input_row.addWidget(self.input, 1)

        self.send_btn = QPushButton("➤  Send")
        self.send_btn.setProperty("accent", True)
        self.send_btn.setToolTip(
            "The first request includes your notes and images. Later requests resume "
            "this window's session, with changed notes if auto-sync is enabled."
        )
        self.send_btn.clicked.connect(self._on_send)
        input_row.addWidget(self.send_btn)

        body_v.addLayout(input_row)
        layout.addWidget(body, 1)
        self._apply_model_selection()
        self._set_status(
            "Ready" if self.runner.submissions_allowed() else "Resetting…",
            busy=False,
        )

    # --- runner wiring ------------------------------------------------

    def _connect_runner(self):
        self.runner.output_received.connect(self._on_output_chunk)
        self.runner.process_started.connect(
            lambda: self._set_status("Running…", busy=True)
        )
        self.runner.process_finished.connect(self._on_finished)
        self.runner.error_occurred.connect(
            lambda msg: self._append_output(f"\n[Error] {msg}\n")
        )
        self.runner.message_accepted.connect(self._on_message_accepted)
        self.runner.message_rejected.connect(self._on_message_rejected)
        self.runner.info_received.connect(self._on_runner_info)

    def _on_output_chunk(self, text: str):
        self._buffer.append(text)
        self._append_output(text)

    # --- event handlers -----------------------------------------------

    def _on_reset_session(self):
        self.reset_session()
        self._append_output("--- Session cleared. The next request starts a new session. ---\n")

    def reset_session(self):
        # Drop UI requests before the runner fences callbacks from the old case.
        self._pending.clear()
        self.runner.reset_session()
        self._buffer = []
        self._task_prompts = []
        self._current_question = ""
        self._failed_messages.clear()
        self.retry_btn.hide()
        self._stopping = False
        self._sent_image_keys.clear()
        self._last_note_hash = ""
        self.output.clear()
        self.delivery_label.setText("Session reset.")
        self._set_status("Ready", busy=False)

    def _on_live_mode_changed(self, enabled: bool):
        if not self.runner.set_live_mode(enabled):
            self.live_chk.blockSignals(True)
            self.live_chk.setChecked(self.runner.live_mode)
            self.live_chk.blockSignals(False)
            return
        self._settings.setValue("copilot/live_input", enabled)
        self.reset_session()
        self.delivery_label.setText(
            "Live session: add requests while running; late messages continue in the next turn."
            if enabled else "Legacy mode: wait for the task to finish before sending another request."
        )

    def _on_model_selection_changed(self, _index: int):
        self._apply_model_selection()

    def _apply_model_selection(self):
        model = str(self.model_combo.currentData() or "")
        effort = str(self.effort_combo.currentData() or "medium")
        self._settings.setValue("copilot/model", model)
        self._settings.setValue("copilot/reasoning_effort", effort)
        self.runner.set_model(model or None, effort if model else None)

    def _on_send(self):
        text = self.input.text().strip()
        if not text:
            if self.runner.is_running():
                self.delivery_label.setText("Enter an additional request first.")
                self.input.setFocus()
                return
            text = "Please review my notes and provide feedback or suggestions."
        self.send_requested.emit(text)

    @staticmethod
    def _hash_note(text: str) -> str:
        return hashlib.md5(
            (text or "").strip().encode("utf-8", errors="replace")
        ).hexdigest()

    def send(self, prompt: str, note: str = "",
             attachments: list | None = None):
        """Dispatch a prompt.

        Behavior:
        - First send in a session: prepend the full note text plus every
          image found in the editor as ``--attachment``.
        - Subsequent sends:
            * If "自动同步笔记更新" is on AND the note text changed since
              the last send → prepend "笔记已更新, 最新版本如下" block.
            * Otherwise (no change or auto-sync off) → only the prompt.
            * Always attach images that were newly added to the editor
              since the last send.
        """
        if self._stopping:
            self.delivery_label.setText("Stopping. Wait until the task stops before sending.")
            return
        addition = self.runner.is_running() or bool(self._pending)
        if addition and not self.runner.live_mode:
            self.delivery_label.setText(
                "Live input is unavailable in legacy mode. Wait for completion or stop the task first."
            )
            return
        if not addition:
            self._buffer = []
            self._task_prompts = []
            self._current_question = prompt

        attachments = attachments or []
        # Compute which attachments are new for this turn.
        new_attachments: list = []
        new_keys: list[str] = []
        for p in attachments:
            try:
                key = str(p.resolve()) if hasattr(p, "resolve") else str(p)
            except Exception:
                key = str(p)
            if key not in self._sent_image_keys:
                new_attachments.append(p)
                new_keys.append(key)

        note_text = (note or "").strip()
        cur_hash = self._hash_note(note_text)
        is_first = not self.runner.has_session_history() and not addition
        auto_sync = self.auto_sync_chk.isChecked()
        note_changed = (cur_hash != self._last_note_hash)

        # Decide whether to embed the (latest) note in this turn.
        embed_full_note = False
        embed_update_note = False
        if is_first and note_text:
            embed_full_note = True
        elif (not is_first) and auto_sync and note_changed and note_text:
            embed_update_note = True

        # Build prompt
        labels: list[str] = []
        if embed_full_note:
            attach_note = (
                f"\n（同时附带 {len(new_attachments)} 张笔记中的图片，"
                "请一并参考。）"
            ) if new_attachments else ""
            full = (
                "以下是我的笔记内容（请作为上下文）:\n"
                "----- BEGIN NOTE -----\n"
                f"{note_text}\n"
                "----- END NOTE -----"
                f"{attach_note}\n\n"
                f"我的问题: {prompt}"
            )
            labels.append("with notes")
        elif embed_update_note:
            attach_note = (
                f"\n（笔记里同时新增 {len(new_attachments)} 张图片，"
                "请一并参考。）"
            ) if new_attachments else ""
            full = (
                "📝 我的笔记已更新，下面是最新完整版本，"
                "请以这个版本为准回答后续问题:\n"
                "----- BEGIN UPDATED NOTE -----\n"
                f"{note_text}\n"
                "----- END UPDATED NOTE -----"
                f"{attach_note}\n\n"
                f"我的问题: {prompt}"
            )
            labels.append("notes updated")
        else:
            full = prompt

        if addition:
            full = (
                "这是对当前任务的补充要求。请保留原任务目标，"
                "结合已完成的工作纳入下面的新条件或问题，"
                "最终统一答复原问题和全部补充问题。"
                "如果原回答已经完成，请在同一会话接续处理；"
                "不要重复执行已经完成的操作。\n\n" + full
            )
        full = _with_final_answer_contract(full)

        if new_attachments:
            labels.append(f"+{len(new_attachments)} images")
        label = f"[{' '.join(labels)}] " if labels else ""
        request_id = uuid.uuid4().hex
        self._pending[request_id] = _PendingMessage(
            prompt, cur_hash if embed_full_note or embed_update_note else None,
            new_keys, addition,
        )
        kind = "addition" if addition else "question"
        self._append_output(f"\n>>> [{kind} · submitting] {label}{prompt}\n")
        self.delivery_label.setText(
            f"Submitting {kind} ({len(self._pending)} awaiting confirmation)…"
        )
        draft = self.input.text()
        submitted = self.runner.submit(
            request_id, full, attachments=new_attachments or None,
        )
        if submitted and self.input.text() == draft:
            self.input.clear()
        elif not submitted and request_id in self._pending:
            self._on_message_rejected(request_id, "Submission failed. Check the error details.")

    def _on_message_accepted(self, request_id: str):
        message = self._pending.pop(request_id, None)
        if message is None:
            return
        self._sent_image_keys.update(message.image_keys)
        if message.note_hash is not None:
            self._last_note_hash = message.note_hash
        self._task_prompts.append(message.prompt)
        self._current_question = self._task_prompts[0]
        for index, prompt in enumerate(self._task_prompts[1:], 1):
            self._current_question += f"\n\nAdditional request {index}: {prompt}"
        description = (
            "CLI accepted the addition for a later step. "
            "If this turn has ended, it will continue in the next turn."
            if message.addition else "CLI accepted the question and is processing it."
        )
        self._append_output(f"\n[Accepted] {message.prompt}\n")
        self.delivery_label.setText(
            f"{description} Awaiting confirmation: {len(self._pending)}."
        )

    def _on_message_rejected(self, request_id: str, reason: str):
        message = self._pending.pop(request_id, None)
        if message is None:
            return
        self._append_output(f"\n[Not delivered] {message.prompt}\n{reason}\n")
        self.delivery_label.setText(f"Not delivered: {reason}")
        self._preserve_failed_message(message.prompt)
        if not self.runner.is_running() and not self._pending:
            self._set_status("Send failed", busy=False)

    def _preserve_failed_message(self, text: str):
        if not self.input.text():
            self.input.setText(text)
        elif self.input.text().strip() != text.strip():
            self._failed_messages.append(text)
            self.retry_btn.setText(f"Restore Unsent ({len(self._failed_messages)})")
            self.retry_btn.show()

    def _restore_failed_message(self):
        if self.input.text().strip():
            self.delivery_label.setText("Send or clear your current draft before restoring an unsent message.")
            return
        if self._failed_messages:
            self.input.setText(self._failed_messages.pop(0))
            self.input.setFocus()
        self.retry_btn.setText(f"Restore Unsent ({len(self._failed_messages)})")
        self.retry_btn.setVisible(bool(self._failed_messages))

    def _on_runner_info(self, text: str):
        self._append_output(f"\n[Session] {text}\n")
        self.delivery_label.setText(text)
        if not self.runner.is_running():
            self._set_status(
                "Ready" if self.runner.submissions_allowed() else "Resetting…",
                busy=False,
            )

    def _on_stop(self):
        if self.runner.is_running():
            self._stopping = True
            self._set_status("Stopping…", busy=True)
            self.runner.stop()
        else:
            self._set_status("No running task", busy=False)

    def _on_finished(self, code: int):
        self._stopping = False
        self._append_output(f"\n[Task ended, code={code}]\n")
        buffered_output = "".join(self._buffer)
        runner_output = self.runner.last_output()
        full_output = runner_output or buffered_output
        for request_id in list(self._pending):
            self._on_message_rejected(
                request_id, "The task ended without confirming receipt of this message. Please resend it."
            )
        if code == -2:
            self._set_status("Stopped", busy=False)
            self.delivery_label.setText(
                "Stopped. Completed actions are not undone. You can send another request."
            )
            self._buffer = []
            return
        if code != 0:
            self._set_status("Task failed", busy=False)
            # Re-send context on retry even if a failed turn persisted partially.
            self._sent_image_keys.clear()
            self._last_note_hash = ""
            if self._current_question:
                self._preserve_failed_message(self._current_question)
            self.delivery_label.setText("Task failed. Your question was preserved; no result was saved.")
            self._append_output(
                "\n[Request failed; no result was saved. Revise your request and try again.]\n"
            )
            self._buffer = []
            return

        self._set_status("Ready", busy=False)
        if self.runner.live_mode:
            # Late steering can create multiple completed turns before idle.
            # Keep every marked final answer, not only the last one.
            marked = list(_FINAL_BLOCK_RE.finditer(full_output))
            answer = "\n\n---\n\n".join(
                _strip_footer(match.group(1)).strip() for match in marked
            ) if marked else _strip_thinking(full_output)
        else:
            answer, _footer = _split_answer_and_footer(full_output)
        if answer:
            self.answer_ready.emit(self._current_question, answer)
        self.delivery_label.setText(
            f"Task complete. {len(self._task_prompts)} messages accepted; see results on the right."
            if answer else "Task ended without a final answer to display."
        )
        self._buffer = []

    # --- helpers ------------------------------------------------------

    def _append_output(self, text: str):
        self.output.moveCursor(QTextCursor.End)
        self.output.insertPlainText(text)
        self.output.moveCursor(QTextCursor.End)

    def _set_status(self, text: str, busy: bool):
        self.status_label.setText(text)
        self.status_label.setProperty("state", "busy" if busy else "ok")
        # Re-polish so the [state="..."] selector takes effect.
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        ready = self.runner.submissions_allowed()
        self.model_combo.setEnabled(not busy and ready)
        self.effort_combo.setEnabled(not busy and ready)
        self.live_chk.setEnabled(not busy and ready and sys.version_info >= (3, 11))
        self.reset_btn.setEnabled(not busy)
        self.stop_btn.setEnabled(busy and not self._stopping)
        self.send_btn.setEnabled(
            ready and not self._stopping and (not busy or self.runner.live_mode)
        )
        self.send_btn.setText("➤  Add Request" if busy else "➤  Send")
        self.input.setPlaceholderText(
            "Add a condition or question… (Enter to submit; completed actions are not undone)"
            if busy and self.runner.live_mode else
            "Ask Copilot… (Enter to send; the first request includes your notes)"
        )

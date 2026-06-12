"""Bottom pane: input + thinking/progress log (no final answer)."""
from __future__ import annotations

import hashlib
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.copilot_runner import CopilotRunner


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
        self.runner = CopilotRunner(self)
        self._buffer: list[str] = []
        self._current_question: str = ""
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
        title = QLabel("💬  对话")
        title.setObjectName("PaneTitle")
        bar.addWidget(title)
        subtitle = QLabel("思考过程 · 最终回答见右侧结果区")
        subtitle.setObjectName("FieldLabel")
        bar.addWidget(subtitle)
        bar.addStretch(1)

        self.auto_sync_chk = QCheckBox("自动同步笔记更新")
        self.auto_sync_chk.setChecked(True)
        self.auto_sync_chk.setToolTip(
            "勾选后：每次发送前自动检测笔记是否变化，"
            "有变化就把最新笔记一起发给 Copilot。\n"
            "取消勾选：仅首次发送带笔记，之后依赖 --continue 记忆。"
        )
        bar.addWidget(self.auto_sync_chk)

        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("StatusPill")
        self.status_label.setProperty("state", "ok")
        bar.addWidget(self.status_label)

        self.reset_btn = QPushButton("清除会话")
        self.reset_btn.setToolTip(
            "结束当前会话，清空对话输出，下次发送会开启全新的 Copilot 会话"
        )
        self.reset_btn.clicked.connect(self._on_reset_session)
        bar.addWidget(self.reset_btn)

        self.stop_btn = QPushButton("停止")
        self.stop_btn.setProperty("danger", True)
        self.stop_btn.clicked.connect(self._on_stop)
        bar.addWidget(self.stop_btn)

        layout.addWidget(header)

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

        # Input row
        input_row = QHBoxLayout()
        input_row.setSpacing(6)
        self.input = QLineEdit()
        self.input.setPlaceholderText(
            "向 Copilot 提问…  (Enter 发送，首条会自动带上方笔记)"
        )
        self.input.returnPressed.connect(self._on_send)
        input_row.addWidget(self.input, 1)

        self.send_btn = QPushButton("➤  发送")
        self.send_btn.setProperty("accent", True)
        self.send_btn.setToolTip(
            "首条发送会自动把上方笔记（文本+图片）作为上下文，"
            "之后的发送依赖 Copilot --continue 记忆，只发输入文本。"
        )
        self.send_btn.clicked.connect(self._on_send)
        input_row.addWidget(self.send_btn)

        body_v.addLayout(input_row)
        layout.addWidget(body, 1)

    # --- runner wiring ------------------------------------------------

    def _connect_runner(self):
        self.runner.output_received.connect(self._on_output_chunk)
        self.runner.process_started.connect(
            lambda: self._set_status("运行中…", busy=True)
        )
        self.runner.process_finished.connect(self._on_finished)
        self.runner.error_occurred.connect(
            lambda msg: self._append_output(f"\n[错误] {msg}\n")
        )

    def _on_output_chunk(self, text: str):
        self._buffer.append(text)
        self._append_output(text)

    # --- event handlers -----------------------------------------------

    def _on_reset_session(self):
        self.runner.reset_session()
        self._sent_image_keys.clear()
        self._last_note_hash = ""
        self.output.clear()
        self._append_output("--- 已清除会话（下次发送将开启全新会话）---\n")
        self._set_status("就绪", busy=False)

    def _on_send(self):
        text = self.input.text().strip()
        if not text:
            text = "请阅读上方笔记内容并给出反馈或建议。"
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
        # Reset per-request answer buffer; remember the question so we
        # can label the result block on the right.
        self._buffer = []
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
        is_first = not self.runner.has_session_history()
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
            labels.append("带笔记")
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
            labels.append("笔记已更新")
        else:
            full = prompt

        full = _with_final_answer_contract(full)

        if new_attachments:
            labels.append(f"+{len(new_attachments)}图")
        label = f"[{' '.join(labels)}] " if labels else ""
        self._append_output(f"\n>>> {label}{prompt}\n")
        self.input.clear()
        self.runner.send(full, attachments=new_attachments or None)

        # Mark these images as already sent so we don't re-attach them
        # next time. Only do this after a successful spawn was issued.
        for key in new_keys:
            self._sent_image_keys.add(key)
        # Remember the note hash we just sent (if any). When we DIDN'T
        # send the note (e.g. auto-sync off, or unchanged), keep the old
        # hash unchanged so the next change is still detected.
        if embed_full_note or embed_update_note:
            self._last_note_hash = cur_hash

    def _on_stop(self):
        if self.runner.is_running():
            self.runner.stop()
            self._append_output("\n[已停止]\n")
            self._set_status("已停止", busy=False)
        else:
            self._set_status("无运行中进程", busy=False)

    def _on_finished(self, code: int):
        self._append_output(f"\n[进程退出 code={code}]\n")
        self._set_status("就绪", busy=False)
        buffered_output = "".join(self._buffer)
        runner_output = self.runner.last_output()
        full_output = runner_output or buffered_output
        answer, _footer = _split_answer_and_footer(full_output)
        if answer:
            self.answer_ready.emit(self._current_question, answer)
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

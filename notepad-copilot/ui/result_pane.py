"""Right pane: render Copilot's final answers as Markdown."""
from __future__ import annotations

import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)


class ResultPane(QWidget):
    """Displays only the final, cleaned-up answers from Copilot."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget()
        header.setObjectName("PaneHeader")
        bar = QHBoxLayout(header)
        bar.setContentsMargins(12, 6, 8, 6)
        bar.setSpacing(6)
        title = QLabel("📋  结果")
        title.setObjectName("PaneTitle")
        bar.addWidget(title)
        subtitle = QLabel("AI 最终回答")
        subtitle.setObjectName("FieldLabel")
        bar.addWidget(subtitle)
        bar.addStretch(1)
        self.archive_btn = QPushButton("📦  本地归档")
        self.archive_btn.setProperty("accent", True)
        self.archive_btn.setToolTip(
            "脱敏后保存到案例目录\n（快捷键 Ctrl+Shift+A）")
        bar.addWidget(self.archive_btn)
        self.cloud_btn = QPushButton("☁  云端归档")
        self.cloud_btn.setToolTip(
            "脱敏后上传到 Azure DevOps Wiki\n（菜单：案例 → Wiki 配置）"
        )
        bar.addWidget(self.cloud_btn)
        self.clear_btn = QPushButton("清空")
        self.clear_btn.setProperty("danger", True)
        self.clear_btn.clicked.connect(self.clear)
        bar.addWidget(self.clear_btn)
        layout.addWidget(header)

        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.view.setReadOnly(True)
        self.view.setFrameShape(QTextBrowser.NoFrame)
        layout.addWidget(self.view, 1)

        self._md_parts: list[str] = []
        self._qa_pairs: list[tuple[str, str, str]] = []  # (ts, q, a)

    def clear(self):
        self._md_parts.clear()
        self._qa_pairs.clear()
        self.view.clear()

    def append_answer(self, question: str, answer: str):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self._qa_pairs.append((ts, question, answer.strip()))
        block = (
            f"\n---\n\n"
            f"**🗨 {ts} — {question}**\n\n"
            f"{answer.strip() or '_（无内容）_'}\n"
        )
        self._md_parts.append(block)
        self.view.setMarkdown("".join(self._md_parts))
        # scroll to bottom
        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.view.setTextCursor(cursor)
        self.view.ensureCursorVisible()

    def load_qa_history(self, history: list[tuple[str, str, str]],
                        banner: str = "") -> None:
        """Replace current view with a previously persisted Q&A history.

        `history` is a list of (timestamp_short, question, answer).
        An optional `banner` (markdown) is prepended.
        """
        self._md_parts.clear()
        self._qa_pairs.clear()
        if banner:
            self._md_parts.append(banner.rstrip() + "\n\n")
        for ts, q, a in history:
            self._qa_pairs.append((ts, q, a))
            self._md_parts.append(
                f"\n---\n\n"
                f"**🗨 {ts} — {q}**\n\n"
                f"{a.strip() or '_（无内容）_'}\n"
            )
        self.view.setMarkdown("".join(self._md_parts))
        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.view.setTextCursor(cursor)
        self.view.ensureCursorVisible()

    def qa_pairs(self) -> list[tuple[str, str, str]]:
        """Return a copy of (timestamp, question, answer) tuples."""
        return list(self._qa_pairs)

    def to_markdown(self) -> str:
        """Render full Q&A history as a markdown string."""
        if not self._qa_pairs:
            return "_（暂无对话）_\n"
        out = []
        for ts, q, a in self._qa_pairs:
            out.append(f"### 🗨 {ts} — {q}\n\n{a or '_（无内容）_'}\n")
        return "\n---\n\n".join(out) + "\n"



"""Right pane: render Copilot's final answers as HTML."""
from __future__ import annotations

import datetime
import html
import os
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from markdown import markdown as _markdown_to_html


_LOG_FILE = (
    Path(os.environ.get("USERPROFILE", os.path.expanduser("~")))
    / "OneDrive - Microsoft" / "Documents" / "VS-Code-Workspace"
    / "copilot-temp" / "sessions" / "notepad-ui.log"
)


def _log(msg: str) -> None:
    try:
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        with _LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] ResultPane {msg}\n")
    except Exception:
        pass


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
        self.subtitle = QLabel("AI 最终回答")
        self.subtitle.setObjectName("FieldLabel")
        bar.addWidget(self.subtitle)
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
        self.view.setLineWrapMode(QTextEdit.WidgetWidth)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(self.view, 1)

        self._md_parts: list[str] = []
        self._qa_pairs: list[tuple[str, str, str]] = []  # (ts, q, a)
        self._banner = ""
        self._render_seq = 0

    def clear(self):
        self._md_parts.clear()
        self._qa_pairs.clear()
        self._banner = ""
        self.view.clear()
        self.subtitle.setText("AI 最终回答")
        _log("clear")

    def append_answer(self, question: str, answer: str):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self._qa_pairs.append((ts, question, answer.strip()))
        block = (
            f"\n---\n\n"
            f"**🗨 {ts} — {question}**\n\n"
            f"{answer.strip() or '_（无内容）_'}\n"
        )
        self._md_parts.append(block)
        self._render()

    def load_qa_history(self, history: list[tuple[str, str, str]],
                        banner: str = "") -> None:
        """Replace current view with a previously persisted Q&A history.

        `history` is a list of (timestamp_short, question, answer).
        An optional `banner` (markdown) is prepended.
        """
        self._md_parts.clear()
        self._qa_pairs.clear()
        self._banner = banner.rstrip() if banner else ""
        if banner:
            self._md_parts.append(banner.rstrip() + "\n\n")
        for ts, q, a in history:
            self._qa_pairs.append((ts, q, a))
            self._md_parts.append(
                f"\n---\n\n"
                f"**🗨 {ts} — {q}**\n\n"
                f"{a.strip() or '_（无内容）_'}\n"
            )
        self._render()

    def _render(self) -> None:
        self._render_seq += 1
        seq = self._render_seq
        text = "".join(self._md_parts)
        # QTextBrowser.setMarkdown() drops large portions of long mixed
        # Chinese/Markdown histories in Qt. Render our own HTML document
        # instead: it keeps every answer intact while preserving Markdown
        # semantics such as headings, lists, tables, and code fences.
        self.view.setHtml(self._to_display_html())
        count = len(self._qa_pairs)
        self.subtitle.setText(
            f"AI 最终回答 · {count} 条" if count else "AI 最终回答"
        )
        _log(f"render seq={seq} qa={count} md_chars={len(text)}")
        self._scroll_to_bottom(seq)

    def _to_display_html(self) -> str:
        cards: list[str] = []
        if self._banner:
            cards.append(
                "<div class='banner'>"
                f"{html.escape(self._banner)}"
                "</div>"
            )

        for idx, (ts, question, answer) in enumerate(self._qa_pairs, start=1):
            q = html.escape(question.strip() or "（无问题）")
            a = self._answer_to_html(answer)
            cards.append(
                "<div class='qa-card'>"
                "<div class='qa-header'>"
                f"<span class='qa-index'>#{idx}</span>"
                f"<span class='qa-time'>🗨 {html.escape(ts)}</span>"
                "</div>"
                f"<div class='qa-question'>{q}</div>"
                f"<div class='qa-answer'>{a}</div>"
                "</div>"
                "<div class='qa-gap'></div>"
            )

        body = "\n".join(cards) if cards else (
            "<div class='empty'>（暂无对话）</div>"
        )
        return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
body {{
  margin: 0;
  padding: 12px;
  color: #1f2937;
  background: #ffffff;
  font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
  font-size: 13px;
  line-height: 1.55;
  overflow-wrap: anywhere;
  word-wrap: break-word;
}}
.banner {{
  margin: 0 0 12px 0;
  padding: 10px 12px;
  border: 1px solid #c7d2fe;
  border-left: 4px solid #6366f1;
  border-radius: 8px;
  background: #eef2ff;
  white-space: pre-wrap;
}}
.qa-card {{
  margin: 0 0 16px 0;
  padding: 0;
  border: 1px solid #cfd8e3;
  background: #ffffff;
  width: 100%;
}}
.qa-header {{
  padding: 8px 12px;
  border-bottom: 1px solid #e5edf5;
  background: #f7fbff;
  color: #334155;
  font-weight: 600;
}}
.qa-gap {{
  height: 14px;
  border-top: 2px solid #e0ecff;
}}
.qa-index {{
  color: #1677ff;
  margin-right: 10px;
}}
.qa-time {{
  color: #475569;
}}
.qa-question {{
  padding: 10px 12px;
  border-bottom: 1px dashed #dbe4ee;
  color: #0f172a;
  font-weight: 600;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-wrap: break-word;
}}
.qa-answer {{
  padding: 12px;
  color: #111827;
  background: #ffffff;
  font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
  font-size: 13px;
  line-height: 1.55;
  overflow-wrap: anywhere;
  word-wrap: break-word;
}}
.qa-answer h1, .qa-answer h2, .qa-answer h3 {{
  margin: 10px 0 8px 0;
  line-height: 1.3;
}}
.qa-answer h1 {{
  font-size: 20px;
  border-bottom: 1px solid #e5edf5;
  padding-bottom: 6px;
}}
.qa-answer h2 {{
  font-size: 17px;
  border-bottom: 1px solid #edf2f7;
  padding-bottom: 4px;
}}
.qa-answer h3 {{
  font-size: 15px;
}}
.qa-answer p {{
  margin: 8px 0;
}}
.qa-answer ul, .qa-answer ol {{
  margin: 8px 0 8px 22px;
  padding: 0;
}}
.qa-answer li {{
  margin: 3px 0;
}}
.qa-answer blockquote {{
  margin: 8px 0;
  padding: 2px 10px;
  color: #475569;
  border-left: 4px solid #cbd5e1;
  background: #f8fafc;
}}
.qa-answer code {{
  padding: 1px 5px;
  background: #f1f5f9;
  border-radius: 4px;
  font-family: "Cascadia Mono", Consolas, "Courier New", monospace;
  font-size: 12px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-wrap: break-word;
}}
.qa-answer pre {{
  margin: 10px 0;
  padding: 12px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-wrap: break-word;
}}
.qa-answer pre code {{
  padding: 0;
  background: transparent;
  white-space: pre-wrap;
}}
.qa-answer table {{
  border-collapse: collapse;
  width: 100%;
  table-layout: fixed;
  margin: 10px 0;
}}
.qa-answer th, .qa-answer td {{
  border: 1px solid #cfd8e3;
  padding: 6px 8px;
  overflow-wrap: anywhere;
  word-wrap: break-word;
}}
.qa-answer th {{
  background: #f1f5f9;
  font-weight: 600;
}}
.empty {{
  padding: 18px;
  color: #64748b;
  text-align: center;
}}
</style>
</head>
<body>
{body}
</body>
</html>"""

    def _answer_to_html(self, answer: str) -> str:
        text = answer.strip()
        if not text:
            return "<p><em>（无内容）</em></p>"
        # Escape raw HTML first so Copilot output cannot inject arbitrary tags,
        # while Markdown syntax is still rendered into readable HTML.
        safe_markdown = html.escape(text, quote=False)
        return _markdown_to_html(
            safe_markdown,
            extensions=["extra", "sane_lists", "nl2br", "tables", "fenced_code"],
            output_format="html5",
        )

    def _scroll_to_bottom(self, seq: int | None = None) -> None:
        self._scroll_to_bottom_now(seq)
        for delay in (0, 50, 200, 500):
            QTimer.singleShot(
                delay,
                lambda seq=seq: self._scroll_to_bottom_now(seq),
            )

    def _scroll_to_bottom_now(self, seq: int | None = None) -> None:
        if seq is not None and seq != self._render_seq:
            return
        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.view.setTextCursor(cursor)
        self.view.ensureCursorVisible()
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())
        _log(
            "scroll "
            f"seq={self._render_seq} value={bar.value()} max={bar.maximum()}"
        )

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

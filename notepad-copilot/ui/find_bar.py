"""Browser-style, non-destructive find for rich-text panes."""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QTextEdit, QToolButton, QWidget,
)


class FindBar(QWidget):
    def __init__(self, target: QTextEdit, scope: QWidget):
        super().__init__(scope)
        self.target = target
        self._matches: list[QTextCursor] = []
        self._current = -1
        self._active = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)

        self.input = QLineEdit()
        self.input.setPlaceholderText("查找当前区域…")
        self.input.setAccessibleName("查找文本")
        self.input.setClearButtonEnabled(True)
        self.input.setMinimumWidth(80)
        layout.addWidget(self.input, 1)
        self.count = QLabel("0/0")
        self.count.setMinimumWidth(48)
        layout.addWidget(self.count)
        self.previous_btn = QToolButton()
        self.previous_btn.setText("↑")
        self.previous_btn.setToolTip("上一个 (Shift+Enter / Shift+F3)")
        self.previous_btn.clicked.connect(lambda: self.navigate(-1))
        layout.addWidget(self.previous_btn)
        self.next_btn = QToolButton()
        self.next_btn.setText("↓")
        self.next_btn.setToolTip("下一个 (Enter / F3)")
        self.next_btn.clicked.connect(lambda: self.navigate(1))
        layout.addWidget(self.next_btn)
        close_btn = QToolButton()
        close_btn.setText("×")
        close_btn.setToolTip("关闭查找 (Esc)")
        close_btn.clicked.connect(self.close_search)
        layout.addWidget(close_btn)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(120)
        self._refresh_timer.timeout.connect(self._refresh)
        self.target.textChanged.connect(self._document_changed)
        self.input.textChanged.connect(lambda: self._refresh(reveal=True))
        self.input.installEventFilter(self)
        self._shortcuts = []
        for key, callback in (
            ("Ctrl+F", self.open_search),
            ("F3", lambda: self.navigate(1)),
            ("Shift+F3", lambda: self.navigate(-1)),
        ):
            shortcut = QShortcut(QKeySequence(key), scope)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)
        self._escape = QShortcut(QKeySequence("Esc"), scope)
        self._escape.setContext(Qt.WidgetWithChildrenShortcut)
        self._escape.activated.connect(self.close_search)
        self._escape.setEnabled(False)
        self.previous_btn.setEnabled(False)
        self.next_btn.setEnabled(False)
        self.hide()

    @property
    def active(self) -> bool:
        return self._active

    def open_search(self):
        selected = self.target.textCursor().selectedText()
        self._active = True
        self.show()
        self._escape.setEnabled(True)
        if selected and "\u2029" not in selected:
            self.input.setText(selected)
        self._refresh(reveal=True)
        self.input.setFocus()
        self.input.selectAll()

    def close_search(self):
        self._active = False
        self._refresh_timer.stop()
        self._escape.setEnabled(False)
        self.hide()
        self._matches.clear()
        self._current = -1
        self.target.setExtraSelections([])
        self.target.setFocus()

    def eventFilter(self, watched, event):
        if watched is self.input and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                self.navigate(-1 if event.modifiers() & Qt.ShiftModifier else 1)
                return True
        return super().eventFilter(watched, event)

    def _document_changed(self):
        if self._active:
            self._refresh_timer.start()

    def _refresh(self, reveal=False):
        self._refresh_timer.stop()
        if not self._active:
            return
        position = (
            self._matches[self._current].selectionStart()
            if 0 <= self._current < len(self._matches)
            else self.target.textCursor().position()
        )
        self._matches = []
        query = self.input.text()
        if query:
            document = self.target.document()
            cursor = document.find(query, 0)
            while not cursor.isNull():
                self._matches.append(cursor)
                cursor = document.find(query, cursor.selectionEnd())
        self._current = -1
        if self._matches:
            self._current = next(
                (i for i, match in enumerate(self._matches)
                 if match.selectionStart() >= position), 0,
            )
        self._highlight()
        if reveal:
            self._reveal_current()

    def navigate(self, direction: int):
        if not self._active:
            self.open_search()
            return
        if self._refresh_timer.isActive():
            self._refresh()
        if self._matches:
            self._current = (self._current + direction) % len(self._matches)
            self._highlight()
            self._reveal_current()

    def _highlight(self):
        selections = []
        for i, cursor in enumerate(self._matches):
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format.setBackground(
                QColor("#fb923c" if i == self._current else "#fef08a")
            )
            selection.format.setForeground(QColor("#111827"))
            selections.append(selection)
        # Extra selections are view-only: never change saved formatting or undo.
        self.target.setExtraSelections(selections)
        total = len(self._matches)
        self.count.setText(
            f"{self._current + 1}/{total}" if total
            else ("无匹配" if self.input.text() else "0/0")
        )
        self.previous_btn.setEnabled(bool(total))
        self.next_btn.setEnabled(bool(total))

    def _reveal_current(self):
        if self._current >= 0:
            cursor = QTextCursor(self._matches[self._current])
            cursor.clearSelection()
            self.target.setTextCursor(cursor)
            self.target.ensureCursorVisible()

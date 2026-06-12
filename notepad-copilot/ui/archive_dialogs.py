"""Small dialogs + worker thread for the archive flow."""
from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QProgressBar,
    QVBoxLayout,
)


class ArchiveOptionsDialog(QDialog):
    """Pre-archive options: redact + TSG refine."""

    def __init__(
        self,
        parent=None,
        *,
        title: str = "归档选项",
        cloud_mode: bool = False,
        default_redact: bool = True,
        default_refine: bool = True,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(520, 320)

        layout = QVBoxLayout(self)

        intro = QLabel(
            f"将笔记区 + 结果区整合为 Markdown + HTML 报告"
            f"{'并上传到 Wiki' if cloud_mode else ''}。"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # --- Redaction ---
        self.redact_chk = QCheckBox(
            "🔒 数据脱敏（GUID / 订阅 ID / 资源名 / 邮箱 / IP / 密钥）"
        )
        self.redact_chk.setChecked(default_redact)
        layout.addWidget(self.redact_chk)
        redact_hint = QLabel(
            "    原值映射保存到归档目录的 `redact_map.json`，请勿外发。"
        )
        redact_hint.setStyleSheet("color: #656d76; padding-left: 4px;")
        layout.addWidget(redact_hint)

        if cloud_mode:
            # Cloud archive forces redaction
            self.redact_chk.setEnabled(False)
            self.redact_chk.setChecked(True)
            cloud_hint = QLabel(
                "    （云端归档强制脱敏；不可关闭）"
            )
            cloud_hint.setStyleSheet("color: #cf222e; padding-left: 4px;")
            layout.addWidget(cloud_hint)

        # --- TSG refinement ---
        self.refine_chk = QCheckBox(
            "✨ 用 Copilot 精炼为 Troubleshooting Guide (TSG) 样式"
        )
        self.refine_chk.setChecked(default_refine)
        layout.addWidget(self.refine_chk)
        refine_hint = QLabel(
            "    自动调用本地 copilot CLI 把原始记录改写成 TSG（含\"现象 / 影响 / "
            "排查 / 根因 / 缓解 / 验证\"等小节），\n"
            "    并保留有价值的截图引用。原始组合内容会另存为 `archive.raw.md`。\n"
            "    精炼过程会调用一次 Copilot（约 30s ~ 2min）。"
        )
        refine_hint.setStyleSheet("color: #656d76; padding-left: 4px;")
        refine_hint.setWordWrap(True)
        layout.addWidget(refine_hint)

        layout.addStretch(1)

        btns = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        btns.button(QDialogButtonBox.Ok).setText("继续")
        btns.button(QDialogButtonBox.Ok).setProperty("accent", True)
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    @property
    def redact(self) -> bool:
        return self.redact_chk.isChecked()

    @property
    def refine_tsg(self) -> bool:
        return self.refine_chk.isChecked()


# ---- archive worker (off-GUI thread) ----------------------------------------

class ArchiveWorker(QThread):
    """Run archive_session() off the GUI thread."""

    succeeded = Signal(object)   # Path to archive.md
    failed = Signal(str)

    def __init__(self, parent, kwargs: dict):
        super().__init__(parent)
        self._kwargs = kwargs

    def run(self):
        from core.archive import archive_session
        try:
            md = archive_session(**self._kwargs)
            self.succeeded.emit(md)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class ArchiveProgressDialog(QDialog):
    """Indeterminate progress while ArchiveWorker runs."""

    def __init__(self, parent=None, *, message: str = "正在归档…"):
        super().__init__(parent)
        self.setWindowTitle("处理中")
        self.setModal(True)
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        self.resize(420, 130)
        layout = QVBoxLayout(self)
        self.label = QLabel(message)
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        bar = QProgressBar()
        bar.setRange(0, 0)
        layout.addWidget(bar)
        hint = QLabel(
            "TSG 精炼会调用一次 Copilot（约 30s ~ 2min），请稍候。"
        )
        hint.setStyleSheet("color: #656d76;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

    def set_message(self, msg: str):
        self.label.setText(msg)

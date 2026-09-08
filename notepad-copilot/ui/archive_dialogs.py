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
        title: str = "Archive Options",
        cloud_mode: bool = False,
        default_redact: bool = True,
        default_refine: bool = True,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(620, 340)

        layout = QVBoxLayout(self)

        intro = QLabel(
            f"Combine notes and results into Markdown and HTML reports"
            f"{' and upload to Wiki' if cloud_mode else ''}."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # --- Redaction ---
        self.redact_chk = QCheckBox(
            "🔒 Redact sensitive data"
        )
        self.redact_chk.setChecked(default_redact)
        layout.addWidget(self.redact_chk)
        redact_hint = QLabel(
            "Redacts GUIDs, subscription IDs, resource names, emails, IPs and keys.\n"
            "Original values are saved in redact_map.json. Keep this file private."
        )
        redact_hint.setWordWrap(True)
        redact_hint.setStyleSheet("color: #656d76; padding-left: 4px;")
        layout.addWidget(redact_hint)

        if cloud_mode:
            # Cloud archive forces redaction
            self.redact_chk.setEnabled(False)
            self.redact_chk.setChecked(True)
            cloud_hint = QLabel(
                "    Redaction is required for cloud archives."
            )
            cloud_hint.setStyleSheet("color: #cf222e; padding-left: 4px;")
            layout.addWidget(cloud_hint)

        # --- TSG refinement ---
        self.refine_chk = QCheckBox(
            "✨ Refine into a Troubleshooting Guide (TSG) with Copilot"
        )
        self.refine_chk.setChecked(default_refine)
        layout.addWidget(self.refine_chk)
        refine_hint = QLabel(
            "Uses the local Copilot CLI to organize records into symptom, impact, "
            "investigation, root cause, mitigation and verification sections.\n"
            "Relevant screenshots are retained. Original content is saved as archive.raw.md.\n"
            "Refinement makes one Copilot request (about 30 seconds to 2 minutes)."
        )
        refine_hint.setStyleSheet("color: #656d76; padding-left: 4px;")
        refine_hint.setWordWrap(True)
        layout.addWidget(refine_hint)

        layout.addStretch(1)

        btns = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        btns.button(QDialogButtonBox.Ok).setText("Continue")
        btns.button(QDialogButtonBox.Ok).setProperty("accent", True)
        btns.button(QDialogButtonBox.Cancel).setText("Cancel")
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

    def __init__(self, parent=None, *, message: str = "Archiving…"):
        super().__init__(parent)
        self.setWindowTitle("Processing")
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
            "TSG refinement makes one Copilot request (about 30 seconds to 2 minutes). Please wait."
        )
        hint.setStyleSheet("color: #656d76;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

    def set_message(self, msg: str):
        self.label.setText(msg)

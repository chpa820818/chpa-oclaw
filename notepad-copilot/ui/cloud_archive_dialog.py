"""Cloud archive dialog: paste a Wiki URL → pick parent path + page name."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from core.wiki_config import WikiProfile, parse_wiki_url


class CloudArchiveDialog(QDialog):
    """Collect upload target from the user.

    On accept, exposes:
      - profile : WikiProfile  (organization / project / wiki_identifier)
      - parent_path : str
      - page_name : str
      - page_path : str   (parent_path + "/" + page_name, normalised)
    """

    def __init__(
        self,
        parent=None,
        *,
        default_url: str = "",
        default_parent: str = "",
        default_page_name: str = "",
    ):
        super().__init__(parent)
        self.setWindowTitle("☁ Cloud Archive - Upload to Wiki")
        self.resize(720, 460)

        self.profile: WikiProfile | None = None
        self.parent_path: str = ""
        self.page_name: str = ""
        self.page_path: str = ""
        # Numeric page id parsed from a friendly Wiki URL (if any). When
        # present, the caller should resolve the page's *real* path from
        # this id rather than trusting parent_path, which is only inferred
        # from the URL slug and loses ancestors / encodes special chars.
        self.page_id: str = ""

        self._default_page_name = default_page_name or "Archive"

        layout = QVBoxLayout(self)

        # --- URL input ------------------------------------------------
        intro = QLabel(
            "Paste a Wiki page or root URL to detect the organization, "
            "project, Wiki and parent path (for page URLs)."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #656d76;")
        layout.addWidget(intro)

        self.url_edit = QPlainTextEdit()
        self.url_edit.setPlaceholderText(
            "Example: https://dev.azure.com/CSS-Mooncake/MCVKB/_wiki/wikis/"
            "Mooncake-Networking-PoD.wiki/215/Welcome-to-MCVKB"
        )
        self.url_edit.setMaximumHeight(80)
        if default_url:
            self.url_edit.setPlainText(default_url)
        self.url_edit.textChanged.connect(self._on_url_changed)
        layout.addWidget(self.url_edit)

        # --- parsed display ------------------------------------------
        parsed_box = QFormLayout()
        self.org_label = QLabel("(not parsed)")
        self.project_label = QLabel("(not parsed)")
        self.wiki_label = QLabel("(not parsed)")
        for w in (self.org_label, self.project_label, self.wiki_label):
            w.setTextInteractionFlags(Qt.TextSelectableByMouse)
            w.setStyleSheet("color: #1f6feb;")
        parsed_box.addRow("Organization:", self.org_label)
        parsed_box.addRow("Project:", self.project_label)
        parsed_box.addRow("Wiki:", self.wiki_label)
        layout.addLayout(parsed_box)

        # --- editable parent + page name -----------------------------
        form = QFormLayout()
        self.parent_edit = QLineEdit()
        self.parent_edit.setPlaceholderText("/  or  /Cases  or  /Team/Drafts")
        self.parent_edit.textChanged.connect(self._update_preview)
        form.addRow("Parent path:", self.parent_edit)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Page name (without /)")
        self.name_edit.textChanged.connect(self._update_preview)
        form.addRow("Page name:", self.name_edit)

        self.preview_label = QLabel("Page path: (waiting for input)")
        self.preview_label.setWordWrap(True)
        self.preview_label.setStyleSheet(
            "color: #1f6feb; font-weight: 600; padding: 6px 0;"
        )
        self.preview_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addRow("", self.preview_label)
        layout.addLayout(form)

        # --- hint -----------------------------------------------------
        hint = QLabel(
            "💡 Updates an existing page or creates a new one.\n"
            "💡 Uses the current Azure CLI account. Wiki write permission is required.\n"
            "💡 Uploaded content is automatically redacted."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #656d76; padding: 4px 0;")
        layout.addWidget(hint)

        # --- buttons --------------------------------------------------
        btns = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        self._ok_btn = btns.button(QDialogButtonBox.Ok)
        self._ok_btn.setText("Upload")
        self._ok_btn.setProperty("accent", True)
        self._ok_btn.setEnabled(False)
        btns.button(QDialogButtonBox.Cancel).setText("Cancel")
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        # initial parse + defaults
        self._on_url_changed()
        if default_parent and not self.parent_edit.text().strip():
            self.parent_edit.setText(default_parent)
        if not self.name_edit.text().strip():
            self.name_edit.setText(self._default_page_name)

    # --- handlers -----------------------------------------------------

    def _on_url_changed(self):
        url = self.url_edit.toPlainText().strip()
        parsed = parse_wiki_url(url) if url else {}
        org = parsed.get("organization", "")
        proj = parsed.get("project", "")
        wiki = parsed.get("wiki_identifier", "")
        parent = parsed.get("parent_path", "")
        # Whether the URL targets a specific page (has a numeric page id).
        # If so, the upload step resolves that page's real path via the API
        # and anchors the new page under it, so the inferred parent below is
        # only a best-effort preview.
        self._url_page_id = parsed.get("page_id", "")
        self.org_label.setText(org or "(not parsed)")
        self.project_label.setText(proj or "(not parsed)")
        self.wiki_label.setText(wiki or "(not parsed)")
        # If URL parsed a parent and the user hasn't edited anything yet,
        # populate parent field. We always overwrite when parsed gives a
        # value to avoid stale parent from a previous URL.
        if parent:
            self.parent_edit.blockSignals(True)
            self.parent_edit.setText(parent)
            self.parent_edit.blockSignals(False)
        self._update_preview()

    def _update_preview(self):
        parent = self.parent_edit.text().strip()
        name = self.name_edit.text().strip()
        if not parent:
            parent = "/"
        if not parent.startswith("/"):
            parent = "/" + parent
        # normalise: drop trailing slash unless root
        if parent != "/" and parent.endswith("/"):
            parent = parent.rstrip("/")
        if not name:
            self.preview_label.setText("Page path: (enter a page name)")
            self._ok_btn.setEnabled(False)
            return
        if "/" in name:
            self.preview_label.setText(
                "Page path: ⚠ Page names cannot contain /"
            )
            self._ok_btn.setEnabled(False)
            return
        path = (parent.rstrip("/") + "/" + name) if parent != "/" \
            else "/" + name
        if getattr(self, "_url_page_id", ""):
            self.preview_label.setText(
                f"Page path: resolve the URL page (ID={self._url_page_id})"
                f" at upload time → child page “{name}”"
            )
        else:
            self.preview_label.setText(f"Page path: {path}")
        # Ok requires a fully-parsed wiki target
        ok = bool(self.org_label.text().startswith("http")
                  and self.project_label.text() not in ("", "(not parsed)")
                  and self.wiki_label.text() not in ("", "(not parsed)"))
        self._ok_btn.setEnabled(ok)

    def _on_accept(self):
        from PySide6.QtWidgets import QMessageBox
        url = self.url_edit.toPlainText().strip()
        parsed = parse_wiki_url(url) if url else {}
        org = parsed.get("organization", "")
        proj = parsed.get("project", "")
        wiki = parsed.get("wiki_identifier", "")
        if not (org and proj and wiki):
            QMessageBox.warning(
                self, "Invalid Wiki URL",
                "Could not detect the organization, project and Wiki from this URL.\n"
                "Use a URL in this format:\n"
                "  https://dev.azure.com/<org>/<project>/_wiki/wikis/"
                "<wiki>[/...]"
            )
            return
        parent = self.parent_edit.text().strip() or "/"
        if not parent.startswith("/"):
            parent = "/" + parent
        if parent != "/" and parent.endswith("/"):
            parent = parent.rstrip("/")
        name = self.name_edit.text().strip()
        if not name or "/" in name:
            QMessageBox.warning(
                self, "Invalid Page Name",
                "Enter a nonempty page name without /."
            )
            return
        page_path = (parent.rstrip("/") + "/" + name) if parent != "/" \
            else "/" + name
        # build the result
        self.profile = WikiProfile(
            name=f"{org} / {proj} / {wiki}",
            organization=org,
            project=proj,
            wiki_identifier=wiki,
            parent_path=parent,
            api_version="7.0",
        )
        self.parent_path = parent
        self.page_name = name
        self.page_path = page_path
        self.page_id = parsed.get("page_id", "")
        self.accept()

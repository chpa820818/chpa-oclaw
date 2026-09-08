"""Settings dialog: manage Wiki profiles for cloud archive."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.wiki_config import (
    WikiConfig,
    WikiProfile,
    load_config,
    parse_wiki_url,
    save_config,
)


class WikiSettingsDialog(QDialog):
    """Edit the list of Wiki profiles + pick a default."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Wiki Settings (Cloud Archive Target)")
        self.resize(720, 460)

        self.cfg: WikiConfig = load_config()
        self._current_index: int = -1

        root = QHBoxLayout(self)

        # Left: list + default picker + add/remove
        left = QVBoxLayout()
        left.addWidget(QLabel("Profiles"))
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._on_select)
        left.addWidget(self.list, 1)

        btn_row = QHBoxLayout()
        self.add_btn = QPushButton("＋ Add")
        self.add_btn.clicked.connect(self._on_add)
        btn_row.addWidget(self.add_btn)
        self.del_btn = QPushButton("－ Delete")
        self.del_btn.setProperty("danger", True)
        self.del_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(self.del_btn)
        left.addLayout(btn_row)

        left.addWidget(QLabel("Default cloud archive profile"))
        self.default_box = QComboBox()
        self.default_box.currentTextChanged.connect(self._on_default_changed)
        left.addWidget(self.default_box)

        left_w = QWidget()
        left_w.setLayout(left)
        left_w.setMinimumWidth(240)
        root.addWidget(left_w)

        # Right: edit form
        right = QVBoxLayout()
        right.addWidget(QLabel("Edit selected profile"))

        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.editingFinished.connect(self._on_field_changed)
        form.addRow("Name:", self.name_edit)

        self.org_edit = QLineEdit()
        self.org_edit.setPlaceholderText(
            "Paste a Wiki URL, or enter https://dev.azure.com/myorg"
        )
        self.org_edit.editingFinished.connect(self._on_field_changed)
        self.org_edit.textChanged.connect(self._on_org_text_changed)
        form.addRow("Organization URL:", self.org_edit)

        self.project_edit = QLineEdit()
        self.project_edit.editingFinished.connect(self._on_field_changed)
        form.addRow("Project:", self.project_edit)

        self.wiki_edit = QLineEdit()
        self.wiki_edit.setPlaceholderText("Wiki name or ID (e.g. MyProject.wiki)")
        self.wiki_edit.editingFinished.connect(self._on_field_changed)
        form.addRow("Wiki Identifier:", self.wiki_edit)

        self.parent_edit = QLineEdit()
        self.parent_edit.setPlaceholderText("/Cases")
        self.parent_edit.editingFinished.connect(self._on_field_changed)
        form.addRow("Default parent path:", self.parent_edit)

        self.api_edit = QLineEdit()
        self.api_edit.setPlaceholderText("7.0")
        self.api_edit.editingFinished.connect(self._on_field_changed)
        form.addRow("API version:", self.api_edit)

        right.addLayout(form)

        # Hint
        hint = QLabel(
            "💡 Uses the current Azure CLI account. "
            "Wiki write permission is required.\n"
            "💡 Review the parent path and page name in the upload dialog."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #656d76; padding: 8px 0;")
        right.addWidget(hint)

        right.addStretch(1)

        btns = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        btns.button(QDialogButtonBox.Save).setText("Save")
        btns.button(QDialogButtonBox.Cancel).setText("Cancel")
        btns.accepted.connect(self._on_save)
        btns.rejected.connect(self.reject)
        right.addWidget(btns)

        right_w = QWidget()
        right_w.setLayout(right)
        root.addWidget(right_w, 1)

        self._reload()

    # --- helpers ------------------------------------------------------

    def _reload(self):
        self.list.blockSignals(True)
        self.list.clear()
        for p in self.cfg.profiles:
            item = QListWidgetItem(p.name or "(unnamed)")
            self.list.addItem(item)
        self.list.blockSignals(False)

        self.default_box.blockSignals(True)
        self.default_box.clear()
        names = [p.name for p in self.cfg.profiles if p.name]
        self.default_box.addItems(names)
        if self.cfg.default and self.cfg.default in names:
            self.default_box.setCurrentText(self.cfg.default)
        elif names:
            self.cfg.default = names[0]
            self.default_box.setCurrentText(names[0])
        self.default_box.blockSignals(False)

        if self.cfg.profiles:
            self.list.setCurrentRow(0)
        else:
            self._populate_form(None)

    def _on_select(self, row: int):
        self._current_index = row
        if 0 <= row < len(self.cfg.profiles):
            self._populate_form(self.cfg.profiles[row])
        else:
            self._populate_form(None)

    def _populate_form(self, p: WikiProfile | None):
        for w in (self.name_edit, self.org_edit, self.project_edit,
                  self.wiki_edit, self.parent_edit, self.api_edit):
            w.blockSignals(True)
            w.setEnabled(p is not None)
        if p is None:
            for w in (self.name_edit, self.org_edit, self.project_edit,
                      self.wiki_edit, self.parent_edit, self.api_edit):
                w.setText("")
                w.blockSignals(False)
            return
        self.name_edit.setText(p.name)
        self.org_edit.setText(p.organization)
        self.project_edit.setText(p.project)
        self.wiki_edit.setText(p.wiki_identifier)
        self.parent_edit.setText(p.parent_path or "/Cases")
        self.api_edit.setText(p.api_version or "7.0")
        for w in (self.name_edit, self.org_edit, self.project_edit,
                  self.wiki_edit, self.parent_edit, self.api_edit):
            w.blockSignals(False)

    def _on_field_changed(self):
        if not (0 <= self._current_index < len(self.cfg.profiles)):
            return
        p = self.cfg.profiles[self._current_index]
        old_name = p.name
        p.name = self.name_edit.text().strip()
        p.organization = self.org_edit.text().strip()
        p.project = self.project_edit.text().strip()
        p.wiki_identifier = self.wiki_edit.text().strip()
        p.parent_path = self.parent_edit.text().strip() or "/"
        p.api_version = self.api_edit.text().strip() or "7.0"
        # Reflect rename in list + default combo
        item = self.list.item(self._current_index)
        if item is not None:
            item.setText(p.name or "(unnamed)")
        if self.cfg.default == old_name:
            self.cfg.default = p.name
        self.default_box.blockSignals(True)
        self.default_box.clear()
        self.default_box.addItems([x.name for x in self.cfg.profiles
                                   if x.name])
        if self.cfg.default:
            self.default_box.setCurrentText(self.cfg.default)
        self.default_box.blockSignals(False)

    def _on_org_text_changed(self, text: str):
        """Auto-parse a pasted Wiki URL into the relevant fields."""
        parsed = parse_wiki_url(text)
        if not parsed:
            return
        # Only fill fields that look like a URL (i.e. parse succeeded with
        # a scheme), and only overwrite if the resulting org differs from
        # the raw text — avoids fighting the user mid-typing.
        new_org = parsed.get("organization", "")
        if not new_org:
            return
        # Replace the org field with the cleaned org URL
        self.org_edit.blockSignals(True)
        self.org_edit.setText(new_org)
        self.org_edit.blockSignals(False)
        if parsed.get("project") and not self.project_edit.text().strip():
            self.project_edit.setText(parsed["project"])
        if (parsed.get("wiki_identifier")
                and not self.wiki_edit.text().strip()):
            self.wiki_edit.setText(parsed["wiki_identifier"])
        # commit to in-memory profile
        self._on_field_changed()

    def _on_default_changed(self, name: str):
        self.cfg.default = name

    def _on_add(self):
        new_p = WikiProfile(name=f"Profile {len(self.cfg.profiles)+1}",
                            parent_path="/Cases", api_version="7.0")
        self.cfg.profiles.append(new_p)
        self._reload()
        self.list.setCurrentRow(len(self.cfg.profiles) - 1)
        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def _on_delete(self):
        if not (0 <= self._current_index < len(self.cfg.profiles)):
            return
        p = self.cfg.profiles[self._current_index]
        ret = QMessageBox.question(
            self, "Delete Profile",
            f"Delete '{p.name}'?",
        )
        if ret != QMessageBox.Yes:
            return
        del self.cfg.profiles[self._current_index]
        if self.cfg.default == p.name:
            self.cfg.default = (
                self.cfg.profiles[0].name if self.cfg.profiles else ""
            )
        self._reload()

    def _on_save(self):
        # commit current edits
        self._on_field_changed()
        # validation
        for p in self.cfg.profiles:
            miss = p.missing_fields()
            if miss:
                QMessageBox.warning(
                    self, "Save Failed",
                    f"Profile '{p.name or '(unnamed)'}' is missing required fields:\n"
                    + ", ".join(miss)
                )
                return
        try:
            save_config(self.cfg)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Save Failed", str(e))
            return
        self.accept()

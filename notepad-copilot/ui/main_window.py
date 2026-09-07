"""Main window: three-pane layout (editor | result) + chat at bottom."""
from __future__ import annotations

import os
import datetime as _datetime
import hashlib as _hashlib
import html as _html
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.archive import archive_session, default_archive_dir
from core.case_store import (
    Case,
    CASE_ROOT,
    create_case,
    get_case_root,
    is_case_root_configured,
    list_cases,
    open_case,
    sanitize_case_id,
    set_case_root,
)
from core.markdown_io import (
    attachments_dir_for,
    load_document,
    save_document,
)
from core.wiki_config import get_default_profile, load_config, save_config
from core.wiki_uploader import upload_archive
from ui.archive_dialogs import (
    ArchiveOptionsDialog,
    ArchiveProgressDialog,
    ArchiveWorker,
)
from ui.az_bar import AzAccountBar
from ui.chat_pane import ChatPane
from ui.cloud_archive_dialog import CloudArchiveDialog
from ui.editor_pane import EditorPane
from ui.find_bar import FindBar
from ui.result_pane import ResultPane
from ui.wiki_settings import WikiSettingsDialog


def _wrap_in_card(inner: QWidget, title_text: str,
                  subtitle_text: str = "",
                  header_widgets: list[QWidget] | None = None) -> QWidget:
    """Wrap a plain widget in a 'card' container with a styled header.

    `header_widgets` are placed on the right side of the header bar.
    """
    card = QWidget()
    card.setObjectName("Card")
    v = QVBoxLayout(card)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(0)

    header = QWidget()
    header.setObjectName("PaneHeader")
    bar = QHBoxLayout(header)
    bar.setContentsMargins(12, 6, 8, 6)
    bar.setSpacing(6)
    title = QLabel(title_text)
    title.setObjectName("PaneTitle")
    bar.addWidget(title)
    if subtitle_text:
        sub = QLabel(subtitle_text)
        sub.setObjectName("FieldLabel")
        bar.addWidget(sub)
    bar.addStretch(1)
    if header_widgets:
        for w in header_widgets:
            bar.addWidget(w)
    v.addWidget(header)

    # Strip inner border so it doesn't double up with the card border.
    try:
        from PySide6.QtWidgets import QFrame
        if isinstance(inner, QFrame):
            inner.setFrameShape(QFrame.NoFrame)
    except Exception:
        pass

    body = QWidget()
    body_v = QVBoxLayout(body)
    body_v.setContentsMargins(8, 8, 8, 8)
    body_v.setSpacing(0)
    body_v.addWidget(inner)
    v.addWidget(body, 1)
    return card


class MainWindow(QMainWindow):
    # Class-level registry to keep spawned windows alive (prevents GC)
    _open_windows: list["MainWindow"] = []

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Notepad + Copilot")
        self.resize(1400, 900)

        self.editor = EditorPane()
        self.result = ResultPane()
        self.chat = ChatPane()
        self.az_bar = AzAccountBar()

        # 📎 Upload button placed inside the editor card header.
        self._btn_upload = QToolButton()
        self._btn_upload.setText("📎  上传…")
        self._btn_upload.setToolTip(
            "上传日志/文件/文件夹到当前案例\n"
            "也可直接拖拽文件或文件夹到笔记区"
        )
        self._btn_upload.setPopupMode(QToolButton.MenuButtonPopup)
        upload_menu = QMenu(self._btn_upload)
        act_upload_files = upload_menu.addAction("上传文件… (可多选)")
        act_upload_files.triggered.connect(self._on_upload_files)
        act_upload_folder = upload_menu.addAction("上传文件夹… (递归)")
        act_upload_folder.triggered.connect(self._on_upload_folder)
        act_upload_mixed = upload_menu.addAction(
            "上传混合内容… (文件 + 文件夹)")
        act_upload_mixed.triggered.connect(self._on_upload_mixed)
        self._btn_upload.setMenu(upload_menu)
        # Default click = files multi-select (most common)
        self._btn_upload.clicked.connect(self._on_upload_files)

        editor_body = QWidget()
        editor_layout = QVBoxLayout(editor_body)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(0)
        self.editor_find = FindBar(self.editor, editor_body)
        editor_layout.addWidget(self.editor_find)
        editor_layout.addWidget(self.editor, 1)
        self._btn_find = QToolButton()
        self._btn_find.setText("查找")
        self._btn_find.setToolTip("查找笔记 (Ctrl+F)")
        self._btn_find.clicked.connect(self.editor_find.open_search)

        editor_card = _wrap_in_card(
            editor_body, "📝  笔记",
            "支持文本 + 截图 + 日志 (Ctrl+V / 拖拽 / 📎 上传)",
            header_widgets=[self._btn_find, self._btn_upload],
        )

        # Top: editor (left) | result (right)
        top_splitter = QSplitter(Qt.Horizontal)
        top_splitter.setHandleWidth(6)
        top_splitter.setChildrenCollapsible(False)
        top_splitter.addWidget(editor_card)
        top_splitter.addWidget(self.result)
        top_splitter.setStretchFactor(0, 1)
        top_splitter.setStretchFactor(1, 1)
        top_splitter.setSizes([700, 700])

        # Outer: top_splitter (top) / chat (bottom)
        outer = QSplitter(Qt.Vertical)
        outer.setHandleWidth(6)
        outer.setChildrenCollapsible(False)
        outer.addWidget(top_splitter)
        outer.addWidget(self.chat)
        outer.setStretchFactor(0, 3)
        outer.setStretchFactor(1, 2)
        outer.setSizes([600, 300])

        # Wrap: az bar on top, then outer splitter (with breathing room)
        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self.az_bar)
        body_wrap = QWidget()
        body_wrap_v = QVBoxLayout(body_wrap)
        body_wrap_v.setContentsMargins(10, 10, 10, 6)
        body_wrap_v.setSpacing(0)
        body_wrap_v.addWidget(outer, 1)
        v.addWidget(body_wrap, 1)
        self.setCentralWidget(central)

        self._current_path: Path | None = None
        self._current_case: Case | None = None
        self._build_menu()

        self.chat.send_requested.connect(self._on_send)
        self.chat.answer_ready.connect(self._on_qa_ready)
        self.result.archive_btn.clicked.connect(self._on_archive)
        self.result.cloud_btn.clicked.connect(self._on_cloud_archive)
        self.az_bar.account_changed.connect(
            lambda: self.statusBar().showMessage("Azure 账户已更新", 3000)
        )
        self.az_bar.busy_changed.connect(self._on_az_busy)
        self.chat.runner.process_started.connect(
            lambda: self._on_chat_busy("Copilot 思考中…", True)
        )
        self.chat.runner.process_finished.connect(
            lambda code: self._on_chat_busy(
                "Copilot 已停止" if code == -2 else
                "Copilot 执行失败" if code else "Copilot 已完成",
                False,
            )
        )
        self.statusBar().showMessage("就绪")

        # Auto-save: only saves when a case is open
        self._autosave_last_note_hash: str = ""
        self._autosave_last_qa_count: int = 0
        self._last_case_save_error: str = ""
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(5000)
        self._autosave_timer.timeout.connect(self._on_autosave_tick)
        self._autosave_timer.start()

    def _on_az_busy(self, msg: str, busy: bool):
        if busy:
            self.statusBar().showMessage(f"⏳ {msg}")
            self.setWindowTitle(f"⏳ {msg} - Notepad + Copilot")
        else:
            self.statusBar().showMessage("就绪", 2000)
            self._update_title()

    def _on_chat_busy(self, msg: str, busy: bool):
        if busy:
            self.statusBar().showMessage(f"💭 {msg}")
        else:
            self.statusBar().showMessage(msg or "Copilot 已完成", 2000)

    # --- menu ---------------------------------------------------------

    def _build_menu(self):
        m = self.menuBar()

        # Three top-level menu-bar actions (no submenus): 新建 / 打开 / 关闭。
        act_new_case = QAction("📂  新建案例", self,
                               shortcut=QKeySequence("Ctrl+Shift+N"))
        act_new_case.triggered.connect(self._on_new_case)
        m.addAction(act_new_case)

        act_open_case = QAction("📁  打开案例", self,
                                shortcut=QKeySequence("Ctrl+Shift+O"))
        act_open_case.triggered.connect(self._on_open_case)
        m.addAction(act_open_case)

        act_close_case = QAction("✖  关闭案例", self,
                                 shortcut=QKeySequence("Ctrl+Shift+W"))
        act_close_case.triggered.connect(self._on_close_case)
        m.addAction(act_close_case)

        # Keep these so other code paths (state updates, archive flows)
        # still have valid attributes; close-case action's enabled state
        # tracks whether a case is currently open.
        self._act_close_case = act_close_case
        self._update_case_menu_state()

    # --- file ops -----------------------------------------------------

    def _new_file(self):
        if not self._confirm_discard():
            return
        self.editor.clear()
        self._current_path = None
        self._update_title()

    def _open_file(self):
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "打开 Markdown", "", "Markdown (*.md *.markdown);;All Files (*)"
        )
        if not path:
            return
        p = Path(path)
        try:
            load_document(p, self.editor.document())
        except Exception as e:
            QMessageBox.critical(self, "打开失败", str(e))
            return
        self._current_path = p
        self.editor.set_attachments_dir(attachments_dir_for(p))
        self._update_title()
        self.statusBar().showMessage(f"已打开 {p}", 5000)

    def _save_file(self):
        # Case mode: always save to the case's note.md (no dialog)
        if self._current_case is not None:
            self._save_case_note()
            return
        # Fallback: if we have a path inside the cases tree, treat as case save
        if (self._current_path is not None
                and self._is_inside_cases(self._current_path)):
            self._do_save(self._current_path)
            self.editor.document().setModified(False)
            self.statusBar().showMessage(
                f"已保存到 {self._current_path}", 4000)
            return
        if self._current_path is None:
            return self._save_file_as()
        self._do_save(self._current_path)

    @staticmethod
    def _is_inside_cases(p: Path) -> bool:
        try:
            return get_case_root().resolve() in p.resolve().parents
        except Exception:
            return False

    def _save_file_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "另存为 Markdown", "note.md",
            "Markdown (*.md);;All Files (*)"
        )
        if not path:
            return
        p = Path(path)
        if p.suffix.lower() not in (".md", ".markdown"):
            p = p.with_suffix(".md")
        self._do_save(p)

    def _do_save(self, p: Path):
        try:
            pending = self.editor.pending_dir()
            save_document(self.editor.document(), p,
                          pending_attachments_dir=pending)
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))
            return
        self._current_path = p
        self.editor.set_attachments_dir(attachments_dir_for(p))
        self.editor.document().setModified(False)
        self._update_title()
        self.statusBar().showMessage(f"已保存到 {p}", 5000)

    # --- helpers ------------------------------------------------------

    def _confirm_discard(self) -> bool:
        if self._current_case is not None and self.editor.document().isModified():
            if self._save_case_note(silent=True):
                return True
            msg = self._last_case_save_error or "未知错误"
            ret = QMessageBox.question(
                self,
                "保存案例笔记失败",
                f"自动保存当前案例笔记失败：\n{msg}\n\n是否丢弃未保存更改？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            return ret == QMessageBox.Yes

        if not self.editor.document().isModified():
            return True
        ret = QMessageBox.question(
            self, "未保存", "当前笔记未保存，是否丢弃？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        return ret == QMessageBox.Yes

    def _update_title(self):
        if self._current_case is not None:
            modified = " *" if self.editor.document().isModified() else ""
            self.setWindowTitle(
                f"📁 [{self._current_case.case_id}] "
                f"{self._current_case.title}{modified} - Notepad + Copilot"
            )
            return
        name = self._current_path.name if self._current_path else "未命名"
        self.setWindowTitle(f"{name} - Notepad + Copilot")

    def _on_send(self, prompt: str):
        text = self.editor.toPlainText()
        images = self.editor.collect_image_paths()
        self.chat.send(prompt, note=text, attachments=images)

    # --- archive ------------------------------------------------------

    def _archive_inputs_empty(self) -> bool:
        """Return True if both note text and QA result are effectively empty.

        Pops a warning so the caller can simply `if self._archive_inputs_empty(): return`.
        Images alone (no text and no QA) also count as empty for archive purposes,
        because TSG精炼对纯截图无能为力。
        """
        note_text = self.editor.toPlainText().strip()
        qa_text = self.result.to_markdown().strip()
        if note_text or qa_text:
            return False
        QMessageBox.warning(
            self,
            "无内容可归档",
            "笔记区与对话区都是空的，归档将得到一份没有内容的"
            "(N/A) 报告。\n\n请先在笔记区写点排查记录，或在右下方与 "
            "Copilot 进行对话，再尝试归档。",
        )
        return True

    def _on_archive(self):
        """Bundle editor + result into markdown and HTML report files."""
        if self._archive_inputs_empty():
            return
        opts = ArchiveOptionsDialog(
            self, title="本地归档选项", cloud_mode=False)
        if opts.exec() != QDialog.Accepted:
            return
        do_redact = opts.redact
        do_refine = opts.refine_tsg

        if self._current_case is not None:
            ts = _datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            default_dir = (self._current_case.archives_dir
                           / f"{ts}-archive")
        else:
            default_dir = default_archive_dir()
        chosen, _ = QFileDialog.getSaveFileName(
            self,
            "归档保存位置（选择目录名，archive.md / archive.html 会写在其中）",
            str(default_dir),
            "归档目录 (*)",
        )
        if not chosen:
            return
        target = Path(chosen)
        if target.suffix.lower() in (".md", ".markdown"):
            target = target.parent / target.stem
        try:
            note_md = self.editor.toMarkdown()
        except Exception:
            note_md = self.editor.toPlainText()
        images = self.editor.collect_image_paths()
        qa_md = self.result.to_markdown()
        title = target.name or "归档报告"

        kwargs = dict(
            target_dir=target,
            note_markdown=note_md,
            note_image_paths=images,
            qa_markdown=qa_md,
            title=title,
            redact=do_redact,
            refine_tsg=do_refine,
        )

        # Run off the GUI thread (esp. when refine is on — it spawns copilot)
        progress = ArchiveProgressDialog(
            self,
            message=("正在归档并精炼为 TSG…" if do_refine else "正在归档…"),
        )
        worker = ArchiveWorker(self, kwargs)
        worker_err: list[str] = []
        worker_md: list[Path] = []
        worker.succeeded.connect(lambda p: (worker_md.append(p),
                                            progress.accept()))
        worker.failed.connect(lambda msg: (worker_err.append(msg),
                                           progress.accept()))
        worker.start()
        progress.exec()
        worker.wait(2000)

        if worker_err:
            QMessageBox.critical(self, "归档失败", worker_err[0])
            return
        if not worker_md:
            QMessageBox.critical(self, "归档失败", "未返回归档路径")
            return
        archive_md = worker_md[0]
        archive_html = archive_md.with_suffix(".html")
        if not archive_html.is_file():
            QMessageBox.critical(
                self,
                "归档失败",
                f"Markdown 已生成，但 HTML 汇总文件缺失:\n{archive_html}",
            )
            return

        redact_note = ""
        if do_redact:
            map_file = archive_md.parent / "redact_map.json"
            if map_file.is_file():
                redact_note = (
                    f"\n🔒 已脱敏，映射: {map_file.name}（保留本地，勿外发）"
                )
            else:
                redact_note = "\n🔒 已应用脱敏（未发现敏感字段）"
        tsg_note = ""
        if do_refine:
            err_file = archive_md.parent / "archive.tsg.error.txt"
            raw_file = archive_md.parent / "archive.raw.md"
            if err_file.is_file():
                tsg_note = (
                    f"\n⚠ TSG 精炼失败，已回退为原始内容；详见 "
                    f"{err_file.name}"
                )
            elif raw_file.is_file():
                tsg_note = (
                    "\n✨ 已精炼为 TSG；原始组合内容见 archive.raw.md"
                )

        html_url = QUrl.fromLocalFile(str(archive_html.resolve())).toString()
        msg_box = QMessageBox(self)
        msg_box.setIcon(QMessageBox.Information)
        msg_box.setWindowTitle("归档完成")
        msg_box.setTextFormat(Qt.RichText)
        msg_box.setTextInteractionFlags(Qt.TextBrowserInteraction)
        msg_box.setText(
            "HTML 汇总已生成：<br>"
            f'<a href="{_html.escape(html_url, quote=True)}">'
            f"{_html.escape(str(archive_html))}</a><br><br>"
            f"Markdown 副本：<br>{_html.escape(str(archive_md))}<br><br>"
            f"图片数: {len([p for p in images if p.is_file()])}<br>"
            f"对话条数: {len(self.result.qa_pairs())}"
            f"{_html.escape(redact_note + tsg_note).replace(chr(10), '<br>')}"
            "<br><br>是否打开所在目录？"
        )
        msg_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg_box.setDefaultButton(QMessageBox.Yes)
        for label in msg_box.findChildren(QLabel):
            label.setOpenExternalLinks(True)
        ret = msg_box.exec()
        if ret == QMessageBox.Yes:
            try:
                if os.name == "nt":
                    subprocess.Popen(
                        ["explorer.exe", "/select,", str(archive_html)],
                        shell=False,
                    )
                else:
                    subprocess.Popen(["xdg-open", str(archive_md.parent)])
            except Exception:  # noqa: BLE001
                pass
        msg = f"已归档 HTML: {archive_html}" + (" (已脱敏)" if do_redact else "")
        self.statusBar().showMessage(msg, 8000)

    # --- cloud archive (Wiki upload) ---------------------------------

    def _on_wiki_settings(self):
        dlg = WikiSettingsDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self.statusBar().showMessage("Wiki 配置已保存", 4000)

    def _on_cloud_archive(self):
        """URL-driven cloud archive: paste Wiki URL → pick path → upload."""
        if self._archive_inputs_empty():
            return
        cfg = load_config()
        case_id = (self._current_case.case_id
                   if self._current_case else "Adhoc")
        ts = _datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        default_page_name = (
            f"{case_id}-{ts}-archive"
            if self._current_case
            else f"{ts}-archive"
        )
        # Prefer last-used; fall back to default profile parent_path
        default_parent = cfg.last_parent_path
        if not default_parent:
            prof = get_default_profile(cfg)
            default_parent = (prof.parent_path if prof else "/Cases")

        dlg = CloudArchiveDialog(
            self,
            default_url=cfg.last_url,
            default_parent=default_parent,
            default_page_name=default_page_name,
        )
        if dlg.exec() != QDialog.Accepted:
            return

        profile = dlg.profile
        page_path = dlg.page_path
        if profile is None or not page_path:
            return

        # Persist last-used for next time
        try:
            cfg.last_url = dlg.url_edit.toPlainText().strip()
            cfg.last_parent_path = dlg.parent_path
            save_config(cfg)
        except Exception:
            pass

        # If the pasted URL pointed at a specific page, that page's id is the
        # only reliable handle — its friendly-URL slug loses ancestors and
        # encodes special chars (e.g. '&'), so the inferred parent path can be
        # wrong and the upload lands at the wiki root. Resolve the page's REAL
        # path via the API and re-anchor the new page under it as a sub-page.
        page_id = getattr(dlg, "page_id", "")
        page_name = getattr(dlg, "page_name", "")
        if page_id and page_name:
            try:
                from core.wiki_uploader import (
                    get_access_token,
                    get_page_path_by_id,
                )
                self.statusBar().showMessage("☁ 正在解析父页面真实路径…")
                token = get_access_token()
                real_parent = get_page_path_by_id(profile, token, page_id)
                if real_parent:
                    page_path = real_parent.rstrip("/") + "/" + page_name
                else:
                    QMessageBox.warning(
                        self, "无法解析父页面",
                        "未能通过 URL 中的页面 ID 找到对应的 Wiki 页面"
                        f"（page id={page_id}）。\n\n"
                        f"将使用根据 URL 推断的路径：\n{page_path}\n\n"
                        "如结果不在期望的父页面下，请改用页面右上角"
                        "「Copy page path」得到的链接，或在父路径中手动填写"
                        "完整路径。",
                    )
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(
                    self, "解析父页面失败",
                    f"解析父页面真实路径时出错，将使用推断路径：\n{page_path}"
                    f"\n\n{e}",
                )

        # Archive options (TSG default ON for cloud, redact forced ON)
        opts = ArchiveOptionsDialog(
            self, title="云端归档选项", cloud_mode=True,
            default_redact=True, default_refine=True,
        )
        if opts.exec() != QDialog.Accepted:
            return
        do_refine = opts.refine_tsg

        # Confirm
        confirm = QMessageBox.question(
            self,
            "确认云端归档",
            f"将上传脱敏{'+TSG 精炼' if do_refine else ''}后的归档到:\n\n"
            f"  Org: {profile.organization}\n"
            f"  Project: {profile.project}\n"
            f"  Wiki: {profile.wiki_identifier}\n"
            f"  页面路径: {page_path}\n\n"
            "（云端归档强制脱敏；继续？）",
        )
        if confirm != QMessageBox.Yes:
            return

        # 1) Build the redacted (and optionally refined) archive locally
        if self._current_case is not None:
            local_target = (
                self._current_case.archives_dir / f"{ts}-cloud-archive"
            )
        else:
            local_target = default_archive_dir().with_name(
                f"{ts}-cloud-archive")

        try:
            note_md = self.editor.toMarkdown()
        except Exception:
            note_md = self.editor.toPlainText()
        images = self.editor.collect_image_paths()
        qa_md = self.result.to_markdown()
        title = (f"[{case_id}] 案例归档"
                 if self._current_case else "归档报告")

        kwargs = dict(
            target_dir=local_target,
            note_markdown=note_md,
            note_image_paths=images,
            qa_markdown=qa_md,
            title=title,
            redact=True,
            refine_tsg=do_refine,
        )
        progress = ArchiveProgressDialog(
            self,
            message=("正在脱敏并精炼为 TSG…"
                     if do_refine else "正在脱敏归档…"),
        )
        worker = ArchiveWorker(self, kwargs)
        worker_err: list[str] = []
        worker_md: list[Path] = []
        worker.succeeded.connect(lambda p: (worker_md.append(p),
                                            progress.accept()))
        worker.failed.connect(lambda m: (worker_err.append(m),
                                         progress.accept()))
        worker.start()
        progress.exec()
        worker.wait(2000)
        if worker_err:
            QMessageBox.critical(
                self, "云端归档失败（本地准备阶段）", worker_err[0])
            return
        if not worker_md:
            QMessageBox.critical(
                self, "云端归档失败（本地准备阶段）", "未返回归档路径")
            return
        archive_md = worker_md[0]

        # 2) Upload it
        self.statusBar().showMessage("☁ 正在上传到 Wiki…")
        attachment_prefix = f"{case_id}-{ts}"
        try:
            result = upload_archive(
                profile,
                archive_md_path=archive_md,
                page_path=page_path,
                attachment_prefix=attachment_prefix,
            )
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(
                self, "云端归档失败（上传阶段）",
                f"本地副本已生成: {archive_md}\n\n上传失败:\n{e}"
            )
            self.statusBar().showMessage("☁ 上传失败", 5000)
            return

        action = "更新" if result.page_updated else "创建"
        ret = QMessageBox.information(
            self,
            "云端归档完成",
            f"✅ 已{action} Wiki 页面:\n  {result.page_path}\n\n"
            f"上传附件数: {result.attachments_uploaded}\n"
            f"本地副本: {archive_md}\n\n"
            "在浏览器中打开 Wiki 页面？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if ret == QMessageBox.Yes:
            try:
                if os.name == "nt":
                    os.startfile(result.page_url)
                else:
                    subprocess.Popen(["xdg-open", result.page_url])
            except Exception:
                pass
        self.statusBar().showMessage(
            f"☁ Wiki {action}成功: {result.page_path}", 10000)

    def closeEvent(self, event):  # noqa: N802
        if not self._confirm_discard():
            event.ignore()
            return
        self.chat.runner.shutdown()
        super().closeEvent(event)

    # --- case follow-up ----------------------------------------------

    def _on_qa_ready(self, question: str, answer: str):
        """Show the final answer and persist it to the active case log."""
        if self._current_case is None:
            self.result.append_answer(question, answer)
            return

        try:
            self._current_case.append_qa(question, answer)
            self.result.load_qa_history(self._current_case.read_qa())
        except Exception as e:  # noqa: BLE001
            self.statusBar().showMessage(
                f"案例日志写入失败: {e}", 5000)

    def _update_case_menu_state(self):
        has = self._current_case is not None
        if hasattr(self, "_act_close_case"):
            self._act_close_case.setEnabled(has)

    def _flash_case_status(self):
        if self._current_case is None:
            self.statusBar().showMessage("已退出案例模式", 4000)
            return
        c = self._current_case
        self.statusBar().showMessage(
            f"📁 案例: {c.case_id}  ({c.root})", 6000
        )

    def _spawn_window_for_case(self, case: Case, fresh: bool) -> "MainWindow":
        """Open a new MainWindow with the given case (fresh AI session).

        Used when a case is newly created/opened so each case lives in
        its own window with an independent Copilot session.
        """
        win = MainWindow()
        # Keep a strong ref so the window isn't garbage-collected
        MainWindow._open_windows.append(win)
        # Drop ref on close to avoid leaks
        win.destroyed.connect(
            lambda _=None, w=win: MainWindow._open_windows.remove(w)
            if w in MainWindow._open_windows else None
        )
        try:
            win._activate_case(case, fresh=fresh)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "新窗口激活案例失败", str(e))
        win.show()
        win.raise_()
        win.activateWindow()
        return win

    def _open_case_in_appropriate_window(self, case: Case, fresh: bool):
        """Activate case in current window if empty, else spawn a new one."""
        if self._current_case is None and not self.editor.document().isModified():
            self._activate_case(case, fresh=fresh)
        else:
            self._spawn_window_for_case(case, fresh=fresh)

    def _ensure_case_root_configured(self) -> bool:
        """If the user hasn't picked a case-root yet, prompt them to.

        Returns True if a root is now configured, False if the user
        cancelled (callers should abort their flow).
        """
        if is_case_root_configured():
            return True
        QMessageBox.information(
            self, "首次使用 — 选择案例根目录",
            "尚未设置案例保存的根目录。\n\n"
            "下面将弹出目录选择框。所有案例都会保存到所选目录下。\n\n"
            f"如果直接取消，将使用默认目录:\n{get_case_root()}",
        )
        chosen = QFileDialog.getExistingDirectory(
            self, "选择案例根目录",
            str(get_case_root()),
        )
        if not chosen:
            # Fall back to default + persist so we don't keep prompting.
            try:
                set_case_root(get_case_root())
            except Exception as e:  # noqa: BLE001
                QMessageBox.critical(self, "设置失败", str(e))
                return False
            return True
        try:
            new_root = set_case_root(chosen)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "设置失败", str(e))
            return False
        self.statusBar().showMessage(
            f"📁 案例根目录已设置: {new_root}", 6000)
        return True

    def _on_change_case_root(self):
        """Let the user pick a new case root anytime."""
        chosen = QFileDialog.getExistingDirectory(
            self, "切换案例根目录",
            str(get_case_root()),
        )
        if not chosen:
            return None
        try:
            new_root = set_case_root(chosen)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "切换失败", str(e))
            return None
        self.statusBar().showMessage(
            f"📁 案例根目录已切换: {new_root}", 6000)
        return new_root

    def _on_new_case(self):
        if not self._ensure_case_root_configured():
            return
        case_id, ok = QInputDialog_get_text(
            self, "新建案例",
            f"案例根目录: {get_case_root()}\n\n"
            "案例号（如: TrackingID-12345）：",
        )
        if not ok or not case_id.strip():
            return
        title, _ok2 = QInputDialog_get_text(
            self, "新建案例", "案例标题（可留空）：", default=case_id.strip(),
        )
        try:
            case = create_case(case_id.strip(), title=title.strip())
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "创建失败", str(e))
            return
        self._open_case_in_appropriate_window(case, fresh=True)

    def _on_open_case(self):
        if not self._ensure_case_root_configured():
            return
        cases = list_cases()
        if not cases:
            ret = QMessageBox.question(
                self, "无案例",
                f"尚未创建任何案例。\n目录: {get_case_root()}\n\n"
                "现在创建一个？\n（如需更换根目录，请点击「No」后再次「打开案例」"
                "在弹出对话框里点「切换根目录…」。）",
            )
            if ret == QMessageBox.Yes:
                self._on_new_case()
            return
        while True:
            dlg = _CasePickerDialog(cases, parent=self)
            ret = dlg.exec()
            if ret == _CasePickerDialog.ChangeRootRequested:
                # User clicked "切换根目录…" — pick new root, refresh list,
                # and reopen the picker.
                if self._on_change_case_root() is None:
                    continue
                cases = list_cases()
                if not cases:
                    QMessageBox.information(
                        self, "新根目录暂无案例",
                        f"目录: {get_case_root()}\n\n"
                        "可点击「📂 新建案例」创建第一个。",
                    )
                    return
                continue
            if ret != QDialog.Accepted:
                return
            case = dlg.selected_case()
            if case is None:
                return
            try:
                case = open_case(case.root)
            except Exception as e:  # noqa: BLE001
                QMessageBox.critical(self, "打开失败", str(e))
                return
            self._open_case_in_appropriate_window(case, fresh=False)
            return

    def _on_close_case(self):
        if self._current_case is None:
            QMessageBox.information(self, "关闭案例", "当前没有打开的案例。")
            return
        case = self._current_case
        # Persist note before closing the window.
        if not self._save_case_note(silent=True):
            msg = self._last_case_save_error or "未知错误"
            ret = QMessageBox.question(
                self, "保存案例笔记失败",
                f"保存笔记时出错：\n{msg}\n\n仍要关闭窗口吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if ret != QMessageBox.Yes:
                return
        # Make sure closeEvent's _confirm_discard doesn't re-prompt
        try:
            self.editor.document().setModified(False)
        except Exception:
            pass
        # Stop the Copilot runner so it doesn't dangle after window close.
        try:
            self.chat.runner.stop()
        except Exception:
            pass
        self.statusBar().showMessage(
            f"已保存并关闭案例 [{case.case_id}]", 3000)
        # Close the window itself.
        self.close()

    def _on_reveal_case(self):
        if self._current_case is None:
            QMessageBox.information(
                self, "打开案例目录", "当前没有打开的案例。")
            return
        # Resolve OneDrive / symlink quirks → real filesystem path.
        try:
            raw = Path(self._current_case.root).resolve(strict=False)
        except Exception:
            raw = Path(self._current_case.root)
        path = str(raw)
        if not raw.is_dir():
            QMessageBox.critical(
                self, "打开目录失败",
                f"案例目录不存在或无法访问:\n{path}",
            )
            return
        # Try Qt's shell handler first (most reliable across OneDrive/UNC).
        opened = False
        try:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl as _QUrl
            opened = QDesktopServices.openUrl(_QUrl.fromLocalFile(path))
        except Exception:
            opened = False
        if not opened:
            try:
                if os.name == "nt":
                    # Fall back: explicit explorer.exe (handles paths os.startfile rejects)
                    subprocess.Popen(
                        ["explorer.exe", path], shell=False
                    )
                    opened = True
                else:
                    subprocess.Popen(["xdg-open", path])
                    opened = True
            except Exception as e:  # noqa: BLE001
                QMessageBox.critical(
                    self, "打开目录失败", f"{path}\n\n{e}")
                return
        # Visible feedback so user knows the action fired.
        self.statusBar().showMessage(
            f"📂 已在文件管理器中打开: {path}", 4000)

    def _on_upload_files(self):
        """Pick one or more files (multi-select)."""
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择要上传的文件 (可多选)",
            "",
            "所有支持类型 (*.log *.txt *.json *.csv *.tsv *.xml *.yaml "
            "*.yml *.out *.err *.tar *.gz *.zip *.7z *.png *.jpg *.jpeg "
            "*.bmp *.gif *.webp);;所有文件 (*)",
        )
        if not paths:
            return
        self._do_attach([Path(p) for p in paths])

    def _on_upload_folder(self):
        """Pick a folder; everything under it is uploaded recursively."""
        folder = QFileDialog.getExistingDirectory(
            self, "选择要上传的文件夹 (递归)", "",
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
        )
        if not folder:
            return
        self._do_attach([Path(folder)])

    def _on_upload_mixed(self):
        """Open a custom dialog that allows picking files AND folders.

        Qt's native file dialog can't multi-select files+folders together,
        so we use the non-native dialog with a tree-style chooser that
        supports both. Selection is iterative — user adds items in
        multiple rounds, then commits.
        """
        targets: list[Path] = []
        # Round-robin: keep showing a small prompt dialog until user
        # presses 上传. This avoids re-implementing a full file tree
        # while still supporting "files + folders together" cleanly.
        from PySide6.QtWidgets import QInputDialog
        while True:
            choice, ok = QInputDialog.getItem(
                self, "上传文件/文件夹",
                f"已选 {len(targets)} 项。继续添加，或选择「完成上传」。",
                ["➕ 添加文件 (可多选)",
                 "➕ 添加文件夹 (递归)",
                 "✅ 完成上传",
                 "❌ 取消"],
                0, False,
            )
            if not ok:
                return
            if choice.startswith("➕ 添加文件"):
                paths, _ = QFileDialog.getOpenFileNames(
                    self, "选择文件 (可多选)", "", "所有文件 (*)")
                targets.extend(Path(p) for p in paths)
            elif choice.startswith("➕ 添加文件夹"):
                folder = QFileDialog.getExistingDirectory(
                    self, "选择文件夹", "",
                    QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
                )
                if folder:
                    targets.append(Path(folder))
            elif choice.startswith("✅"):
                if not targets:
                    return
                self._do_attach(targets)
                return
            else:
                return

    def _do_attach(self, items: list[Path]):
        """Attach a mixed list of files and/or folders."""
        if not items:
            return
        n_files = 0
        n_bytes = 0
        errors: list[str] = []
        for raw in items:
            try:
                files, size = self.editor.attach_path(raw)
                n_files += files
                n_bytes += size
            except Exception as e:  # noqa: BLE001
                errors.append(f"{raw}\n  → {e}")

        if n_files:
            where = (
                str(self._current_case.attachments_dir)
                if self._current_case is not None
                else f"(暂存) {self.editor.pending_dir()}"
            )
            kb = max(1, n_bytes // 1024)
            size_str = (f"{kb} KB" if kb < 1024
                        else f"{kb / 1024:.1f} MB")
            self.statusBar().showMessage(
                f"📎 已上传 {n_files} 个文件 ({size_str}) 到 {where}",
                6000,
            )
        if errors:
            QMessageBox.warning(
                self, "部分内容上传失败",
                "以下项目未能上传：\n\n" + "\n\n".join(errors),
            )

    def _activate_case(self, case: Case, fresh: bool):
        """Switch UI into case mode: reset chat, load notes & QA history."""
        # 0. Activate FIRST so UI controls work even if subsequent steps fail
        self._current_case = case
        self._current_path = case.note_path
        # Reset autosave baselines so we don't rewrite freshly-loaded content
        self._autosave_last_note_hash = ""
        self._autosave_last_qa_count = 0
        # NOTE: do NOT touch() the case here — `updated` should reflect the
        # last actual edit/save/Q&A, which is what the case picker sorts by.
        self._update_case_menu_state()
        self._update_title()

        errors: list[str] = []

        # 1. Reset Copilot session so we don't carry stale memory
        try:
            self.chat.reset_session()
        except Exception as e:  # noqa: BLE001
            errors.append(f"重置会话: {e}")

        # 2. Load note.md into editor (or start blank with template)
        try:
            self.editor.clear()
            if case.note_path.is_file():
                load_document(case.note_path, self.editor.document())
            self.editor.set_attachments_dir(case.attachments_dir)
            self.editor.document().setModified(False)
        except Exception as e:  # noqa: BLE001
            errors.append(f"载入笔记: {e}")

        # 3. Load chat history into result pane
        try:
            history = case.read_qa()
            if history:
                banner = (
                    f"# 📁 案例 [{case.case_id}] {case.title}\n\n"
                    f"_已载入 {len(history)} 条历史对话_\n"
                    f"_案例目录: `{case.root}`_"
                )
                self.result.load_qa_history(history, banner=banner)
                self.chat.output.appendPlainText(
                    f"--- 已载入案例 [{case.case_id}] 的 "
                    f"{len(history)} 条历史对话（仅展示，"
                    "Copilot 上下文已重置）---"
                )
            else:
                self.result.clear()
                if not fresh:
                    self.chat.output.appendPlainText(
                        f"--- 已打开案例 [{case.case_id}]，"
                        "暂无历史对话 ---"
                    )
        except Exception as e:  # noqa: BLE001
            errors.append(f"载入历史对话: {e}")

        # 4. Surface any partial-failure info
        if errors:
            QMessageBox.warning(
                self, "案例已打开（部分步骤失败）",
                f"案例 [{case.case_id}] 已激活，但以下步骤出错：\n\n"
                + "\n".join(f"• {m}" for m in errors),
            )
            self.statusBar().showMessage(
                f"⚠ 案例 {case.case_id} 部分步骤失败", 8000)
        else:
            self.statusBar().showMessage(
                f"📁 案例已激活: {case.case_id}（{case.root}）", 8000)

    def _on_autosave_tick(self):
        """Periodic autosave (every 5s). Only active when a case is open."""
        if self._current_case is None:
            return

        # 1) Autosave the editor note if its content changed.
        # Hash the markdown serialization (not toPlainText) so that pasted
        # screenshots — which add image refs but no plain text — also
        # trigger autosave. Otherwise an image-only change would never
        # persist and the screenshot would be lost when switching cases.
        note_hash = self._current_note_hash()
        if note_hash != self._autosave_last_note_hash:
            if not self._save_case_note(silent=True):
                self.statusBar().showMessage(
                    f"自动保存案例笔记失败: {self._last_case_save_error}",
                    8000,
                )

        # 2) Autosave the result/QA log to result-snapshot.md
        try:
            qa_count = len(self.result.qa_pairs())
        except Exception:
            qa_count = 0
        if qa_count != self._autosave_last_qa_count:
            try:
                snap = self._current_case.root / "result-snapshot.md"
                qa_md = self.result.to_markdown() or "(空)"
                ts = _datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                snap.write_text(
                    f"# 结果快照\n\n_最后更新: {ts}_\n\n{qa_md}\n",
                    encoding="utf-8",
                )
                self._autosave_last_qa_count = qa_count
                self._current_case.touch()
            except Exception:
                pass

    def _current_note_hash(self) -> str:
        try:
            note_md = self.editor.document().toMarkdown()
        except Exception:
            try:
                note_md = self.editor.toPlainText()
            except Exception:
                note_md = ""
        return _hashlib.md5(note_md.encode("utf-8")).hexdigest()

    def _save_case_note(self, silent: bool = False) -> bool:
        """Save current editor content into the active case's note.md."""
        if self._current_case is None:
            return False
        try:
            pending = self.editor.pending_dir()
            save_document(
                self.editor.document(),
                self._current_case.note_path,
                pending_attachments_dir=pending,
            )
            self.editor.set_attachments_dir(
                self._current_case.attachments_dir)
            self.editor.document().setModified(False)
            self._current_case.touch()
            self._autosave_last_note_hash = self._current_note_hash()
            self._last_case_save_error = ""
            if not silent:
                self.statusBar().showMessage(
                    f"已保存案例笔记: {self._current_case.note_path}", 4000)
            return True
        except Exception as e:  # noqa: BLE001
            self._last_case_save_error = str(e)
            if not silent:
                QMessageBox.critical(self, "保存案例笔记失败", str(e))
            return False


def QInputDialog_get_text(parent, title: str, label: str,
                          default: str = "") -> tuple[str, bool]:
    """Local helper so we don't import QInputDialog at top-level."""
    from PySide6.QtWidgets import QInputDialog
    text, ok = QInputDialog.getText(parent, title, label, text=default)
    return text.strip(), ok


class _CasePickerDialog(QDialog):
    """List existing cases and let user pick one.

    Returns:
      QDialog.Accepted        — user picked a case (read it via selected_case)
      QDialog.Rejected        — user cancelled
      ChangeRootRequested     — user clicked "切换根目录…"; caller should
                                handle it then re-show the dialog.
    """

    ChangeRootRequested = 1234  # custom exec() return code

    def __init__(self, cases: list[Case], parent=None):
        super().__init__(parent)
        self.setWindowTitle("打开案例")
        self.resize(640, 460)
        v = QVBoxLayout(self)

        # Header row: current root + "switch root" button
        head = QHBoxLayout()
        self._root_label = QLabel(
            f"案例根目录: {get_case_root()}"
        )
        self._root_label.setWordWrap(True)
        head.addWidget(self._root_label, 1)
        btn_change = QToolButton()
        btn_change.setText("📂 切换根目录…")
        btn_change.setToolTip("修改案例的保存根目录（设置会持久化）")
        btn_change.clicked.connect(self._emit_change_root)
        head.addWidget(btn_change, 0)
        v.addLayout(head)

        v.addWidget(QLabel(
            "（按最后修改/保存时间倒序排列；最近使用的在最上方）"
        ))

        row = QHBoxLayout()
        row.addWidget(QLabel("过滤:"))
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("输入案例号或标题片段…")
        self._filter.textChanged.connect(self._apply_filter)
        row.addWidget(self._filter, 1)
        v.addLayout(row)

        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(lambda _: self.accept())
        v.addWidget(self._list, 1)

        self._cases = cases
        self._populate(cases)

        btns = QDialogButtonBox(
            QDialogButtonBox.Open | QDialogButtonBox.Cancel
        )
        btns.button(QDialogButtonBox.Open).setText("打开")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

    def _emit_change_root(self):
        self.done(self.ChangeRootRequested)

    def _populate(self, cases: list[Case]):
        self._list.clear()
        for c in cases:
            n_qa = 0
            try:
                if c.chat_log_path.is_file():
                    n_qa = sum(
                        1 for line in c.chat_log_path.read_text(
                            encoding="utf-8", errors="replace"
                        ).splitlines() if line.strip()
                    )
            except Exception:
                pass
            updated = c.updated.replace("T", " ") if c.updated else "?"
            label = (
                f"[{c.case_id}]  {c.title or ''}\n"
                f"   更新: {updated}   ·   对话: {n_qa} 条"
            )
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, c)
            self._list.addItem(item)
        if self._list.count() > 0:
            self._list.setCurrentRow(0)

    def _apply_filter(self, text: str):
        text = text.lower().strip()
        for i in range(self._list.count()):
            item = self._list.item(i)
            c: Case = item.data(Qt.UserRole)
            visible = (
                not text
                or text in c.case_id.lower()
                or text in (c.title or "").lower()
            )
            item.setHidden(not visible)

    def selected_case(self) -> Case | None:
        item = self._list.currentItem()
        if item is None or item.isHidden():
            for i in range(self._list.count()):
                it = self._list.item(i)
                if not it.isHidden():
                    return it.data(Qt.UserRole)
            return None
        return item.data(Qt.UserRole)

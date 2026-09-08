"""English application chrome without translating user content or AI contracts."""
import ast
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QAction
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QComboBox, QDialogButtonBox, QWidget,
)

from test_chat_steering import FakeRunner, FakeSettings, marked
from core.archive import _write_html_report
from core.tsg_summarizer import _ensure_tsg_structure, _TSG_PROMPT_TEMPLATE
from core.wiki_config import WikiConfig
from ui.archive_dialogs import ArchiveOptionsDialog, ArchiveProgressDialog
from ui.cloud_archive_dialog import CloudArchiveDialog
from ui.main_window import MainWindow, _CasePickerDialog
from ui.theme import apply_theme
from ui.wiki_settings import WikiSettingsDialog


CJK = re.compile(r"[\u3400-\u9fff]")
ROOT = Path(__file__).resolve().parents[1]


class EnglishUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")
        apply_theme(cls.app)

    def setUp(self):
        self.patches = [
            patch("ui.chat_pane.ConversationRunner", FakeRunner),
            patch("ui.chat_pane.QSettings", FakeSettings),
            patch("ui.az_bar.AzAccountBar.refresh", lambda self: None),
            patch("ui.wiki_settings.load_config", lambda: WikiConfig()),
            patch("ui.main_window.get_case_root", lambda: Path(r"C:\Cases")),
        ]
        for item in self.patches:
            item.start()
        self.window = MainWindow()
        self.window._autosave_timer.stop()
        self.widgets = [self.window]

    def tearDown(self):
        # Drain ResultPane's existing delayed scroll callbacks before deletion.
        QTest.qWait(600)
        for widget in reversed(self.widgets):
            widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        for item in reversed(self.patches):
            item.stop()

    def assertEnglishChrome(self, root):
        for obj in [root, *root.findChildren(QWidget), *root.findChildren(QAction)]:
            for name in (
                "text", "toolTip", "statusTip", "windowTitle",
                "placeholderText", "accessibleName",
            ):
                getter = getattr(obj, name, None)
                if callable(getter):
                    value = getter()
                    if isinstance(value, str):
                        self.assertNotRegex(value, CJK, f"{type(obj).__name__}.{name}")
            if isinstance(obj, QComboBox):
                for index in range(obj.count()):
                    self.assertNotRegex(obj.itemText(index), CJK)

    def assertButtonsFit(self, root):
        root.show()
        self.app.processEvents()
        for button in root.findChildren(QAbstractButton):
            if button.text() and button.isVisibleTo(root):
                self.assertGreaterEqual(
                    button.width(), button.sizeHint().width(), button.text()
                )

    def test_main_actions_buttons_models_and_shortcuts(self):
        actions = self.window.menuBar().actions()
        self.assertEqual(
            [action.text() for action in actions],
            ["📂  New Case", "📁  Open Case", "✖  Close Case"],
        )
        self.assertEqual(
            [action.shortcut().toString() for action in actions],
            ["Ctrl+Shift+N", "Ctrl+Shift+O", "Ctrl+Shift+W"],
        )
        self.assertEqual(
            [self.window.chat.model_combo.itemData(i) for i in range(4)],
            ["", "gpt-5.6-sol-fast", "gpt-5.6-sol", "gpt-6-astra"],
        )
        self.assertEqual(self.window.statusBar().currentMessage(), "Ready")
        self.assertEnglishChrome(self.window)
        self.assertButtonsFit(self.window)
        self.assertLessEqual(self.window.width(), 1400)

    def test_find_labels_and_non_destructive_search(self):
        self.window.editor.setPlainText("Alpha alpha\n用户笔记")
        before = self.window.editor.toMarkdown()
        search = self.window.editor_find
        search.open_search()
        search.input.setText("alpha")
        self.assertEqual(search.count.text(), "1/2")
        search.navigate(1)
        self.assertEqual(search.count.text(), "2/2")
        search.input.setText("missing")
        self.assertEqual(search.count.text(), "No matches")
        self.assertEnglishChrome(search)
        self.assertButtonsFit(self.window)
        search.close_search()
        self.assertEqual(self.window.editor.toMarkdown(), before)

    def test_archive_and_wiki_dialog_chrome(self):
        for dialog in (
            ArchiveOptionsDialog(), ArchiveOptionsDialog(cloud_mode=True),
            ArchiveProgressDialog(), CloudArchiveDialog(),
            WikiSettingsDialog(), _CasePickerDialog([]),
        ):
            self.widgets.append(dialog)
            self.assertEnglishChrome(dialog)
            self.assertButtonsFit(dialog)
        cloud_options = self.widgets[2]
        self.assertTrue(cloud_options.redact)
        self.assertFalse(cloud_options.redact_chk.isEnabled())

    def test_cloud_validation_and_url_preview(self):
        dialog = CloudArchiveDialog()
        self.widgets.append(dialog)
        self.assertEqual(dialog.name_edit.text(), "Archive")
        self.assertFalse(dialog._ok_btn.isEnabled())
        with patch("PySide6.QtWidgets.QMessageBox.warning") as warning:
            dialog._on_accept()
            self.assertEqual(warning.call_args.args[1], "Invalid Wiki URL")
            self.assertNotRegex(warning.call_args.args[2], CJK)
        dialog.url_edit.setPlainText(
            "https://dev.azure.com/example/project/_wiki/wikis/project.wiki"
        )
        dialog.name_edit.setText("child/page")
        self.assertFalse(dialog._ok_btn.isEnabled())
        self.assertIn("cannot contain /", dialog.preview_label.text())
        dialog.name_edit.setText("Report")
        self.assertTrue(dialog._ok_btn.isEnabled())
        self.assertEnglishChrome(dialog)
        dialog._on_accept()
        self.assertTrue(dialog.page_path.endswith("/Report"))

    def test_message_boxes_and_upload_branch_labels(self):
        with patch("ui.main_window.QMessageBox.warning") as warning, patch.object(
            self.window.result, "to_markdown", return_value=""
        ):
            self.assertTrue(self.window._archive_inputs_empty())
            self.assertEqual(warning.call_args.args[1], "Nothing to Archive")
            self.assertNotRegex(warning.call_args.args[2], CJK)
        with patch("PySide6.QtWidgets.QInputDialog.getItem", side_effect=[
            ("➕ Add Folder (recursive)", True), ("✅ Finish Upload", True),
        ]) as chooser, patch(
            "ui.main_window.QFileDialog.getExistingDirectory", return_value=r"C:\Logs"
        ) as folder, patch(
            "ui.main_window.QFileDialog.getOpenFileNames"
        ) as files, patch.object(self.window, "_do_attach") as attach:
            self.window._on_upload_mixed()
            folder.assert_called_once()
            files.assert_not_called()
            attach.assert_called_once_with([Path(r"C:\Logs")])
            self.assertNotRegex(chooser.call_args.args[2], CJK)
            for label in chooser.call_args.args[3]:
                self.assertNotRegex(label, CJK)

    def test_azure_empty_and_busy_states_fit(self):
        bar = self.window.az_bar
        bar._apply_refresh_data({
            "clouds": ["AzureChinaCloud"], "current_cloud": "AzureChinaCloud",
            "account": None, "subs": [],
        })
        self.assertEqual(bar.user_box.currentText(), "(not signed in)")
        bar._begin_busy("Loading Azure accounts and subscriptions…")
        self.assertEnglishChrome(bar)
        self.assertButtonsFit(self.window)
        self.assertLessEqual(self.window.width(), 1400)
        bar._end_busy()

    def test_busy_interjection_acknowledgement_and_completion(self):
        pane = self.window.chat
        runner = pane.runner
        pane.send("original")
        self.assertEqual(pane.status_label.text(), "Running…")
        self.assertEqual(pane.send_btn.text(), "➤  Add Request")
        self.assertIn("Add a condition", pane.input.placeholderText())
        self.assertEnglishChrome(pane)
        self.assertButtonsFit(self.window)
        runner.accept(0)
        pane.send("extra")
        self.assertIn("Submitting addition", pane.delivery_label.text())
        runner.accept(1)
        self.assertEqual(pane._current_question, "original\n\nAdditional request 1: extra")
        self.assertIn("CLI accepted the addition", pane.delivery_label.text())
        runner.finish(marked("Complete answer"))
        self.assertEqual(pane.status_label.text(), "Ready")
        self.assertIn("Task complete", pane.delivery_label.text())
        self.assertIn("Additional request 1: extra", self.window.result.to_markdown())
        self.assertNotRegex(self.window.result.view.toPlainText(), CJK)
        self.assertNotRegex(pane.output.toPlainText(), CJK)
        self.assertEnglishChrome(self.window)

    def test_rejection_stop_and_legacy_states(self):
        pane = self.window.chat
        runner = pane.runner
        pane.send("original")
        runner.accept(0)
        pane.send("extra")
        pane.input.setText("new draft")
        runner.message_rejected.emit(runner.requests[1][0], "Offline")
        self.assertIn("Not delivered", pane.delivery_label.text())
        self.assertEqual(pane.retry_btn.text(), "Restore Unsent (1)")
        pane._restore_failed_message()
        self.assertIn("Send or clear", pane.delivery_label.text())
        self.assertEnglishChrome(pane)
        pane._on_stop()
        self.assertEqual(pane.status_label.text(), "Stopping…")
        self.assertEnglishChrome(pane)
        runner.finish(code=-2)
        self.assertEqual(pane.status_label.text(), "Stopped")
        self.assertIn("not undone", pane.delivery_label.text())
        pane._on_live_mode_changed(False)
        self.assertIn("Legacy mode", pane.delivery_label.text())
        pane.send("legacy")
        pane.send("too soon")
        self.assertIn("legacy mode", pane.delivery_label.text())
        self.assertEnglishChrome(pane)

    def test_failure_empty_answer_and_reset_states(self):
        pane = self.window.chat
        pane.send("question")
        pane.runner.accept(0)
        pane.runner.finish(code=1)
        self.assertEqual(pane.status_label.text(), "Task failed")
        self.assertEnglishChrome(pane)
        pane.reset_session()
        self.assertEqual(pane.delivery_label.text(), "Session reset.")
        pane.send("again")
        pane.runner.accept(1)
        pane.runner.finish()
        self.assertIn("without a final answer", pane.delivery_label.text())
        self.assertEnglishChrome(pane)

    def test_user_history_is_not_translated(self):
        history = [("10:00", "原始问题", "原始答案")]
        self.window.result.load_qa_history(history)
        self.assertEqual(self.window.result.qa_pairs(), history)
        self.assertIn("原始答案", self.window.result.to_markdown())
        self.assertEqual(self.window.result.subtitle.text(), "Final answers · 1")

    def test_generated_report_labels_are_english(self):
        with patch.object(Path, "read_text", return_value="## Notes\n\nUser content"), patch.object(
            Path, "write_text"
        ) as write:
            _write_html_report(Path("archive.md"), Path("archive.html"), title="", redacted=True)
            report = write.call_args.args[0]
            self.assertIn('lang="en"', report)
            self.assertIn("Archive Report", report)
            self.assertIn("Redacted", report)
            self.assertNotRegex(report, CJK)
        self.assertIn("## 1. Symptom", _ensure_tsg_structure("# Report\n\nEvidence\n## 2. Scope", "Report"))
        for line in _TSG_PROMPT_TEMPLATE.splitlines():
            if line.startswith(("## ", "> _")):
                self.assertNotRegex(line, CJK)


class EnglishSourceTests(unittest.TestCase):
    def test_no_cjk_literals_outside_documentation_and_internal_prompts(self):
        violations = []
        for path in [ROOT / "main.py", *sorted((ROOT / "ui").glob("*.py")),
                     *sorted((ROOT / "core").glob("*.py"))]:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            ignored = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    if (node.body and isinstance(node.body[0], ast.Expr)
                            and isinstance(node.body[0].value, ast.Constant)
                            and isinstance(node.body[0].value.value, str)):
                        ignored.update(id(child) for child in ast.walk(node.body[0]))
                # Internal instructions are not UI chrome or stored user questions.
                if (path.name == "chat_pane.py" and isinstance(node, ast.FunctionDef)
                        and node.name == "_with_final_answer_contract"):
                    ignored.update(id(child) for child in ast.walk(node))
                if isinstance(node, ast.Assign):
                    targets = {target.id for target in node.targets if isinstance(target, ast.Name)}
                    if ((path.name == "chat_pane.py" and targets & {"full", "attach_note"})
                            or (path.name == "tsg_summarizer.py" and "_TSG_PROMPT_TEMPLATE" in targets)):
                        ignored.update(id(child) for child in ast.walk(node.value))
                if path.name == "copilot_runner.py" and isinstance(node, ast.Return):
                    # Only the long-prompt file-reading instruction may remain Chinese.
                    if isinstance(node.value, ast.JoinedStr):
                        values = [child.value for child in node.value.values if isinstance(child, ast.Constant)]
                        if values and str(values[0]).startswith("我的完整问题和上下文太长"):
                            ignored.update(id(child) for child in ast.walk(node.value))
            for node in ast.walk(tree):
                if (id(node) not in ignored and isinstance(node, ast.Constant)
                        and isinstance(node.value, str) and CJK.search(node.value)):
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: {node.value[:80]}")
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()

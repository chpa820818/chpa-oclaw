"""Qt UI regressions; run with python -m unittest discover -s tests."""
import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from ui.chat_pane import ChatPane, _FINAL_BEGIN, _FINAL_END


class FakeSettings:
    def __init__(self, *args):
        pass

    def value(self, key, default=None, type=None):
        return default

    def setValue(self, key, value):
        pass


class FakeRunner(QObject):
    output_received = Signal(str)
    process_started = Signal()
    process_finished = Signal(int)
    error_occurred = Signal(str)
    message_accepted = Signal(str)
    message_rejected = Signal(str, str)
    info_received = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.live_mode = True
        self.running = False
        self.history = False
        self.requests = []
        self.final = ""
        self.stopped = False

    def set_live_mode(self, enabled):
        self.live_mode = enabled
        return True

    def set_model(self, model, effort):
        pass

    def submit(self, request_id, prompt, attachments=None):
        self.requests.append((request_id, prompt, attachments))
        if not self.running:
            self.running = True
            self.process_started.emit()
        return True

    def is_running(self):
        return self.running

    def has_session_history(self):
        return self.history

    def submissions_allowed(self):
        return not self.stopped and (self.live_mode or not self.running)

    def last_output(self):
        return self.final

    def accept(self, index):
        self.history = True
        self.message_accepted.emit(self.requests[index][0])

    def stop(self):
        self.stopped = True

    def reset_session(self):
        self.running = False
        self.history = False

    def finish(self, text="", code=0):
        self.final = text
        self.running = False
        self.stopped = False
        self.process_finished.emit(code)


def marked(text):
    return f"{_FINAL_BEGIN}\n{text}\n{_FINAL_END}"


class ChatSteeringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")

    def setUp(self):
        with patch("ui.chat_pane.ConversationRunner", FakeRunner), patch(
            "ui.chat_pane.QSettings", FakeSettings
        ):
            self.pane = ChatPane()
        self.runner = self.pane.runner
        self.answers = []
        self.pane.answer_ready.connect(
            lambda q, a: self.answers.append((q, a))
        )

    def tearDown(self):
        self.pane.deleteLater()
        self.app.processEvents()

    def test_busy_addition_preserves_question_and_stream(self):
        self.pane.input.setText("original")
        self.pane.send("original", note="note")
        self.runner.accept(0)
        self.runner.output_received.emit("progress")
        self.pane.input.setText("extra condition")
        self.pane.send("extra condition", note="note")
        self.assertEqual(self.pane._buffer, ["progress"])
        self.assertEqual(self.pane._current_question, "original")
        self.assertTrue(self.pane.send_btn.isEnabled())
        self.assertFalse(self.pane.model_combo.isEnabled())
        self.runner.accept(1)
        self.runner.finish(marked("combined answer"))
        self.assertEqual(len(self.answers), 1)
        self.assertIn("original", self.answers[0][0])
        self.assertIn("extra condition", self.answers[0][0])
        self.assertEqual(self.answers[0][1], "combined answer")

    def test_acknowledgement_controls_note_and_image_sync(self):
        image = Path(r"C:\example\image.png")
        self.pane.send("question", note="first note", attachments=[image])
        self.assertFalse(self.pane._sent_image_keys)
        self.assertEqual(self.pane._last_note_hash, "")
        self.runner.accept(0)
        self.assertEqual(len(self.pane._sent_image_keys), 1)
        self.assertEqual(
            self.pane._last_note_hash, self.pane._hash_note("first note")
        )
        self.pane.send("extra", note="changed note", attachments=[image])
        self.assertIn("changed note", self.runner.requests[1][1])
        self.assertIsNone(self.runner.requests[1][2])
        self.runner.message_rejected.emit(self.runner.requests[1][0], "offline")
        self.assertEqual(
            self.pane._last_note_hash, self.pane._hash_note("first note")
        )
        self.assertEqual(self.pane._current_question, "question")

    def test_rejection_keeps_new_draft_and_has_recovery(self):
        self.pane.input.setText("first")
        self.pane.send("first")
        self.runner.accept(0)
        self.pane.input.setText("addition")
        self.pane.send("addition")
        self.pane.input.setText("new draft")
        self.runner.message_rejected.emit(self.runner.requests[1][0], "offline")
        self.assertEqual(self.pane.input.text(), "new draft")
        self.assertEqual(self.pane._failed_messages, ["addition"])
        self.pane.input.clear()
        self.pane._restore_failed_message()
        self.assertEqual(self.pane.input.text(), "addition")

    def test_late_addition_preserves_both_completed_turns(self):
        self.pane.send("original")
        self.runner.accept(0)
        self.pane.send("late question")
        self.runner.accept(1)
        self.runner.finish(marked("first answer") + "\n" + marked("late answer"))
        self.assertIn("first answer", self.answers[0][1])
        self.assertIn("late answer", self.answers[0][1])
        self.assertIn("late question", self.answers[0][0])

    def test_stop_does_not_publish_partial_answer(self):
        self.pane.send("original")
        self.runner.accept(0)
        self.pane._on_stop()
        self.assertTrue(self.runner.stopped)
        self.assertFalse(self.pane.send_btn.isEnabled())
        self.pane.send("too early")
        self.assertEqual(len(self.runner.requests), 1)
        self.runner.finish(marked("partial"), -2)
        self.assertFalse(self.answers)
        self.assertTrue(self.pane.send_btn.isEnabled())

    def test_reset_ignores_old_acknowledgements(self):
        self.pane.send("old question", note="old note")
        old_id = self.runner.requests[0][0]
        self.pane.reset_session()
        self.runner.message_accepted.emit(old_id)
        self.runner.message_rejected.emit(old_id, "cancelled")
        self.assertEqual(self.pane._current_question, "")
        self.assertEqual(self.pane._last_note_hash, "")
        self.assertFalse(self.pane._failed_messages)

    def test_empty_busy_input_does_not_send_default_question(self):
        emitted = []
        self.pane.send_requested.connect(emitted.append)
        self.pane.send("original")
        self.pane.input.clear()
        self.pane._on_send()
        self.assertEqual(emitted, [])

    def test_legacy_rejects_busy_input_without_clearing_buffer(self):
        self.runner.live_mode = False
        self.pane.send("original")
        self.runner.accept(0)
        self.runner.output_received.emit("progress")
        self.pane.input.setText("new draft")
        self.pane.send("new draft")
        self.assertEqual(len(self.runner.requests), 1)
        self.assertEqual(self.pane._buffer, ["progress"])
        self.assertEqual(self.pane._current_question, "original")
        self.assertEqual(self.pane.input.text(), "new draft")

    def test_failure_resends_context_and_keeps_questions(self):
        self.pane.send("original", note="note")
        self.runner.accept(0)
        self.pane.send("addition")
        self.runner.accept(1)
        self.runner.finish(marked("partial"), 1)
        self.assertFalse(self.answers)
        self.assertEqual(self.pane._last_note_hash, "")
        self.assertIn("original", self.pane.input.text())
        self.assertIn("addition", self.pane.input.text())


if __name__ == "__main__":
    unittest.main()

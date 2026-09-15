"""Qt UI regressions; run with python -m unittest discover -s tests."""
import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from ui.chat_pane import (
    ChatPane, _FINAL_BEGIN, _FINAL_END, _with_final_answer_contract,
)


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

    def shutdown(self):
        self.stopped = True

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

    def test_default_brief_contract_preserves_required_output(self):
        for live_mode in (True, False):
            with self.subTest(live_mode=live_mode):
                self.pane.reset_session()
                self.runner.live_mode = live_mode
                self.pane.send("Return the complete query", note="Evidence")
                prompt = self.runner.requests[-1][1]
                self.assertIn("Return the complete query", prompt)
                self.assertIn("Evidence", prompt)
                self.assertIn("默认简要回答当前问题", prompt)
                self.assertIn("150至300字", prompt)
                self.assertIn("不为缩短篇幅截断必要内容", prompt)
                self.assertNotIn("不要只给一句摘要", prompt)
                self.assertIn(_FINAL_BEGIN, prompt)
                self.assertIn(_FINAL_END, prompt)

    def test_explicit_detail_contract_is_per_turn(self):
        detailed = _with_final_answer_contract("Explain", detailed=True)
        self.assertIn("本轮为按需展开", detailed)
        self.assertIn("不要重新执行已完成的操作", detailed)
        self.assertIn("不要虚构证据", detailed)
        self.assertNotIn("150至300字", detailed)
        self.assertIn("默认简要回答当前问题", _with_final_answer_contract("Next"))

    def test_programmatic_expansion_preserves_draft_and_separates_context(self):
        self.pane.input.setText("Unsent draft")
        self.pane.send(
            "Explain more: question", detailed=True,
            answer_context="Existing answer", preserve_draft=True,
        )
        self.assertEqual(self.pane.input.text(), "Unsent draft")
        self.assertIn("Existing answer", self.runner.requests[0][1])
        self.runner.accept(0)
        self.runner.finish(marked("Detailed explanation"))
        self.assertEqual(
            self.answers, [("Explain more: question", "Detailed explanation")]
        )
        self.pane.send("Next question")
        self.assertIn("默认简要回答当前问题", self.runner.requests[1][1])
        self.assertNotIn("Existing answer", self.runner.requests[1][1])

    def test_expansion_unavailable_before_async_start(self):
        states = []
        self.pane.expansion_available.connect(states.append)
        with patch.object(self.runner, "submit", return_value=True):
            self.pane.send("Pending question")
        self.assertFalse(self.runner.is_running())
        self.assertFalse(self.pane.can_expand())
        self.assertEqual(states[-1], False)
        request_id = next(iter(self.pane._pending))
        self.runner.message_rejected.emit(request_id, "Offline")
        self.assertTrue(self.pane.can_expand())
        self.assertEqual(states[-1], True)


if __name__ == "__main__":
    unittest.main()

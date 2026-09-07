import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from core.conversation_runner import ConversationRunner
from core.runtime_launcher import _version_key


class FakeBackend(QObject):
    output_received = Signal(str)
    process_started = Signal()
    process_finished = Signal(int)
    error_occurred = Signal(str)
    message_accepted = Signal(str)
    message_rejected = Signal(str, str)
    info_received = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.running = False
        self.ready = True
        self.received = []
        self.closed = False
        self.history = False

    def set_model(self, model, effort):
        pass

    def submit(self, *args):
        self.received.append(args)
        self.running = True
        return True

    def send(self, prompt, attachments):
        self.received.append((prompt, attachments))
        self.running = True
        return True

    def is_running(self):
        return self.running

    def has_session_history(self):
        return self.history

    def submissions_allowed(self):
        return self.ready

    def last_output(self):
        return "answer"

    def stop(self):
        self.running = False

    def reset_session(self):
        self.running = False
        self.history = False

    def shutdown(self):
        self.closed = True


class ConversationRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")

    def setUp(self):
        self.patches = [
            patch("core.conversation_runner.CopilotRunner", FakeBackend),
            patch("core.live_runner.LiveCopilotRunner", FakeBackend),
        ]
        for item in self.patches:
            item.start()
        self.runner = ConversationRunner()

    def tearDown(self):
        self.runner.shutdown()
        self.runner.deleteLater()
        for item in reversed(self.patches):
            item.stop()
        self.app.processEvents()

    def test_live_attachments_are_sdk_objects_and_model_is_forwarded(self):
        self.runner.set_model("gpt-5.6-sol", "low")
        image = Path(r"C:\example\image.png")
        self.assertTrue(self.runner.submit("request", "prompt", [image]))
        args = self.runner._live.received[0]
        self.assertEqual(args[:2], ("request", "prompt"))
        self.assertEqual(args[2], [{
            "type": "file", "path": str(image.resolve()), "displayName": image.name,
        }])
        self.assertEqual(args[3:], ("gpt-5.6-sol", "low"))
        from copilot.generated.session_events import AttachmentFile
        self.assertEqual(AttachmentFile.from_dict(args[2][0]).display_name, image.name)

    def test_legacy_keeps_original_paths_and_acknowledges(self):
        self.runner.set_live_mode(False)
        accepted = []
        self.runner.message_accepted.connect(accepted.append)
        image = Path(r"C:\example\image.png")
        self.runner.submit("request", "prompt", [image])
        self.assertEqual(self.runner._legacy.received, [("prompt", [image])])
        self.assertEqual(accepted, ["request"])

    def test_unselected_or_closed_backend_cannot_publish(self):
        received = []
        self.runner.output_received.connect(received.append)
        self.runner._legacy.output_received.emit("wrong backend")
        self.assertEqual(received, [])
        self.runner.submit("request", "prompt")
        self.runner._live.output_received.emit("live")
        self.assertEqual(received, ["live"])
        self.runner.shutdown()
        self.runner._live.output_received.emit("stale")
        self.assertEqual(received, ["live"])

    def test_mode_cannot_change_during_task_and_reset_readiness_is_exposed(self):
        self.runner.submit("request", "prompt")
        self.assertFalse(self.runner.set_live_mode(False))
        self.assertTrue(self.runner.live_mode)
        self.runner._live.ready = False
        self.assertFalse(self.runner.submissions_allowed())

    def test_runtime_versions_sort_numerically(self):
        self.assertGreater(_version_key("1.0.84-1"), _version_key("1.0.24"))
        self.assertGreater(_version_key("1.0.84"), _version_key("1.0.84-1"))
        self.assertGreater(_version_key("1.0.84-10"), _version_key("1.0.84-2"))
        self.assertEqual(_version_key("incomplete"), ())


if __name__ == "__main__":
    unittest.main()

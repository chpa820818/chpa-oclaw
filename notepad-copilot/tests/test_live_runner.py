"""Live backend regressions using an in-memory SDK, never model requests."""
import asyncio
import os
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from core.live_runner import LiveCopilotRunner, _load_sdk


class FakeSession:
    def __init__(self, controller, config):
        self.controller = controller
        self.config = config
        self.callback = config["on_event"]
        self.calls = []
        self.switches = []
        self.disconnected = False
        self.aborted = False
        self.active = False
        self.queue_items = []
        self.steering_messages = []
        self.rpc = SimpleNamespace(
            queue=SimpleNamespace(pending_items=self.pending_items),
            metadata=SimpleNamespace(activity=self.activity),
            model=SimpleNamespace(get_current=self.get_current_model),
        )

    async def get_current_model(self, **kwargs):
        return SimpleNamespace(model_id="default-model")

    async def pending_items(self, **kwargs):
        return SimpleNamespace(items=self.queue_items, steering_messages=self.steering_messages)

    async def activity(self, **kwargs):
        return SimpleNamespace(has_active_work=self.active)

    def event(self, kind, *, agent_id=None, event_id=None, **data):
        if not agent_id:
            if kind == "assistant.turn_start":
                self.active = True
            elif kind == "session.idle":
                self.active = False
        self.callback(SimpleNamespace(
            type=kind, data=SimpleNamespace(**data), agent_id=agent_id, id=event_id,
        ))

    async def send(self, prompt, **options):
        self.calls.append((prompt, options))
        self.event("assistant.turn_start")
        if self.controller.behaviors:
            behavior = self.controller.behaviors.pop(0)
            await behavior(self)
        return str(len(self.calls))

    async def set_model(self, model, **options):
        self.switches.append((model, options))

    async def abort(self):
        self.aborted = True
        self.event("session.idle")

    async def disconnect(self):
        self.disconnected = True


class FakeClient:
    def __init__(self, controller):
        self.controller = controller
        self.stopped = False

    async def start(self):
        if self.controller.start_gate:
            await self.controller.start_gate.wait()
        if self.controller.start_error:
            raise self.controller.start_error

    async def create_session(self, **config):
        session = FakeSession(self.controller, config)
        self.controller.sessions.append(session)
        return session

    async def resume_session(self, session_id, **config):
        config["session_id"] = session_id
        self.controller.resumes.append(config)
        session = await self.create_session(**config)
        session.event("assistant.message", content="replayed history")
        session.event("session.error", message="historical error")
        return session

    async def stop(self):
        self.controller.stop_entered.set()
        if self.controller.stop_gate:
            await self.controller.stop_gate.wait()
        if self.controller.stop_error:
            raise self.controller.stop_error
        self.stopped = True

    async def force_stop(self):
        self.stopped = True

    async def ping(self):
        if self.controller.ping_error:
            raise self.controller.ping_error
        return None


class Controller:
    def __init__(self):
        self.clients = []
        self.sessions = []
        self.resumes = []
        self.behaviors = []
        self.start_error = None
        self.start_gate = None
        self.stop_gate = None
        self.stop_entered = threading.Event()
        self.stop_error = None
        self.ping_error = None

    def new_client(self):
        client = FakeClient(self)
        self.clients.append(client)
        return client, lambda request, invocation: None


class LiveRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        log_patch = patch("core.live_runner._log")
        log_patch.start()
        self.addCleanup(log_patch.stop)
        self.controller = Controller()
        self.runner = LiveCopilotRunner(working_directory=os.getcwd())
        self.runner._new_client = self.controller.new_client
        self.accepted = []
        self.rejected = []
        self.finished = []
        self.started = []
        self.errors = []
        self.output = []
        self.info = []
        self.runner.message_accepted.connect(self.accepted.append)
        self.runner.message_rejected.connect(lambda rid, why: self.rejected.append((rid, why)))
        self.runner.process_finished.connect(self.finished.append)
        self.runner.process_started.connect(lambda: self.started.append(True))
        self.runner.error_occurred.connect(self.errors.append)
        self.runner.output_received.connect(self.output.append)
        self.runner.info_received.connect(self.info.append)

    def tearDown(self):
        self.runner.shutdown()
        if self.runner._thread:
            self.runner._thread.join(timeout=2)
            self.assertFalse(self.runner._thread.is_alive(), "SDK worker leaked")
        self.app.processEvents()

    def wait(self, predicate, timeout=2):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.005)
        self.fail("Timed out waiting for fake SDK state")

    def pump(self, duration=0.1):
        end = time.monotonic() + duration
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.005)

    def event(self, kind, session=None, **data):
        session = session or self.controller.sessions[-1]
        self.runner._loop.call_soon_threadsafe(lambda: session.event(kind, **data))

    def submit(self, rid="one", prompt="first", **kwargs):
        self.assertTrue(self.runner.submit(rid, prompt, **kwargs))

    def test_steers_active_task_and_preserves_root_final_messages(self):
        self.submit(attachments=[{"type": "file", "path": r"C:\image.png"}])
        self.wait(lambda: self.accepted == ["one"])
        session = self.controller.sessions[0]
        self.assertTrue(self.runner.is_running())
        self.submit("two", "correction")
        self.wait(lambda: self.accepted == ["one", "two"])
        self.assertEqual([call[1]["mode"] for call in session.calls], ["immediate"] * 2)
        self.assertEqual(session.calls[0][1]["attachments"][0]["path"], r"C:\image.png")
        self.event("assistant.message_delta", delta_content="stream")
        self.event("assistant.message", content="first final", message_id="m1")
        self.event("assistant.message", content="subagent secret", agent_id="child")
        self.event("assistant.message", content="revised first", message_id="m1")
        self.event("assistant.message", content="second final", message_id="m2")
        self.event("tool.execution_start", tool_name="read_file", arguments={"secret": "hidden"})
        self.event("session.idle")
        self.wait(lambda: self.finished == [0])
        self.assertEqual(self.runner.last_output(), "revised first\n\nsecond final")
        self.assertEqual(len(self.started), 1)
        self.assertNotIn("hidden", "".join(self.output))
        self.assertIn("read_file", "".join(self.output))
        self.assertTrue(self.runner.has_session_history())
        self.assertFalse(self.runner.is_running())
        self.assertFalse(self.controller.clients[0].stopped)
        self.assertTrue(session.config["enable_config_discovery"])
        self.assertTrue(session.config["enable_skills"])
        self.assertFalse(session.config["skip_custom_instructions"])

    def test_idle_during_pending_send_and_before_ack_does_not_finish_early(self):
        self.submit()
        self.wait(lambda: len(self.accepted) == 1)
        entered = threading.Event()
        release = asyncio.Event()

        async def delayed(session):
            entered.set()
            await release.wait()
            session.event("assistant.message", content="all done")
            session.event("session.idle")  # SDK notification beats send response.

        self.controller.behaviors.append(delayed)
        self.submit("two", "more")
        self.wait(entered.is_set)
        self.event("session.idle")
        self.pump()
        self.assertEqual(self.finished, [])
        self.assertTrue(self.runner.is_running())
        self.runner._loop.call_soon_threadsafe(release.set)
        self.wait(lambda: self.finished == [0])
        self.assertEqual(self.accepted, ["one", "two"])
        self.assertEqual(self.runner.last_output(), "all done")

    def test_late_send_fences_queued_idle_completion_and_keeps_all_blocks(self):
        self.submit()
        self.wait(lambda: len(self.accepted) == 1)
        self.event("assistant.message", content="[[FINAL]]first[[END]]", message_id="a")
        self.event("session.idle")
        # Let completion queue on the Qt thread without delivering it.
        time.sleep(0.12)
        self.submit("two", "late")
        self.wait(lambda: len(self.accepted) == 2)
        self.assertEqual(self.finished, [])
        self.event("assistant.message", content="[[FINAL]]second[[END]]", message_id="b")
        self.event("session.idle")
        self.wait(lambda: self.finished == [0])
        self.assertIn("first", self.runner.last_output())
        self.assertIn("second", self.runner.last_output())
        self.assertEqual(len(self.started), 1)

    def test_old_idle_during_send_does_not_finish_acknowledged_queued_continuation(self):
        self.submit()
        self.wait(lambda: len(self.accepted) == 1)
        entered = threading.Event()
        release = asyncio.Event()

        async def queued(session):
            session.queue_items.append("continuation")
            session.event("session.idle")
            entered.set()
            await release.wait()

        self.controller.behaviors.append(queued)
        self.submit("two", "continue")
        self.wait(entered.is_set)
        self.runner._loop.call_soon_threadsafe(release.set)
        self.wait(lambda: len(self.accepted) == 2)
        self.pump(0.15)
        self.assertEqual(self.finished, [])
        session = self.controller.sessions[-1]
        self.runner._loop.call_soon_threadsafe(session.queue_items.clear)
        self.event("assistant.turn_start")
        self.event("assistant.message", content="continuation result")
        self.event("session.idle")
        self.wait(lambda: self.finished == [0])

    def test_rejected_send_surfaces_error_and_allows_retry(self):
        async def reject(session):
            raise RuntimeError("send refused")

        self.controller.behaviors.append(reject)
        self.submit()
        self.wait(lambda: self.finished == [-1])
        self.assertEqual(self.accepted, [])
        self.assertEqual(self.rejected, [("one", "send refused")])
        self.assertTrue(self.controller.clients[0].stopped)
        self.submit("retry", "try again")
        self.wait(lambda: self.accepted == ["retry"])
        self.assertEqual(len(self.controller.resumes), 1)
        self.event("session.idle")
        self.wait(lambda: self.finished == [-1, 0])

    def test_rejected_steering_does_not_cancel_original_task(self):
        self.submit()
        self.wait(lambda: len(self.accepted) == 1)

        async def reject(session):
            raise RuntimeError("invalid attachment")

        self.controller.behaviors.append(reject)
        self.submit("bad", "extra")
        self.wait(lambda: bool(self.rejected))
        self.assertTrue(self.runner.is_running())
        self.assertEqual(self.finished, [])
        self.event("assistant.message", content="original task finished")
        self.event("session.idle")
        self.wait(lambda: self.finished == [0])

    def test_stop_rejects_pending_then_finishes_cancelled_and_can_resume(self):
        self.submit()
        self.wait(lambda: len(self.accepted) == 1)
        old = self.controller.sessions[0]
        entered = threading.Event()

        async def blocked(session):
            entered.set()
            await asyncio.Event().wait()

        self.controller.behaviors.append(blocked)
        self.submit("pending", "queued")
        self.wait(entered.is_set)
        start = time.monotonic()
        self.runner.stop()
        self.assertLess(time.monotonic() - start, 0.1)
        self.assertFalse(self.runner.submissions_allowed())
        self.wait(lambda: self.finished == [-2])
        self.assertTrue(old.aborted)
        self.assertTrue(old.disconnected)
        self.assertEqual(self.rejected[0][0], "pending")
        self.submit("new", "after cancel")
        self.wait(lambda: self.accepted == ["one", "new"])
        self.assertFalse(self.controller.resumes[-1]["continue_pending_work"])
        self.assertNotIn("replayed history", self.runner.last_output())
        self.event("assistant.message", session=old, content="stale cancelled result")
        self.event("session.idle", session=old)
        self.pump()
        self.assertEqual(self.finished, [-2])
        self.assertNotIn("stale", self.runner.last_output())
        self.event("session.idle")
        self.wait(lambda: self.finished == [-2, 0])

    def test_reset_fences_already_queued_callbacks_and_new_session_uuid(self):
        self.submit()
        self.wait(lambda: len(self.accepted) == 1)
        old = self.controller.sessions[0]
        old_id = old.config["session_id"]
        self.event("assistant.message_delta", delta_content="stale delta")
        time.sleep(0.03)
        self.runner.reset_session()
        self.wait(self.runner.submissions_allowed)
        self.assertEqual(self.output, [])
        self.assertFalse(self.runner.has_session_history())
        self.submit("new", "different case")
        self.wait(lambda: self.accepted == ["one", "new"])
        self.assertNotEqual(self.controller.sessions[-1].config["session_id"], old_id)
        self.event("assistant.message", session=old, content="stale answer")
        self.event("session.idle", session=old)
        self.pump()
        self.assertEqual(self.finished, [])
        self.assertEqual(self.runner.last_output(), "")
        self.event("assistant.message", content="new answer")
        self.event("session.idle")
        self.wait(lambda: self.finished == [0])
        self.assertEqual(self.runner.last_output(), "new answer")

    def test_reset_does_not_cancel_failure_cleanup_and_leak_runtime(self):
        async def reject(session):
            raise RuntimeError("rejected")

        self.controller.behaviors.append(reject)
        self.controller.stop_gate = asyncio.Event()
        self.submit()
        self.wait(self.controller.stop_entered.is_set)
        old_client = self.controller.clients[0]
        self.runner.reset_session()
        self.pump(0.05)
        self.assertFalse(self.runner.submissions_allowed())
        self.assertFalse(old_client.stopped)
        self.runner._loop.call_soon_threadsafe(self.controller.stop_gate.set)
        self.wait(self.runner.submissions_allowed)
        self.assertTrue(old_client.stopped)
        self.assertEqual(self.finished, [])

    def test_startup_failure_and_missing_dependency_are_actionable(self):
        self.runner._new_client = lambda: (_ for _ in ()).throw(
            RuntimeError("Install requirements.txt for github-copilot-sdk; Python 3.11+ required")
        )
        self.submit()
        self.wait(lambda: self.finished == [-1])
        self.assertIn("github-copilot-sdk", self.errors[0])
        self.assertEqual(len(self.rejected), 1)

    def test_stop_during_startup_rejects_every_pending_request(self):
        self.controller.start_gate = asyncio.Event()
        self.submit()
        self.submit("two", "queued during startup")
        self.wait(lambda: bool(self.controller.clients))
        self.runner.stop()
        self.wait(lambda: self.finished == [-2])
        self.assertEqual({rid for rid, _ in self.rejected}, {"one", "two"})
        self.assertEqual(self.accepted, [])
        self.assertTrue(self.controller.clients[0].stopped)
        self.assertFalse(self.runner.has_session_history())

    def test_launcher_and_permissions_use_existing_helpers(self):
        captured = {}
        permission = object()

        class Connection:
            @staticmethod
            def for_stdio(**kwargs):
                captured["connection"] = kwargs
                return kwargs

        class Client:
            def __init__(self, **kwargs):
                captured["client"] = kwargs

        with patch("core.live_runner._load_sdk", return_value=(
            Client, Connection, SimpleNamespace(approve_all=permission),
        )), patch.dict("sys.modules", {
            "core.runtime_launcher": SimpleNamespace(
                _resolve_live_launcher=lambda: r"C:\runtime\copilot.exe",
            ),
        }), patch("core.live_runner._build_env", return_value={"TEST": "yes"}):
            client, handler = LiveCopilotRunner._new_client(self.runner)
        self.assertIsInstance(client, Client)
        self.assertIs(handler, permission)
        self.assertEqual(captured["connection"], {
            "path": r"C:\runtime\copilot.exe",
        })
        self.assertTrue(captured["client"]["use_logged_in_user"])
        self.assertEqual(captured["client"]["env"], {"TEST": "yes"})

    def test_graceful_shutdown_failure_is_reported_before_forced_cleanup(self):
        self.submit()
        self.wait(lambda: len(self.accepted) == 1)
        self.controller.stop_error = RuntimeError("runtime.shutdown: method not found")
        self.runner.stop()
        self.wait(lambda: self.finished == [-2])
        self.assertTrue(self.controller.clients[0].stopped)
        self.assertTrue(any("forcing runtime shutdown" in message for message in self.info))

    def test_models_change_only_before_idle_send(self):
        self.submit(model="a", reasoning_effort="high")
        self.wait(lambda: len(self.accepted) == 1)
        self.submit("steer", "now", model="b", reasoning_effort="low")
        self.wait(lambda: len(self.accepted) == 2)
        session = self.controller.sessions[0]
        self.assertEqual(session.switches, [("a", {"reasoning_effort": "high"})])
        self.assertTrue(self.info)
        self.event("session.idle")
        self.wait(lambda: self.finished == [0])
        self.submit("next", "later", model="b", reasoning_effort="low")
        self.wait(lambda: len(self.accepted) == 3)
        self.assertEqual(session.switches, [
            ("a", {"reasoning_effort": "high"}),
            ("b", {"reasoning_effort": "low"}),
        ])
        self.event("session.idle")
        self.wait(lambda: self.finished == [0, 0])

    def test_effort_update_without_explicit_model_uses_current_model(self):
        self.submit(reasoning_effort="high")
        self.wait(lambda: len(self.accepted) == 1)
        self.event("session.idle")
        self.wait(lambda: self.finished == [0])
        self.submit("next", "later", reasoning_effort="low")
        self.wait(lambda: len(self.accepted) == 2)
        self.assertEqual(
            self.controller.sessions[0].switches,
            [("default-model", {"reasoning_effort": "low"})],
        )
        self.event("session.idle")
        self.wait(lambda: self.finished == [0, 0])

    def test_python_310_rejection_is_actionable_and_sdk_is_lazy(self):
        with patch("core.live_runner.sys.version_info", (3, 10, 0)):
            with self.assertRaisesRegex(RuntimeError, "Python 3.11"):
                _load_sdk()

    def test_delivery_status_distinguishes_steering_and_queueing(self):
        self.submit()
        self.wait(lambda: len(self.accepted) == 1)
        self.event("user.message", delivery="steering")
        self.event("user.message", delivery="queued")
        self.wait(lambda: len(self.info) == 2)
        self.assertIn("current task", self.info[0])
        self.assertIn("later turn", self.info[1])
        self.event("session.idle")
        self.wait(lambda: self.finished == [0])

    def test_unhandled_sdk_callback_error_is_terminal_not_silent(self):
        self.submit()
        self.wait(lambda: len(self.accepted) == 1)
        self.runner._loop.call_soon_threadsafe(
            self.runner._loop.call_exception_handler,
            {"message": "callback failed", "exception": AssertionError("schema")},
        )
        self.wait(lambda: self.finished == [-1])
        self.assertIn("AssertionError", self.errors[-1])

    def test_switch_back_to_discovered_user_default(self):
        self.submit(model="explicit", reasoning_effort="high")
        self.wait(lambda: len(self.accepted) == 1)
        self.event("session.idle")
        self.wait(lambda: self.finished == [0])
        self.submit("default", "use default", model=None)
        self.wait(lambda: len(self.accepted) == 2)
        self.assertEqual(
            self.controller.sessions[0].switches[-1],
            ("default-model", {"reasoning_effort": None}),
        )
        self.event("session.idle")
        self.wait(lambda: self.finished == [0, 0])

    def test_bad_initial_model_can_retry_existing_session(self):
        async def unavailable(session, model, **options):
            raise RuntimeError("model unavailable")

        with patch.object(FakeSession, "set_model", unavailable):
            self.submit(model="unavailable")
            self.wait(lambda: self.finished == [-1])
        self.assertEqual(self.accepted, [])
        original_id = self.controller.sessions[0].config["session_id"]
        self.submit("retry", "try available model", model="available")
        self.wait(lambda: self.accepted == ["retry"])
        self.assertEqual(self.controller.resumes[0]["session_id"], original_id)
        self.event("session.idle")
        self.wait(lambda: self.finished == [-1, 0])

    def test_transport_failure_is_terminal_and_retryable(self):
        self.runner._HEALTH_INTERVAL = 0.02
        self.submit()
        self.wait(lambda: len(self.accepted) == 1)
        self.controller.ping_error = RuntimeError("pipe closed")
        self.wait(lambda: self.finished == [-1])
        self.assertIn("connection lost", self.errors[-1])
        self.controller.ping_error = None
        self.submit("retry", "again")
        self.wait(lambda: len(self.accepted) == 2)
        self.event("session.idle")
        self.wait(lambda: self.finished == [-1, 0])

    def test_shutdown_suppresses_queued_public_signals(self):
        self.submit()
        self.wait(lambda: len(self.accepted) == 1)
        self.event("assistant.message_delta", delta_content="queued")
        self.event("session.idle")
        time.sleep(0.08)
        self.runner.shutdown()
        self.pump()
        self.assertEqual(self.output, [])
        self.assertEqual(self.finished, [])
        self.assertFalse(self.runner.is_running())
        self.assertFalse(self.runner.submissions_allowed())


if __name__ == "__main__":
    unittest.main()

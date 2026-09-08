"""Persistent Copilot SDK backend; SDK imports stay optional for legacy mode."""
from __future__ import annotations

import asyncio
from concurrent.futures import TimeoutError as FutureTimeoutError
import os
import sys
import threading
import uuid

from PySide6.QtCore import QObject, Qt, Signal, Slot

from core.copilot_runner import _build_env, _log, _sanitize_prompt


def _load_sdk():
    if sys.version_info < (3, 11):
        raise RuntimeError(
            "Live input requires Python 3.11+ and the application's SDK requirements. "
            "Install requirements.txt or use legacy mode with Python 3.10."
        )
    try:
        from copilot import CopilotClient, RuntimeConnection
        from copilot.session import PermissionHandler
    except ImportError as exc:
        raise RuntimeError(
            "Live input requires Python 3.11+ and github-copilot-sdk. "
            "Install the application's requirements.txt or select legacy mode."
        ) from exc
    return CopilotClient, RuntimeConnection, PermissionHandler


class LiveCopilotRunner(QObject):
    output_received = Signal(str)
    process_started = Signal()
    process_finished = Signal(int)
    error_occurred = Signal(str)
    message_accepted = Signal(str)
    message_rejected = Signal(str, str)
    info_received = Signal(str)
    _dispatch = Signal(object)

    _CLEANUP_TIMEOUT = 4.0
    _ABORT_TIMEOUT = 10.0
    _IDLE_SETTLE = 0.05
    _HEALTH_INTERVAL = 3.0

    def __init__(self, parent=None, *, working_directory=None):
        super().__init__(parent)
        self.working_directory = str(working_directory or os.getcwd())
        self._lock = threading.RLock()
        self._generation = 0
        self._task_serial = 0
        self._revision = 0
        self._session_id = str(uuid.uuid4())
        self._created = False
        self._runtime_serial = 0
        self._history = False
        self._running = False
        self._stopping = False
        self._resetting = False
        self._closed = False
        self._cleanup_failed = False
        self._pending = {}
        self._parts = {}
        self._last_output = ""
        self._accepted_count = 0
        self._collecting = False
        self._idle = False
        self._idle_version = 0
        self._loop = None
        self._thread = None
        self._client = None
        self._session = None
        self._send_lock = None
        self._dispose_task = None
        self._send_tasks = set()
        self._idle_waiter = None
        self._health_task = None
        self._finish_task = None
        self._model = None
        self._effort = None
        self._default_model = None
        self._dispatch.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    def _notify(self, kind, *args, generation=None):
        with self._lock:
            generation = self._generation if generation is None else generation
            if self._closed or generation != self._generation:
                return
            self._dispatch.emit((generation, kind, args))

    @Slot(object)
    def _deliver(self, item):
        generation, kind, args = item
        with self._lock:
            if self._closed or generation != self._generation:
                return
            if kind == "complete":
                serial, revision, code = args
                if (serial != self._task_serial or not self._running
                        or (code == 0 and (revision != self._revision
                                          or self._pending or not self._idle
                                          or self._stopping))):
                    return
                self._running = False
                self._stopping = False
                self.process_finished.emit(code)
            else:
                getattr(self, kind).emit(*args)

    def is_running(self):
        with self._lock:
            return self._running

    def submissions_allowed(self):
        with self._lock:
            return not (
                self._closed or self._resetting or self._stopping or self._cleanup_failed
            )

    def has_session_history(self):
        with self._lock:
            return self._history

    def last_output(self):
        with self._lock:
            return self._last_output

    def _ensure_loop(self):
        if self._loop is not None:
            return
        loop = asyncio.new_event_loop()
        loop.set_exception_handler(self._on_loop_error)
        self._loop = loop

        def run():
            asyncio.set_event_loop(loop)
            try:
                loop.run_forever()
            finally:
                tasks = asyncio.all_tasks(loop)
                for task in tasks:
                    task.cancel()
                if tasks:
                    loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
                loop.close()

        self._thread = threading.Thread(
            target=run, name="copilot-live", daemon=False,
        )
        self._thread.start()

    def _on_loop_error(self, loop, context):
        error = context.get("exception")
        reason = (
            f"Copilot event processing failed: {type(error).__name__}: {error}"
            if error is not None else
            f"Copilot event processing failed: {context.get('message', 'unknown error')}"
        )
        # Do not log callback arguments: user events may contain notes or images.
        _log(reason)
        if not self._closed:
            loop.create_task(self._fatal(self._generation, reason))

    def submit(self, request_id, prompt, attachments=None, model=None,
               reasoning_effort=None):
        with self._lock:
            reason = None
            if not self.submissions_allowed():
                reason = "The live session is stopping or resetting."
            elif not prompt.strip():
                reason = "Enter a message before sending."
            elif request_id in self._pending:
                reason = "This submission is already pending."
            if reason:
                self._notify("message_rejected", request_id, reason)
                return False
            try:
                self._ensure_loop()
            except Exception as exc:
                self._notify("message_rejected", request_id, str(exc))
                self._notify("error_occurred", str(exc))
                return False
            if not self._running:
                self._task_serial += 1
                self._parts = {}
                self._last_output = ""
                self._accepted_count = 0
                self._collecting = False
                self._running = True
                self._idle = False
                self._notify("process_started")
            self._revision += 1
            generation = self._generation
            self._pending[request_id] = generation
            try:
                asyncio.run_coroutine_threadsafe(
                    self._send(generation, request_id, _sanitize_prompt(prompt),
                               list(attachments or []), model, reasoning_effort),
                    self._loop,
                )
            except Exception as exc:
                self._pending.pop(request_id, None)
                self._notify("message_rejected", request_id, str(exc))
                self._notify("error_occurred", str(exc))
                if not self._pending and not self._accepted_count:
                    self._notify("complete", self._task_serial, self._revision, -1)
                return False
            return True

    def _current(self, generation):
        with self._lock:
            return not self._closed and generation == self._generation

    def _new_client(self):
        from core.runtime_launcher import _resolve_live_launcher

        client_type, connection_type, permissions = _load_sdk()
        runtime_path = _resolve_live_launcher()
        if not runtime_path:
            raise RuntimeError(
                "A compatible Copilot runtime was not found. Update the Copilot CLI "
                "or select legacy mode."
            )
        connection = connection_type.for_stdio(path=runtime_path)
        client = client_type(
            connection=connection, working_directory=self.working_directory,
            env=_build_env(), use_logged_in_user=True,
        )
        return client, permissions.approve_all

    async def _ensure_session(self, generation, model, effort):
        if self._session is not None:
            return
        if self._client is None:
            self._client, permission_handler = self._new_client()
            self._permission_handler = permission_handler
            await self._client.start()
        if not self._current(generation) or self._stopping:
            raise asyncio.CancelledError()
        self._runtime_serial += 1
        runtime_serial = self._runtime_serial
        fresh = not self._created
        config = dict(
            working_directory=self.working_directory,
            streaming=True,
            on_event=lambda event: self._on_event(generation, event, runtime_serial),
            on_permission_request=self._permission_handler,
            enable_config_discovery=True,
            enable_skills=True,
            skip_custom_instructions=False,
            enable_on_demand_instruction_discovery=True,
        )
        if model and not fresh:
            config["model"] = model
        if effort:
            config["reasoning_effort"] = effort
        if self._created:
            session = await self._client.resume_session(
                self._session_id, continue_pending_work=False, **config,
            )
        else:
            session = await self._client.create_session(
                session_id=self._session_id, **config,
            )
        if not self._current(generation):
            await session.disconnect()
            raise asyncio.CancelledError()
        self._session = session
        self._created = True
        if fresh:
            # Remember the discovered user default before an explicit override.
            current = await session.rpc.model.get_current(timeout=10)
            self._default_model = current.model_id
            if model:
                await session.set_model(model, reasoning_effort=effort)
        elif model is None and self._default_model:
            await session.set_model(self._default_model, reasoning_effort=effort)
        self._model, self._effort = model, effort
        self._idle_waiter = asyncio.Event()
        self._health_task = asyncio.create_task(self._watch_health(generation))

    async def _send(self, generation, request_id, prompt, attachments, model, effort):
        task = asyncio.current_task()
        self._send_tasks.add(task)
        if self._send_lock is None:
            self._send_lock = asyncio.Lock()
        was_idle = False
        try:
            async with self._send_lock:
                if not self._current(generation) or self._stopping:
                    raise asyncio.CancelledError()
                await self._ensure_session(generation, model, effort)
                with self._lock:
                    was_idle = self._idle or self._accepted_count == 0
                if (model, effort) != (self._model, self._effort):
                    if was_idle:
                        activity = await self._session.rpc.metadata.activity(timeout=10)
                        queue = await self._session.rpc.queue.pending_items(timeout=10)
                        was_idle = not (
                            activity.has_active_work or queue.items or queue.steering_messages
                        )
                    if was_idle:
                        target_model = model or self._default_model
                        if not target_model:
                            current = await self._session.rpc.model.get_current(timeout=10)
                            target_model = current.model_id
                        if not target_model:
                            raise RuntimeError("Cannot determine the current Copilot model.")
                        await self._session.set_model(target_model, reasoning_effort=effort)
                        self._model, self._effort = model, effort
                    else:
                        self._notify(
                            "info_received",
                            "Model/effort changes apply to the next idle task; "
                            "this input steers the current task.",
                            generation=generation,
                        )
                with self._lock:
                    if not self._current(generation) or self._stopping:
                        raise asyncio.CancelledError()
                    self._idle = False
                    self._collecting = True
                    self._idle_waiter.clear()
                await self._session.send(
                    prompt, attachments=attachments or None, mode="immediate",
                )
                with self._lock:
                    if not self._current(generation):
                        return
                    self._history = True
                    self._accepted_count += 1
                    self._pending.pop(request_id, None)
                    self._notify("message_accepted", request_id, generation=generation)
        except asyncio.CancelledError:
            if self._current(generation):
                self._reject(request_id, "Submission cancelled.", generation)
        except Exception as exc:
            if self._current(generation):
                self._reject(request_id, str(exc), generation)
                self._notify("error_occurred", str(exc), generation=generation)
                with self._lock:
                    no_work = not self._accepted_count and not self._pending
                    if no_work:
                        self._stopping = True
                    if was_idle:
                        self._idle = True
                if no_work:
                    await self._dispose_runtime()
                    self._notify("complete", self._task_serial, self._revision, -1,
                                 generation=generation)
        finally:
            self._send_tasks.discard(task)
            if self._current(generation):
                self._maybe_finish(generation)

    def _reject(self, request_id, reason, generation):
        with self._lock:
            if self._pending.pop(request_id, None) is not None:
                self._notify("message_rejected", request_id, reason, generation=generation)

    def _on_event(self, generation, event, runtime_serial=None):
        if (not self._current(generation)
                or (runtime_serial is not None and runtime_serial != self._runtime_serial)):
            return
        kind = getattr(event.type, "value", event.type)
        data = event.data
        root = not getattr(event, "agent_id", None)
        with self._lock:
            if kind == "session.idle":
                if not root:
                    return
                self._idle = True
                self._idle_version += 1
                if self._idle_waiter:
                    self._idle_waiter.set()
                self._maybe_finish(generation)
                return
            if kind == "session.error":
                if not root:
                    return
                message = getattr(data, "message", None) or "Copilot session failed."
                if not self._collecting:
                    self._notify("info_received", message, generation=generation)
                    return
                asyncio.create_task(self._fatal(generation, message))
                return
            # Resume may replay persisted history before the first new send.
            if not self._running or self._stopping or not self._collecting:
                return
            if kind in ("assistant.turn_start", "assistant.message_delta",
                        "assistant.message", "tool.execution_start", "user.message"):
                self._idle = False
                if self._idle_waiter:
                    self._idle_waiter.clear()
            if kind == "user.message" and root:
                delivery = getattr(data, "delivery", None)
                delivery = getattr(delivery, "value", delivery)
                if delivery == "steering":
                    self._notify(
                        "info_received", "The runtime added your message to the current task.",
                        generation=generation,
                    )
                elif delivery == "queued":
                    self._notify(
                        "info_received", "Your message will continue in a later turn of the same session.",
                        generation=generation,
                    )
            if kind == "assistant.message" and root:
                content = getattr(data, "content", "") or ""
                key = (getattr(data, "message_id", None)
                       or getattr(event, "id", None) or str(uuid.uuid4()))
                self._parts[key] = content
                self._last_output = "\n\n".join(self._parts.values())
            elif kind == "assistant.message_delta" and root:
                self._notify("output_received", getattr(data, "delta_content", "") or "",
                             generation=generation)
            elif kind == "tool.execution_start":
                name = getattr(data, "tool_name", "") or "tool"
                self._notify("output_received", f"\n[Tool: {name}]\n",
                             generation=generation)

    def _maybe_finish(self, generation):
        with self._lock:
            if (not self._current(generation) or not self._running or self._stopping
                    or self._pending or not self._idle or not self._accepted_count):
                return
            if self._finish_task:
                self._finish_task.cancel()
            self._finish_task = asyncio.create_task(
                self._settle_idle(generation, self._task_serial,
                                  self._revision, self._idle_version)
            )

    async def _settle_idle(self, generation, serial, revision, idle_version):
        await asyncio.sleep(self._IDLE_SETTLE)
        session = self._session
        if session is None or not self._current(generation):
            return
        try:
            # An earlier idle can arrive while a new send RPC is awaiting its reply.
            # Query after acceptance: neither queued steering nor an active continuation
            # may be mistaken for a completed logical task.
            queue = await session.rpc.queue.pending_items(timeout=10)
            activity = await session.rpc.metadata.activity(timeout=10)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._fatal(
                generation,
                f"Cannot verify Copilot task completion; check the CLI/SDK versions: {exc}",
            )
            return
        with self._lock:
            if (self._current(generation) and session is self._session and self._idle
                    and serial == self._task_serial and revision == self._revision
                    and not self._pending and not self._stopping
                    and idle_version == self._idle_version):
                if queue.items or queue.steering_messages or activity.has_active_work:
                    self._idle = False
                    return
                self._notify("complete", serial, revision, 0, generation=generation)

    async def _watch_health(self, generation):
        try:
            while self._current(generation) and self._client is not None:
                await asyncio.sleep(self._HEALTH_INTERVAL)
                await asyncio.wait_for(self._client.ping(), timeout=10)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            await self._fatal(generation, f"Copilot connection lost: {exc}")

    async def _fatal(self, generation, reason):
        with self._lock:
            if not self._current(generation) or self._stopping:
                return
            self._stopping = True
            requests = list(self._pending)
        for request_id in requests:
            self._reject(request_id, reason, generation)
        self._notify("error_occurred", reason, generation=generation)
        await self._cancel_sends()
        await self._dispose_runtime()
        with self._lock:
            if self._current(generation):
                if self._running:
                    self._notify("complete", self._task_serial, self._revision, -1,
                                 generation=generation)
                else:
                    self._stopping = False

    async def _cancel_sends(self):
        tasks = [task for task in self._send_tasks
                 if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def stop(self):
        with self._lock:
            if not self._running or self._stopping or self._closed:
                return
            self._stopping = True
            asyncio.run_coroutine_threadsafe(self._stop(self._generation), self._loop)

    async def _stop(self, generation):
        await self._cancel_sends()
        try:
            if self._session is not None:
                # Clearing first catches an idle notification arriving before abort's reply.
                self._idle_waiter.clear()
                await asyncio.wait_for(self._session.abort(), self._ABORT_TIMEOUT)
                if not self._idle:
                    await asyncio.wait_for(self._idle_waiter.wait(), self._ABORT_TIMEOUT)
        except Exception as exc:
            self._notify("info_received",
                         f"Stopping the runtime because cancellation did not settle: {exc}",
                         generation=generation)
        # Discard any runtime-side queued input as well as the active turn.
        clean = await self._dispose_runtime()
        self._notify("complete", self._task_serial, self._revision, -2 if clean else -1,
                     generation=generation)

    async def _dispose_runtime(self):
        if self._dispose_task is None or self._dispose_task.done():
            self._dispose_task = asyncio.create_task(
                self._close_runtime(asyncio.current_task())
            )
        # Reset may cancel a send while it is handling a startup failure. Runtime
        # teardown must survive that cancellation, and reset must await the same job.
        return await asyncio.shield(self._dispose_task)

    async def _close_runtime(self, owner):
        self._runtime_serial += 1
        for task in (self._health_task, self._finish_task):
            if task is not None and task is not owner:
                task.cancel()
        self._health_task = self._finish_task = None
        client, session = self._client, self._session
        self._client = self._session = None
        if session is not None:
            try:
                await asyncio.wait_for(session.disconnect(), self._CLEANUP_TIMEOUT)
            except Exception as exc:
                warning = f"Copilot session detach failed; shutting down its runtime: {exc}"
                _log(warning)
                self._notify("info_received", warning)
        if client is not None:
            try:
                await asyncio.wait_for(client.stop(), self._CLEANUP_TIMEOUT)
            except Exception as exc:
                warning = f"Graceful Copilot shutdown failed; forcing runtime shutdown: {exc}"
                _log(warning)
                self._notify("info_received", warning)
                try:
                    await asyncio.wait_for(client.force_stop(), self._CLEANUP_TIMEOUT)
                except Exception as exc:
                    self._client = client
                    self._cleanup_failed = True
                    _log(f"Copilot runtime cleanup failed: {exc}")
                    self._notify("error_occurred", f"Copilot runtime cleanup failed: {exc}")
                    return False
        self._cleanup_failed = False
        return True

    def reset_session(self):
        with self._lock:
            if self._closed:
                return
            self._generation += 1
            self._session_id = str(uuid.uuid4())
            self._created = False
            self._default_model = None
            self._history = self._running = self._stopping = False
            self._pending.clear()
            self._parts.clear()
            self._last_output = ""
            self._idle = False
            self._collecting = False
            self._resetting = self._loop is not None
            if self._loop is not None:
                asyncio.run_coroutine_threadsafe(self._reset(self._generation), self._loop)

    async def _reset(self, generation):
        await self._cancel_sends()
        clean = await self._dispose_runtime()
        with self._lock:
            if self._current(generation):
                self._resetting = not clean
                if clean:
                    self._notify("info_received", "Live session reset.", generation=generation)

    def shutdown(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._generation += 1
            self._pending.clear()
            self._running = False
            loop = self._loop
            if loop is None:
                return
            future = asyncio.run_coroutine_threadsafe(self._close_loop(), loop)
        # Cleanup itself is asynchronous; window closure never waits for an LLM/tool.
        try:
            future.result(timeout=0.1)
        except FutureTimeoutError:
            # The non-daemon worker keeps Python alive until bounded cleanup ends.
            future.add_done_callback(self._log_shutdown_result)

    @staticmethod
    def _log_shutdown_result(future):
        error = future.exception()
        if error is not None:
            _log(f"Live runner shutdown failed: {error}")

    async def _close_loop(self):
        try:
            await self._cancel_sends()
            await self._dispose_runtime()
        finally:
            asyncio.get_running_loop().call_soon(asyncio.get_running_loop().stop)

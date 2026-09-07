"""Wrap the `copilot` CLI as a subprocess.

Single-mode design: every send spawns `copilot -p "<prompt>"`. The first
send is launched with a unique `--name <session>` so this runner gets its
own Copilot session; subsequent sends use `--resume=<session>` to resume
that exact session (NOT `--continue`, which would resume the globally
most-recent session — broken when multiple windows run concurrently).
Call `reset_session()` to start over (regenerates the session name).

Implementation note: We use Python's `subprocess.Popen` + a reader thread
rather than QProcess. QProcess on Windows has been observed to crash the
child (exit 62097 / Crashed) when prompts contain newlines or the U+FFFC
object-replacement char. Going through Popen with stdin=DEVNULL keeps
things deterministic.
"""
from __future__ import annotations

import datetime
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal


# Diagnostic log so we can trace what was actually launched.
_LOG_FILE = (
    Path(os.environ.get("USERPROFILE", os.path.expanduser("~")))
    / "OneDrive - Microsoft" / "Documents" / "VS-Code-Workspace"
    / "copilot-temp" / "sessions" / "copilot-runner.log"
)

_PROMPT_FILE_THRESHOLD = 12000
_SESSION_NOT_FOUND_RE = re.compile(
    r"No session, task, or name matched\b",
    re.IGNORECASE,
)


def _log(msg: str) -> None:
    try:
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        with _LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


# Strip ANSI/VT escape sequences and common terminal control chars.
_ANSI_RE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"   # CSI
    r"|\x1b\][^\x07]*\x07"        # OSC ... BEL
    r"|\x1b[PX^_].*?\x1b\\"       # other 7-bit sequences
    r"|\x1b[@-Z\\-_]"             # 2-byte ESC sequences
    r"|[\x00-\x08\x0b\x0c\x0e-\x1f]"  # control chars (keep \t \n \r)
)


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


# Replace characters that some shells/CLIs choke on but carry no info.
def _sanitize_prompt(text: str) -> str:
    # U+FFFC (object replacement char, Qt's marker for embedded images)
    # carries no meaning once the image is gone — drop it.
    return text.replace("\ufffc", "[图片]")


def _resolve_launcher() -> tuple[str | None, list[str]]:
    """Find the best way to launch the Copilot CLI.

    Preferred: invoke `node <npm-loader.js>` directly. Falls back to
    running the .cmd via cmd.exe /c if node + loader can't be located.
    Returns (program, prefix_args).
    """
    bootstrapper_marker = os.path.normcase(
        os.path.join("globalstorage", "github.copilot-chat", "copilotcli")
    )

    def _is_bootstrapper(p: str) -> bool:
        return bootstrapper_marker in os.path.normcase(p)

    cmd_path: str | None = None
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d:
            continue
        for name in ("copilot.cmd", "copilot.bat"):
            candidate = os.path.join(d, name)
            if os.path.isfile(candidate) and not _is_bootstrapper(candidate):
                cmd_path = candidate
                break
        if cmd_path:
            break

    if not cmd_path:
        cmd_path = shutil.which("copilot.cmd") or shutil.which("copilot.bat")

    if not cmd_path:
        return None, []

    cmd_dir = Path(cmd_path).parent
    loader = cmd_dir / "node_modules" / "@github" / "copilot" / "npm-loader.js"
    node_exe = shutil.which("node") or shutil.which("node.exe")

    if loader.is_file() and node_exe:
        _log(f"launcher: node + {loader}")
        return node_exe, [str(loader)]

    _log(f"launcher: cmd.exe /c {cmd_path} (fallback)")
    return "cmd.exe", ["/c", cmd_path]


def _build_env() -> dict:
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    env["FORCE_COLOR"] = "0"
    env["CI"] = "1"
    env["COPILOT_ALLOW_ALL"] = "1"
    return env


# Reader threads use signals to talk back to the GUI thread safely.

class _ReaderThread(QThread):
    """Reads bytes from a stream until EOF, emits decoded chunks."""

    chunk = Signal(str)
    finished_reading = Signal()

    def __init__(self, stream, parent=None):
        super().__init__(parent)
        self._stream = stream
        self._chunks: list[str] = []

    def run(self):
        try:
            while True:
                data = self._stream.read(4096)
                if not data:
                    break
                if isinstance(data, bytes):
                    text = data.decode("utf-8", errors="replace")
                else:
                    text = data
                text = _strip_ansi(text)
                self._chunks.append(text)
                self.chunk.emit(text)
        except Exception as e:
            _log(f"reader exception: {e}")
        finally:
            self.finished_reading.emit()

    def text(self) -> str:
        """Return every chunk read from the child process."""
        return "".join(self._chunks)


class _WaiterThread(QThread):
    """Waits for the process to exit, emits the exit code."""

    done = Signal(int)

    def __init__(self, popen, parent=None):
        super().__init__(parent)
        self._popen = popen

    def run(self):
        try:
            code = self._popen.wait()
        except Exception as e:
            _log(f"waiter exception: {e}")
            code = -1
        self.done.emit(int(code))


class CopilotRunner(QObject):
    """Run the Copilot CLI and stream output via signals."""

    output_received = Signal(str)
    process_started = Signal()
    process_finished = Signal(int)
    error_occurred = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._popen: subprocess.Popen | None = None
        self._reader: _ReaderThread | None = None
        self._waiter: _WaiterThread | None = None
        self._prompt_file: Path | None = None
        self._last_output: str = ""
        self._stdin_lock = threading.Lock()
        # Only completed runs count. Popen succeeding does not mean the CLI
        # actually created the named session.
        self._sent_in_session: int = 0
        self._active_resume: bool = False
        # Unique per-runner session name so multiple windows / runners don't
        # collide via Copilot CLI's global "most-recent session" pointer.
        self._session_name: str = self._new_session_name()
        self._model: str | None = None
        self._reasoning_effort: str | None = None
        self._program, self._prefix_args = _resolve_launcher()
        _log(
            f"=== CopilotRunner init === program={self._program!r} "
            f"prefix_args={self._prefix_args!r} session={self._session_name!r}"
        )

    @staticmethod
    def _new_session_name() -> str:
        return f"notepad-{uuid.uuid4().hex[:12]}"

    # --- lifecycle -----------------------------------------------------

    def is_running(self) -> bool:
        # Include the interval between process exit and queued Qt completion.
        return self._popen is not None

    def stop(self):
        if self._popen is None:
            self._cleanup_prompt_file()
            return
        try:
            if self._popen.poll() is None:
                self._popen.kill()
                try:
                    self._popen.wait(timeout=2)
                except Exception:
                    pass
        finally:
            for t in (self._reader, self._waiter):
                if t is not None:
                    t.wait(2000)
            self._popen = None
            self._reader = None
            self._waiter = None
            self._cleanup_prompt_file()

    # --- send ----------------------------------------------------------

    def send(self, prompt: str, attachments: list | None = None) -> bool:
        if not prompt.strip():
            self.error_occurred.emit("请输入消息后再发送。")
            return False
        if self._program is None:
            self.error_occurred.emit(
                "Copilot CLI 未找到。请确保 `copilot` 在 PATH 中。"
            )
            return False
        if self.is_running():
            self.error_occurred.emit(
                "上一条请求仍在运行中，请等待完成或点击 [停止] 后再发送。"
            )
            return False

        prompt = _sanitize_prompt(prompt)
        attachments = [str(p) for p in (attachments or [])]

        resume = self._sent_in_session > 0
        return self._spawn(prompt, resume=resume, attachments=attachments)

    def reset_session(self):
        """Drop session continuity so the next send starts fresh."""
        self.stop()
        self._sent_in_session = 0
        self._active_resume = False
        # New session name so we don't accidentally re-resume the old one.
        self._session_name = self._new_session_name()
        _log(f"reset_session: new name={self._session_name!r}")

    def has_session_history(self) -> bool:
        """True if at least one prompt has been sent since last reset."""
        return self._sent_in_session > 0

    def set_model(self, model: str | None,
                  reasoning_effort: str | None = None) -> None:
        """Set explicit model options for subsequent CLI invocations."""
        self._model = model or None
        self._reasoning_effort = reasoning_effort or None
        _log(
            f"model selection: model={self._model!r} "
            f"effort={self._reasoning_effort!r}"
        )

    def last_output(self) -> str:
        """Return the complete stdout collected for the most recent run."""
        return self._last_output

    # --- spawn ---------------------------------------------------------

    def _spawn(self, prompt: str, resume: bool,
               attachments: list[str] | None = None) -> bool:
        self.stop()
        self._active_resume = resume
        self._last_output = ""
        # --allow-all = --allow-all-tools + --allow-all-paths + --allow-all-urls
        # Path permission matters when the model needs to read files
        # outside the current working directory (e.g. skill folders in
        # OneDrive). Without --allow-all-paths, copilot would prompt
        # for confirmation, which fails in our non-TTY subprocess.
        extra: list[str] = ["--allow-all"]
        if self._model:
            extra.extend(["--model", self._model])
        if self._reasoning_effort:
            extra.extend(["--reasoning-effort", self._reasoning_effort])
        if resume:
            extra.append(f"--resume={self._session_name}")
        else:
            extra.extend(["--name", self._session_name])
        for path in (attachments or []):
            extra.extend(["--attachment", path])
        prompt_arg = self._prompt_arg(prompt)
        argv = [self._program, *self._prefix_args, "-p", prompt_arg, *extra]
        _log(
            f"spawn: resume={resume} session={self._session_name!r} "
            f"argc={len(argv)} prompt_len={len(prompt)} "
            f"prompt_arg_len={len(prompt_arg)} "
            f"attachments={attachments}"
        )
        try:
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NO_WINDOW
            popen = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=_build_env(),
                creationflags=creationflags,
                bufsize=0,
            )
        except Exception as e:
            _log(f"Popen failed: {e!r}")
            self._cleanup_prompt_file()
            self.error_occurred.emit(f"启动失败: {e}")
            return False
        self._popen = popen
        self._start_threads(popen)
        self.process_started.emit()
        return True

    # --- helpers -------------------------------------------------------

    def _start_threads(self, popen: subprocess.Popen):
        reader = _ReaderThread(popen.stdout, self)
        reader.chunk.connect(
            lambda text: self.output_received.emit(text)
            if self._popen is popen else None
        )
        self._reader = reader
        reader.start()

        waiter = _WaiterThread(popen, self)
        waiter.done.connect(
            lambda code: self._on_finished(code)
            if self._popen is popen else None
        )
        self._waiter = waiter
        waiter.start()

    def _prompt_arg(self, prompt: str) -> str:
        """Return a short CLI prompt, spilling very large prompts to a file."""
        if len(prompt) <= _PROMPT_FILE_THRESHOLD:
            return prompt
        try:
            _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            prompt_file = (
                _LOG_FILE.parent
                / f"notepad-prompt-{uuid.uuid4().hex[:12]}.md"
            )
            prompt_file.write_text(prompt, encoding="utf-8")
            self._prompt_file = prompt_file
            _log(f"prompt spilled to file: {prompt_file}")
            return (
                "我的完整问题和上下文太长，已保存到本地 UTF-8 文件：\n"
                f"{prompt_file}\n\n"
                "请先读取该文件的全部内容，再根据其中的笔记、问题和上下文回答。"
            )
        except Exception as e:
            _log(f"prompt spill failed: {e!r}; using inline prompt")
            return prompt

    def _cleanup_prompt_file(self) -> None:
        p = self._prompt_file
        self._prompt_file = None
        if p is None:
            return
        try:
            p.unlink(missing_ok=True)
        except Exception as e:
            _log(f"prompt cleanup failed: {e!r}")

    def _on_finished(self, code: int):
        _log(f"process finished: code={code}")
        # Make sure reader drains before we report finish.
        if self._reader is not None:
            self._reader.wait(2000)
            self._last_output = self._reader.text()
        if code == 0:
            self._sent_in_session += 1
        elif not self._active_resume or _SESSION_NOT_FOUND_RE.search(
            self._last_output
        ):
            # A failed fresh run may never have created its named session.
            # A missing resumed session may have been removed externally.
            # In both cases the next send must start a genuinely new session.
            self._sent_in_session = 0
            self._session_name = self._new_session_name()
            _log(
                "session unavailable after failed run; "
                f"new name={self._session_name!r}"
            )
        self._active_resume = False
        self._popen = None
        self._reader = None
        self._waiter = None
        self._cleanup_prompt_file()
        self.process_finished.emit(int(code))

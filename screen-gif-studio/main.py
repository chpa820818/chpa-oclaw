from __future__ import annotations

import ctypes
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from dataclasses import dataclass
from ctypes import wintypes
from pathlib import Path
from typing import Callable
from tkinter import (
    BOTH,
    END,
    LEFT,
    RIGHT,
    X,
    Y,
    BooleanVar,
    DoubleVar,
    Button,
    Entry,
    Frame,
    Label,
    Listbox,
    PhotoImage,
    StringVar,
    Text,
    Tk,
    Toplevel,
    filedialog,
    messagebox,
    ttk,
)


APP_VERSION = "1.0.0"
APP_TITLE = f"录屏 GIF 工具 v{APP_VERSION}"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
COLOR_BG = "#f5f7fb"
COLOR_CARD = "#ffffff"
COLOR_TEXT = "#1f2937"
COLOR_MUTED = "#64748b"
COLOR_ACCENT = "#2563eb"
COLOR_ACCENT_DARK = "#1d4ed8"
COLOR_DANGER = "#dc2626"
COLOR_LOG_BG = "#0f172a"
COLOR_LOG_TEXT = "#dbeafe"


def workspace_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_output_dir() -> Path:
    out = workspace_root() / "reports" / "screen-gif-studio"
    out.mkdir(parents=True, exist_ok=True)
    return out


def temp_dir() -> Path:
    out = workspace_root().parent / "copilot-temp" / "screen-gif-studio"
    out.mkdir(parents=True, exist_ok=True)
    return out


def ffmpeg_path() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe",
        Path(os.environ.get("ProgramFiles", "")) / "ffmpeg" / "bin" / "ffmpeg.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Gyan.FFmpeg" / "bin" / "ffmpeg.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def ffprobe_path() -> str | None:
    found = shutil.which("ffprobe")
    if found:
        return found
    ffmpeg = ffmpeg_path()
    if ffmpeg:
        candidate = Path(ffmpeg).with_name("ffprobe.exe")
        if candidate.exists():
            return str(candidate)
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "ffprobe.exe",
        Path(os.environ.get("ProgramFiles", "")) / "ffmpeg" / "bin" / "ffprobe.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Gyan.FFmpeg" / "bin" / "ffprobe.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def winget_path() -> str | None:
    return shutil.which("winget")


def parse_positive_int(value: str, name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是数字。") from exc
    if parsed <= 0:
        raise ValueError(f"{name} 必须大于 0。")
    return parsed


def format_timestamp(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    ms = total_ms % 1000
    total_seconds = total_ms // 1000
    sec = total_seconds % 60
    minutes = (total_seconds // 60) % 60
    hours = total_seconds // 3600
    return f"{hours:02d}:{minutes:02d}:{sec:02d}.{ms:03d}"


def parse_speed(value: str) -> float:
    try:
        return max(0.1, float(value.rstrip("x")))
    except ValueError:
        return 1.0


HOTKEY_CHOICES = [chr(code) for code in range(ord("A"), ord("Z") + 1)] + [f"F{index}" for index in range(1, 13)]


def hotkey_key_code(key: str) -> int | None:
    normalized = key.strip().upper()
    if len(normalized) == 1 and "A" <= normalized <= "Z":
        return ord(normalized)
    if normalized.startswith("F"):
        try:
            number = int(normalized[1:])
        except ValueError:
            return None
        if 1 <= number <= 12:
            return 0x70 + number - 1
    return None


def safe_preview_second(position: float, duration: float) -> float:
    if duration <= 0:
        return 0.0
    return max(0.0, min(position, max(0.0, duration - 0.05)))


@dataclass
class CommandResult:
    returncode: int
    command: list[str]


class CommandRunner:
    def __init__(
        self,
        log_queue: queue.Queue[str],
        done_queue: queue.Queue[tuple[Callable[[CommandResult], None], CommandResult]],
    ) -> None:
        self.log_queue = log_queue
        self.done_queue = done_queue
        self.busy = False

    def run(
        self,
        command: list[str],
        on_done: Callable[[CommandResult], None] | None = None,
        label: str = "Running command",
    ) -> None:
        if self.busy:
            messagebox.showwarning(APP_TITLE, "已有任务正在运行。")
            return
        self.busy = True

        def worker() -> None:
            self.log_queue.put(f"\n== {label} ==\n")
            self.log_queue.put(" ".join(command) + "\n")
            returncode = 1
            try:
                proc = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=CREATE_NO_WINDOW,
                )
                assert proc.stdout is not None
                for line in proc.stdout:
                    self.log_queue.put(line)
                returncode = proc.wait()
            except Exception as exc:  # Surface unexpected command-launch failures in the UI.
                self.log_queue.put(f"错误：{exc}\n")
            finally:
                self.busy = False
                result = CommandResult(returncode, command)
                self.log_queue.put(f"== 已完成，退出码 {returncode} ==\n")
                if on_done:
                    self.done_queue.put((on_done, result))

        threading.Thread(target=worker, daemon=True).start()


class RecordingHotkeys:
    MOD_ALT = 0x0001
    MOD_CONTROL = 0x0002
    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012
    HOTKEY_PAUSE = 1
    HOTKEY_STOP = 2

    def __init__(self, app: "ScreenGifStudio") -> None:
        self.app = app
        self.thread: threading.Thread | None = None
        self.thread_id = 0
        self.active = False

    def start(self) -> None:
        if self.active or os.name != "nt":
            return
        self.active = True
        self.thread = threading.Thread(target=self._message_loop, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if not self.active:
            return
        self.active = False
        if self.thread_id:
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, self.WM_QUIT, 0, 0)

    def _message_loop(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self.thread_id = kernel32.GetCurrentThreadId()
        modifiers = self.MOD_CONTROL | self.MOD_ALT
        pause_key = self.app.record_pause_hotkey.get()
        stop_key = self.app.record_stop_hotkey.get()
        pause_code = hotkey_key_code(pause_key)
        stop_code = hotkey_key_code(stop_key)
        pause_ok = bool(pause_code and user32.RegisterHotKey(None, self.HOTKEY_PAUSE, modifiers, pause_code))
        stop_ok = bool(stop_code and user32.RegisterHotKey(None, self.HOTKEY_STOP, modifiers, stop_code))
        if not pause_ok:
            self.app.after(0, lambda: self.app.log_queue.put(f"快捷键 Ctrl+Alt+{pause_key} 注册失败，可能已被占用。\n"))
        if not stop_ok:
            self.app.after(0, lambda: self.app.log_queue.put(f"快捷键 Ctrl+Alt+{stop_key} 注册失败，可能已被占用。\n"))

        msg = wintypes.MSG()
        try:
            while self.active and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                if msg.message == self.WM_HOTKEY:
                    hotkey_id = int(msg.wParam)
                    if hotkey_id == self.HOTKEY_PAUSE:
                        self.app.after(0, self.app._toggle_record_pause)
                    elif hotkey_id == self.HOTKEY_STOP:
                        self.app.after(0, self.app._stop_recording)
        finally:
            if pause_ok:
                user32.UnregisterHotKey(None, self.HOTKEY_PAUSE)
            if stop_ok:
                user32.UnregisterHotKey(None, self.HOTKEY_STOP)
            self.thread_id = 0


class ScreenGifStudio(Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1280x900")
        self.minsize(1100, 780)

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.done_queue: queue.Queue[tuple[Callable[[CommandResult], None], CommandResult]] = queue.Queue()
        self.runner = CommandRunner(self.log_queue, self.done_queue)
        self.record_proc: subprocess.Popen[str] | None = None
        self.record_started_at: float | None = None
        self.record_output: Path | None = None
        self.record_paused = False
        self.record_hotkeys = RecordingHotkeys(self)
        self.record_preview_status = StringVar(value="录制或转换完成后会在这里自动预览播放。")
        self.record_preview_image: PhotoImage | None = None
        self.record_preview_job: str | None = None
        self.record_preview_token = 0
        self.record_preview_path: Path | None = None
        self.record_preview_duration = 0.0
        self.record_preview_position = 0.0
        self.record_preview_position_var = DoubleVar(value=0.0)
        self.record_preview_position_label = StringVar(value="00:00:00.000 / 未加载")
        self.record_preview_quality = StringVar(value="高清")
        self.record_playback_speed = StringVar(value="1.0x")
        self.record_playing = False
        self.record_play_job: str | None = None
        self.record_pause_hotkey = StringVar(value="P")
        self.record_stop_hotkey = StringVar(value="S")

        self.output_dir = StringVar(value=str(default_output_dir()))
        self.record_name = StringVar(value="recording")
        self.record_fps = StringVar(value="15")
        self.gif_after_record = BooleanVar(value=True)
        self.gif_width = StringVar(value="960")
        self.merge_width = StringVar(value="960")
        self.merge_height = StringVar(value="540")
        self.merge_fps = StringVar(value="15")
        self.merge_progress = DoubleVar(value=0.0)
        self.merge_status = StringVar(value="尚未开始拼接。")
        self.merge_preview_status = StringVar(value="拼接完成后会在这里自动预览播放。")
        self.merge_preview_image: PhotoImage | None = None
        self.merge_preview_job: str | None = None
        self.merge_preview_token = 0
        self.merge_preview_path: Path | None = None
        self.merge_preview_duration = 0.0
        self.merge_preview_position = 0.0
        self.merge_preview_position_var = DoubleVar(value=0.0)
        self.merge_preview_position_label = StringVar(value="00:00:00.000 / 未加载")
        self.merge_preview_quality = StringVar(value="高清")
        self.merge_playback_speed = StringVar(value="1.0x")
        self.merge_playing = False
        self.merge_play_job: str | None = None
        self.split_duration_seconds = 0.0
        self.split_cut_points: list[float] = []
        self.split_position = DoubleVar(value=0.0)
        self.split_position_label = StringVar(value="00:00:00.000 / 未加载")
        self.split_output_format = StringVar(value=".mp4")
        self.split_preview_quality = StringVar(value="高清")
        self.split_playback_speed = StringVar(value="1.0x")
        self.split_preview_status = StringVar(value="选择文件后会在这里显示当前时间点的画面预览。")
        self.split_preview_image: PhotoImage | None = None
        self.split_preview_job: str | None = None
        self.split_preview_token = 0
        self.split_playing = False
        self.split_play_job: str | None = None
        self.split_export_progress = DoubleVar(value=0.0)
        self.split_export_status = StringVar(value="尚未开始导出。")
        self.split_export_outputs: list[Path] = []

        self._apply_style()
        self._build_ui()
        self._refresh_ffmpeg_status()
        self.after(100, self._drain_logs)
        self.after(500, self._update_record_timer)

    def _apply_style(self) -> None:
        self.configure(bg=COLOR_BG)
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        default_font = ("Microsoft YaHei UI", 9)
        title_font = ("Microsoft YaHei UI", 14, "bold")
        subtitle_font = ("Microsoft YaHei UI", 8)
        section_font = ("Microsoft YaHei UI", 10, "bold")

        style.configure(".", font=default_font)
        style.configure("App.TFrame", background=COLOR_BG)
        style.configure("Card.TFrame", background=COLOR_CARD)
        style.configure("Header.TFrame", background=COLOR_ACCENT)
        style.configure("Title.TLabel", background=COLOR_ACCENT, foreground="white", font=title_font)
        style.configure("Subtitle.TLabel", background=COLOR_ACCENT, foreground="#dbeafe", font=subtitle_font)
        style.configure("Status.TLabel", background=COLOR_CARD, foreground=COLOR_MUTED, padding=(8, 4))
        style.configure(
            "Preview.TLabel",
            background="#111827",
            foreground="#dbeafe",
            anchor="center",
            padding=12,
            font=("Microsoft YaHei UI", 10),
        )
        style.configure("TLabel", background=COLOR_BG, foreground=COLOR_TEXT)
        style.configure("Card.TLabel", background=COLOR_CARD, foreground=COLOR_TEXT)
        style.configure("Muted.TLabel", background=COLOR_CARD, foreground=COLOR_MUTED)
        style.configure("TLabelframe", background=COLOR_CARD, bordercolor="#dbe3ef", relief="solid")
        style.configure("TLabelframe.Label", background=COLOR_CARD, foreground=COLOR_TEXT, font=section_font)
        style.configure("TEntry", fieldbackground="white", bordercolor="#cbd5e1", padding=4)
        style.configure("TButton", padding=(12, 7), background="#e2e8f0", foreground=COLOR_TEXT, borderwidth=0)
        style.map("TButton", background=[("active", "#cbd5e1"), ("disabled", "#e5e7eb")])
        style.configure("Accent.TButton", background=COLOR_ACCENT, foreground="white")
        style.map("Accent.TButton", background=[("active", COLOR_ACCENT_DARK), ("disabled", "#93c5fd")])
        style.configure("Danger.TButton", background=COLOR_DANGER, foreground="white")
        style.map("Danger.TButton", background=[("active", "#b91c1c"), ("disabled", "#fecaca")])
        style.configure("TNotebook", background=COLOR_BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(16, 9), background="#e2e8f0", foreground=COLOR_TEXT)
        style.map("TNotebook.Tab", background=[("selected", COLOR_CARD), ("active", "#dbeafe")])
        style.configure("TCheckbutton", background=COLOR_CARD, foreground=COLOR_TEXT)

    def _build_ui(self) -> None:
        header = ttk.Frame(self, style="Header.TFrame")
        header.pack(fill=X)
        title_block = ttk.Frame(header, style="Header.TFrame")
        title_block.pack(side=LEFT, fill=X, expand=True, padx=14, pady=6)
        ttk.Label(title_block, text=APP_TITLE, style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            title_block,
            text="录屏、转 GIF、拼接与拆分，全部在一个简洁工具里完成",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        header_settings = ttk.Frame(header, style="Header.TFrame")
        header_settings.pack(side=RIGHT, fill=Y, padx=10, pady=5)
        self.ffmpeg_status = StringVar()
        ffmpeg_row = ttk.Frame(header_settings, style="Header.TFrame")
        ffmpeg_row.pack(fill=X)
        ttk.Label(ffmpeg_row, textvariable=self.ffmpeg_status, style="Subtitle.TLabel").pack(side=LEFT, padx=(0, 8))
        self.install_ffmpeg_button = ttk.Button(
            ffmpeg_row, text="安装 FFmpeg", command=self._install_ffmpeg, style="Accent.TButton"
        )
        self.install_ffmpeg_button.pack(side=RIGHT, padx=(6, 0))
        ttk.Button(ffmpeg_row, text="刷新 FFmpeg", command=self._refresh_ffmpeg_status).pack(side=RIGHT, padx=(6, 0))
        ttk.Button(ffmpeg_row, text="打开 FFmpeg 官网", command=self._open_ffmpeg_website).pack(side=RIGHT)

        output_row = ttk.Frame(header_settings, style="Header.TFrame")
        output_row.pack(fill=X, pady=(4, 0))
        ttk.Label(output_row, text="输出目录", style="Subtitle.TLabel").pack(side=LEFT, padx=(0, 8))
        ttk.Entry(output_row, textvariable=self.output_dir, width=72).pack(side=LEFT, fill=X, expand=True)
        ttk.Button(output_row, text="浏览", command=self._choose_output_dir).pack(side=RIGHT, padx=(6, 0))

        tabs = ttk.Notebook(self)
        tabs.pack(fill=BOTH, expand=True, padx=10, pady=4)

        self._build_record_tab(tabs)
        self._build_merge_tab(tabs)
        self._build_split_tab(tabs)

        log_frame = ttk.LabelFrame(self, text="运行日志", padding=10)
        log_frame.pack(fill=X, padx=10, pady=(2, 8))
        self.log = Text(
            log_frame,
            height=3,
            wrap="word",
            bg=COLOR_LOG_BG,
            fg=COLOR_LOG_TEXT,
            insertbackground=COLOR_LOG_TEXT,
            relief="flat",
            padx=10,
            pady=8,
            font=("Consolas", 9),
        )
        self.log.pack(fill=BOTH, expand=True)

    def _build_record_tab(self, tabs: ttk.Notebook) -> None:
        tab = ttk.Frame(tabs, style="App.TFrame")
        tabs.add(tab, text="录屏 / 转 GIF")

        work_area = ttk.Frame(tab, style="App.TFrame")
        work_area.pack(fill=BOTH, expand=True, padx=10, pady=(8, 4))

        controls = ttk.LabelFrame(work_area, text="录制操作面板", padding=8)
        controls.pack(side=LEFT, fill=Y, padx=(0, 8))
        controls.configure(width=390)
        controls.pack_propagate(False)

        settings = ttk.LabelFrame(controls, text="录制参数", padding=8)
        settings.pack(fill=X, pady=(0, 8))
        ttk.Label(settings, text="文件名前缀", style="Card.TLabel").pack(side=LEFT)
        ttk.Entry(settings, textvariable=self.record_name, width=24).pack(side=LEFT, padx=8)
        ttk.Label(settings, text="FPS", style="Card.TLabel").pack(side=LEFT)
        ttk.Entry(settings, textvariable=self.record_fps, width=8).pack(side=LEFT, padx=8)
        ttk.Checkbutton(settings, text="停止后自动转 GIF", variable=self.gif_after_record).pack(
            side=LEFT, padx=8
        )
        ttk.Label(settings, text="GIF 宽度", style="Card.TLabel").pack(side=LEFT)
        ttk.Entry(settings, textvariable=self.gif_width, width=8).pack(side=LEFT, padx=8)

        actions = ttk.LabelFrame(controls, text="录屏操作", padding=8)
        actions.pack(fill=X, pady=(0, 8))
        self.record_button = ttk.Button(actions, text="开始录制桌面", command=self._start_recording, style="Accent.TButton")
        self.record_button.pack(fill=X, pady=(0, 6))
        self.stop_button = ttk.Button(actions, text="停止录制", command=self._stop_recording, state="disabled", style="Danger.TButton")
        self.stop_button.pack(fill=X, pady=(0, 6))
        self.record_timer = StringVar(value="未录制")
        ttk.Label(actions, textvariable=self.record_timer, style="Muted.TLabel", wraplength=350).pack(fill=X, pady=(0, 6))
        ttk.Button(actions, text="选择已有视频转 GIF", command=self._convert_existing_to_gif).pack(fill=X)

        help_text = (
            "使用 FFmpeg gdigrab 录制整个桌面并保存为 MP4。"
            "停止后可自动生成 GIF 副本。"
        )
        ttk.Label(controls, text=help_text, style="Muted.TLabel", anchor="w", justify=LEFT, wraplength=350).pack(
            fill=X, pady=(0, 8)
        )

        hotkeys = ttk.LabelFrame(controls, text="录制快捷键", padding=8)
        hotkeys.pack(fill=X, pady=(0, 8))
        ttk.Label(
            hotkeys,
            text="开始录制后窗口会隐藏，可用以下全局快捷键控制录制。",
            style="Muted.TLabel",
            wraplength=350,
        ).pack(fill=X, pady=(0, 6))
        hotkey_row = ttk.Frame(hotkeys, style="Card.TFrame")
        hotkey_row.pack(fill=X)
        ttk.Label(hotkey_row, text="暂停/继续 Ctrl+Alt+", style="Card.TLabel").pack(side=LEFT)
        ttk.Combobox(
            hotkey_row,
            textvariable=self.record_pause_hotkey,
            values=HOTKEY_CHOICES,
            width=5,
            state="readonly",
        ).pack(side=LEFT, padx=(4, 10))
        ttk.Label(hotkey_row, text="停止 Ctrl+Alt+", style="Card.TLabel").pack(side=LEFT)
        ttk.Combobox(
            hotkey_row,
            textvariable=self.record_stop_hotkey,
            values=HOTKEY_CHOICES,
            width=5,
            state="readonly",
        ).pack(side=LEFT, padx=(4, 0))

        preview = ttk.LabelFrame(work_area, text="录制结果视频预览（右侧主区域）", padding=8)
        preview.pack(side=RIGHT, fill=BOTH, expand=True)
        self.record_preview_label = ttk.Label(
            preview,
            textvariable=self.record_preview_status,
            style="Preview.TLabel",
            width=120,
        )
        self.record_preview_label.pack(side="top", fill=BOTH, expand=True)
        player_controls = ttk.Frame(preview, style="Card.TFrame")
        player_controls.pack(side="bottom", fill=X, pady=(8, 0))
        ttk.Label(player_controls, textvariable=self.record_preview_position_label, style="Card.TLabel").pack(
            fill=X, pady=(0, 4)
        )
        self.record_preview_scale = ttk.Scale(
            player_controls,
            from_=0,
            to=1,
            orient="horizontal",
            variable=self.record_preview_position_var,
            command=self._record_timeline_changed,
        )
        self.record_preview_scale.pack(fill=X, pady=(0, 6))
        player_options = ttk.Frame(player_controls, style="Card.TFrame")
        player_options.pack(fill=X)
        self.record_play_button = ttk.Button(player_options, text="播放预览", command=self._toggle_record_playback)
        self.record_play_button.pack(side=LEFT, padx=(0, 8))
        ttk.Button(player_options, text="刷新帧", command=self._update_record_preview).pack(side=LEFT, padx=(0, 8))
        ttk.Button(player_options, text="放大预览", command=self._open_large_record_preview).pack(side=LEFT, padx=(0, 12))
        ttk.Label(player_options, text="倍速", style="Card.TLabel").pack(side=LEFT)
        ttk.Combobox(
            player_options,
            textvariable=self.record_playback_speed,
            values=("0.5x", "1.0x", "1.5x", "2.0x", "3.0x"),
            width=7,
            state="readonly",
        ).pack(side=LEFT, padx=(6, 12))
        ttk.Label(player_options, text="清晰度", style="Card.TLabel").pack(side=LEFT)
        self.record_preview_quality_combo = ttk.Combobox(
            player_options,
            textvariable=self.record_preview_quality,
            values=("普通", "高清", "超清"),
            width=8,
            state="readonly",
        )
        self.record_preview_quality_combo.pack(side=LEFT, padx=(6, 0))
        self.record_preview_quality_combo.bind("<<ComboboxSelected>>", lambda _event: self._update_record_preview())

    def _build_merge_tab(self, tabs: ttk.Notebook) -> None:
        tab = ttk.Frame(tabs, style="App.TFrame")
        tabs.add(tab, text="拼接")

        work_area = ttk.Frame(tab, style="App.TFrame")
        work_area.pack(fill=BOTH, expand=True, padx=10, pady=(8, 4))

        controls = ttk.LabelFrame(work_area, text="拼接操作面板", padding=8)
        controls.pack(side=LEFT, fill=Y, padx=(0, 8))
        controls.configure(width=390)
        controls.pack_propagate(False)

        file_panel = ttk.LabelFrame(controls, text="待拼接文件（按从上到下顺序合并）", padding=8)
        file_panel.pack(fill=BOTH, expand=True, pady=(0, 8))
        self.merge_list = Listbox(
            file_panel,
            height=10,
            bg="white",
            fg=COLOR_TEXT,
            selectbackground=COLOR_ACCENT,
            selectforeground="white",
            relief="flat",
            highlightthickness=1,
            highlightbackground="#cbd5e1",
        )
        self.merge_list.pack(fill=BOTH, expand=True, pady=(0, 8))
        buttons = ttk.Frame(file_panel, style="Card.TFrame")
        buttons.pack(fill=X)
        ttk.Button(buttons, text="添加", command=self._merge_add, style="Accent.TButton").pack(side=LEFT, fill=X, expand=True, padx=(0, 6))
        ttk.Button(buttons, text="移除", command=self._merge_remove).pack(side=LEFT, fill=X, expand=True, padx=(0, 6))
        ttk.Button(buttons, text="上移", command=lambda: self._merge_move(-1)).pack(side=LEFT, fill=X, expand=True, padx=(0, 6))
        ttk.Button(buttons, text="下移", command=lambda: self._merge_move(1)).pack(side=LEFT, fill=X, expand=True)

        output_panel = ttk.LabelFrame(controls, text="输出参数", padding=8)
        output_panel.pack(fill=X, pady=(0, 8))
        size_row = ttk.Frame(output_panel, style="Card.TFrame")
        size_row.pack(fill=X, pady=(0, 8))
        ttk.Label(size_row, text="宽度", style="Card.TLabel").pack(side=LEFT)
        ttk.Entry(size_row, textvariable=self.merge_width, width=7).pack(side=LEFT, padx=(6, 8))
        ttk.Label(size_row, text="高度", style="Card.TLabel").pack(side=LEFT)
        ttk.Entry(size_row, textvariable=self.merge_height, width=7).pack(side=LEFT, padx=(6, 8))
        ttk.Label(size_row, text="FPS", style="Card.TLabel").pack(side=LEFT)
        ttk.Entry(size_row, textvariable=self.merge_fps, width=7).pack(side=LEFT, padx=(6, 0))
        ttk.Button(output_panel, text="拼接为 MP4 或 GIF", command=self._merge_files, style="Accent.TButton").pack(fill=X)

        merge_progress = ttk.LabelFrame(controls, text="拼接进度", padding=8)
        merge_progress.pack(fill=X, pady=(0, 8))
        ttk.Label(merge_progress, textvariable=self.merge_status, style="Card.TLabel").pack(anchor="w")
        self.merge_progress_bar = ttk.Progressbar(
            merge_progress,
            variable=self.merge_progress,
            maximum=100,
            mode="determinate",
        )
        self.merge_progress_bar.pack(fill=X, pady=(8, 0))
        ttk.Label(
            controls,
            text="请选择以 .mp4 或 .gif 结尾的输出文件。GIF 会先生成临时 MP4，再转换为 GIF。",
            style="Muted.TLabel",
            wraplength=350,
            justify=LEFT,
        ).pack(anchor="w", pady=(0, 8))

        preview = ttk.LabelFrame(work_area, text="拼接结果视频预览（右侧主区域）", padding=8)
        preview.pack(side=RIGHT, fill=BOTH, expand=True)
        self.merge_preview_label = ttk.Label(
            preview,
            textvariable=self.merge_preview_status,
            style="Preview.TLabel",
            width=120,
        )
        self.merge_preview_label.pack(side="top", fill=BOTH, expand=True)
        preview_controls = ttk.Frame(preview, style="Card.TFrame")
        preview_controls.pack(side="bottom", fill=X, pady=(8, 0))
        ttk.Label(preview_controls, textvariable=self.merge_preview_position_label, style="Card.TLabel").pack(
            fill=X, pady=(0, 4)
        )
        self.merge_preview_scale = ttk.Scale(
            preview_controls,
            from_=0,
            to=1,
            orient="horizontal",
            variable=self.merge_preview_position_var,
            command=self._merge_timeline_changed,
        )
        self.merge_preview_scale.pack(fill=X, pady=(0, 6))
        merge_preview_options = ttk.Frame(preview_controls, style="Card.TFrame")
        merge_preview_options.pack(fill=X)
        self.merge_play_button = ttk.Button(merge_preview_options, text="播放预览", command=self._toggle_merge_playback)
        self.merge_play_button.pack(side=LEFT, padx=(0, 8))
        ttk.Button(merge_preview_options, text="刷新当前帧", command=self._update_merge_preview).pack(side=LEFT, padx=(0, 8))
        ttk.Label(merge_preview_options, text="倍速", style="Card.TLabel").pack(side=LEFT)
        ttk.Combobox(
            merge_preview_options,
            textvariable=self.merge_playback_speed,
            values=("0.5x", "1.0x", "1.5x", "2.0x", "3.0x"),
            width=7,
            state="readonly",
        ).pack(side=LEFT, padx=(6, 12))
        ttk.Label(merge_preview_options, text="清晰度", style="Card.TLabel").pack(side=LEFT)
        self.merge_preview_quality_combo = ttk.Combobox(
            merge_preview_options,
            textvariable=self.merge_preview_quality,
            values=("普通", "高清", "超清"),
            width=7,
            state="readonly",
        )
        self.merge_preview_quality_combo.pack(side=LEFT, padx=(6, 0))
        self.merge_preview_quality_combo.bind("<<ComboboxSelected>>", lambda _event: self._update_merge_preview())

    def _build_split_tab(self, tabs: ttk.Notebook) -> None:
        tab = ttk.Frame(tabs, style="App.TFrame")
        tabs.add(tab, text="拆分")

        self.split_input = StringVar()
        work_area = ttk.Frame(tab, style="App.TFrame")
        work_area.pack(fill=BOTH, expand=True, padx=10, pady=(8, 4))

        controls = ttk.LabelFrame(work_area, text="操作面板", padding=8)
        controls.pack(side=LEFT, fill=Y, padx=(0, 8))
        controls.configure(width=390)
        controls.pack_propagate(False)

        file_panel = ttk.LabelFrame(controls, text="输入文件", padding=8)
        file_panel.pack(fill=X, pady=(0, 8))
        ttk.Entry(file_panel, textvariable=self.split_input).pack(fill=X, pady=(0, 6))
        file_buttons = ttk.Frame(file_panel, style="Card.TFrame")
        file_buttons.pack(fill=X)
        ttk.Button(file_buttons, text="浏览", command=self._choose_split_input).pack(side=LEFT, fill=X, expand=True, padx=(0, 6))
        ttk.Button(file_buttons, text="外部播放", command=self._open_split_input).pack(side=LEFT, fill=X, expand=True)

        cut_actions = ttk.LabelFrame(controls, text="切点操作", padding=8)
        cut_actions.pack(fill=X, pady=(0, 8))
        ttk.Button(cut_actions, text="在当前播放位置切一刀", command=self._add_split_cut, style="Accent.TButton").pack(
            fill=X, pady=(0, 6)
        )
        cut_buttons = ttk.Frame(cut_actions, style="Card.TFrame")
        cut_buttons.pack(fill=X)
        ttk.Button(cut_buttons, text="删除选中切点", command=self._remove_selected_cut).pack(
            side=LEFT, fill=X, expand=True, padx=(0, 8)
        )
        ttk.Button(cut_buttons, text="清空切点", command=self._clear_split_cuts).pack(side=LEFT, fill=X, expand=True)

        cuts_panel = ttk.LabelFrame(controls, text="当前切点", padding=8)
        cuts_panel.pack(fill=X, pady=(0, 8))
        self.split_cut_list = Listbox(
            cuts_panel,
            height=4,
            bg="white",
            fg=COLOR_TEXT,
            selectbackground=COLOR_ACCENT,
            selectforeground="white",
            relief="flat",
            highlightthickness=1,
            highlightbackground="#cbd5e1",
        )
        self.split_cut_list.pack(fill=X)

        export_panel = ttk.LabelFrame(controls, text="导出", padding=8)
        export_panel.pack(fill=X, pady=(0, 8))
        format_row = ttk.Frame(export_panel, style="Card.TFrame")
        format_row.pack(fill=X, pady=(0, 8))
        ttk.Label(format_row, text="输出格式", style="Card.TLabel").pack(side=LEFT)
        ttk.Combobox(
            format_row,
            textvariable=self.split_output_format,
            values=(".mp4", ".gif"),
            width=8,
            state="readonly",
        ).pack(side=LEFT, padx=(8, 0))

        ttk.Button(export_panel, text="按切点导出", command=self._split_from_cuts, style="Accent.TButton").pack(
            fill=X, pady=(0, 8)
        )
        ttk.Button(export_panel, text="按下方配置导出", command=self._split_file).pack(fill=X)

        progress = ttk.LabelFrame(controls, text="导出进度", padding=8)
        progress.pack(fill=X, pady=(0, 8))
        ttk.Label(progress, textvariable=self.split_export_status, style="Card.TLabel", wraplength=270).pack(anchor="w")
        self.split_export_bar = ttk.Progressbar(
            progress,
            variable=self.split_export_progress,
            maximum=100,
            mode="determinate",
        )
        self.split_export_bar.pack(fill=X, pady=(8, 0))

        editor = ttk.LabelFrame(controls, text="片段配置（可手动微调）", padding=8)
        editor.pack(fill=BOTH, expand=True)
        ttk.Label(
            editor,
            text="片段列表：每行一个，格式为 开始时间,持续时间,输出文件名   示例：00:00:05,00:00:10,part1.gif",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(0, 8))
        self.split_text = Text(
            editor,
            height=4,
            bg="white",
            fg=COLOR_TEXT,
            relief="flat",
            highlightthickness=1,
            highlightbackground="#cbd5e1",
            padx=10,
            pady=8,
            font=("Consolas", 10),
        )
        self.split_text.pack(fill=BOTH, expand=True)
        self.split_text.insert("1.0", "00:00:00,00:00:05,part1.mp4\n00:00:05,00:00:05,part2.gif\n")

        preview = ttk.LabelFrame(work_area, text="视频预览（右侧主区域）", padding=8)
        preview.pack(side=RIGHT, fill=BOTH, expand=True)
        self.split_preview_label = ttk.Label(
            preview,
            textvariable=self.split_preview_status,
            style="Preview.TLabel",
            width=120,
        )
        self.split_preview_label.pack(side="top", fill=BOTH, expand=True)
        player_controls = ttk.Frame(preview, style="Card.TFrame")
        player_controls.pack(side="bottom", fill=X, pady=(8, 0))
        ttk.Label(player_controls, textvariable=self.split_position_label, style="Card.TLabel").pack(fill=X, pady=(0, 4))
        self.split_scale = ttk.Scale(
            player_controls,
            from_=0,
            to=1,
            orient="horizontal",
            variable=self.split_position,
            command=self._timeline_changed,
        )
        self.split_scale.pack(fill=X, pady=(0, 6))
        player_options = ttk.Frame(player_controls, style="Card.TFrame")
        player_options.pack(fill=X)
        self.split_play_button = ttk.Button(player_options, text="播放预览", command=self._toggle_split_playback)
        self.split_play_button.pack(side=LEFT, padx=(0, 8))
        ttk.Button(player_options, text="刷新帧", command=self._update_split_preview).pack(side=LEFT, padx=(0, 8))
        ttk.Button(player_options, text="放大预览", command=self._open_large_split_preview).pack(side=LEFT, padx=(0, 12))
        ttk.Label(player_options, text="倍速", style="Card.TLabel").pack(side=LEFT)
        ttk.Combobox(
            player_options,
            textvariable=self.split_playback_speed,
            values=("0.5x", "1.0x", "1.5x", "2.0x", "3.0x"),
            width=7,
            state="readonly",
        ).pack(side=LEFT, padx=(6, 12))
        ttk.Label(player_options, text="清晰度", style="Card.TLabel").pack(side=LEFT)
        self.split_preview_quality_combo = ttk.Combobox(
            player_options,
            textvariable=self.split_preview_quality,
            values=("普通", "高清", "超清"),
            width=8,
            state="readonly",
        )
        self.split_preview_quality_combo.pack(side=LEFT, padx=(6, 0))
        self.split_preview_quality_combo.bind("<<ComboboxSelected>>", lambda _event: self._update_split_preview())

    def _refresh_ffmpeg_status(self) -> None:
        path = ffmpeg_path()
        if path:
            self.ffmpeg_status.set(f"FFmpeg: {path}")
            self.install_ffmpeg_button.config(state="disabled", text="FFmpeg 已安装")
        else:
            if winget_path():
                self.ffmpeg_status.set("未找到 FFmpeg。点击“安装 FFmpeg”可通过 winget 自动安装。")
                self.install_ffmpeg_button.config(state="normal", text="安装 FFmpeg")
            else:
                self.ffmpeg_status.set("未找到 FFmpeg，且 winget 不可用；请点击“打开 FFmpeg 官网”手动安装。")
                self.install_ffmpeg_button.config(state="disabled", text="安装 FFmpeg")

    def _install_ffmpeg(self) -> None:
        winget = winget_path()
        if not winget:
            messagebox.showerror(APP_TITLE, "未找到 winget。请点击“打开 FFmpeg 官网”手动安装。")
            return
        if not messagebox.askyesno(
            APP_TITLE,
            "是否使用 winget 安装 FFmpeg？\n\n过程中可能会弹出 Windows 安装或权限提示。",
        ):
            return
        command = [
            winget,
            "install",
            "--id",
            "Gyan.FFmpeg",
            "--exact",
            "--source",
            "winget",
            "--accept-source-agreements",
            "--accept-package-agreements",
        ]
        self.install_ffmpeg_button.config(state="disabled", text="正在安装...")
        self.runner.run(command, on_done=self._after_install_ffmpeg, label="正在安装 FFmpeg")

    def _after_install_ffmpeg(self, result: CommandResult) -> None:
        self._refresh_ffmpeg_status()
        if result.returncode == 0 and not ffmpeg_path():
            messagebox.showinfo(
                APP_TITLE,
                "FFmpeg 安装已完成，但当前应用暂时还检测不到。\n\n请关闭并重新打开应用，然后点击“刷新 FFmpeg”。",
            )
        elif result.returncode != 0:
            messagebox.showerror(APP_TITLE, "FFmpeg 安装失败，请查看底部日志。")

    def _open_ffmpeg_website(self) -> None:
        webbrowser.open("https://www.gyan.dev/ffmpeg/builds/")

    def _choose_output_dir(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.output_dir.get())
        if chosen:
            self.output_dir.set(chosen)

    def _require_ffmpeg(self) -> str | None:
        path = ffmpeg_path()
        if not path:
            messagebox.showerror(
                APP_TITLE,
                "此功能需要 FFmpeg，但当前未检测到。\n\n请先安装 FFmpeg，确保已加入 PATH，然后点击“刷新 FFmpeg”。",
            )
            return None
        return path

    def _safe_output_path(self, base_name: str, suffix: str) -> Path:
        clean = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in base_name).strip("._")
        if not clean:
            clean = "recording"
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out_dir = Path(self.output_dir.get())
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir / f"{clean}-{stamp}{suffix}"

    def _hotkey_text(self) -> str:
        return f"Ctrl+Alt+{self.record_pause_hotkey.get()} 暂停/继续，Ctrl+Alt+{self.record_stop_hotkey.get()} 停止"

    def _validate_record_hotkeys(self, pause_key: str, stop_key: str) -> bool:
        if not hotkey_key_code(pause_key) or not hotkey_key_code(stop_key):
            messagebox.showerror(APP_TITLE, "快捷键必须选择 A-Z 或 F1-F12。")
            return False
        if pause_key.upper() == stop_key.upper():
            messagebox.showerror(APP_TITLE, "暂停/继续和停止不能使用同一个快捷键。")
            return False
        return True

    def _confirm_record_hotkeys(self) -> bool:
        dialog = Toplevel(self)
        dialog.title("确认录制快捷键")
        dialog.configure(bg=COLOR_BG)
        dialog.transient(self)
        dialog.resizable(False, False)

        pause_key = StringVar(value=self.record_pause_hotkey.get())
        stop_key = StringVar(value=self.record_stop_hotkey.get())
        confirmed = {"value": False}

        body = ttk.Frame(dialog, style="Card.TFrame", padding=18)
        body.pack(fill=BOTH, expand=True)
        ttk.Label(body, text="开始录制后主窗口会自动隐藏", style="Card.TLabel", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
        ttk.Label(
            body,
            text="请确认或修改录制控制快捷键。快捷键为全局生效，如果被其他程序占用，日志中会提示注册失败。",
            style="Muted.TLabel",
            wraplength=520,
            justify=LEFT,
        ).pack(anchor="w", pady=(8, 12))

        row1 = ttk.Frame(body, style="Card.TFrame")
        row1.pack(fill=X, pady=(0, 8))
        ttk.Label(row1, text="暂停/继续：Ctrl+Alt+", style="Card.TLabel").pack(side=LEFT)
        ttk.Combobox(row1, textvariable=pause_key, values=HOTKEY_CHOICES, width=8, state="readonly").pack(side=LEFT)

        row2 = ttk.Frame(body, style="Card.TFrame")
        row2.pack(fill=X, pady=(0, 12))
        ttk.Label(row2, text="停止录制：Ctrl+Alt+", style="Card.TLabel").pack(side=LEFT)
        ttk.Combobox(row2, textvariable=stop_key, values=HOTKEY_CHOICES, width=8, state="readonly").pack(side=LEFT)

        def start() -> None:
            pause = pause_key.get().upper()
            stop = stop_key.get().upper()
            if not self._validate_record_hotkeys(pause, stop):
                return
            self.record_pause_hotkey.set(pause)
            self.record_stop_hotkey.set(stop)
            confirmed["value"] = True
            dialog.destroy()

        buttons = ttk.Frame(body, style="Card.TFrame")
        buttons.pack(fill=X)
        ttk.Button(buttons, text="开始录制并隐藏窗口", command=start, style="Accent.TButton").pack(side=LEFT)
        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side=RIGHT)

        dialog.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dialog.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        dialog.grab_set()
        self.wait_window(dialog)
        return confirmed["value"]

    def _start_recording(self) -> None:
        ffmpeg = self._require_ffmpeg()
        if not ffmpeg:
            return
        if self.record_proc:
            messagebox.showwarning(APP_TITLE, "录制已经在进行中。")
            return
        try:
            fps = parse_positive_int(self.record_fps.get(), "FPS")
        except ValueError as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        if not self._validate_record_hotkeys(self.record_pause_hotkey.get(), self.record_stop_hotkey.get()):
            return
        if not self._confirm_record_hotkeys():
            return

        out_path = self._safe_output_path(self.record_name.get(), ".mp4")
        command = [
            ffmpeg,
            "-y",
            "-f",
            "gdigrab",
            "-framerate",
            str(fps),
            "-i",
            "desktop",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            str(out_path),
        ]
        try:
            self.record_proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception as exc:
            self.record_proc = None
            messagebox.showerror(APP_TITLE, f"无法开始录制：\n{exc}")
            return

        self.record_output = out_path
        self.record_started_at = time.time()
        self.record_paused = False
        self.record_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self.log_queue.put("\n== 已开始录制 ==\n")
        self.log_queue.put(" ".join(command) + "\n")
        self.log_queue.put(f"主窗口已自动隐藏。快捷键：{self._hotkey_text()}。\n")

        def reader() -> None:
            assert self.record_proc is not None
            assert self.record_proc.stdout is not None
            for line in self.record_proc.stdout:
                self.log_queue.put(line)
            code = self.record_proc.wait()
            self.log_queue.put(f"== 录制结束，退出码 {code} ==\n")
            self.after(0, lambda: self._recording_finished(code))

        threading.Thread(target=reader, daemon=True).start()
        self.record_hotkeys.start()
        self.after(300, self.withdraw)

    def _stop_recording(self) -> None:
        if not self.record_proc:
            return
        if self.record_paused:
            self._resume_record_process()
        self.record_started_at = None
        self.record_paused = False
        self.record_timer.set("正在保存录制文件，请稍候...")
        self.log_queue.put("正在停止录制并保存文件...\n")
        try:
            if self.record_proc.stdin:
                self.record_proc.stdin.write("q\n")
                self.record_proc.stdin.flush()
        except Exception:
            self.record_proc.terminate()
        self.stop_button.config(state="disabled")

    def _recording_finished(self, returncode: int) -> None:
        out_path = self.record_output
        self.record_proc = None
        self.record_paused = False
        self.record_hotkeys.stop()
        self._restore_main_window()
        self.stop_button.config(state="disabled")

        if returncode != 0 or not out_path or not out_path.exists():
            self.record_button.config(state="normal")
            self.record_timer.set("录制保存失败，请查看日志。")
            return

        if self.gif_after_record.get():
            self.record_timer.set("录制已保存，正在转换为 GIF，请稍候...")
            self.log_queue.put("录制文件已保存，正在转换为 GIF...\n")
            gif_path = out_path.with_suffix(".gif")
            self._run_gif_conversion(
                out_path,
                gif_path,
                on_done=lambda result: self._recording_gif_finished(result, gif_path, out_path),
            )
            return

        self._finish_record_export(out_path, "录制文件已保存，可以打开目录查看或预览文件。")

    def _recording_gif_finished(self, result: CommandResult, gif_path: Path, mp4_path: Path) -> None:
        if result.returncode == 0 and gif_path.exists():
            self._finish_record_export(gif_path, "GIF 已转换完成，可以打开目录查看或直接预览。")
            return
        self._finish_record_export(mp4_path, "GIF 转换失败，但 MP4 录制文件已保存。可以打开目录查看或预览 MP4。")

    def _finish_record_export(self, output_path: Path, message: str) -> None:
        self.record_button.config(state="normal")
        self.stop_button.config(state="disabled")
        self.record_timer.set(message)
        self.log_queue.put(f"{message}\n输出文件：{output_path}\n")
        self._load_record_preview(output_path, autoplay=True)
        self._show_recording_result(output_path, message)

    def _restore_main_window(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()

    def _toggle_record_pause(self) -> None:
        if not self.record_proc or self.record_proc.poll() is not None:
            return
        if self.record_paused:
            self._resume_record_process()
            return
        self._suspend_record_process()

    def _suspend_record_process(self) -> None:
        if not self.record_proc:
            return
        if self._set_process_suspended(self.record_proc.pid, suspend=True):
            self.record_paused = True
            self.record_timer.set(f"录制已暂停。{self._hotkey_text()}。")
            self.log_queue.put(f"录制已通过 Ctrl+Alt+{self.record_pause_hotkey.get()} 暂停。\n")

    def _resume_record_process(self) -> None:
        if not self.record_proc:
            return
        if self._set_process_suspended(self.record_proc.pid, suspend=False):
            self.record_paused = False
            self.record_timer.set(f"录制已继续。{self._hotkey_text()}。")
            self.log_queue.put("录制已继续。\n")

    def _set_process_suspended(self, pid: int, suspend: bool) -> bool:
        if os.name != "nt":
            self.log_queue.put("当前系统不支持进程暂停快捷键。\n")
            return False
        PROCESS_SUSPEND_RESUME = 0x0800
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_SUSPEND_RESUME, False, pid)
        if not handle:
            self.log_queue.put("无法打开录制进程用于暂停/继续。\n")
            return False
        try:
            if suspend:
                status = ctypes.windll.ntdll.NtSuspendProcess(handle)
            else:
                status = ctypes.windll.ntdll.NtResumeProcess(handle)
            if status != 0:
                action = "暂停" if suspend else "继续"
                self.log_queue.put(f"录制进程{action}失败，状态码：{status}\n")
                return False
            return True
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)

    def _load_record_preview(self, output_path: Path, autoplay: bool = False) -> None:
        self._stop_record_playback()
        self.record_preview_path = output_path
        try:
            self.record_preview_duration = self._probe_duration(output_path)
        except RuntimeError as exc:
            self.record_preview_duration = 0.0
            self.record_preview_status.set(f"录制文件已生成，但无法读取预览时长：{exc}")
            return
        self.record_preview_position = 0.0
        self.record_preview_position_var.set(0.0)
        self.record_preview_scale.config(to=max(self.record_preview_duration, 1.0))
        self.record_preview_status.set(f"已加载录制结果：{output_path.name}")
        self._record_timeline_changed("0")
        self._update_record_preview()
        if autoplay:
            self.after(500, self._toggle_record_playback)

    def _record_timeline_changed(self, value: str) -> None:
        self.record_preview_position = float(value)
        if self.record_preview_duration > 0:
            self.record_preview_position_label.set(
                f"当前位置：{format_timestamp(self.record_preview_position)} / 总时长：{format_timestamp(self.record_preview_duration)}"
            )
            self._schedule_record_preview_update()
        else:
            self.record_preview_position_label.set("00:00:00.000 / 未加载")

    def _toggle_record_playback(self) -> None:
        if not self.record_preview_path or self.record_preview_duration <= 0:
            messagebox.showwarning(APP_TITLE, "暂无可播放的录制结果。")
            return
        if self.record_playing:
            self._stop_record_playback()
            return
        self.record_playing = True
        self.record_play_button.config(text="暂停预览")
        self._advance_record_playback()

    def _stop_record_playback(self) -> None:
        self.record_playing = False
        if hasattr(self, "record_play_button"):
            self.record_play_button.config(text="播放预览")
        if self.record_play_job:
            self.after_cancel(self.record_play_job)
            self.record_play_job = None

    def _advance_record_playback(self) -> None:
        if not self.record_playing:
            return
        self.record_preview_position += 0.5 * parse_speed(self.record_playback_speed.get())
        if self.record_preview_position >= self.record_preview_duration:
            self.record_preview_position = self.record_preview_duration
            self._stop_record_playback()
        self.record_preview_position_var.set(self.record_preview_position)
        self._record_timeline_changed(str(self.record_preview_position))
        self.record_play_job = self.after(520, self._advance_record_playback)

    def _schedule_record_preview_update(self) -> None:
        if self.record_preview_job:
            self.after_cancel(self.record_preview_job)
        self.record_preview_job = self.after(350, self._update_record_preview)

    def _record_preview_dimensions(self) -> tuple[int, int]:
        quality = self.record_preview_quality.get()
        if quality == "超清":
            return 1360, 765
        if quality == "普通":
            return 960, 540
        return 1180, 664

    def _update_record_preview(self) -> None:
        self.record_preview_job = None
        ffmpeg = ffmpeg_path()
        if not self.record_preview_path or not self.record_preview_path.exists():
            self.record_preview_status.set("录制或转换完成后会在这里自动预览播放。")
            self.record_preview_label.configure(image="", textvariable=self.record_preview_status)
            self.record_preview_image = None
            return
        if not ffmpeg:
            self.record_preview_status.set("未找到 FFmpeg，无法生成录制预览。")
            self.record_preview_label.configure(image="", textvariable=self.record_preview_status)
            self.record_preview_image = None
            return

        self.record_preview_token += 1
        token = self.record_preview_token
        second = safe_preview_second(self.record_preview_position, self.record_preview_duration)
        preview_path = temp_dir() / f"record-preview-{os.getpid()}-{token}.ppm"
        if self.record_preview_image is None:
            self.record_preview_status.set(f"正在生成 {format_timestamp(second)} 的录制预览...")
            self.record_preview_label.configure(image="", textvariable=self.record_preview_status)

        def worker() -> None:
            proc = subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-ss",
                    format_timestamp(second),
                    "-i",
                    str(self.record_preview_path),
                    "-frames:v",
                    "1",
                    "-vf",
                    self._split_preview_filter(*self._record_preview_dimensions()),
                    "-f",
                    "image2",
                    "-vcodec",
                    "ppm",
                    str(preview_path),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATE_NO_WINDOW,
                check=False,
            )
            self.after(0, lambda: self._apply_record_preview(token, preview_path, proc.returncode, proc.stdout))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_record_preview(self, token: int, preview_path: Path, returncode: int, output: str) -> None:
        if token != self.record_preview_token:
            preview_path.unlink(missing_ok=True)
            return
        if returncode != 0 or not preview_path.exists():
            if self.record_preview_image is None:
                self.record_preview_label.configure(image="", textvariable=self.record_preview_status)
                self.record_preview_status.set("录制预览生成失败，请确认输出文件是否可播放。")
            else:
                self.record_preview_status.set("当前帧预览失败，已保留上一帧。")
            self.log_queue.put(f"录制预览生成失败：{output}\n")
            return
        try:
            image = PhotoImage(file=str(preview_path))
        except Exception as exc:
            if self.record_preview_image is None:
                self.record_preview_label.configure(image="", textvariable=self.record_preview_status)
                self.record_preview_status.set("录制预览图片加载失败。")
            else:
                self.record_preview_status.set("当前帧加载失败，已保留上一帧。")
            self.log_queue.put(f"录制预览图片加载失败：{exc}\n")
            return
        self.record_preview_image = image
        self.record_preview_label.configure(image=image)
        preview_path.unlink(missing_ok=True)

    def _open_large_record_preview(self) -> None:
        if not self.record_preview_path or not self.record_preview_path.exists():
            messagebox.showwarning(APP_TITLE, "请先生成或选择录制结果。")
            return
        self._open_path(self.record_preview_path)

    def _show_recording_result(self, output_path: Path, message: str) -> None:
        dialog = Toplevel(self)
        dialog.title("录制完成")
        dialog.configure(bg=COLOR_BG)
        dialog.transient(self)
        dialog.resizable(False, False)

        body = ttk.Frame(dialog, style="Card.TFrame", padding=18)
        body.pack(fill=BOTH, expand=True)
        ttk.Label(body, text="录制内容已准备好", style="Card.TLabel", font=("Microsoft YaHei UI", 12, "bold")).pack(
            anchor="w"
        )
        ttk.Label(body, text=message, style="Muted.TLabel", wraplength=460, justify=LEFT).pack(anchor="w", pady=(8, 6))
        ttk.Label(body, text=str(output_path), style="Muted.TLabel", wraplength=460, justify=LEFT).pack(
            anchor="w", pady=(0, 14)
        )

        buttons = ttk.Frame(body, style="Card.TFrame")
        buttons.pack(fill=X)
        ttk.Button(buttons, text="打开所在目录", command=lambda: self._open_path(output_path.parent)).pack(
            side=LEFT, padx=(0, 8)
        )
        ttk.Button(buttons, text="打开文件预览", command=lambda: self._open_path(output_path)).pack(
            side=LEFT, padx=(0, 8)
        )
        ttk.Button(buttons, text="复制文件路径", command=lambda: self._copy_output_path(output_path)).pack(
            side=LEFT, padx=(0, 8)
        )
        ttk.Button(buttons, text="关闭", command=dialog.destroy).pack(side=RIGHT)

        dialog.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dialog.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        dialog.grab_set()

    def _open_path(self, path: Path) -> None:
        try:
            os.startfile(str(path))
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"无法打开：\n{path}\n\n{exc}")

    def _copy_output_path(self, output_path: Path) -> None:
        self.clipboard_clear()
        self.clipboard_append(str(output_path))
        self.record_timer.set("文件路径已复制到剪贴板。")

    def _update_record_timer(self) -> None:
        if self.record_started_at and not self.record_paused:
            elapsed = int(time.time() - self.record_started_at)
            self.record_timer.set(f"录制中：{elapsed // 60:02d}:{elapsed % 60:02d}")
        self.after(500, self._update_record_timer)

    def _convert_existing_to_gif(self) -> None:
        source = filedialog.askopenfilename(
            title="选择视频",
            filetypes=[("视频文件", "*.mp4 *.mov *.mkv *.avi *.webm *.gif"), ("所有文件", "*.*")],
        )
        if not source:
            return
        default_name = Path(source).with_suffix(".gif").name
        target = filedialog.asksaveasfilename(
            title="保存 GIF",
            initialdir=self.output_dir.get(),
            initialfile=default_name,
            defaultextension=".gif",
            filetypes=[("GIF", "*.gif")],
        )
        if target:
            target_path = Path(target)
            self._run_gif_conversion(
                Path(source),
                target_path,
                on_done=lambda result: self._existing_gif_conversion_finished(result, target_path),
            )

    def _existing_gif_conversion_finished(self, result: CommandResult, target_path: Path) -> None:
        if result.returncode == 0 and target_path.exists():
            self.record_timer.set("已有视频已转换为 GIF，并已加载预览。")
            self._load_record_preview(target_path, autoplay=True)
            return
        messagebox.showerror(APP_TITLE, "已有视频转 GIF 失败，请查看日志。")

    def _run_gif_conversion(
        self,
        source: Path,
        target: Path,
        on_done: Callable[[CommandResult], None] | None = None,
    ) -> None:
        ffmpeg = self._require_ffmpeg()
        if not ffmpeg:
            return
        try:
            width = parse_positive_int(self.gif_width.get(), "GIF 宽度")
        except ValueError as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        vf = (
            f"fps=10,scale={width}:-1:flags=lanczos,"
            "split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse=dither=bayer:bayer_scale=5"
        )
        command = [ffmpeg, "-y", "-i", str(source), "-vf", vf, str(target)]
        self.runner.run(command, on_done=on_done, label=f"正在转换为 GIF：{target.name}")

    def _merge_add(self) -> None:
        files = filedialog.askopenfilenames(
            title="选择文件",
            filetypes=[("媒体文件", "*.mp4 *.mov *.mkv *.avi *.webm *.gif"), ("所有文件", "*.*")],
        )
        for file in files:
            self.merge_list.insert(END, file)

    def _merge_remove(self) -> None:
        for index in reversed(self.merge_list.curselection()):
            self.merge_list.delete(index)

    def _merge_move(self, direction: int) -> None:
        selection = self.merge_list.curselection()
        if not selection:
            return
        index = selection[0]
        new_index = index + direction
        if new_index < 0 or new_index >= self.merge_list.size():
            return
        item = self.merge_list.get(index)
        self.merge_list.delete(index)
        self.merge_list.insert(new_index, item)
        self.merge_list.selection_set(new_index)

    def _merge_files(self) -> None:
        ffmpeg = self._require_ffmpeg()
        if not ffmpeg:
            return
        files = [Path(self.merge_list.get(i)) for i in range(self.merge_list.size())]
        if len(files) < 2:
            messagebox.showwarning(APP_TITLE, "请至少选择两个文件进行拼接。")
            return
        try:
            width = parse_positive_int(self.merge_width.get(), "宽度")
            height = parse_positive_int(self.merge_height.get(), "高度")
            fps = parse_positive_int(self.merge_fps.get(), "FPS")
        except ValueError as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        target = filedialog.asksaveasfilename(
            title="保存拼接文件",
            initialdir=self.output_dir.get(),
            defaultextension=".mp4",
            filetypes=[("MP4", "*.mp4"), ("GIF", "*.gif")],
        )
        if not target:
            return

        target_path = Path(target)
        if target_path.suffix.lower() not in {".mp4", ".gif"}:
            messagebox.showerror(APP_TITLE, "输出文件必须以 .mp4 或 .gif 结尾。")
            return

        mp4_target = target_path
        gif_target: Path | None = None
        if target_path.suffix.lower() == ".gif":
            gif_target = target_path
            fd, tmp = tempfile.mkstemp(prefix="merge-", suffix=".mp4", dir=temp_dir())
            os.close(fd)
            mp4_target = Path(tmp)

        command = [ffmpeg, "-y"]
        for file in files:
            command.extend(["-i", str(file)])

        filters = []
        concat_inputs = []
        for index in range(len(files)):
            filters.append(
                f"[{index}:v]fps={fps},"
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
                f"setsar=1[v{index}]"
            )
            concat_inputs.append(f"[v{index}]")
        filter_complex = ";".join(filters) + ";" + "".join(concat_inputs) + f"concat=n={len(files)}:v=1:a=0[outv]"
        command.extend(
            [
                "-filter_complex",
                filter_complex,
                "-map",
                "[outv]",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                str(mp4_target),
            ]
        )

        def after_merge(result: CommandResult) -> None:
            if result.returncode != 0:
                self._finish_merge_export(target_path, False, "拼接失败，请查看日志。")
                return
            self.merge_progress.set(60)
            if gif_target:
                self.merge_status.set("视频拼接完成，正在转换为 GIF...")
                self._run_gif_conversion(
                    mp4_target,
                    gif_target,
                    on_done=lambda gif_result: self._merge_gif_finished(gif_result, gif_target, mp4_target),
                )
                return
            self._finish_merge_export(mp4_target, True, "拼接完成。")

        self._start_merge_progress("正在拼接视频...")
        self.runner.run(command, on_done=after_merge, label=f"正在拼接 {len(files)} 个文件")

    def _start_merge_progress(self, message: str) -> None:
        self.merge_progress.set(10)
        self.merge_progress_bar.config(mode="indeterminate")
        self.merge_progress_bar.start(12)
        self.merge_status.set(message)

    def _merge_gif_finished(self, result: CommandResult, gif_target: Path, temp_mp4: Path) -> None:
        self._delete_temp(temp_mp4)
        if result.returncode == 0 and gif_target.exists():
            self._finish_merge_export(gif_target, True, "拼接并转换 GIF 完成。")
            return
        self._finish_merge_export(gif_target, False, "GIF 转换失败，请查看日志。")

    def _finish_merge_export(self, output_path: Path, success: bool, message: str) -> None:
        self.merge_progress_bar.stop()
        self.merge_progress_bar.config(mode="determinate")
        self.merge_progress.set(100 if success else 0)
        self.merge_status.set(message)
        if success and output_path.exists():
            self._load_merge_preview(output_path, autoplay=True)
            self._show_merge_export_result(output_path, message)
        elif not success:
            messagebox.showerror(APP_TITLE, message)

    def _load_merge_preview(self, output_path: Path, autoplay: bool = False) -> None:
        self._stop_merge_playback()
        self.merge_preview_path = output_path
        try:
            self.merge_preview_duration = self._probe_duration(output_path)
        except RuntimeError as exc:
            self.merge_preview_duration = 0.0
            self.merge_preview_status.set(f"拼接文件已生成，但无法读取预览时长：{exc}")
            return
        self.merge_preview_position = 0.0
        self.merge_preview_status.set(f"已加载拼接结果：{output_path.name}")
        self.merge_preview_position_var.set(0.0)
        self.merge_preview_scale.config(to=max(self.merge_preview_duration, 1.0))
        self._merge_timeline_changed("0")
        self._update_merge_preview()
        if autoplay:
            self.after(500, self._toggle_merge_playback)

    def _merge_timeline_changed(self, value: str) -> None:
        self.merge_preview_position = float(value)
        if self.merge_preview_duration > 0:
            self.merge_preview_position_label.set(
                f"当前位置：{format_timestamp(self.merge_preview_position)} / 总时长：{format_timestamp(self.merge_preview_duration)}"
            )
            self._schedule_merge_preview_update()
        else:
            self.merge_preview_position_label.set("00:00:00.000 / 未加载")

    def _toggle_merge_playback(self) -> None:
        if not self.merge_preview_path or self.merge_preview_duration <= 0:
            messagebox.showwarning(APP_TITLE, "暂无可播放的拼接结果。")
            return
        if self.merge_playing:
            self._stop_merge_playback()
            return
        self.merge_playing = True
        self.merge_play_button.config(text="暂停预览")
        self._advance_merge_playback()

    def _stop_merge_playback(self) -> None:
        self.merge_playing = False
        if hasattr(self, "merge_play_button"):
            self.merge_play_button.config(text="播放预览")
        if self.merge_play_job:
            self.after_cancel(self.merge_play_job)
            self.merge_play_job = None

    def _advance_merge_playback(self) -> None:
        if not self.merge_playing:
            return
        self.merge_preview_position += 0.5 * parse_speed(self.merge_playback_speed.get())
        if self.merge_preview_position >= self.merge_preview_duration:
            self.merge_preview_position = self.merge_preview_duration
            self._stop_merge_playback()
        self.merge_preview_position_var.set(self.merge_preview_position)
        self._merge_timeline_changed(str(self.merge_preview_position))
        self.merge_play_job = self.after(520, self._advance_merge_playback)

    def _schedule_merge_preview_update(self) -> None:
        if self.merge_preview_job:
            self.after_cancel(self.merge_preview_job)
        self.merge_preview_job = self.after(350, self._update_merge_preview)

    def _merge_preview_dimensions(self) -> tuple[int, int]:
        quality = self.merge_preview_quality.get()
        if quality == "超清":
            return 1360, 765
        if quality == "普通":
            return 960, 540
        return 1180, 664

    def _update_merge_preview(self) -> None:
        self.merge_preview_job = None
        ffmpeg = ffmpeg_path()
        if not self.merge_preview_path or not self.merge_preview_path.exists():
            self.merge_preview_status.set("拼接完成后会在这里自动预览播放。")
            self.merge_preview_label.configure(image="", textvariable=self.merge_preview_status)
            self.merge_preview_image = None
            return
        if not ffmpeg:
            self.merge_preview_status.set("未找到 FFmpeg，无法生成拼接预览。")
            self.merge_preview_label.configure(image="", textvariable=self.merge_preview_status)
            self.merge_preview_image = None
            return

        self.merge_preview_token += 1
        token = self.merge_preview_token
        second = safe_preview_second(self.merge_preview_position, self.merge_preview_duration)
        preview_path = temp_dir() / f"merge-preview-{os.getpid()}-{token}.ppm"
        if self.merge_preview_image is None:
            self.merge_preview_status.set(f"正在生成 {format_timestamp(second)} 的拼接预览...")
            self.merge_preview_label.configure(image="", textvariable=self.merge_preview_status)

        def worker() -> None:
            proc = subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-ss",
                    format_timestamp(second),
                    "-i",
                    str(self.merge_preview_path),
                    "-frames:v",
                    "1",
                    "-vf",
                    self._split_preview_filter(*self._merge_preview_dimensions()),
                    "-f",
                    "image2",
                    "-vcodec",
                    "ppm",
                    str(preview_path),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATE_NO_WINDOW,
                check=False,
            )
            self.after(0, lambda: self._apply_merge_preview(token, preview_path, proc.returncode, proc.stdout))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_merge_preview(self, token: int, preview_path: Path, returncode: int, output: str) -> None:
        if token != self.merge_preview_token:
            preview_path.unlink(missing_ok=True)
            return
        if returncode != 0 or not preview_path.exists():
            if self.merge_preview_image is None:
                self.merge_preview_label.configure(image="", textvariable=self.merge_preview_status)
                self.merge_preview_status.set("拼接预览生成失败，请确认输出文件是否可播放。")
            else:
                self.merge_preview_status.set("当前帧预览失败，已保留上一帧。")
            self.log_queue.put(f"拼接预览生成失败：{output}\n")
            return
        try:
            image = PhotoImage(file=str(preview_path))
        except Exception as exc:
            if self.merge_preview_image is None:
                self.merge_preview_label.configure(image="", textvariable=self.merge_preview_status)
                self.merge_preview_status.set("拼接预览图片加载失败。")
            else:
                self.merge_preview_status.set("当前帧加载失败，已保留上一帧。")
            self.log_queue.put(f"拼接预览图片加载失败：{exc}\n")
            return
        self.merge_preview_image = image
        self.merge_preview_label.configure(image=image)
        preview_path.unlink(missing_ok=True)

    def _show_merge_export_result(self, output_path: Path, message: str) -> None:
        dialog = Toplevel(self)
        dialog.title("拼接完成")
        dialog.configure(bg=COLOR_BG)
        dialog.transient(self)
        dialog.geometry("680x260")

        body = ttk.Frame(dialog, style="Card.TFrame", padding=18)
        body.pack(fill=BOTH, expand=True)
        ttk.Label(body, text="拼接文件已生成", style="Card.TLabel", font=("Microsoft YaHei UI", 12, "bold")).pack(
            anchor="w"
        )
        ttk.Label(body, text=message, style="Muted.TLabel").pack(anchor="w", pady=(8, 6))
        ttk.Label(body, text=str(output_path), style="Muted.TLabel", wraplength=620).pack(anchor="w", pady=(0, 14))

        buttons = ttk.Frame(body, style="Card.TFrame")
        buttons.pack(fill=X)
        ttk.Button(buttons, text="打开所在目录", command=lambda: self._open_path(output_path.parent)).pack(
            side=LEFT, padx=(0, 8)
        )
        ttk.Button(buttons, text="打开文件预览", command=lambda: self._open_path(output_path)).pack(
            side=LEFT, padx=(0, 8)
        )
        ttk.Button(buttons, text="复制文件路径", command=lambda: self._copy_output_path(output_path)).pack(
            side=LEFT, padx=(0, 8)
        )
        ttk.Button(buttons, text="关闭", command=dialog.destroy).pack(side=RIGHT)

    def _delete_temp(self, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            self.log_queue.put(f"无法删除临时文件 {path}：{exc}\n")

    def _choose_split_input(self) -> None:
        source = filedialog.askopenfilename(
            title="选择要拆分的文件",
            filetypes=[("媒体文件", "*.mp4 *.mov *.mkv *.avi *.webm *.gif"), ("所有文件", "*.*")],
        )
        if source:
            self.split_input.set(source)
            self._load_split_timeline(Path(source))

    def _load_split_timeline(self, source: Path) -> None:
        self._stop_split_playback()
        try:
            duration = self._probe_duration(source)
        except RuntimeError as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        self.split_duration_seconds = duration
        self.split_cut_points.clear()
        self.split_position.set(0.0)
        self.split_scale.config(to=max(duration, 1.0))
        self._timeline_changed("0")
        self._refresh_cut_points()
        self._schedule_split_preview_update()
        self.log_queue.put(f"已加载拆分文件：{source}\n时长：{format_timestamp(duration)}\n")

    def _probe_duration(self, source: Path) -> float:
        probe = ffprobe_path()
        if not probe:
            raise RuntimeError("未找到 ffprobe。请先安装 FFmpeg，或点击“刷新 FFmpeg”后重试。")
        proc = subprocess.run(
            [
                probe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(source),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"无法读取视频时长：\n{proc.stderr.strip() or proc.stdout.strip()}")
        try:
            duration = float(proc.stdout.strip())
        except ValueError as exc:
            raise RuntimeError("无法解析视频时长，请确认文件是否可播放。") from exc
        if duration <= 0:
            raise RuntimeError("视频时长无效，请确认文件是否可播放。")
        return duration

    def _timeline_changed(self, value: str) -> None:
        current = float(value)
        if self.split_duration_seconds > 0:
            self.split_position_label.set(
                f"当前位置：{format_timestamp(current)} / 总时长：{format_timestamp(self.split_duration_seconds)}"
            )
            self._schedule_split_preview_update()
        else:
            self.split_position_label.set("00:00:00.000 / 未加载")

    def _toggle_split_playback(self) -> None:
        if self.split_duration_seconds <= 0:
            messagebox.showwarning(APP_TITLE, "请先选择要预览的文件。")
            return
        if self.split_playing:
            self._stop_split_playback()
            return
        self.split_playing = True
        self.split_play_button.config(text="暂停预览")
        self._advance_split_playback()

    def _stop_split_playback(self) -> None:
        self.split_playing = False
        if hasattr(self, "split_play_button"):
            self.split_play_button.config(text="播放预览")
        if self.split_play_job:
            self.after_cancel(self.split_play_job)
            self.split_play_job = None

    def _advance_split_playback(self) -> None:
        if not self.split_playing:
            return
        current = float(self.split_position.get())
        next_position = current + 0.5 * parse_speed(self.split_playback_speed.get())
        if next_position >= self.split_duration_seconds:
            next_position = self.split_duration_seconds
            self._stop_split_playback()
        self.split_position.set(next_position)
        self._timeline_changed(str(next_position))
        self.split_play_job = self.after(520, self._advance_split_playback)

    def _schedule_split_preview_update(self) -> None:
        if self.split_preview_job:
            self.after_cancel(self.split_preview_job)
        self.split_preview_job = self.after(350, self._update_split_preview)

    def _update_split_preview(self) -> None:
        self.split_preview_job = None
        source = Path(self.split_input.get())
        ffmpeg = ffmpeg_path()
        if not source.exists() or self.split_duration_seconds <= 0:
            self.split_preview_status.set("选择文件后会在这里显示当前时间点的画面预览。")
            self.split_preview_label.configure(image="", textvariable=self.split_preview_status)
            self.split_preview_image = None
            return
        if not ffmpeg:
            self.split_preview_status.set("未找到 FFmpeg，无法生成预览帧。")
            self.split_preview_label.configure(image="", textvariable=self.split_preview_status)
            self.split_preview_image = None
            return

        self.split_preview_token += 1
        token = self.split_preview_token
        second = safe_preview_second(float(self.split_position.get()), self.split_duration_seconds)
        preview_path = temp_dir() / f"preview-{os.getpid()}-{token}.ppm"
        preview_filter = self._split_preview_filter(*self._split_preview_dimensions())
        self.split_preview_status.set(f"正在生成 {format_timestamp(second)} 的预览...")
        if self.split_preview_image is None:
            self.split_preview_label.configure(image="", textvariable=self.split_preview_status)

        def worker() -> None:
            proc = subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-ss",
                    format_timestamp(second),
                    "-i",
                    str(source),
                    "-frames:v",
                    "1",
                    "-vf",
                    preview_filter,
                    "-f",
                    "image2",
                    "-vcodec",
                    "ppm",
                    str(preview_path),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATE_NO_WINDOW,
                check=False,
            )
            self.after(0, lambda: self._apply_split_preview(token, preview_path, proc.returncode, proc.stdout))

        threading.Thread(target=worker, daemon=True).start()

    def _split_preview_dimensions(self) -> tuple[int, int]:
        quality = self.split_preview_quality.get()
        if quality == "超清":
            return 1360, 765
        if quality == "普通":
            return 960, 540
        return 1180, 664

    def _split_preview_filter(self, width: int, height: int) -> str:
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x111827"
        )

    def _apply_split_preview(self, token: int, preview_path: Path, returncode: int, output: str) -> None:
        if token != self.split_preview_token:
            preview_path.unlink(missing_ok=True)
            return
        if returncode != 0 or not preview_path.exists():
            if self.split_preview_image is None:
                self.split_preview_label.configure(image="", textvariable=self.split_preview_status)
                self.split_preview_status.set("预览生成失败，请确认文件是否可播放。")
            else:
                self.split_preview_status.set("当前帧预览失败，已保留上一帧。")
            self.log_queue.put(f"预览生成失败：{output}\n")
            return
        try:
            image = PhotoImage(file=str(preview_path))
        except Exception as exc:
            if self.split_preview_image is None:
                self.split_preview_label.configure(image="", textvariable=self.split_preview_status)
                self.split_preview_status.set("预览图片加载失败。")
            else:
                self.split_preview_status.set("当前帧加载失败，已保留上一帧。")
            self.log_queue.put(f"预览图片加载失败：{exc}\n")
            return
        self.split_preview_image = image
        self.split_preview_label.configure(image=image)
        preview_path.unlink(missing_ok=True)

    def _open_split_input(self) -> None:
        source = Path(self.split_input.get())
        if not source.exists():
            messagebox.showwarning(APP_TITLE, "请先选择要预览的文件。")
            return
        self._open_path(source)

    def _open_large_split_preview(self) -> None:
        source = Path(self.split_input.get())
        ffmpeg = ffmpeg_path()
        if not source.exists() or self.split_duration_seconds <= 0:
            messagebox.showwarning(APP_TITLE, "请先选择要预览的文件。")
            return
        if not ffmpeg:
            messagebox.showerror(APP_TITLE, "未找到 FFmpeg，无法生成放大预览。")
            return

        second = safe_preview_second(float(self.split_position.get()), self.split_duration_seconds)
        preview_path = temp_dir() / f"large-preview-{os.getpid()}-{int(time.time() * 1000)}.ppm"
        dialog = Toplevel(self)
        dialog.title(f"放大预览 - {format_timestamp(second)}")
        dialog.configure(bg=COLOR_BG)
        dialog.transient(self)
        dialog.geometry("1500x900")

        body = ttk.Frame(dialog, style="Card.TFrame", padding=12)
        body.pack(fill=BOTH, expand=True)
        status = StringVar(value="正在生成高清预览...")
        label = ttk.Label(body, textvariable=status, style="Preview.TLabel")
        label.pack(fill=BOTH, expand=True)

        def worker() -> None:
            proc = subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-ss",
                    format_timestamp(second),
                    "-i",
                    str(source),
                    "-frames:v",
                    "1",
                    "-vf",
                    self._split_preview_filter(1440, 810),
                    "-f",
                    "image2",
                    "-vcodec",
                    "ppm",
                    str(preview_path),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATE_NO_WINDOW,
                check=False,
            )
            self.after(0, lambda: apply_large_preview(proc.returncode, proc.stdout))

        def apply_large_preview(returncode: int, output: str) -> None:
            if returncode != 0 or not preview_path.exists():
                status.set("放大预览生成失败，请查看日志。")
                self.log_queue.put(f"放大预览生成失败：{output}\n")
                return
            try:
                image = PhotoImage(file=str(preview_path))
            except Exception as exc:
                status.set("放大预览图片加载失败。")
                self.log_queue.put(f"放大预览图片加载失败：{exc}\n")
                return
            dialog.preview_image = image
            label.configure(image=image, textvariable="")
            preview_path.unlink(missing_ok=True)

        threading.Thread(target=worker, daemon=True).start()

    def _add_split_cut(self) -> None:
        if self.split_duration_seconds <= 0:
            messagebox.showwarning(APP_TITLE, "请先选择要拆分的文件。")
            return
        cut = round(float(self.split_position.get()), 3)
        if cut <= 0.05 or cut >= self.split_duration_seconds - 0.05:
            messagebox.showwarning(APP_TITLE, "切点不能放在视频开头或结尾。")
            return
        if any(abs(existing - cut) < 0.1 for existing in self.split_cut_points):
            messagebox.showwarning(APP_TITLE, "这个位置附近已经有切点。")
            return
        self.split_cut_points.append(cut)
        self.split_cut_points.sort()
        self._refresh_cut_points()

    def _remove_selected_cut(self) -> None:
        selection = self.split_cut_list.curselection()
        if not selection:
            return
        index = selection[0]
        if 0 <= index < len(self.split_cut_points):
            del self.split_cut_points[index]
            self._refresh_cut_points()

    def _clear_split_cuts(self) -> None:
        self.split_cut_points.clear()
        self._refresh_cut_points()

    def _refresh_cut_points(self) -> None:
        self.split_cut_list.delete(0, END)
        for index, cut in enumerate(self.split_cut_points, start=1):
            self.split_cut_list.insert(END, f"{index}. {format_timestamp(cut)}")
        if self.split_duration_seconds > 0:
            self._write_split_lines_from_cuts()

    def _write_split_lines_from_cuts(self) -> None:
        source = Path(self.split_input.get())
        if self.split_duration_seconds <= 0 or not source.name:
            return
        ext = self.split_output_format.get()
        boundaries = [0.0, *self.split_cut_points, self.split_duration_seconds]
        lines = []
        stem = source.stem or "clip"
        for index, (start, end) in enumerate(zip(boundaries, boundaries[1:]), start=1):
            duration = max(0.0, end - start)
            if duration <= 0.05:
                continue
            lines.append(f"{format_timestamp(start)},{format_timestamp(duration)},{stem}_part{index:02d}{ext}")
        self.split_text.delete("1.0", END)
        self.split_text.insert("1.0", "\n".join(lines) + ("\n" if lines else ""))

    def _split_from_cuts(self) -> None:
        if not self.split_cut_points:
            messagebox.showwarning(APP_TITLE, "请先拖动进度条到目标位置，然后点击“切一刀”。")
            return
        self._write_split_lines_from_cuts()
        self._split_file()

    def _split_file(self) -> None:
        ffmpeg = self._require_ffmpeg()
        if not ffmpeg:
            return
        source = Path(self.split_input.get())
        if not source.exists():
            messagebox.showerror(APP_TITLE, "输入文件不存在。")
            return
        lines = [line.strip() for line in self.split_text.get("1.0", END).splitlines() if line.strip()]
        if not lines:
            messagebox.showwarning(APP_TITLE, "请至少添加一行片段配置。")
            return
        out_dir = Path(self.output_dir.get())
        out_dir.mkdir(parents=True, exist_ok=True)

        commands: list[list[str]] = []
        output_paths: list[Path] = []
        try:
            width = parse_positive_int(self.gif_width.get(), "GIF 宽度")
        except ValueError as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return

        for line_number, line in enumerate(lines, start=1):
            parts = [part.strip() for part in line.split(",")]
            if len(parts) != 3:
                messagebox.showerror(APP_TITLE, f"第 {line_number} 行格式必须为：开始时间,持续时间,输出文件名")
                return
            start, duration, name = parts
            if not start or not duration or not name:
                messagebox.showerror(APP_TITLE, f"第 {line_number} 行存在空值。")
                return
            target = out_dir / Path(name).name
            if target.suffix.lower() == ".gif":
                vf = (
                    f"fps=10,scale={width}:-1:flags=lanczos,"
                    "split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse=dither=bayer:bayer_scale=5"
                )
                commands.append([ffmpeg, "-y", "-ss", start, "-t", duration, "-i", str(source), "-vf", vf, str(target)])
                output_paths.append(target)
            elif target.suffix.lower() == ".mp4":
                commands.append(
                    [
                        ffmpeg,
                        "-y",
                        "-ss",
                        start,
                        "-t",
                        duration,
                        "-i",
                        str(source),
                        "-c:v",
                        "libx264",
                        "-preset",
                        "veryfast",
                        "-pix_fmt",
                        "yuv420p",
                        str(target),
                    ]
                )
                output_paths.append(target)
            else:
                messagebox.showerror(APP_TITLE, f"第 {line_number} 行的输出文件必须以 .mp4 或 .gif 结尾。")
                return

        self.split_export_outputs = output_paths
        self._start_split_export_progress(len(commands))
        self._run_command_sequence(
            commands,
            "正在拆分文件",
            on_progress=self._update_split_export_progress,
            on_done=self._split_export_finished,
        )

    def _start_split_export_progress(self, total: int) -> None:
        self.split_export_progress.set(0.0)
        self.split_export_bar.config(maximum=max(total, 1))
        self.split_export_status.set(f"准备导出 {total} 个片段...")

    def _update_split_export_progress(self, current: int, total: int, code: int | None) -> None:
        if code is None:
            self.split_export_status.set(f"正在导出第 {current}/{total} 个片段...")
            self.split_export_progress.set(max(current - 1, 0))
            return
        self.split_export_progress.set(current)
        if code == 0:
            self.split_export_status.set(f"已完成第 {current}/{total} 个片段。")
        else:
            self.split_export_status.set(f"第 {current}/{total} 个片段导出失败，请查看日志。")

    def _split_export_finished(self, success: bool) -> None:
        existing_outputs = [path for path in self.split_export_outputs if path.exists()]
        total = len(self.split_export_outputs)
        self.split_export_progress.set(len(existing_outputs))
        if success:
            self.split_export_status.set(f"导出完成：已生成 {len(existing_outputs)}/{total} 个文件。")
        else:
            self.split_export_status.set(f"导出未完全成功：已生成 {len(existing_outputs)}/{total} 个文件。")
        self._show_split_export_result(existing_outputs, success)

    def _show_split_export_result(self, output_paths: list[Path], success: bool) -> None:
        dialog = Toplevel(self)
        dialog.title("拆分导出完成" if success else "拆分导出未完全成功")
        dialog.configure(bg=COLOR_BG)
        dialog.transient(self)
        dialog.geometry("760x420")

        body = ttk.Frame(dialog, style="Card.TFrame", padding=18)
        body.pack(fill=BOTH, expand=True)
        title = "拆分导出完成" if success else "拆分导出未完全成功"
        ttk.Label(body, text=title, style="Card.TLabel", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
        ttk.Label(
            body,
            text=f"已生成 {len(output_paths)} 个文件。可打开目录查看，或选择文件后直接预览。",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(6, 10))

        file_list = Listbox(
            body,
            height=12,
            bg="white",
            fg=COLOR_TEXT,
            selectbackground=COLOR_ACCENT,
            selectforeground="white",
            relief="flat",
            highlightthickness=1,
            highlightbackground="#cbd5e1",
        )
        file_list.pack(fill=BOTH, expand=True)
        for path in output_paths:
            file_list.insert(END, str(path))

        buttons = ttk.Frame(body, style="Card.TFrame")
        buttons.pack(fill=X, pady=(12, 0))
        output_dir = output_paths[0].parent if output_paths else Path(self.output_dir.get())
        ttk.Button(buttons, text="打开输出目录", command=lambda: self._open_path(output_dir)).pack(side=LEFT, padx=(0, 8))
        ttk.Button(buttons, text="打开选中文件", command=lambda: self._open_selected_list_file(file_list)).pack(
            side=LEFT, padx=(0, 8)
        )
        ttk.Button(buttons, text="复制选中文件路径", command=lambda: self._copy_selected_list_file(file_list)).pack(
            side=LEFT, padx=(0, 8)
        )
        ttk.Button(buttons, text="关闭", command=dialog.destroy).pack(side=RIGHT)

    def _selected_list_file(self, file_list: Listbox) -> Path | None:
        selection = file_list.curselection()
        if not selection:
            messagebox.showwarning(APP_TITLE, "请先选择一个文件。")
            return None
        return Path(file_list.get(selection[0]))

    def _open_selected_list_file(self, file_list: Listbox) -> None:
        path = self._selected_list_file(file_list)
        if path:
            self._open_path(path)

    def _copy_selected_list_file(self, file_list: Listbox) -> None:
        path = self._selected_list_file(file_list)
        if not path:
            return
        self.clipboard_clear()
        self.clipboard_append(str(path))
        self.split_export_status.set("选中文件路径已复制到剪贴板。")

    def _run_command_sequence(
        self,
        commands: list[list[str]],
        label: str,
        on_progress: Callable[[int, int, int | None], None] | None = None,
        on_done: Callable[[bool], None] | None = None,
    ) -> None:
        if self.runner.busy:
            messagebox.showwarning(APP_TITLE, "已有任务正在运行。")
            return

        def worker() -> None:
            self.runner.busy = True
            self.log_queue.put(f"\n== {label}：{len(commands)} 个命令 ==\n")
            success = True
            try:
                for index, command in enumerate(commands, start=1):
                    if on_progress:
                        self.after(0, lambda i=index: on_progress(i, len(commands), None))
                    self.log_queue.put(f"\n-- 命令 {index}/{len(commands)} --\n")
                    self.log_queue.put(" ".join(command) + "\n")
                    proc = subprocess.Popen(
                        command,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        creationflags=CREATE_NO_WINDOW,
                    )
                    assert proc.stdout is not None
                    for line in proc.stdout:
                        self.log_queue.put(line)
                    code = proc.wait()
                    self.log_queue.put(f"-- 退出码 {code} --\n")
                    if on_progress:
                        self.after(0, lambda i=index, c=code: on_progress(i, len(commands), c))
                    if code != 0:
                        success = False
                        break
            except Exception as exc:
                success = False
                self.log_queue.put(f"导出命令执行失败：{exc}\n")
            finally:
                self.runner.busy = False
                self.log_queue.put(f"== {label}完成 ==\n")
                if on_done:
                    self.after(0, lambda ok=success: on_done(ok))

        threading.Thread(target=worker, daemon=True).start()

    def _drain_logs(self) -> None:
        while True:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log.insert(END, line)
            self.log.see(END)
        while True:
            try:
                callback, result = self.done_queue.get_nowait()
            except queue.Empty:
                break
            callback(result)
        self.after(100, self._drain_logs)


def main() -> int:
    app = ScreenGifStudio()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

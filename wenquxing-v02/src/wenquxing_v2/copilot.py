from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path


class CopilotError(RuntimeError):
    pass


class CopilotRunner:
    def __init__(self, executable: Path, work_dir: Path, timeout_seconds: int):
        self.executable = executable
        self.work_dir = work_dir
        self.timeout_seconds = timeout_seconds
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def ask(self, session_id: str, prompt: str) -> str:
        guarded_prompt = (
            "你是文曲星，一个通过飞书提供服务的个人对话助手。"
            "直接回答用户问题；不要调用工具、访问文件、执行命令或发起网络请求。"
            "如果用户要求执行外部操作，请说明当前只能提供文字回答。\n\n"
            f"用户消息：{prompt}"
        )
        command = [
            str(self.executable),
            "--prompt",
            guarded_prompt,
            f"--session-id={session_id}",
            "--silent",
            "--no-color",
            "--stream=off",
            "--no-auto-update",
            "--no-custom-instructions",
            "--disable-builtin-mcps",
            "--available-tools=",
            "--no-ask-user",
            "--no-remote",
            "--no-remote-export",
            "--disallow-temp-dir",
            "--log-level=error",
        ]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            result = subprocess.run(
                command,
                cwd=self.work_dir,
                env=self._safe_environment(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                creationflags=flags,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CopilotError(
                f"Copilot CLI 超过 {self.timeout_seconds} 秒未完成"
            ) from exc
        except OSError as exc:
            raise CopilotError(f"无法启动 Copilot CLI：{exc}") from exc

        answer = result.stdout.strip()
        if result.returncode != 0:
            detail = result.stderr.strip() or answer or "没有错误详情"
            raise CopilotError(
                f"Copilot CLI 退出码 {result.returncode}：{detail[:500]}"
            )
        if not answer:
            raise CopilotError("Copilot CLI 未返回内容")
        return answer

    def version(self) -> str:
        result = subprocess.run(
            [str(self.executable), "--version"],
            env=self._safe_environment(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            check=False,
        )
        if result.returncode != 0:
            raise CopilotError(result.stderr.strip() or "Copilot CLI version failed")
        return result.stdout.strip()

    def delete_session(self, session_id: str) -> None:
        copilot_home = Path(
            os.environ.get("USERPROFILE", str(Path.home()))
        ) / ".copilot"
        database = copilot_home / "session-store.db"
        if database.is_file():
            with sqlite3.connect(database, timeout=30) as db:
                db.execute("PRAGMA busy_timeout = 30000")
                db.execute("BEGIN IMMEDIATE")
                for table in (
                    "assistant_usage_events",
                    "checkpoints",
                    "forge_trajectory_events",
                    "session_files",
                    "session_refs",
                    "turns",
                    "search_index",
                ):
                    db.execute(
                        f"DELETE FROM {table} WHERE session_id = ?", (session_id,)
                    )
                db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        state_dir = copilot_home / "session-state" / session_id
        if state_dir.is_dir():
            shutil.rmtree(state_dir)

    @staticmethod
    def _safe_environment() -> dict[str, str]:
        allowed = {
            "APPDATA",
            "COMSPEC",
            "HOME",
            "HOMEDRIVE",
            "HOMEPATH",
            "LOCALAPPDATA",
            "PATH",
            "PATHEXT",
            "PROGRAMDATA",
            "PROGRAMFILES",
            "PROGRAMFILES(X86)",
            "SYSTEMDRIVE",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
            "USERDOMAIN",
            "USERNAME",
            "USERPROFILE",
            "WINDIR",
        }
        env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
        env.update(
            {
                "NO_COLOR": "1",
                "FORCE_COLOR": "0",
                "CI": "1",
            }
        )
        return env

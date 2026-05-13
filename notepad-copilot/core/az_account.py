"""Wrap the `az` CLI for login / cloud / subscription management.

All calls are synchronous and short-lived except `login()`, which spawns
the interactive browser flow in a detached process.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass


@dataclass
class AzAccount:
    user: str
    tenant_id: str
    subscription_id: str
    subscription_name: str
    cloud: str

    def short(self) -> str:
        return (
            f"{self.user} | {self.subscription_name} ({self.cloud})"
        )


class AzCli:
    """Thin wrapper around `az`. All methods raise RuntimeError on
    non-zero exit, except `current_account` which returns None when the
    user isn't logged in."""

    def __init__(self):
        self._exe = shutil.which("az") or shutil.which("az.cmd")

    @property
    def available(self) -> bool:
        return self._exe is not None

    # --- internal ------------------------------------------------------

    def _run(self, args: list[str], timeout: int = 30) -> str:
        if not self._exe:
            raise RuntimeError("az CLI 未找到，请先安装 Azure CLI。")
        argv = [self._exe, *args]
        creationflags = 0
        if os.name == "nt":
            creationflags = 0x08000000  # CREATE_NO_WINDOW
            # Windows: pass a properly-quoted command line string so that
            # paths with spaces (e.g. "Program Files (x86)") survive.
            cmdline = subprocess.list2cmdline(argv)
            popen_args: list | str = cmdline
            use_shell = True
        else:
            popen_args = argv
            use_shell = False
        try:
            res = subprocess.run(
                popen_args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                creationflags=creationflags,
                shell=use_shell,
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"az 调用超时: {' '.join(args)}") from e
        if res.returncode != 0:
            msg = (res.stderr or res.stdout or "").strip()
            raise RuntimeError(
                f"az {' '.join(args)} 失败 (exit {res.returncode}): {msg}"
            )
        return res.stdout

    # --- queries -------------------------------------------------------

    def current_account(self) -> AzAccount | None:
        if not self._exe:
            return None
        try:
            out = self._run([
                "account", "show",
                "--query",
                "{user:user.name, tenantId:tenantId, id:id, name:name,"
                " env:environmentName}",
                "-o", "json",
            ])
        except RuntimeError:
            return None
        data = json.loads(out)
        return AzAccount(
            user=data.get("user") or "",
            tenant_id=data.get("tenantId") or "",
            subscription_id=data.get("id") or "",
            subscription_name=data.get("name") or "",
            cloud=data.get("env") or "",
        )

    def list_subscriptions(self) -> list[dict]:
        out = self._run([
            "account", "list",
            "--query",
            "[].{id:id, name:name, tenantId:tenantId, isDefault:isDefault,"
            " state:state, user:user.name, cloud:cloudName}",
            "-o", "json",
        ])
        return json.loads(out) or []

    def list_clouds(self) -> list[str]:
        try:
            out = self._run([
                "cloud", "list", "--query", "[].name", "-o", "json"
            ])
            return json.loads(out) or []
        except RuntimeError:
            return ["AzureCloud", "AzureChinaCloud", "AzureUSGovernment"]

    def current_cloud(self) -> str:
        try:
            return self._run(["cloud", "show", "--query", "name", "-o", "tsv"]).strip()
        except RuntimeError:
            return ""

    # --- mutations -----------------------------------------------------

    def set_subscription(self, subscription_id: str) -> None:
        self._run(["account", "set", "--subscription", subscription_id])

    def set_cloud(self, cloud_name: str) -> None:
        # Switching cloud invalidates the active session; user will need
        # to `az login` again, which the GUI prompts for.
        self._run(["cloud", "set", "--name", cloud_name])

    def logout(self) -> None:
        try:
            self._run(["logout"])
        except RuntimeError:
            pass

    def spawn_login(self, tenant: str | None = None) -> subprocess.Popen:
        """Launch interactive `az login` (browser) in a detached process.

        Per project rules: never use --use-device-code.
        """
        if not self._exe:
            raise RuntimeError("az CLI 未找到。")
        argv = [self._exe, "login"]
        if tenant:
            argv.extend(["--tenant", tenant])
        if os.name == "nt":
            # Use a temp .bat so we avoid all the quoting/parens hazards
            # of inline cmd /c strings (the az.CMD path contains parens
            # and spaces, which collide with `() && ()` syntax).
            import tempfile
            inner = subprocess.list2cmdline(argv)
            bat = (
                "@echo off\r\n"
                "chcp 65001 >nul\r\n"
                f"call {inner}\r\n"
                "set RC=%ERRORLEVEL%\r\n"
                "echo.\r\n"
                "if %RC% EQU 0 (\r\n"
                "    echo === 登录成功，3 秒后窗口关闭 ===\r\n"
                "    timeout /t 3 >nul\r\n"
                ") else (\r\n"
                "    echo === 登录失败 (exit=%RC%) ===\r\n"
                "    echo 按任意键关闭窗口...\r\n"
                "    pause >nul\r\n"
                ")\r\n"
            )
            fd, path = tempfile.mkstemp(suffix=".bat", prefix="azlogin_")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(bat)
            except Exception:
                os.close(fd)
                raise
            return subprocess.Popen(
                ["cmd.exe", "/c", path],
                creationflags=0x00000010,  # CREATE_NEW_CONSOLE
                shell=False,
            )
        return subprocess.Popen(argv)

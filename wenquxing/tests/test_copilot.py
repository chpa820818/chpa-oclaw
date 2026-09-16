from __future__ import annotations

from pathlib import Path
import sqlite3
from types import SimpleNamespace

from wenquxing_v2.copilot import CopilotRunner


def test_safe_environment_does_not_expose_application_secrets(
    monkeypatch,
) -> None:
    monkeypatch.setenv("WX_APP_SECRET", "secret")
    monkeypatch.setenv("WX_APP_ID", "app")
    monkeypatch.setenv("PATH", "path")

    environment = CopilotRunner._safe_environment()

    assert environment["PATH"] == "path"
    assert "WX_APP_SECRET" not in environment
    assert "WX_APP_ID" not in environment


def test_delete_session_removes_only_target_copilot_data(
    tmp_path: Path, monkeypatch
) -> None:
    copilot_home = tmp_path / ".copilot"
    state_dir = copilot_home / "session-state" / "target"
    state_dir.mkdir(parents=True)
    (state_dir / "state.json").write_text("{}", encoding="utf-8")
    database = copilot_home / "session-store.db"
    tables = (
        "assistant_usage_events",
        "checkpoints",
        "forge_trajectory_events",
        "session_files",
        "session_refs",
        "turns",
        "search_index",
    )
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY)")
        for table in tables:
            db.execute(f"CREATE TABLE {table} (session_id TEXT)")
            db.executemany(
                f"INSERT INTO {table} VALUES (?)", [("target",), ("keep",)]
            )
        db.executemany("INSERT INTO sessions VALUES (?)", [("target",), ("keep",)])
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    runner = CopilotRunner(tmp_path / "copilot.exe", tmp_path / "work", 30)

    runner.delete_session("target")

    with sqlite3.connect(database) as db:
        assert db.execute("SELECT id FROM sessions").fetchall() == [("keep",)]
        for table in tables:
            assert db.execute(f"SELECT session_id FROM {table}").fetchall() == [
                ("keep",)
            ]
    assert not state_dir.exists()


def test_ask_passes_only_explicit_attachments(
    tmp_path: Path, monkeypatch
) -> None:
    captured: list[str] = []

    def fake_run(command, **_kwargs):
        captured.extend(command)
        return SimpleNamespace(returncode=0, stdout="已分析", stderr="")

    monkeypatch.setattr("wenquxing_v2.copilot.subprocess.run", fake_run)
    attachment = tmp_path / "report.txt"
    attachment.write_text("data", encoding="utf-8")
    runner = CopilotRunner(tmp_path / "copilot.exe", tmp_path / "work", 30)

    assert runner.ask("session", "分析", [attachment]) == "已分析"
    assert "--attachment" in captured
    assert str(attachment.resolve()) in captured
    assert "--allow-tool=write" in captured
    assert "--deny-tool=shell" in captured
    assert "--deny-url=*" in captured
    assert not any(item.startswith("--available-tools") for item in captured)
    assert "--allow-all-paths" not in captured

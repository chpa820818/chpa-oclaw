from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class Session:
    id: int
    public_id: str
    name: str
    copilot_session_id: str
    owner_open_id: str
    chat_id: str


@dataclass(frozen=True)
class Job:
    id: int
    message_id: str
    chat_id: str
    owner_open_id: str
    session_id: int | None
    prompt: str | None
    response_type: str
    response_content: str | None
    attempts: int


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    public_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    auto_name_pending INTEGER NOT NULL DEFAULT 0,
                    archived_at TEXT,
                    copilot_session_id TEXT NOT NULL UNIQUE,
                    owner_open_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(owner_open_id, chat_id, public_id),
                    UNIQUE(owner_open_id, chat_id, name)
                );

                CREATE TABLE IF NOT EXISTS current_sessions (
                    owner_open_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    session_id INTEGER NOT NULL REFERENCES sessions(id),
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(owner_open_id, chat_id)
                );

                CREATE TABLE IF NOT EXISTS inbox_messages (
                    message_id TEXT PRIMARY KEY,
                    owner_open_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    body TEXT NOT NULL,
                    received_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT NOT NULL UNIQUE REFERENCES inbox_messages(message_id),
                    owner_open_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    session_id INTEGER REFERENCES sessions(id),
                    prompt TEXT,
                    response_type TEXT NOT NULL DEFAULT 'text',
                    response_content TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT NOT NULL,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS jobs_ready
                    ON jobs(status, next_attempt_at, id);
                """
            )
            session_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(sessions)")
            }
            if "auto_name_pending" not in session_columns:
                db.execute(
                    """
                    ALTER TABLE sessions
                    ADD COLUMN auto_name_pending INTEGER NOT NULL DEFAULT 0
                    """
                )
            if "archived_at" not in session_columns:
                db.execute("ALTER TABLE sessions ADD COLUMN archived_at TEXT")
                db.execute(
                    """
                    UPDATE sessions
                    SET auto_name_pending = 1
                    WHERE name = '默认会话' OR name GLOB '会话 [0-9]*'
                    """
                )
            db.execute(
                "UPDATE jobs SET status = 'pending', updated_at = ? WHERE status = 'running'",
                (utc_now(),),
            )

    def accept_prompt(
        self, message_id: str, owner_open_id: str, chat_id: str, body: str
    ) -> bool:
        now = utc_now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM inbox_messages WHERE message_id = ?", (message_id,)
            ).fetchone():
                return False
            session = self._get_or_create_current(db, owner_open_id, chat_id)
            pending = db.execute(
                "SELECT auto_name_pending FROM sessions WHERE id = ?",
                (session.id,),
            ).fetchone()["auto_name_pending"]
            if pending:
                generated_name = self._unique_name(
                    db,
                    owner_open_id,
                    chat_id,
                    self._name_from_prompt(body),
                )
                db.execute(
                    """
                    UPDATE sessions
                    SET name = ?, auto_name_pending = 0, updated_at = ?
                    WHERE id = ?
                    """,
                    (generated_name, now, session.id),
                )
            db.execute(
                """
                INSERT INTO inbox_messages
                    (message_id, owner_open_id, chat_id, body, received_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (message_id, owner_open_id, chat_id, body, now),
            )
            db.execute(
                """
                INSERT INTO jobs
                    (message_id, owner_open_id, chat_id, session_id, prompt,
                     next_attempt_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    owner_open_id,
                    chat_id,
                    session.id,
                    body,
                    now,
                    now,
                    now,
                ),
            )
        return True

    def accept_local_response(
        self,
        message_id: str,
        owner_open_id: str,
        chat_id: str,
        body: str,
        response_type: str,
        response_content: str,
    ) -> bool:
        now = utc_now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM inbox_messages WHERE message_id = ?", (message_id,)
            ).fetchone():
                return False
            db.execute(
                """
                INSERT INTO inbox_messages
                    (message_id, owner_open_id, chat_id, body, received_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (message_id, owner_open_id, chat_id, body, now),
            )
            db.execute(
                """
                INSERT INTO jobs
                    (message_id, owner_open_id, chat_id, response_type,
                     response_content, next_attempt_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    owner_open_id,
                    chat_id,
                    response_type,
                    response_content,
                    now,
                    now,
                    now,
                ),
            )
        return True

    def is_received(self, message_id: str) -> bool:
        with self.connect() as db:
            return (
                db.execute(
                    "SELECT 1 FROM inbox_messages WHERE message_id = ?", (message_id,)
                ).fetchone()
                is not None
            )

    def current_session(self, owner_open_id: str, chat_id: str) -> Session:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._get_or_create_current(db, owner_open_id, chat_id)

    def list_sessions(self, owner_open_id: str, chat_id: str) -> list[Session]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT s.*
                FROM sessions AS s
                WHERE s.owner_open_id = ? AND s.chat_id = ?
                  AND s.archived_at IS NULL
                ORDER BY s.updated_at DESC, s.id DESC
                """,
                (owner_open_id, chat_id),
            ).fetchall()
        return [self._session(row) for row in rows]

    def list_archived_sessions(
        self, owner_open_id: str, chat_id: str
    ) -> list[Session]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT * FROM sessions
                WHERE owner_open_id = ? AND chat_id = ?
                  AND archived_at IS NOT NULL
                ORDER BY archived_at DESC, id DESC
                """,
                (owner_open_id, chat_id),
            ).fetchall()
        return [self._session(row) for row in rows]

    def create_session(
        self, owner_open_id: str, chat_id: str, name: str | None = None
    ) -> Session:
        now = utc_now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            number = self._next_number(db, owner_open_id, chat_id)
            public_id = f"C-{number:04d}"
            session_name = self._unique_name(
                db, owner_open_id, chat_id, (name or f"会话 {number}").strip()
            )
            auto_name_pending = 1 if name is None else 0
            cursor = db.execute(
                """
                INSERT INTO sessions
                    (public_id, name, auto_name_pending, copilot_session_id,
                     owner_open_id, chat_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    public_id,
                    session_name,
                    auto_name_pending,
                    str(uuid.uuid4()),
                    owner_open_id,
                    chat_id,
                    now,
                    now,
                ),
            )
            session_id = int(cursor.lastrowid)
            self._set_current(db, owner_open_id, chat_id, session_id, now)
            row = db.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return self._session(row)

    def switch_session(
        self, owner_open_id: str, chat_id: str, selector: str
    ) -> Session | None:
        selector = selector.strip()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """
                SELECT * FROM sessions
                WHERE owner_open_id = ? AND chat_id = ?
                  AND archived_at IS NULL
                  AND (public_id = ? COLLATE NOCASE OR name = ? COLLATE NOCASE)
                """,
                (owner_open_id, chat_id, selector, selector),
            ).fetchone()
            if row is None:
                return None
            now = utc_now()
            self._set_current(db, owner_open_id, chat_id, row["id"], now)
            db.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?", (now, row["id"])
            )
        return self._session(row)

    def find_session(
        self, owner_open_id: str, chat_id: str, selector: str
    ) -> Session | None:
        with self.connect() as db:
            row = db.execute(
                """
                SELECT * FROM sessions
                WHERE owner_open_id = ? AND chat_id = ?
                  AND (public_id = ? COLLATE NOCASE OR name = ? COLLATE NOCASE)
                """,
                (owner_open_id, chat_id, selector.strip(), selector.strip()),
            ).fetchone()
        return self._session(row) if row is not None else None

    def archive_session(
        self, owner_open_id: str, chat_id: str, selector: str
    ) -> Session | None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            target = self._find_session_row(
                db, owner_open_id, chat_id, selector, archived=False
            )
            if target is None:
                return None
            was_current = db.execute(
                "SELECT 1 FROM current_sessions WHERE session_id = ?",
                (target["id"],),
            ).fetchone()
            now = utc_now()
            db.execute(
                "UPDATE sessions SET archived_at = ?, updated_at = ? WHERE id = ?",
                (now, now, target["id"]),
            )
            if was_current:
                self._ensure_active_current(
                    db, owner_open_id, chat_id, excluded_id=target["id"]
                )
        return self._session(target)

    def restore_session(
        self, owner_open_id: str, chat_id: str, selector: str
    ) -> Session | None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            target = self._find_session_row(
                db, owner_open_id, chat_id, selector, archived=True
            )
            if target is None:
                return None
            was_current = db.execute(
                "SELECT 1 FROM current_sessions WHERE session_id = ?",
                (target["id"],),
            ).fetchone()
            now = utc_now()
            db.execute(
                "UPDATE sessions SET archived_at = NULL, updated_at = ? WHERE id = ?",
                (now, target["id"]),
            )
            self._set_current(db, owner_open_id, chat_id, target["id"], now)
            row = db.execute(
                "SELECT * FROM sessions WHERE id = ?", (target["id"],)
            ).fetchone()
        return self._session(row)

    def permanently_delete_session(
        self, owner_open_id: str, chat_id: str, selector: str
    ) -> Session | None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            target = self._find_session_row(
                db, owner_open_id, chat_id, selector, archived=None
            )
            if target is None:
                return None
            was_current = db.execute(
                "SELECT 1 FROM current_sessions WHERE session_id = ?",
                (target["id"],),
            ).fetchone()
            running = db.execute(
                "SELECT 1 FROM jobs WHERE session_id = ? AND status = 'running'",
                (target["id"],),
            ).fetchone()
            if running:
                raise RuntimeError("该会话仍在处理消息，请稍后再删除")
            message_ids = [
                row["message_id"]
                for row in db.execute(
                    "SELECT message_id FROM jobs WHERE session_id = ?",
                    (target["id"],),
                )
            ]
            db.execute("DELETE FROM jobs WHERE session_id = ?", (target["id"],))
            if message_ids:
                placeholders = ",".join("?" for _ in message_ids)
                db.execute(
                    f"DELETE FROM inbox_messages WHERE message_id IN ({placeholders})",
                    message_ids,
                )
            db.execute(
                "DELETE FROM current_sessions WHERE session_id = ?", (target["id"],)
            )
            db.execute("DELETE FROM sessions WHERE id = ?", (target["id"],))
            if was_current:
                self._ensure_active_current(db, owner_open_id, chat_id)
        return self._session(target)

    def rename_current(
        self, owner_open_id: str, chat_id: str, name: str
    ) -> Session:
        current = self.current_session(owner_open_id, chat_id)
        renamed = self.rename_session(
            owner_open_id, chat_id, current.public_id, name
        )
        if renamed is None:
            raise RuntimeError("当前会话不存在")
        return renamed

    def rename_session(
        self, owner_open_id: str, chat_id: str, selector: str, name: str
    ) -> Session | None:
        name = name.strip()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            target = db.execute(
                """
                SELECT * FROM sessions
                WHERE owner_open_id = ? AND chat_id = ?
                  AND archived_at IS NULL
                  AND (public_id = ? COLLATE NOCASE OR name = ? COLLATE NOCASE)
                """,
                (owner_open_id, chat_id, selector, selector),
            ).fetchone()
            if target is None:
                return None
            existing = db.execute(
                """
                SELECT 1 FROM sessions
                WHERE owner_open_id = ? AND chat_id = ? AND name = ? COLLATE NOCASE
                  AND id <> ?
                """,
                (owner_open_id, chat_id, name, target["id"]),
            ).fetchone()
            if existing:
                raise ValueError("会话名称已存在")
            now = utc_now()
            db.execute(
                """
                UPDATE sessions
                SET name = ?, auto_name_pending = 0, updated_at = ?
                WHERE id = ?
                """,
                (name, now, target["id"]),
            )
            row = db.execute(
                "SELECT * FROM sessions WHERE id = ?", (target["id"],)
            ).fetchone()
        return self._session(row)

    def clear_current(self, owner_open_id: str, chat_id: str) -> Session:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._get_or_create_current(db, owner_open_id, chat_id)
            now = utc_now()
            db.execute(
                """
                UPDATE sessions
                SET copilot_session_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (str(uuid.uuid4()), now, current.id),
            )
            row = db.execute(
                "SELECT * FROM sessions WHERE id = ?", (current.id,)
            ).fetchone()
        return self._session(row)

    def get_session(self, session_id: int) -> Session:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"session {session_id} not found")
        return self._session(row)

    def claim_jobs(self, limit: int) -> list[Job]:
        now = utc_now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                """
                SELECT candidate.*
                FROM jobs AS candidate
                WHERE candidate.status IN ('pending', 'ready')
                  AND candidate.next_attempt_at <= ?
                  AND NOT EXISTS (
                      SELECT 1
                      FROM jobs AS earlier
                      WHERE earlier.id < candidate.id
                        AND earlier.status IN ('pending', 'ready', 'running')
                        AND (
                            (earlier.session_id = candidate.session_id)
                            OR (
                                earlier.session_id IS NULL
                                AND candidate.session_id IS NULL
                                AND earlier.owner_open_id = candidate.owner_open_id
                                AND earlier.chat_id = candidate.chat_id
                            )
                        )
                  )
                ORDER BY candidate.id
                LIMIT ?
                """,
                (now, limit),
            ).fetchall()
            ids = [row["id"] for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                db.execute(
                    f"""
                    UPDATE jobs
                    SET status = 'running', attempts = attempts + 1, updated_at = ?
                    WHERE id IN ({placeholders})
                    """,
                    (now, *ids),
                )
        return [self._job(row) for row in rows]

    def save_response(self, job_id: int, content: str) -> None:
        with self.connect() as db:
            db.execute(
                """
                UPDATE jobs
                SET response_type = 'text', response_content = ?,
                    status = 'ready', updated_at = ?, last_error = NULL
                WHERE id = ?
                """,
                (content, utc_now(), job_id),
            )

    def complete_job(self, job_id: int) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET status = 'done', updated_at = ? WHERE id = ?",
                (utc_now(), job_id),
            )

    def retry_job(self, job_id: int, attempts: int, error: str) -> None:
        if attempts >= 5:
            status = "failed"
            next_attempt = utc_now()
        else:
            status = "ready"
            delay = min(60, 2 ** max(0, attempts - 1))
            next_attempt = (datetime.now(UTC) + timedelta(seconds=delay)).isoformat()
        with self.connect() as db:
            db.execute(
                """
                UPDATE jobs
                SET status = ?, next_attempt_at = ?, last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, next_attempt, error[:1000], utc_now(), job_id),
            )

    def status_counts(self) -> dict[str, int]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT status, COUNT(*) AS count FROM jobs GROUP BY status"
            ).fetchall()
        return {row["status"]: row["count"] for row in rows}

    def _get_or_create_current(
        self, db: sqlite3.Connection, owner_open_id: str, chat_id: str
    ) -> Session:
        row = db.execute(
            """
            SELECT s.*
            FROM current_sessions AS c
            JOIN sessions AS s ON s.id = c.session_id
            WHERE c.owner_open_id = ? AND c.chat_id = ?
              AND s.archived_at IS NULL
            """,
            (owner_open_id, chat_id),
        ).fetchone()
        if row:
            return self._session(row)
        return self._ensure_active_current(db, owner_open_id, chat_id)

    def _ensure_active_current(
        self,
        db: sqlite3.Connection,
        owner_open_id: str,
        chat_id: str,
        excluded_id: int | None = None,
    ) -> Session:
        query = """
            SELECT * FROM sessions
            WHERE owner_open_id = ? AND chat_id = ?
              AND archived_at IS NULL
        """
        params: list[object] = [owner_open_id, chat_id]
        if excluded_id is not None:
            query += " AND id <> ?"
            params.append(excluded_id)
        query += " ORDER BY updated_at DESC, id DESC LIMIT 1"
        row = db.execute(query, params).fetchone()
        if row is None:
            now = utc_now()
            number = self._next_number(db, owner_open_id, chat_id)
            cursor = db.execute(
                """
                INSERT INTO sessions
                    (public_id, name, auto_name_pending, copilot_session_id,
                     owner_open_id, chat_id, created_at, updated_at)
                VALUES (?, '新会话', 1, ?, ?, ?, ?, ?)
                """,
                (
                    f"C-{number:04d}",
                    str(uuid.uuid4()),
                    owner_open_id,
                    chat_id,
                    now,
                    now,
                ),
            )
            row = db.execute(
                "SELECT * FROM sessions WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        self._set_current(db, owner_open_id, chat_id, row["id"], utc_now())
        return self._session(row)

    @staticmethod
    def _find_session_row(
        db: sqlite3.Connection,
        owner_open_id: str,
        chat_id: str,
        selector: str,
        archived: bool | None,
    ) -> sqlite3.Row | None:
        query = """
            SELECT * FROM sessions
            WHERE owner_open_id = ? AND chat_id = ?
              AND (public_id = ? COLLATE NOCASE OR name = ? COLLATE NOCASE)
        """
        if archived is True:
            query += " AND archived_at IS NOT NULL"
        elif archived is False:
            query += " AND archived_at IS NULL"
        return db.execute(
            query, (owner_open_id, chat_id, selector.strip(), selector.strip())
        ).fetchone()

    @staticmethod
    def _set_current(
        db: sqlite3.Connection,
        owner_open_id: str,
        chat_id: str,
        session_id: int,
        now: str,
    ) -> None:
        db.execute(
            """
            INSERT INTO current_sessions
                (owner_open_id, chat_id, session_id, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(owner_open_id, chat_id) DO UPDATE SET
                session_id = excluded.session_id,
                updated_at = excluded.updated_at
            """,
            (owner_open_id, chat_id, session_id, now),
        )

    @staticmethod
    def _next_number(
        db: sqlite3.Connection, owner_open_id: str, chat_id: str
    ) -> int:
        row = db.execute(
            """
            SELECT MAX(
                COALESCE((
                    SELECT MAX(CAST(SUBSTR(public_id, 3) AS INTEGER))
                    FROM sessions
                    WHERE owner_open_id = ? AND chat_id = ?
                ), 0),
                COALESCE((
                    SELECT seq FROM sqlite_sequence WHERE name = 'sessions'
                ), 0)
            ) + 1 AS n
            """,
            (owner_open_id, chat_id),
        ).fetchone()
        return int(row["n"])

    @staticmethod
    def _unique_name(
        db: sqlite3.Connection, owner_open_id: str, chat_id: str, base: str
    ) -> str:
        base = base[:40] or "新会话"
        candidate = base
        suffix = 2
        while db.execute(
            """
            SELECT 1 FROM sessions
            WHERE owner_open_id = ? AND chat_id = ? AND name = ? COLLATE NOCASE
            """,
            (owner_open_id, chat_id, candidate),
        ).fetchone():
            marker = f" ({suffix})"
            candidate = base[: 40 - len(marker)] + marker
            suffix += 1
        return candidate

    @staticmethod
    def _name_from_prompt(prompt: str) -> str:
        name = " ".join(prompt.split()).strip(" \t\r\n，。！？!?：:；;、")
        if not name:
            return "新会话"
        return name[:24] + ("…" if len(name) > 24 else "")

    @staticmethod
    def _session(row: sqlite3.Row) -> Session:
        return Session(
            id=row["id"],
            public_id=row["public_id"],
            name=row["name"],
            copilot_session_id=row["copilot_session_id"],
            owner_open_id=row["owner_open_id"],
            chat_id=row["chat_id"],
        )

    @staticmethod
    def _job(row: sqlite3.Row) -> Job:
        return Job(
            id=row["id"],
            message_id=row["message_id"],
            chat_id=row["chat_id"],
            owner_open_id=row["owner_open_id"],
            session_id=row["session_id"],
            prompt=row["prompt"],
            response_type=row["response_type"],
            response_content=row["response_content"],
            attempts=row["attempts"] + 1,
        )

"""Case follow-up store: per-case directory with notes, chat log, archives."""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

# Default fallback root (used only when user hasn't picked one yet):
# repo_root / copilot-workspace / reports / cases
_THIS = Path(__file__).resolve()
_REPO_ROOT = _THIS.parents[4]
DEFAULT_CASE_ROOT = _REPO_ROOT / "copilot-workspace" / "reports" / "cases"

# User settings file (case_root + future settings)
SETTINGS_DIR = Path(os.path.expanduser("~")) / ".copilot" / "notepad-copilot"
SETTINGS_FILE = SETTINGS_DIR / "settings.json"


def _load_settings() -> dict:
    if SETTINGS_FILE.is_file():
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_settings(d: dict) -> None:
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(
        json.dumps(d, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def is_case_root_configured() -> bool:
    """True if the user has explicitly chosen a case root."""
    raw = _load_settings().get("case_root")
    return bool(raw) and Path(raw).is_dir()


def get_case_root() -> Path:
    """Return the active case root (user-configured or legacy default)."""
    raw = _load_settings().get("case_root")
    if raw:
        return Path(raw)
    return DEFAULT_CASE_ROOT


def set_case_root(path: str | Path) -> Path:
    """Persist a new case root. Creates the directory if missing."""
    p = Path(path).expanduser()
    try:
        p = p.resolve()
    except Exception:
        pass
    p.mkdir(parents=True, exist_ok=True)
    settings = _load_settings()
    settings["case_root"] = str(p)
    _save_settings(settings)
    return p


# Backwards-compatible module attribute. Note: this is a snapshot at import
# time; code that needs the *current* root should call get_case_root().
CASE_ROOT = get_case_root()

_SAFE_RE = re.compile(r"[^A-Za-z0-9._\-]+")


def sanitize_case_id(raw: str) -> str:
    """Make `raw` safe for use as a directory name."""
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("case id 不能为空")
    cleaned = _SAFE_RE.sub("-", raw).strip("-._")
    if not cleaned:
        raise ValueError(f"case id '{raw}' 处理后为空，请使用字母/数字/-_")
    return cleaned[:80]


@dataclass
class Case:
    case_id: str
    root: Path
    title: str = ""
    created: str = ""
    updated: str = ""

    # ---- standard sub-paths ----
    @property
    def meta_path(self) -> Path:
        return self.root / "case.json"

    @property
    def note_path(self) -> Path:
        return self.root / "note.md"

    @property
    def attachments_dir(self) -> Path:
        # Must match markdown_io.ATTACHMENTS_DIR (".attachments") so that
        # save_document() and load_document() find the same folder. Older
        # versions used "note.attachments" — leftover empty dirs are harmless.
        return self.root / ".attachments"

    @property
    def chat_log_path(self) -> Path:
        return self.root / "chat-history.jsonl"

    @property
    def archives_dir(self) -> Path:
        return self.root / "archives"

    # ---- meta ----
    def save_meta(self):
        self.updated = _dt.datetime.now().isoformat(timespec="seconds")
        self.meta_path.write_text(
            json.dumps(
                {
                    "case_id": self.case_id,
                    "title": self.title,
                    "created": self.created,
                    "updated": self.updated,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def touch(self):
        """Update `updated` timestamp without rewriting other fields."""
        try:
            self.save_meta()
        except Exception:
            pass

    # ---- chat log ----
    def append_qa(self, question: str, answer: str,
                  ts: str | None = None) -> None:
        ts = ts or _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rec = {"ts": ts, "q": question, "a": answer}
        with self.chat_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.touch()

    def read_qa(self) -> list[tuple[str, str, str]]:
        """Return (ts_short, q, a) tuples in file order."""
        if not self.chat_log_path.is_file():
            return []
        out: list[tuple[str, str, str]] = []
        for line in self.chat_log_path.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            ts = str(rec.get("ts", ""))
            ts_short = ts.split(" ")[-1][:8] if " " in ts else ts[:8]
            out.append((ts_short, str(rec.get("q", "")),
                        str(rec.get("a", ""))))
        return out


# --------------------------------------------------------------------------
# Module-level helpers
# --------------------------------------------------------------------------

def _ensure_dirs(case: Case):
    case.root.mkdir(parents=True, exist_ok=True)
    case.attachments_dir.mkdir(parents=True, exist_ok=True)
    case.archives_dir.mkdir(parents=True, exist_ok=True)


def create_case(raw_id: str, title: str = "") -> Case:
    case_id = sanitize_case_id(raw_id)
    root = get_case_root() / case_id
    if root.exists():
        raise FileExistsError(f"案例 '{case_id}' 已存在: {root}")
    now = _dt.datetime.now().isoformat(timespec="seconds")
    case = Case(case_id=case_id, root=root, title=title or case_id,
                created=now, updated=now)
    _ensure_dirs(case)
    case.save_meta()
    if not case.note_path.exists():
        case.note_path.write_text(
            f"# {case.title}\n\n_案例创建于 {now}_\n\n",
            encoding="utf-8",
        )
    return case


def open_case(case_id_or_path: str | Path) -> Case:
    """Open by id (under current case root) or by absolute path."""
    p = Path(case_id_or_path)
    if p.is_absolute() and p.exists():
        root = p
        case_id = root.name
    else:
        case_id = sanitize_case_id(str(case_id_or_path))
        root = get_case_root() / case_id
    if not root.is_dir():
        raise FileNotFoundError(f"案例不存在: {root}")
    title = case_id
    created = ""
    updated = ""
    meta_path = root / "case.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            title = meta.get("title", case_id)
            created = meta.get("created", "")
            updated = meta.get("updated", "")
        except Exception:
            pass
    case = Case(case_id=case_id, root=root, title=title,
                created=created, updated=updated)
    _ensure_dirs(case)
    return case


def list_cases() -> list[Case]:
    root = get_case_root()
    if not root.is_dir():
        return []
    out: list[Case] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        try:
            out.append(open_case(child))
        except Exception:
            continue
    # most recently updated first
    out.sort(key=lambda c: c.updated or "", reverse=True)
    return out

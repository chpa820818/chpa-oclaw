from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileState:
    size: int
    modified_ns: int


def snapshot(root: Path) -> dict[Path, FileState]:
    result: dict[Path, FileState] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
            result[path.resolve()] = FileState(stat.st_size, stat.st_mtime_ns)
        except OSError:
            continue
    return result


def output_files(
    root: Path,
    max_files: int = 5,
    max_bytes: int = 30 * 1024 * 1024,
) -> list[Path]:
    if not root.is_dir():
        return []
    files: list[Path] = []
    for path, state in snapshot(root).items():
        if state.size > max_bytes:
            continue
        files.append(path)
    files.sort(key=lambda item: (item.parent, item.name))
    return files[:max_files]

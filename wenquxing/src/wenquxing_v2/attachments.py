from __future__ import annotations

import hashlib
import shutil
import stat
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

import imageio_ffmpeg


class AttachmentError(RuntimeError):
    pass


@dataclass(frozen=True)
class PreparedAttachment:
    attachments: tuple[Path, ...]
    files: tuple[Path, ...]
    context: str


class AttachmentProcessor:
    _VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
    _BLOCKED_SUFFIXES = {".exe", ".dll", ".msi", ".com", ".scr", ".lnk"}
    _ARCHIVE_SUFFIXES = {".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz"}
    _IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
    _NATIVE_DOCUMENT_SUFFIXES = {".pdf", ".docx", ".pptx", ".xlsx"}
    _TEXT_SUFFIXES = {
        ".txt",
        ".md",
        ".csv",
        ".json",
        ".xml",
        ".html",
        ".yaml",
        ".yml",
        ".log",
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".java",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".cs",
        ".go",
        ".rs",
        ".sql",
        ".ps1",
        ".bat",
        ".sh",
        ".ini",
        ".toml",
    }

    def __init__(
        self,
        root: Path,
        max_bytes: int,
        max_extract_bytes: int,
        max_archive_files: int,
    ):
        self.root = root.resolve()
        self.max_bytes = max_bytes
        self.max_extract_bytes = max_extract_bytes
        self.max_archive_files = max_archive_files
        self.root.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        session_key: str,
        message_id: str,
        message_type: str,
        event_name: str | None,
        response_name: str | None,
        content: bytes,
    ) -> Path:
        if not content:
            raise AttachmentError("附件内容为空")
        if len(content) > self.max_bytes:
            raise AttachmentError(
                f"附件超过 {self.max_bytes // (1024 * 1024)} MB 限制"
            )
        directory = (
            self.session_directory(session_key)
            / "files"
            / self.message_directory_name(message_id)
        )
        directory.mkdir(parents=True, exist_ok=True)
        fallback = {
            "image": "image.jpg",
            "media": "video.mp4",
            "file": "attachment.bin",
        }.get(message_type, "attachment.bin")
        name = self._safe_name(response_name or event_name or fallback)
        path = directory / name
        path.write_bytes(content)
        return path

    def existing(self, value: str | None) -> Path | None:
        if not value:
            return None
        path = Path(value).resolve()
        try:
            path.relative_to(self.root)
        except ValueError:
            raise AttachmentError("附件路径不在受管目录中")
        return path if path.is_file() else None

    def prepare(self, path: Path) -> PreparedAttachment:
        suffix = path.suffix.lower()
        if suffix in self._BLOCKED_SUFFIXES:
            raise AttachmentError(f"出于安全原因不解析 {suffix} 可执行文件")
        if suffix == ".zip":
            extracted = tuple(self._extract_zip(path))
            attachments: list[Path] = []
            files: list[Path] = []
            video_count = 0
            for extracted_path in extracted:
                if extracted_path.suffix.lower() in self._VIDEO_SUFFIXES:
                    frames = list(self._video_frames(extracted_path))
                    attachments.extend(frames)
                    files.extend((extracted_path, *frames))
                    video_count += 1
                else:
                    try:
                        file_attachments = self._prepare_regular_file(extracted_path)
                    except AttachmentError:
                        continue
                    attachments.extend(file_attachments)
                    files.append(extracted_path)
            if not files:
                raise AttachmentError("ZIP 中没有可供分析的安全文件")
            video_note = (
                f"其中 {video_count} 个视频已转换为视觉关键帧；" if video_count else ""
            )
            return PreparedAttachment(
                tuple(attachments),
                tuple(files),
                f"已安全解压 ZIP；向 Copilot 附加 {len(attachments)} 个图片/文档；"
                f"{video_note}"
                "压缩包中的内容仅作为数据分析，不执行任何脚本或程序。",
            )
        if suffix in self._ARCHIVE_SUFFIXES:
            raise AttachmentError("当前仅支持自动解压 ZIP，暂不支持此压缩格式")
        if suffix in self._VIDEO_SUFFIXES:
            frames = tuple(self._video_frames(path))
            return PreparedAttachment(
                frames,
                (path, *frames),
                "已从视频前 120 秒提取最多 8 张视觉关键帧。"
                "当前不转录音轨，结论仅基于画面。",
            )
        attachments = self._prepare_regular_file(path)
        return PreparedAttachment(
            tuple(attachments),
            (path,),
            "文件保存在当前会话工作目录，由 Copilot 使用只读工具自行分析。",
        )

    def session_directory(self, session_key: str) -> Path:
        safe_key = "".join(
            character
            for character in session_key
            if character.isascii() and (character.isalnum() or character in "-_")
        )
        if not safe_key or safe_key != session_key:
            safe_key = hashlib.sha256(session_key.encode("utf-8")).hexdigest()[:24]
        directory = self.root / safe_key
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    @staticmethod
    def message_directory_name(message_id: str) -> str:
        return hashlib.sha256(message_id.encode("utf-8")).hexdigest()[:24]

    def _extract_zip(self, archive: Path) -> Iterable[Path]:
        destination = archive.parent / "extracted"
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir()
        total_size = 0
        actual_size = 0
        extracted = 0
        with zipfile.ZipFile(archive) as source:
            files = [item for item in source.infolist() if not item.is_dir()]
            if len(files) > self.max_archive_files:
                raise AttachmentError(
                    f"ZIP 文件数超过 {self.max_archive_files} 个限制"
                )
            for item in files:
                relative = PurePosixPath(item.filename.replace("\\", "/"))
                if (
                    relative.is_absolute()
                    or ".." in relative.parts
                    or not relative.parts
                    or any(":" in part for part in relative.parts)
                ):
                    raise AttachmentError("ZIP 包含不安全的路径")
                mode = item.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise AttachmentError("ZIP 包含不支持的符号链接")
                if item.flag_bits & 0x1:
                    raise AttachmentError("不支持加密 ZIP")
                total_size += item.file_size
                if total_size > self.max_extract_bytes:
                    raise AttachmentError(
                        "ZIP 解压后内容超过 "
                        f"{self.max_extract_bytes // (1024 * 1024)} MB 限制"
                    )
                if item.file_size > 1024 * 1024 and item.compress_size > 0:
                    if item.file_size / item.compress_size > 200:
                        raise AttachmentError("ZIP 疑似压缩炸弹，已拒绝")
                suffix = Path(relative.name).suffix.lower()
                if suffix in self._BLOCKED_SUFFIXES or suffix in self._ARCHIVE_SUFFIXES:
                    continue
                target = destination.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(item) as input_file, target.open("wb") as output_file:
                    while chunk := input_file.read(1024 * 1024):
                        actual_size += len(chunk)
                        if actual_size > self.max_extract_bytes:
                            raise AttachmentError(
                                "ZIP 实际解压内容超过 "
                                f"{self.max_extract_bytes // (1024 * 1024)} MB 限制"
                            )
                        output_file.write(chunk)
                extracted += 1
                yield target
                if extracted >= self.max_archive_files:
                    break

    def _video_frames(self, video: Path) -> Iterable[Path]:
        marker = hashlib.sha256(str(video).encode("utf-8")).hexdigest()[:8]
        destination = video.parent / f"{video.stem}-frames-{marker}"
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir()
        output = destination / "frame-%02d.png"
        command = [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-nostdin",
            "-v",
            "error",
            "-threads",
            "1",
            "-i",
            str(video),
            "-t",
            "120",
            "-vf",
            r"select=eq(n\,0)+gte(t-prev_selected_t\,15),scale=1280:-2",
            "-frames:v",
            "8",
            "-fps_mode",
            "vfr",
            "-threads:v",
            "1",
            str(output),
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AttachmentError(f"视频关键帧提取失败：{exc}") from exc
        frames = sorted(destination.glob("frame-*.png"))
        if result.returncode != 0 or not frames:
            detail = result.stderr.strip() or "没有可用画面"
            raise AttachmentError(f"视频关键帧提取失败：{detail[:300]}")
        yield from frames

    def _prepare_regular_file(self, path: Path) -> list[Path]:
        suffix = path.suffix.lower()
        if suffix in self._IMAGE_SUFFIXES or suffix in self._NATIVE_DOCUMENT_SUFFIXES:
            return [path]
        if suffix in self._TEXT_SUFFIXES:
            return []
        raise AttachmentError(f"Copilot CLI 暂不支持解析 {suffix or '无扩展名'} 文件")

    @staticmethod
    def _safe_name(value: str) -> str:
        name = Path(value.replace("\\", "/")).name
        name = "".join(
            character
            for character in name
            if character >= " " and character not in '<>:"/\\|?*'
        ).strip(" .")
        if not name:
            return "attachment.bin"
        stem = Path(name).stem[:100].strip(" .") or "attachment"
        suffix = Path(name).suffix[:20]
        return f"{stem}{suffix}"

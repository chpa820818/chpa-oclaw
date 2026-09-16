from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

import imageio_ffmpeg
import pytest

from wenquxing_v2.attachments import AttachmentError, AttachmentProcessor
from wenquxing_v2.feishu import _attachment_details


def processor(tmp_path: Path) -> AttachmentProcessor:
    return AttachmentProcessor(
        tmp_path / "attachments",
        max_bytes=1024 * 1024,
        max_extract_bytes=2 * 1024 * 1024,
        max_archive_files=12,
    )


def test_save_sanitizes_name_and_enforces_size(tmp_path: Path) -> None:
    attachments = processor(tmp_path)

    saved = attachments.save(
        "C-0001", "m1", "file", "../../report.txt", None, b"hello"
    )

    assert saved.name == "report.txt"
    assert saved.read_bytes() == b"hello"
    with pytest.raises(AttachmentError, match="超过"):
        attachments.save(
            "C-0001",
            "m2",
            "file",
            "large.bin",
            None,
            b"x" * (1024 * 1024 + 1),
        )


def test_zip_is_extracted_without_executables(tmp_path: Path) -> None:
    attachments = processor(tmp_path)
    archive = tmp_path / "safe.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("docs/readme.md", "# hello")
        output.writestr("bin/program.exe", b"MZ")

    prepared = attachments.prepare(archive)

    assert prepared.attachments == ()
    assert [path.name for path in prepared.files] == ["readme.md"]


def test_zip_rejects_path_traversal(tmp_path: Path) -> None:
    attachments = processor(tmp_path)
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("../outside.txt", "no")

    with pytest.raises(AttachmentError, match="不安全"):
        attachments.prepare(archive)

    assert not (tmp_path / "outside.txt").exists()


def test_zip_rejects_windows_path_traversal(tmp_path: Path) -> None:
    attachments = processor(tmp_path)
    archive = tmp_path / "unsafe-windows.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(r"..\outside.txt", "no")

    with pytest.raises(AttachmentError, match="不安全"):
        attachments.prepare(archive)

    assert not (tmp_path / "outside.txt").exists()


def test_video_is_converted_to_visual_frames(tmp_path: Path) -> None:
    attachments = processor(tmp_path)
    video = tmp_path / "sample.mp4"
    result = subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x240:d=2",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0

    prepared = attachments.prepare(video)

    assert prepared.attachments
    assert all(path.suffix == ".png" for path in prepared.attachments)
    assert "不转录音轨" in prepared.context


def test_feishu_attachment_content_is_parsed() -> None:
    assert _attachment_details("image", {"image_key": "img-key"}) == (
        "img-key",
        "image.jpg",
    )
    assert _attachment_details(
        "media", {"file_key": "file-key", "file_name": "clip.mp4"}
    ) == ("file-key", "clip.mp4")
    assert _attachment_details("file", {}) is None

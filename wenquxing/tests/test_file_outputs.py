from __future__ import annotations

from pathlib import Path

from PIL import Image

from wenquxing_v2.file_outputs import output_files
from wenquxing_v2.file_tool import FileToolError, _annotate_image, _managed_path


def test_annotate_image_creates_visible_output(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    output = tmp_path / "output.png"
    Image.new("RGB", (640, 360), "navy").save(source)

    _annotate_image(source, output, "Hello Vic!", "top-left", 48)

    assert output.is_file()
    with Image.open(output) as image:
        assert image.size == (640, 360)
        assert image.getbbox() is not None


def test_file_tool_rejects_path_outside_session_root(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    root.mkdir()
    outside = tmp_path / "outside.png"
    Image.new("RGB", (10, 10)).save(outside)

    try:
        _managed_path(root, str(outside), must_exist=True)
    except FileToolError as exc:
        assert "outside" in str(exc)
    else:
        raise AssertionError("outside path was accepted")


def test_output_files_only_returns_bounded_regular_files(tmp_path: Path) -> None:
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    first = outbox / "first.txt"
    first.write_text("one", encoding="utf-8")
    (outbox / "too-large.bin").write_bytes(b"x" * 20)

    files = output_files(outbox, max_bytes=10)

    assert files == [first.resolve()]

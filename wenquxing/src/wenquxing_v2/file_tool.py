from __future__ import annotations

import argparse
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


class FileToolError(RuntimeError):
    pass


def main() -> None:
    parser = argparse.ArgumentParser(prog="wenquxing-file")
    subparsers = parser.add_subparsers(dest="command", required=True)
    annotate = subparsers.add_parser("annotate-image")
    annotate.add_argument("--input", required=True)
    annotate.add_argument("--output", required=True)
    annotate.add_argument("--text", required=True)
    annotate.add_argument(
        "--position",
        choices=("top-left", "top-right", "bottom-left", "bottom-right", "center"),
        default="top-left",
    )
    annotate.add_argument("--font-size", type=int)
    args = parser.parse_args()

    try:
        root_text = os.environ.get("WENQUXING_SESSION_ROOT", "").strip()
        if not root_text:
            raise FileToolError("WENQUXING_SESSION_ROOT is not configured")
        root = Path(root_text).resolve()
        source = _managed_path(root, args.input, must_exist=True)
        output = _managed_path(root, args.output, must_exist=False)
        if output.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise FileToolError("output must be PNG, JPEG, or WEBP")
        output.parent.mkdir(parents=True, exist_ok=True)
        _annotate_image(
            source,
            output,
            args.text,
            args.position,
            args.font_size,
        )
        print(output.relative_to(root))
    except (FileToolError, OSError, ValueError) as exc:
        parser.exit(1, f"wenquxing-file: {exc}\n")


def _managed_path(root: Path, value: str, must_exist: bool) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise FileToolError("path is outside the Wenquxing session root") from exc
    if must_exist and not path.is_file():
        raise FileToolError(f"input file does not exist: {value}")
    return path


def _annotate_image(
    source: Path,
    output: Path,
    text: str,
    position: str,
    font_size: int | None,
) -> None:
    if not text.strip():
        raise FileToolError("text cannot be empty")
    with Image.open(source) as original:
        image = original.convert("RGBA")
    size = font_size or max(24, min(image.width, image.height) // 12)
    font = _font(size)
    draw = ImageDraw.Draw(image)
    box = draw.textbbox((0, 0), text, font=font, stroke_width=max(1, size // 18))
    width, height = box[2] - box[0], box[3] - box[1]
    margin = max(18, size // 2)
    positions = {
        "top-left": (margin, margin),
        "top-right": (image.width - width - margin, margin),
        "bottom-left": (margin, image.height - height - margin),
        "bottom-right": (
            image.width - width - margin,
            image.height - height - margin,
        ),
        "center": ((image.width - width) // 2, (image.height - height) // 2),
    }
    x, y = positions[position]
    draw.text(
        (max(margin, x), max(margin, y)),
        text,
        font=font,
        fill="white",
        stroke_width=max(2, size // 14),
        stroke_fill="black",
    )
    if output.suffix.lower() in {".jpg", ".jpeg"}:
        image.convert("RGB").save(output, quality=95)
    else:
        image.save(output)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    for name in ("msyh.ttc", "msyhbd.ttc", "arial.ttf"):
        path = windir / "Fonts" / name
        if path.is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


if __name__ == "__main__":
    main()

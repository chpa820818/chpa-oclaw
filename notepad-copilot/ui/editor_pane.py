"""Top half: rich-text editor with image paste/drop support."""
from __future__ import annotations

import re
import shutil
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QMimeData, QUrl, Qt
from PySide6.QtGui import QImage, QTextDocument, QTextImageFormat
from PySide6.QtWidgets import QTextEdit

from core.markdown_io import new_attachment_path


# File extensions treated as embeddable images (paste / drop / attach).
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}


def _is_image_path(p: Path) -> bool:
    return p.suffix.lower() in _IMAGE_EXTS


class EditorPane(QTextEdit):
    """QTextEdit that stores pasted/dropped images into an attachments dir."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(True)
        self.setAcceptDrops(True)
        # Until the note is saved, images live in a temp directory.
        self._pending_dir = Path(tempfile.mkdtemp(prefix="notepad-copilot-"))
        self._attach_dir: Path = self._pending_dir
        # Track inserted image paths in document order for fast retrieval.
        self._inserted_images: list[Path] = []
        # Track inserted log/attachment files (non-image) for completeness.
        self._inserted_files: list[Path] = []
        self.setPlaceholderText(
            "✍  Write your notes here…\n"
            "    · Ctrl+V to paste screenshots\n"
            "    · Drop images or log files (.log / .txt / .json …)\n"
            "    · Save as Markdown (.md)"
        )
        self.setFrameShape(QTextEdit.NoFrame)

    # --- attachments dir management -----------------------------------

    @property
    def attachments_dir(self) -> Path:
        return self._attach_dir

    def set_attachments_dir(self, path: Path):
        self._attach_dir = Path(path)
        self._attach_dir.mkdir(parents=True, exist_ok=True)

    def pending_dir(self) -> Path:
        return self._pending_dir

    # --- image enumeration --------------------------------------------

    def image_paths(self) -> list[Path]:
        """Return paths of all images currently referenced in the document.

        Combines (a) images we tracked when inserting (paste/drop), and
        (b) images parsed out of the markdown serialization (covers the
        case where a note was loaded from disk).
        """
        seen: set[str] = set()
        result: list[Path] = []

        for p in self._inserted_images:
            try:
                key = str(p.resolve())
            except Exception:
                key = str(p)
            if key in seen:
                continue
            if p.is_file():
                seen.add(key)
                result.append(p)

        try:
            md = self.toMarkdown()
        except Exception:
            md = ""
        for m in re.finditer(r"!\[[^\]]*\]\(([^)]+)\)", md):
            ref = m.group(1).strip()
            if ref.startswith(("http://", "https://")):
                continue
            if ref.startswith("file:///"):
                ref = ref[len("file:///"):]
            elif ref.startswith("file://"):
                ref = ref[len("file://"):]
            if ref.startswith("/.attachments/"):
                p = self._attach_dir / ref[len("/.attachments/"):]
            elif ref.startswith(".attachments/"):
                p = self._attach_dir / ref[len(".attachments/"):]
            else:
                p = Path(ref)
            try:
                key = str(p.resolve())
            except Exception:
                key = str(p)
            if key in seen:
                continue
            if p.is_file():
                seen.add(key)
                result.append(p)

        return result

    # --- paste / drop overrides ---------------------------------------

    def canInsertFromMimeData(self, source: QMimeData) -> bool:  # noqa: N802
        if source.hasImage() or source.hasUrls():
            return True
        return super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source: QMimeData):  # noqa: N802
        # 1) Image directly on clipboard (e.g., Snipping Tool, screenshot)
        if source.hasImage():
            img = source.imageData()
            if isinstance(img, QImage) and not img.isNull():
                self._insert_image(img)
                return

        # 2) URLs (dragged image / log files / folders)
        if source.hasUrls():
            handled = False
            for url in source.urls():
                if not url.isLocalFile():
                    continue
                p = Path(url.toLocalFile())
                if not p.exists():
                    continue
                try:
                    self.attach_path(p)
                    handled = True
                except Exception:
                    pass
            if handled:
                return

        super().insertFromMimeData(source)

    # --- helpers ------------------------------------------------------

    def _insert_image(self, img: QImage, ext: str = "png"):
        path = new_attachment_path(self._attach_dir, ext=ext)
        img.save(str(path))
        self._inserted_images.append(path)
        url = QUrl.fromLocalFile(str(path))
        self.document().addResource(
            QTextDocument.ImageResource, url, img
        )
        cursor = self.textCursor()
        fmt = QTextImageFormat()
        fmt.setName(str(path))
        # Cap displayed width so huge screenshots don't blow up the editor.
        max_w = max(200, self.viewport().width() - 40)
        if img.width() > max_w:
            ratio = max_w / img.width()
            fmt.setWidth(max_w)
            fmt.setHeight(img.height() * ratio)
        cursor.insertImage(fmt)

    # --- log / file attachments ---------------------------------------

    def attach_path(self, src: Path) -> tuple[int, int]:
        """Attach a file OR a folder (recursive). Returns (n_files, n_bytes)."""
        src = Path(src)
        if src.is_file():
            dst = (self._insert_image_from_path(src)
                   if _is_image_path(src)
                   else self.attach_log_file(src))
            return 1, dst.stat().st_size
        if src.is_dir():
            return self.attach_folder(src)
        raise FileNotFoundError(str(src))

    def _insert_image_from_path(self, src: Path) -> Path:
        """Load an image file and embed it via _insert_image."""
        img = QImage(str(src))
        if img.isNull():
            # Fall back to treating it as a generic attachment.
            return self.attach_log_file(src)
        # _insert_image creates a fresh attach path; copy semantics ok.
        path = new_attachment_path(
            self._attach_dir, ext=src.suffix.lstrip(".") or "png")
        img.save(str(path))
        self._inserted_images.append(path)
        url = QUrl.fromLocalFile(str(path))
        self.document().addResource(
            QTextDocument.ImageResource, url, img
        )
        cursor = self.textCursor()
        fmt = QTextImageFormat()
        fmt.setName(str(path))
        max_w = max(200, self.viewport().width() - 40)
        if img.width() > max_w:
            ratio = max_w / img.width()
            fmt.setWidth(max_w)
            fmt.setHeight(img.height() * ratio)
        cursor.insertImage(fmt)
        return path

    def attach_folder(self, src_dir: Path) -> tuple[int, int]:
        """Recursively copy a folder into attachments, insert a summary block.

        Layout in attachments dir:
            <attach>/<folder_name[-ts]>/<original/relative/file>

        Returns (n_files_copied, total_bytes).
        """
        src_dir = Path(src_dir)
        if not src_dir.is_dir():
            raise NotADirectoryError(str(src_dir))
        self._attach_dir.mkdir(parents=True, exist_ok=True)

        # Pick a non-colliding folder dest.
        base_name = src_dir.name or "folder"
        dest_root = self._attach_dir / base_name
        if dest_root.exists():
            ts = time.strftime("%Y%m%d-%H%M%S")
            dest_root = self._attach_dir / f"{base_name}-{ts}"
            i = 1
            while dest_root.exists():
                dest_root = self._attach_dir / f"{base_name}-{ts}-{i}"
                i += 1
        dest_root.mkdir(parents=True)

        copied: list[Path] = []
        total = 0
        for p in sorted(src_dir.rglob("*")):
            if not p.is_file():
                continue
            try:
                rel = p.relative_to(src_dir)
            except ValueError:
                rel = Path(p.name)
            target = dest_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(p, target)
            except Exception:
                continue
            copied.append(target)
            try:
                total += target.stat().st_size
            except OSError:
                pass

        self._inserted_files.extend(copied)

        # Insert a folder summary block: header link + tree-style list.
        href_root = QUrl.fromLocalFile(str(dest_root)).toString()
        size_kb = max(1, total // 1024)
        size_str = (f"{size_kb} KB" if size_kb < 1024
                    else f"{size_kb / 1024:.1f} MB")
        cursor = self.textCursor()
        cursor.insertHtml(
            f'<p>📁 <a href="{href_root}">{dest_root.name}/</a> '
            f'<i>({len(copied)} files · {size_str})</i></p>'
        )
        # List children (cap to avoid wall-of-text on huge dumps).
        max_listed = 50
        listed = copied[:max_listed]
        # Build an HTML <ul> so toMarkdown produces a normal markdown list.
        items_html = []
        for f in listed:
            try:
                rel = f.relative_to(dest_root).as_posix()
            except ValueError:
                rel = f.name
            href = QUrl.fromLocalFile(str(f)).toString()
            try:
                kb = max(1, f.stat().st_size // 1024)
            except OSError:
                kb = 0
            items_html.append(
                f'<li><a href="{href}">{rel}</a> '
                f'<i>({kb} KB)</i></li>'
            )
        if len(copied) > max_listed:
            items_html.append(
                f'<li><i>… {len(copied) - max_listed} more '
                f'files not shown</i></li>'
            )
        if items_html:
            cursor.insertHtml(
                "<ul>" + "".join(items_html) + "</ul>"
            )
        cursor.insertText(" ")
        return len(copied), total

    def attach_log_file(self, src: Path) -> Path:
        """Copy `src` into the attachments dir and insert a link.

        Returns the destination path inside the attachments dir.
        Used by drag-drop of non-image files and by the
        '📎 上传日志文件…' menu / button in the main window.
        """
        src = Path(src)
        if not src.is_file():
            raise FileNotFoundError(str(src))
        self._attach_dir.mkdir(parents=True, exist_ok=True)
        dst = self._unique_attachment_dest(src.name)
        shutil.copy2(src, dst)
        self._inserted_files.append(dst)

        # Insert a clickable hyperlink pointing at the local file.
        # Qt's toMarkdown() will serialize <a href="X">Y</a>
        # as `[Y](X)` faithfully, so the link survives save / load.
        href = QUrl.fromLocalFile(str(dst)).toString()
        size_kb = max(1, dst.stat().st_size // 1024)
        label = f"📎 {dst.name} ({size_kb} KB)"
        cursor = self.textCursor()
        # Make sure we don't clobber any active char format.
        cursor.insertHtml(
            f'<a href="{href}">{label}</a>'
        )
        # Trailing space so further typing isn't styled as link text.
        cursor.insertText(" ")
        return dst

    def _unique_attachment_dest(self, name: str) -> Path:
        """Pick a non-colliding destination filename inside attach dir."""
        dst = self._attach_dir / name
        if not dst.exists():
            return dst
        stem = dst.stem
        suffix = dst.suffix
        ts = time.strftime("%Y%m%d-%H%M%S")
        candidate = self._attach_dir / f"{stem}-{ts}{suffix}"
        i = 1
        while candidate.exists():
            candidate = self._attach_dir / f"{stem}-{ts}-{i}{suffix}"
            i += 1
        return candidate


    def collect_image_paths(self) -> list[Path]:
        """Walk the document and return all embedded image file paths."""
        from PySide6.QtGui import QTextImageFormat as _Fmt
        out: list[Path] = []
        seen: set[str] = set()
        block = self.document().begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid():
                    fmt = frag.charFormat()
                    if fmt.isImageFormat():
                        name = _Fmt(fmt).name()
                        if name and name not in seen:
                            seen.add(name)
                            # name may be a file path or a file:// URL
                            if name.startswith("file:///"):
                                p = Path(name[len("file:///"):])
                            elif name.startswith("file://"):
                                p = Path(name[len("file://"):])
                            else:
                                p = Path(name)
                            if p.is_file():
                                out.append(p)
                it += 1
            block = block.next()
        return out

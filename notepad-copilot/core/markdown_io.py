"""Save / load notes as Markdown + .attachments folder, ADO-Wiki compatible."""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QImage, QTextDocument


ATTACHMENTS_DIR = ".attachments"


def attachments_dir_for(md_path: Path) -> Path:
    return md_path.parent / ATTACHMENTS_DIR


def save_document(doc: QTextDocument, md_path: Path,
                  pending_attachments_dir: Path | None = None) -> None:
    """Save document as Markdown.

    Any images currently held in `pending_attachments_dir` (used while the
    note was unsaved) are moved next to the saved .md file.
    """
    md_path = Path(md_path)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    target_attach = attachments_dir_for(md_path)
    target_attach.mkdir(exist_ok=True)

    # Move any pending attachments to the final location.
    if pending_attachments_dir and pending_attachments_dir.exists() \
            and pending_attachments_dir.resolve() != target_attach.resolve():
        for f in pending_attachments_dir.iterdir():
            if f.is_file():
                dst = target_attach / f.name
                if not dst.exists():
                    shutil.move(str(f), str(dst))

    md = doc.toMarkdown()
    md = _rewrite_image_paths(md, target_attach)
    md_path.write_text(md, encoding="utf-8")


def load_document(md_path: Path, doc: QTextDocument) -> None:
    md_path = Path(md_path)
    text = md_path.read_text(encoding="utf-8")
    attach = attachments_dir_for(md_path)

    # First, repair anything Qt may have written escaped or wrapped:
    # `\![alt](file:///C:/Users/.../OneDrive - Microsoft/...png)` →
    # `![alt](/.attachments/x.png)`. Doing this on the in-memory text is
    # safe; we also persist the cleaned form on the next save.
    text = _rewrite_image_paths(text, attach)

    # Step 1: rewrite ADO-style `/.attachments/foo.png` → proper `file:///`
    # URL pointing at the real local file. Plain absolute paths and bare
    # `.attachments/foo.png` forms are normalized too. After this rewrite
    # every local image ref in `text_for_qt` is a fully-qualified file URI.
    def _to_file_uri(local: Path) -> str:
        return QUrl.fromLocalFile(str(local.resolve())).toString()

    def _rewrite(m: re.Match) -> str:
        alt = m.group(1)
        ref = re.sub(r"[\r\n]+", "", m.group(2).strip())
        # Drop any leading '<' / trailing '>' if the source already wrapped.
        if ref.startswith("<") and ref.endswith(">"):
            ref = ref[1:-1]
        local = _resolve_image_ref(ref, md_path.parent, attach)
        if not (local and local.exists()):
            # Recovery: try basename-only lookup in attach dir.
            bn = Path(ref).name
            if bn:
                cand = attach / bn
                if cand.is_file():
                    local = cand
        if local and local.exists():
            # CommonMark requires URLs containing spaces to be wrapped in
            # angle brackets: `![alt](<file:///path with space/x.png>)`.
            # Without the brackets Qt renders the whole image syntax as
            # plain text, which is the bug we're fixing.
            return f"![{alt}](<{_to_file_uri(local)}>)"
        return m.group(0)

    text_for_qt = re.sub(
        r"\\?!\[([^\]]*)\]\(([^)]+)\)", _rewrite, text, flags=re.DOTALL,
    )

    # Step 2: pre-register every image as a document resource keyed by the
    # exact same URL Qt will look up during setMarkdown. Without this Qt
    # falls back to plain link rendering for paths it can't auto-resolve.
    # Note the angle-bracket wrapping `<...>` from step 1, so the regex
    # matches `(<file:...>)`.
    for m in re.finditer(
        r"!\[[^\]]*\]\(<?(file:[^)>\s]+)>?\)", text_for_qt,
    ):
        url = m.group(1).strip()
        qurl = QUrl(url)
        local_path = qurl.toLocalFile() if qurl.isLocalFile() else url
        if not local_path:
            continue
        img = QImage(local_path)
        if img.isNull():
            continue
        doc.addResource(QTextDocument.ImageResource, qurl, img)
        doc.addResource(
            QTextDocument.ImageResource,
            QUrl.fromLocalFile(local_path),
            img,
        )

    # Give Qt a base URL so any leftover relative refs resolve correctly.
    doc.setBaseUrl(QUrl.fromLocalFile(str(md_path.parent) + "/"))
    doc.setMarkdown(text_for_qt)


def new_attachment_path(attach_dir: Path, ext: str = "png") -> Path:
    attach_dir.mkdir(parents=True, exist_ok=True)
    name = f"img_{int(time.time() * 1000)}.{ext}"
    p = attach_dir / name
    # ensure unique
    i = 1
    while p.exists():
        p = attach_dir / f"img_{int(time.time() * 1000)}_{i}.{ext}"
        i += 1
    return p


def _rewrite_image_paths(md: str, attach_dir: Path) -> str:
    """Rewrite local image paths to ADO Wiki style /.attachments/<name>.

    Qt's ``toMarkdown()`` sometimes emits ``\\!`` (escaped bang) and inlines
    file URIs that wrap across lines. We tolerate both, but **must preserve
    spaces inside the path** itself (e.g. ``OneDrive - Microsoft``). Only
    line breaks introduced by Qt's wrapping are stripped.
    """
    def repl(m: re.Match) -> str:
        alt = m.group(1)
        ref = m.group(2).strip()
        # Strip ONLY CR/LF that Qt may have inserted while wrapping; keep
        # legitimate spaces inside path components.
        ref = re.sub(r"[\r\n]+", "", ref)
        # Skip http(s) and already-correct ADO-style refs.
        if ref.startswith(("http://", "https://", "/.attachments/")):
            return f"![{alt}]({ref})"
        # file:// or absolute / relative paths
        if ref.startswith("file:///"):
            ref = ref[len("file:///"):]
        elif ref.startswith("file://"):
            ref = ref[len("file://"):]
        # Percent-decode (Qt may have URL-encoded the path).
        try:
            from urllib.parse import unquote
            ref = unquote(ref)
        except Exception:
            pass
        p = Path(ref)
        if not p.is_absolute():
            p = (attach_dir.parent / ref).resolve()
        # Primary: if the (possibly invented) absolute path lives inside
        # attach_dir, write the canonical /.attachments/ form.
        try:
            rel = p.relative_to(attach_dir.resolve())
            return f"![{alt}](/.attachments/{rel.as_posix()})"
        except ValueError:
            pass
        # Fallback A: absolute path exists outside attachments — copy it in.
        if p.exists() and p.is_file():
            dst = attach_dir / p.name
            if not dst.exists():
                shutil.copy2(p, dst)
            return f"![{alt}](/.attachments/{dst.name})"
        # Fallback B: path is broken (e.g. previous save corrupted it)
        # but a file with the same basename exists in attach_dir → recover.
        bn = Path(ref).name
        if bn:
            cand = attach_dir / bn
            if cand.is_file():
                return f"![{alt}](/.attachments/{bn})"
        return f"![{alt}]({ref})"

    return re.sub(
        r"\\?!\[([^\]]*)\]\(([^)]+)\)", repl, md, flags=re.DOTALL,
    )


def _resolve_image_ref(ref: str, base_dir: Path, attach: Path) -> Path | None:
    if ref.startswith(("http://", "https://")):
        return None
    if ref.startswith("/.attachments/"):
        return attach / ref[len("/.attachments/"):]
    if ref.startswith(".attachments/"):
        return attach / ref[len(".attachments/"):]
    if ref.startswith("file:///"):
        return Path(ref[len("file:///"):])
    if ref.startswith("file://"):
        return Path(ref[len("file://"):])
    p = Path(ref)
    if p.is_absolute():
        return p
    return (base_dir / ref).resolve()

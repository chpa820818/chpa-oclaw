"""Save / load notes as Markdown + .attachments folder, ADO-Wiki compatible."""
from __future__ import annotations

import re
import shutil
import time
from os import PathLike
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QImage, QTextDocument


ATTACHMENTS_DIR = ".attachments"
_PATH_ERRORS = (OSError, ValueError, RuntimeError)


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
    if pending_attachments_dir and _path_exists(pending_attachments_dir) \
            and _path_resolve(pending_attachments_dir) != _path_resolve(target_attach):
        for f in pending_attachments_dir.iterdir():
            if _path_is_file(f):
                dst = target_attach / f.name
                if not _path_exists(dst):
                    shutil.move(str(f), str(dst))

    md = doc.toMarkdown()
    try:
        md = _rewrite_image_paths(md, target_attach)
    except _PATH_ERRORS:
        pass
    try:
        md = _rewrite_local_link_paths(md, target_attach)
    except _PATH_ERRORS:
        pass
    md_path.write_text(md, encoding="utf-8")


def load_document(md_path: Path, doc: QTextDocument) -> None:
    md_path = Path(md_path)
    text = md_path.read_text(encoding="utf-8")
    attach = attachments_dir_for(md_path)

    # First, repair anything Qt may have written escaped or wrapped:
    # `\![alt](file:///C:/Users/.../OneDrive - Microsoft/...png)` →
    # `![alt](/.attachments/x.png)`. Doing this on the in-memory text is
    # safe; we also persist the cleaned form on the next save.
    text = _unwrap_attachment_fences(text)
    text = _rewrite_image_paths(text, attach)
    text = _rewrite_local_link_paths(text, attach)

    # Step 1: rewrite ADO-style `/.attachments/foo.png` → proper `file:///`
    # URL pointing at the real local file. Plain absolute paths and bare
    # `.attachments/foo.png` forms are normalized too. After this rewrite
    # every local image ref in `text_for_qt` is a fully-qualified file URI.
    def _to_file_uri(local: Path) -> str:
        return QUrl.fromLocalFile(str(_path_resolve(local))).toString()

    def _clean_label(label: str) -> str:
        return re.sub(r"\s*[\r\n]+\s*", " ", label).strip()

    def _rewrite(m: re.Match) -> str:
        alt = _clean_label(m.group(1))
        ref = _clean_ref(m.group(2))
        # Drop any leading '<' / trailing '>' if the source already wrapped.
        if ref.startswith("<") and ref.endswith(">"):
            ref = ref[1:-1]
        local = _resolve_local_ref(ref, md_path.parent, attach)
        if not (local and _path_exists(local)):
            # Recovery: try basename-only lookup in attach dir.
            bn = Path(ref).name
            if bn:
                cand = attach / bn
                if _path_is_file(cand):
                    local = cand
        if local and _path_exists(local):
            # CommonMark requires URLs containing spaces to be wrapped in
            # angle brackets: `![alt](<file:///path with space/x.png>)`.
            # Without the brackets Qt renders the whole image syntax as
            # plain text, which is the bug we're fixing.
            return f"![{alt}](<{_to_file_uri(_path_resolve(local))}>)"
        return m.group(0)

    text_for_qt = re.sub(
        r"\\?!\[([^\]]*)\]\(([^)]+)\)", _rewrite, text, flags=re.DOTALL,
    )
    text_for_qt = _rewrite_local_links_for_qt(text_for_qt, md_path.parent, attach)

    # Step 2: pre-register every image as a document resource keyed by the
    # exact same URL Qt will look up during setMarkdown. Without this Qt
    # falls back to plain link rendering for paths it can't auto-resolve.
    # Note the angle-bracket wrapping `<...>` from step 1, so the regex
    # matches `(<file:...>)`.
    for m in re.finditer(
        r"!\[[^\]]*\]\(<?(file:[^)>\r\n]+)>?\)", text_for_qt,
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
    while _path_exists(p):
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
        try:
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
                p = _path_resolve(attach_dir.parent / ref)
            # Primary: if the (possibly invented) absolute path lives inside
            # attach_dir, write the canonical /.attachments/ form.
            try:
                rel = p.relative_to(_path_resolve(attach_dir))
                return f"![{alt}](/.attachments/{rel.as_posix()})"
            except ValueError:
                pass
            # Fallback A: absolute path exists outside attachments — copy it in.
            if _path_is_file(p):
                dst = attach_dir / p.name
                if not _path_exists(dst):
                    shutil.copy2(p, dst)
                return f"![{alt}](/.attachments/{dst.name})"
            # Fallback B: path is broken (e.g. previous save corrupted it)
            # but a file with the same basename exists in attach_dir → recover.
            bn = _path_name(ref)
            if bn:
                cand = attach_dir / bn
                if _path_is_file(cand):
                    return f"![{alt}](/.attachments/{bn})"
            return f"![{alt}]({ref})"
        except _PATH_ERRORS:
            return m.group(0)

    return re.sub(
        r"\\?!\[([^\]]*)\]\(([^)]+)\)", repl, md, flags=re.DOTALL,
    )


def _rewrite_local_link_paths(md: str, attach_dir: Path) -> str:
    """Rewrite local non-image links under .attachments to stable paths."""
    def repl(m: re.Match) -> str:
        try:
            label = re.sub(r"\s*[\r\n]+\s*", " ", m.group(1)).strip()
            ref = _clean_ref(m.group(2))
            if ref.startswith(("http://", "https://", "/.attachments/")):
                return f"[{label}]({ref})"
            local = _resolve_local_ref(ref, attach_dir.parent, attach_dir)
            if not (local and _path_exists(local)):
                local = _recover_attachment_by_basename(ref, attach_dir)
            if not local:
                return f"[{label}]({ref})"
            try:
                rel = _path_resolve(local).relative_to(_path_resolve(attach_dir))
                return f"[{label}](/.attachments/{rel.as_posix()})"
            except ValueError:
                pass
            return f"[{label}]({ref})"
        except _PATH_ERRORS:
            return m.group(0)

    return re.sub(
        r"(?<!!)\\?\[([^\]]+)\]\(([^)]+)\)", repl, md, flags=re.DOTALL,
    )


def _rewrite_local_links_for_qt(text: str, base_dir: Path, attach: Path) -> str:
    """Wrap local file links as <file:///...> so Qt renders them clickable."""
    def repl(m: re.Match) -> str:
        try:
            label = re.sub(r"\s*[\r\n]+\s*", " ", m.group(1)).strip()
            ref = _clean_ref(m.group(2))
            if ref.startswith(("http://", "https://")):
                return f"[{label}]({ref})"
            local = _resolve_local_ref(ref, base_dir, attach)
            if not (local and _path_exists(local)):
                local = _recover_attachment_by_basename(ref, attach)
            if local and _path_exists(local):
                uri = QUrl.fromLocalFile(str(_path_resolve(local))).toString()
                return f"[{label}](<{uri}>)"
            return f"[{label}]({ref})"
        except _PATH_ERRORS:
            return m.group(0)

    return re.sub(
        r"(?<!!)\\?\[([^\]]+)\]\(([^)]+)\)", repl, text, flags=re.DOTALL,
    )


def _clean_ref(ref: str) -> str:
    ref = (ref or "").strip()
    if ref.startswith("<") and ref.endswith(">"):
        ref = ref[1:-1].strip()
    ref = re.sub(r"[\r\n]+", "", ref)
    # Qt may wrap unescaped OneDrive paths exactly after "OneDrive -".
    ref = ref.replace("OneDrive -Microsoft", "OneDrive - Microsoft")
    try:
        from urllib.parse import unquote
        ref = unquote(ref)
    except Exception:
        pass
    return ref


def _unwrap_attachment_fences(text: str) -> str:
    """Remove accidental ``` fences around local attachment markdown.

    QTextDocument.toMarkdown can occasionally preserve copied/preformatted
    blocks as fenced code. If that block contains our own local image/link
    syntax, reopening the case shows literal markdown instead of rich content.
    This only unwraps fences whose body references local attachments.
    """
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].strip() != "```":
            out.append(lines[i])
            i += 1
            continue
        j = i + 1
        body: list[str] = []
        while j < len(lines) and lines[j].strip() != "```":
            body.append(lines[j])
            j += 1
        if j < len(lines) and _looks_like_attachment_block("\n".join(body)):
            out.extend(body)
            i = j + 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def _looks_like_attachment_block(body: str) -> bool:
    return (
        "![image]" in body
        and (
            "/.attachments/" in body
            or ".attachments/" in body
            or "file:///" in body
        )
    )


def _recover_attachment_by_basename(ref: str, attach: Path) -> Path | None:
    bn = _path_name(_clean_ref(ref))
    if not bn or not _path_exists(attach):
        return None
    direct = attach / bn
    if _path_exists(direct):
        return direct
    try:
        for cand in attach.rglob(bn):
            if _path_exists(cand):
                return cand
    except Exception:
        return None
    return None


def _resolve_local_ref(ref: str, base_dir: Path, attach: Path) -> Path | None:
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
    return _path_resolve(base_dir / ref)


def _resolve_image_ref(ref: str, base_dir: Path, attach: Path) -> Path | None:
    return _resolve_local_ref(ref, base_dir, attach)


def _path_exists(path: PathLike[str] | str) -> bool:
    try:
        return Path(path).exists()
    except _PATH_ERRORS:
        return False


def _path_is_file(path: PathLike[str] | str) -> bool:
    try:
        return Path(path).is_file()
    except _PATH_ERRORS:
        return False


def _path_resolve(path: PathLike[str] | str) -> Path:
    p = Path(path)
    try:
        return p.resolve()
    except _PATH_ERRORS:
        return p


def _path_name(path: PathLike[str] | str) -> str:
    try:
        return Path(path).name
    except _PATH_ERRORS:
        return ""

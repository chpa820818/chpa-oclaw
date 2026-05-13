"""One-click archive: bundle editor notes + result Q&A into a markdown
report directory under copilot-workspace\\reports\\archive\\.

Layout produced::

    <archive_root>/<YYYYMMDD-HHMMSS>-archive/
        archive.md           # combined report
        redact_map.json      # placeholder -> original (only when redacted)
        assets/              # copied screenshots/images
            img-001.png
            ...
"""
from __future__ import annotations

import datetime as _dt
import json as _json
import re
import shutil
from pathlib import Path

from core.redact import Redactor

# Default archive root: <workspace>/copilot-workspace/reports/archive
# This module lives at copilot-workspace/tools/notepad-copilot/core/archive.py
_PKG_DIR = Path(__file__).resolve().parent
_NOTEPAD_DIR = _PKG_DIR.parent
_TOOLS_DIR = _NOTEPAD_DIR.parent
_COPILOT_WORKSPACE = _TOOLS_DIR.parent
DEFAULT_ARCHIVE_ROOT = _COPILOT_WORKSPACE / "reports" / "archive"


def default_archive_dir(now: _dt.datetime | None = None) -> Path:
    now = now or _dt.datetime.now()
    return DEFAULT_ARCHIVE_ROOT / now.strftime("%Y%m%d-%H%M%S-archive")


def archive_session(
    target_dir: Path,
    note_markdown: str,
    note_image_paths: list[Path],
    qa_markdown: str,
    title: str | None = None,
    redact: bool = True,
    refine_tsg: bool = False,
) -> Path:
    """Write a single ``archive.md`` plus assets/ into target_dir.

    When ``redact`` is True, customer PII (GUIDs, emails, IPs, RG/resource
    names inside ARM IDs, secrets) is replaced with stable placeholders
    and the mapping is dumped to ``redact_map.json`` next to archive.md.

    When ``refine_tsg`` is True, after producing the raw combined report
    we additionally invoke the local ``copilot`` CLI to refine it into a
    Troubleshooting Guide (TSG) style document. The refined version
    becomes ``archive.md``; the raw combined report is preserved as
    ``archive.raw.md``. If refinement fails, the raw version is kept as
    ``archive.md`` and ``archive.tsg.error.txt`` records the error.

    Returns the path of the written archive.md file.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = target_dir / "assets"

    # Copy images into assets/ and build name mapping (orig_path -> new_rel)
    name_map: dict[str, str] = {}
    if note_image_paths:
        assets_dir.mkdir(exist_ok=True)
        for i, src in enumerate(note_image_paths, 1):
            try:
                if not src.is_file():
                    continue
                ext = src.suffix or ".png"
                dst_name = f"img-{i:03d}{ext}"
                dst = assets_dir / dst_name
                shutil.copy2(src, dst)
                name_map[str(src)] = f"assets/{dst_name}"
                name_map[src.as_uri()] = f"assets/{dst_name}"
                name_map[str(src).replace("\\", "/")] = f"assets/{dst_name}"
            except Exception:  # noqa: BLE001
                continue

    # Rewrite ![](path) refs in note markdown to point to assets/
    rewritten_note = _rewrite_image_refs(note_markdown, name_map)

    # Apply redaction AFTER image-path rewrite so we don't scrub asset paths.
    redactor: Redactor | None = None
    if redact:
        redactor = Redactor()
        rewritten_note = redactor.redact(rewritten_note)
        qa_markdown = redactor.redact(qa_markdown)

    now = _dt.datetime.now()
    title = title or "归档报告"
    redact_badge = " (已脱敏)" if redact else ""
    header = (
        f"# {title}{redact_badge}\n\n"
        f"_归档时间: {now.strftime('%Y-%m-%d %H:%M:%S')}_\n"
    )
    if redact:
        header += (
            "_已对 GUID / 订阅 ID / 资源名 / 邮箱 / IP / 密钥等敏感信息脱敏；"
            "原值映射见同目录 `redact_map.json`（请勿外发）_\n"
        )
    header += "\n"
    note_section = (
        "## 📝 笔记内容\n\n"
        f"{rewritten_note.strip() or '_（笔记区为空）_'}\n\n"
    )
    if note_image_paths:
        kept = len([p for p in note_image_paths if p.is_file()])
        note_section += (
            f"\n_共附带 {kept} 张图片，已保存至 `assets/` 目录_\n\n"
        )
    qa_section = (
        "## 💬 对话与结果\n\n"
        f"{qa_markdown.strip() or '_（无对话）_'}\n"
    )

    archive_md = target_dir / "archive.md"
    raw_body = header + note_section + qa_section
    archive_md.write_text(raw_body, encoding="utf-8")

    if redactor and redactor.mapping:
        (target_dir / "redact_map.json").write_text(
            _json.dumps(redactor.mapping, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # Optional TSG refinement (after redaction so we never send raw PII)
    # Skip if both note and QA are empty — TSG would just be all (N/A).
    note_has_content = bool(rewritten_note.strip())
    qa_has_content = bool(qa_markdown.strip())
    if refine_tsg and not (note_has_content or qa_has_content):
        (target_dir / "archive.tsg.error.txt").write_text(
            "Skipped TSG refinement: note and QA are both empty.\n",
            encoding="utf-8",
        )
        refine_tsg = False
    if refine_tsg:
        try:
            from core.tsg_summarizer import summarize_to_tsg
            tsg_md = summarize_to_tsg(raw_body, title=title)
            # Preserve the raw report as archive.raw.md, replace archive.md
            (target_dir / "archive.raw.md").write_text(
                raw_body, encoding="utf-8"
            )
            tsg_header = ""
            if redact:
                tsg_header = (
                    "> _本文档由原始排查记录脱敏后，由 Copilot 自动精炼为 "
                    "TSG 风格。原始组合内容见 `archive.raw.md`，敏感字段映射 "
                    "见 `redact_map.json`（请勿外发）。_\n\n"
                )
            else:
                tsg_header = (
                    "> _本文档由原始排查记录精炼为 TSG 风格。原始组合内容见 "
                    "`archive.raw.md`。_\n\n"
                )
            archive_md.write_text(tsg_header + tsg_md, encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            (target_dir / "archive.tsg.error.txt").write_text(
                f"TSG refinement failed:\n{e}\n", encoding="utf-8"
            )
            # Keep the raw report as archive.md (already written above)

    return archive_md


_IMG_RE = re.compile(r"(!\[[^\]]*\]\()([^)\s]+)([^)]*\))")


def _rewrite_image_refs(md: str, name_map: dict[str, str]) -> str:
    if not md or not name_map:
        return md

    def _sub(m: re.Match) -> str:
        prefix, ref, suffix = m.group(1), m.group(2), m.group(3)
        ref_clean = ref
        if ref_clean.startswith("file:///"):
            ref_clean = ref_clean[len("file:///"):]
        elif ref_clean.startswith("file://"):
            ref_clean = ref_clean[len("file://"):]
        for cand in (ref, ref_clean,
                     ref_clean.replace("/", "\\"),
                     ref_clean.replace("\\", "/")):
            if cand in name_map:
                return f"{prefix}{name_map[cand]}{suffix}"
        return m.group(0)

    return _IMG_RE.sub(_sub, md)

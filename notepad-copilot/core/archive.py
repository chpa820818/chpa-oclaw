"""One-click archive: bundle editor notes + result Q&A into markdown/HTML
report directory under copilot-workspace\\reports\\archive\\.

Layout produced::

    <archive_root>/<YYYYMMDD-HHMMSS>-archive/
        archive.md           # combined report
        archive.html         # combined report rendered as standalone HTML
        redact_map.json      # placeholder -> original (only when redacted)
        assets/              # copied screenshots/images
            img-001.png
            ...
"""
from __future__ import annotations

import datetime as _dt
import html as _html
import json as _json
import re
import shutil
from pathlib import Path

from markdown import markdown as _markdown_to_html

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
    """Write ``archive.md`` + ``archive.html`` plus assets/ into target_dir.

    When ``redact`` is True, customer PII (GUIDs, emails, IPs, RG/resource
    names inside ARM IDs, secrets) is replaced with stable placeholders
    and the mapping is dumped to ``redact_map.json`` next to the archive.

    When ``refine_tsg`` is True, after producing the raw combined report
    we additionally invoke the local ``copilot`` CLI to refine it into a
    Troubleshooting Guide (TSG) style document. The refined version
    becomes ``archive.md``; the raw combined report is preserved as
    ``archive.raw.md``. If refinement fails, the raw version is kept as
    ``archive.md`` and ``archive.tsg.error.txt`` records the error.

    ``archive.html`` is rendered from the final markdown after redaction and
    optional TSG refinement, so it is safe to share under the same constraints
    as ``archive.md``.

    Returns the path of the written archive.md file for existing callers.
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
    archive_html = target_dir / "archive.html"
    raw_body = header + note_section + qa_section
    archive_md.write_text(raw_body, encoding="utf-8")
    _write_html_report(
        archive_md,
        archive_html,
        title=title + redact_badge,
        redacted=redact,
    )
    if not archive_html.is_file():
        raise RuntimeError(f"HTML 归档生成失败: {archive_html}")

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

    # Refresh HTML after optional TSG refinement. If refinement failed, this
    # keeps the already-written raw HTML in place and simply regenerates it
    # from the final archive.md.
    _write_html_report(
        archive_md,
        archive_html,
        title=title + redact_badge,
        redacted=redact,
    )
    if not archive_html.is_file():
        raise RuntimeError(f"HTML 归档生成失败: {archive_html}")

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


def _write_html_report(
    markdown_path: Path,
    html_path: Path,
    *,
    title: str,
    redacted: bool,
) -> None:
    """Render the final archive markdown into a standalone HTML report."""
    md = markdown_path.read_text(encoding="utf-8")
    body = _markdown_to_html(
        md,
        extensions=["extra", "sane_lists", "nl2br", "tables", "fenced_code"],
        output_format="html5",
    )
    safe_title = _html.escape(title or "归档报告")
    badge = (
        '<span class="badge">已脱敏</span>'
        if redacted else '<span class="badge badge-warn">未脱敏</span>'
    )
    doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <style>
    :root {{
      color-scheme: light;
      --fg: #24292f;
      --muted: #57606a;
      --border: #d0d7de;
      --bg: #ffffff;
      --soft: #f6f8fa;
      --accent: #0969da;
      --safe: #1a7f37;
      --warn: #9a6700;
    }}
    body {{
      margin: 0;
      background: var(--soft);
      color: var(--fg);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.6;
    }}
    main {{
      max-width: 1040px;
      margin: 24px auto;
      padding: 32px 40px;
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      box-shadow: 0 8px 24px rgba(140, 149, 159, 0.18);
    }}
    .topline {{
      display: flex;
      justify-content: flex-end;
      margin-bottom: 12px;
    }}
    .badge {{
      display: inline-block;
      padding: 3px 10px;
      border-radius: 999px;
      background: #dafbe1;
      color: var(--safe);
      font-size: 12px;
      font-weight: 600;
    }}
    .badge-warn {{
      background: #fff8c5;
      color: var(--warn);
    }}
    h1, h2, h3 {{
      line-height: 1.25;
      margin-top: 1.4em;
    }}
    h1 {{
      margin-top: 0;
      padding-bottom: 0.3em;
      border-bottom: 1px solid var(--border);
    }}
    h2 {{
      padding-bottom: 0.2em;
      border-bottom: 1px solid #eaeef2;
    }}
    a {{ color: var(--accent); }}
    blockquote {{
      margin: 1em 0;
      padding: 0 1em;
      color: var(--muted);
      border-left: 0.25em solid var(--border);
    }}
    code {{
      padding: 0.2em 0.4em;
      background: var(--soft);
      border-radius: 6px;
      font-family: "Cascadia Mono", Consolas, monospace;
      font-size: 0.92em;
    }}
    pre {{
      overflow: auto;
      padding: 16px;
      background: var(--soft);
      border-radius: 8px;
    }}
    pre code {{
      padding: 0;
      background: transparent;
    }}
    table {{
      border-collapse: collapse;
      width: 100%;
      display: block;
      overflow-x: auto;
    }}
    th, td {{
      border: 1px solid var(--border);
      padding: 6px 10px;
    }}
    th {{ background: var(--soft); }}
    img {{
      max-width: 100%;
      border: 1px solid var(--border);
      border-radius: 8px;
    }}
    @media print {{
      body {{ background: #fff; }}
      main {{
        margin: 0;
        padding: 0;
        border: 0;
        box-shadow: none;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <div class="topline">{badge}</div>
    {body}
  </main>
</body>
</html>
"""
    html_path.write_text(doc, encoding="utf-8")

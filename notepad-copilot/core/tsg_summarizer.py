"""Refine a raw archive markdown into a Troubleshooting Guide (TSG).

Spawns the same `copilot` CLI used by the chat pane, but synchronously
(blocking the caller — caller should run this off the GUI thread, or accept
a brief freeze with a busy cursor).
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import datetime as _dt
from pathlib import Path

from core.copilot_runner import (
    _build_env,
    _resolve_launcher,
    _strip_ansi,
)

# ---- footer / chrome cleanup -------------------------------------------------

# Lines from copilot's progress chrome that we want to drop from the TSG output.
_FOOTER_LINE_RE = re.compile(
    r"^\s*(Changes\b|Requests\b|Tokens\b|AI Units\b|↑|↓|Premium\b)"
)
# Tool/thinking marker lines (●, ⏺, ✓, etc. as the very first non-space char)
_CHROME_LINE_RE = re.compile(r"^[\s]*[●⏺✓✗★◇◆▶▷▸✱]\s")


def _clean_cli_output(raw: str) -> str:
    """Strip ANSI + footer + tool-progress lines, return just the answer.

    The CLI footer (Changes / Requests / Tokens / ↑ / ↓ / Premium) always
    sits at the very end of the stream, often as several consecutive lines.
    We walk from the **bottom** and trim a contiguous tail block whose lines
    are either footer matches or blank. Scanning from the top is unsafe —
    the model's own answer may legitimately contain words like "Premium SKU"
    or "Changes:" at line start and we'd chop the entire body.
    """
    text = _strip_ansi(raw)
    lines = text.splitlines()
    end = len(lines)
    # Walk backwards while line is footer-like or blank.
    while end > 0:
        ln = lines[end - 1]
        if not ln.strip() or _FOOTER_LINE_RE.match(ln):
            end -= 1
            continue
        break
    body = lines[:end]
    # Drop chrome lines (tool/thinking indicators) anywhere in the body.
    body = [ln for ln in body if not _CHROME_LINE_RE.match(ln)]
    text = "\n".join(body).strip()
    text = _strip_inline_footer(text)
    return _trim_to_markdown_report(text)


def _strip_inline_footer(text: str) -> str:
    """Remove CLI stats even when they are not a clean trailing block."""
    lines = text.splitlines()
    kept: list[str] = []
    for line in lines:
        if _FOOTER_LINE_RE.match(line):
            break
        kept.append(line)
    return "\n".join(kept).strip()


def _trim_to_markdown_report(text: str) -> str:
    """Remove CLI/tool preamble before the model's actual markdown report."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("# ") or stripped.startswith("## "):
            return "\n".join(lines[i:]).strip()
    return text.strip()


def _ensure_tsg_structure(text: str, title: str) -> str:
    """Add missing top-level/first-section headings without changing content."""
    text = text.strip()
    if not text:
        return text
    has_h1 = any(line.startswith("# ") for line in text.splitlines())
    if not has_h1:
        text = f"# {title}\n\n" + text
    if "## 1." not in text and "## 1. " not in text:
        marker = "\n## 2."
        idx = text.find(marker)
        if idx > 0:
            before = text[:idx].rstrip()
            after = text[idx:].lstrip()
            lines = before.splitlines()
            if lines and lines[0].startswith("# "):
                h1 = lines[0]
                body = "\n".join(lines[1:]).strip()
                text = (
                    f"{h1}\n\n"
                    "## 1. Symptom\n\n"
                    f"{body}\n\n"
                    f"{after}"
                )
    lines = text.rstrip().splitlines()
    if lines and lines[-1].lstrip().startswith("#"):
        text = text.rstrip() + "\n\n_(N/A)_\n"
    return text


def _extract_markdown_block(text: str) -> str:
    """If the model wrapped its answer in ```markdown fences, extract it.

    Otherwise return the text as-is.
    """
    m = re.fullmatch(
        r"\s*```(?:markdown|md)\s*\n(.+?)\n```\s*",
        text, flags=re.DOTALL | re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()
    return text.strip()


def _salvage_markdown_from_output(raw: str, title: str) -> str:
    """Best-effort recovery when normal cleanup misclassifies valid output."""
    text = _strip_ansi(raw)
    text = _strip_inline_footer(text)
    text = _extract_markdown_block(text)
    text = _trim_to_markdown_report(text)
    text = _ensure_tsg_structure(text, title)
    return text.strip()


def _write_debug_output(raw: str, cleaned: str) -> Path | None:
    """Persist failed TSG cleanup details for troubleshooting."""
    try:
        root = (
            Path.home()
            / "OneDrive - Microsoft"
            / "Documents"
            / "VS-Code-Workspace"
            / "copilot-temp"
            / "sessions"
            / "tsg-summarizer-debug"
        )
        root.mkdir(parents=True, exist_ok=True)
        ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        p = root / f"{ts}.txt"
        p.write_text(
            "===== CLEANED =====\n"
            + cleaned
            + "\n\n===== RAW =====\n"
            + _strip_ansi(raw),
            encoding="utf-8",
        )
        return p
    except Exception:
        return None


# ---- prompt -----------------------------------------------------------------

_TSG_PROMPT_TEMPLATE = """\
你是一名 Azure 客户支持工程师。请把下面的"原始排查记录"提炼为一份
**Troubleshooting Guide (TSG)** 风格的 Markdown 文档。

原始排查记录已保存为本地文件：

{raw_file}

请先读取这个文件的内容后再生成报告；不要照搬原始笔记、聊天结论或附件
列表，必须做归纳、去重、整理和技术提炼。

【输出要求】
- 仅输出一段 Markdown，**不要**任何前言、解释、寒暄。
- 必须使用 `## ` 开头的二级标题；使用以下固定结构，每个二级标题都必须出现；若某节无信息，写
  "_(N/A)_"，不要省略：

```
# {title}

> _Automatically generated troubleshooting summary. Original records and redaction mappings are retained in the same folder._

## 1. Symptom
## 2. Scope / Impact
## 3. Investigation Steps
## 4. Root Cause
## 5. Mitigation / Resolution
## 6. Verification
## 7. References / Appendix
```

【内容规则】
- 用英文撰写报告，保留原始证据中的命令、错误信息和引用。
- 先从笔记、截图描述、上传文件链接、对话结论中抽取关键事实，再重组
  为可读的 TSG；不要保留原始记录里混乱的换行、表格残片、复制粘贴噪音。
- 对截图：只保留与错误、配置差异、关键证据相关的截图引用，并在图片前
  用一句话说明它证明了什么；没有证据价值的截图不要放入正文。
- 对上传的文件/文件夹：在"参考 / 附录"中整理为"证据文件"清单，说明
  文件类型和用途；不要逐行照搬本地 file:/// 路径。
- 对用户笔记中的 JSON/ARM/DCR 配置：提炼关键字段和配置差异，必要时用
  表格比较；大段 JSON 只摘录关键片段。
- 对 Copilot/人工结论：不能直接照搬，必须结合前文证据说明"为什么"。
- 命令、KQL、ARM 路径、错误信息可用代码块保留，但只保留关键片段。
- 排查步骤按时间/逻辑顺序编号；只保留**有信息量**的步骤，
  省略寒暄、确认、礼貌话术。
- 原始内容里如有图片引用 `![...](assets/xxx)`：
  - 仅当截图为错误截图、控制台、拓扑图、关键证据时**保留**该引用，
    放在最贴近的小节（通常是"现象"或"排查步骤"）。
  - 装饰性截图（头像、空白、无关页面）请**丢弃**。
- 不要编造未在原始内容里出现过的事实；不确定就写 "_(N/A)_"。
- 不要输出 ``` 代码围栏包裹整个文档；直接输出 Markdown。

请直接输出 TSG Markdown：
"""


# ---- main entry --------------------------------------------------------------

def summarize_to_tsg(
    raw_markdown: str,
    *,
    title: str = "Case Troubleshooting Guide",
    timeout: int = 600,
) -> str:
    """Run copilot CLI synchronously to produce TSG markdown.

    Raises RuntimeError on launcher resolution / non-zero exit / timeout.
    """
    program, prefix = _resolve_launcher()
    if not program:
        raise RuntimeError(
            "Copilot CLI was not found. Install GitHub Copilot CLI and ensure it is on PATH."
        )
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            suffix=".md",
            prefix="notepad-copilot-raw-archive-",
            delete=False,
        ) as f:
            f.write(raw_markdown)
            tmp_path = Path(f.name)
        raw_file = str(tmp_path)
        prompt = _TSG_PROMPT_TEMPLATE.format(title=title, raw_file=raw_file)
        argv = [
            program, *prefix,
            "-p", prompt,
            "--allow-all",
        ]
    except Exception as e:
        raise RuntimeError(f"Could not prepare the TSG input file: {e}") from e
    env = _build_env()
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW
    try:
        result = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            timeout=timeout,
            creationflags=creationflags,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"TSG refinement timed out (>{timeout}s). The input may be too large or the network slow."
        ) from e
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
    out = result.stdout.decode("utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(
            f"Copilot CLI exit code: {result.returncode}\n"
            f"Output tail:\n{_strip_ansi(out)[-1500:]}"
        )
    cleaned = _clean_cli_output(out)
    cleaned = _extract_markdown_block(cleaned)
    cleaned = _ensure_tsg_structure(cleaned, title)
    if not cleaned or len(cleaned) < 80:
        salvaged = _salvage_markdown_from_output(out, title)
        if salvaged and len(salvaged) >= 80:
            return salvaged
        debug_path = _write_debug_output(out, cleaned)
        debug_note = f"\nDebug output: {debug_path}" if debug_path else ""
        raise RuntimeError(
            "TSG refinement output was empty or too short. Raw output tail:\n"
            + _strip_ansi(out)[-1500:]
            + debug_note
        )
    return cleaned

"""Refine a raw archive markdown into a Troubleshooting Guide (TSG).

Spawns the same `copilot` CLI used by the chat pane, but synchronously
(blocking the caller — caller should run this off the GUI thread, or accept
a brief freeze with a busy cursor).
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from core.copilot_runner import (
    _build_env,
    _resolve_launcher,
    _strip_ansi,
)

# ---- footer / chrome cleanup -------------------------------------------------

# Lines from copilot's progress chrome that we want to drop from the TSG output.
_FOOTER_LINE_RE = re.compile(
    r"^\s*(Changes\b|Requests\b|Tokens\b|↑|↓|Premium\b)"
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
    return "\n".join(body).strip()


def _extract_markdown_block(text: str) -> str:
    """If the model wrapped its answer in ```markdown fences, extract it.

    Otherwise return the text as-is.
    """
    m = re.search(
        r"```(?:markdown|md)?\s*\n(.+?)\n```",
        text, flags=re.DOTALL | re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()
    return text.strip()


# ---- prompt -----------------------------------------------------------------

_TSG_PROMPT_TEMPLATE = """\
你是一名 Azure 客户支持工程师。请把下面的"原始排查记录"提炼为一份
**Troubleshooting Guide (TSG)** 风格的 Markdown 文档。

【输出要求】
- 仅输出一段 Markdown，**不要**任何前言、解释、寒暄。
- 使用以下固定结构，每个二级标题都必须出现；若某节无信息，写
  "_(N/A)_"，不要省略：

```
# {title}

> _自动生成的排查总结。原始记录与脱敏映射保留在同目录。_

## 1. 现象 (Symptom)
## 2. 影响范围 (Scope / Impact)
## 3. 排查步骤 (Investigation Steps)
## 4. 根因 (Root Cause)
## 5. 缓解 / 解决方案 (Mitigation / Resolution)
## 6. 验证 (Verification)
## 7. 参考 / 附录 (References / Appendix)
```

【内容规则】
- 中文为主，技术术语保留英文。
- 命令、KQL、ARM 路径、错误信息一律用代码块原样保留。
- 排查步骤按时间/逻辑顺序编号；只保留**有信息量**的步骤，
  省略寒暄、确认、礼貌话术。
- 原始内容里如有图片引用 `![...](assets/xxx)`：
  - 仅当截图为错误截图、控制台、拓扑图、关键证据时**保留**该引用，
    放在最贴近的小节（通常是"现象"或"排查步骤"）。
  - 装饰性截图（头像、空白、无关页面）请**丢弃**。
- 不要编造未在原始内容里出现过的事实；不确定就写 "_(N/A)_"。
- 不要输出 ``` 代码围栏包裹整个文档；直接输出 Markdown。

【原始排查记录开始】
---
{raw}
---
【原始排查记录结束】

请直接输出 TSG Markdown：
"""


# ---- main entry --------------------------------------------------------------

def summarize_to_tsg(
    raw_markdown: str,
    *,
    title: str = "案例排查指南",
    timeout: int = 600,
) -> str:
    """Run copilot CLI synchronously to produce TSG markdown.

    Raises RuntimeError on launcher resolution / non-zero exit / timeout.
    """
    program, prefix = _resolve_launcher()
    if not program:
        raise RuntimeError(
            "未找到 copilot CLI。请确认 GitHub Copilot CLI 已安装并在 PATH 中。"
        )
    prompt = _TSG_PROMPT_TEMPLATE.format(title=title, raw=raw_markdown)
    argv = [program, *prefix, "-p", prompt, "--allow-all"]
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
            f"TSG 精炼超时（>{timeout}s），可能是输入过大或网络慢。"
        ) from e
    out = result.stdout.decode("utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(
            f"copilot CLI 退出码 {result.returncode}\n"
            f"输出尾部:\n{_strip_ansi(out)[-1500:]}"
        )
    cleaned = _clean_cli_output(out)
    cleaned = _extract_markdown_block(cleaned)
    if not cleaned or "##" not in cleaned:
        raise RuntimeError(
            "TSG 精炼输出不像 Markdown（没有任何 ## 小节）；原始输出尾部:\n"
            + _strip_ansi(out)[-1500:]
        )
    return cleaned

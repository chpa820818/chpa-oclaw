# Notepad + Copilot CLI

> Windows 桌面工具：把"OneNote 风格的笔记"和"GitHub Copilot CLI 交互"放在同一个窗口里，
> 按"案例 (Case)"组织内容，方便排障/调研工作流的全流程记录与归档。

---

## ✨ 功能概览

| 区域 | 能力 |
|---|---|
| 📝 **笔记编辑器**（顶部） | 富文本 + Markdown 兼容；`Ctrl+V` 粘贴截图；多目标拖拽/上传文件、文件夹及子目录；附件自动落到 `<case>/.attachments/` |
| 🤖 **Copilot 交互区**（中部） | 内嵌 `copilot` CLI 子进程；每个案例窗口独享 session（`--name` / `--resume`），互不串扰；自动剥离 AI thinking 内容 |
| 📊 **结果快照**（底部） | 自动汇总 Q&A，可导出 Markdown |
| 🗂 **案例 (Case)** | 三选项菜单：新建 / 打开 / 关闭；列表按"最后修改"倒序；每个案例独立 UI 窗口 |
| 🔐 **多 Azure 账户** | 内置 `az` 账户切换栏，支持 Global / China 双云、多账户共存 |
| ☁️ **归档** | 一键归档当前案例到本地 ZIP 或 Azure DevOps Wiki；带敏感信息脱敏（订阅 ID、Key、Bearer Token、邮箱等） |
| 📁 **可配置案例根目录** | 首次新建案例时弹出目录选择；后续可在"打开案例"对话框中切换；持久化到 `~/.copilot/notepad-copilot/settings.json` |

---

## 🚀 快速开始

### 一键安装（推荐）

右键 → 「以管理员身份运行」`install.bat`，脚本会：

1. 通过 `winget` 自动检测/安装 Python 3.12、Node.js LTS、GitHub Copilot CLI、Azure CLI
2. 创建独立 `.venv` 并安装 `requirements.txt`
3. 生成 `launch.bat`（用 `pythonw.exe`，无黑窗）
4. 在桌面 + 开始菜单创建快捷方式

完成后双击桌面的 **Notepad + Copilot** 即可启动。

> 详细步骤、可选参数、排障表请见 **[INSTALL.md](INSTALL.md)**。

### 手动安装

```powershell
# 前置：Python 3.10+、Node.js LTS、Copilot CLI (npm i -g @github/copilot)、Azure CLI
git clone https://github.com/chpa820818/chpa-oclaw.git
cd chpa-oclaw\notepad-copilot
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\pythonw.exe main.py
```

---

## 📂 项目结构

```
notepad-copilot/
├── main.py                  # 入口
├── requirements.txt
├── install.ps1 / install.bat / run.bat
├── INSTALL.md               # 安装/配置完整文档
├── core/                    # 业务逻辑（无 UI 依赖）
│   ├── case_store.py        # 案例 CRUD + 案例根目录设置
│   ├── copilot_runner.py    # Copilot CLI 子进程封装（per-window session）
│   ├── markdown_io.py       # 笔记 ↔ Markdown 序列化
│   ├── archive.py           # 案例打包归档
│   ├── redact.py            # 敏感信息脱敏（SAS / Key / Token / Email…）
│   ├── tsg_summarizer.py    # AI 输出净化（剥离 thinking、footer）
│   ├── az_account.py        # az CLI 账户管理
│   ├── wiki_config.py       # ADO Wiki 配置
│   └── wiki_uploader.py     # ADO Wiki REST 客户端
└── ui/                      # PySide6 视图层
    ├── main_window.py       # 主窗口、菜单、案例 picker
    ├── editor_pane.py       # 笔记编辑器
    ├── chat_pane.py         # Copilot 交互
    ├── result_pane.py       # 结果快照
    ├── archive_dialogs.py
    ├── cloud_archive_dialog.py
    ├── wiki_settings.py
    ├── az_bar.py
    └── theme.py
```

---

## 🗃 数据存储

| 内容 | 位置 |
|---|---|
| 案例数据 | `<案例根目录>/<case_id>/`（首次新建时由用户选择） |
| 案例配置 | `~/.copilot/notepad-copilot/settings.json` |
| Wiki 归档配置 | `~/.copilot/notepad-copilot/wiki.json` |

每个案例目录布局：

```
<case_id>/
├── case.json              # 元数据（标题、创建/更新时间）
├── note.md                # 笔记正文
├── .attachments/          # 截图、上传文件
├── chat-history.jsonl     # AI 对话历史（一行一条 Q/A）
├── result-snapshot.md     # 结果汇总
└── archives/<时间戳>-(local|cloud)-archive/
```

---

## 🛠 技术栈

- **UI**：PySide6 6.6+（Qt for Python）
- **AI 后端**：`@github/copilot` CLI（多账户/多 session）
- **Azure 集成**：`az` CLI（Global + China 双云）
- **打包脚本**：纯 PowerShell + winget，无需额外构建工具

---

## 🙋 作者

[@chpa820818](https://github.com/chpa820818) · 内部排障工作流自用工具，欢迎 fork & 改造。

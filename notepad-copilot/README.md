# Notepad + Copilot CLI

> Windows 桌面工具：把"OneNote 风格的笔记"和"GitHub Copilot CLI 交互"放在同一个窗口里，
> 按"案例 (Case)"组织内容，方便排障/调研工作流的全流程记录与归档。

**当前发布版本**：[`v0.1.3`](https://github.com/chpa820818/chpa-oclaw/releases/tag/v0.1.3)

**下载发布包**：[`notepad-copilot-v0.1.3.zip`](https://github.com/chpa820818/chpa-oclaw/releases/download/v0.1.3/notepad-copilot-v0.1.3.zip)

---

## ✨ 功能概览

| 区域 | 能力 |
|---|---|
| 📝 **笔记编辑器**（顶部） | 富文本 + Markdown 兼容；`Ctrl+V` 粘贴截图；多目标拖拽/上传文件、文件夹及子目录；附件自动落到 `<case>/.attachments/` |
| 🤖 **Copilot 交互区**（底部） | 默认通过 SDK 保持每窗口独立会话，支持运行中补充要求；可切换传统 `--name` / `--resume` 模式 |
| 📊 **结果区**（右侧） | HTML 呈现完整回答，保留 Markdown 结构；自动汇总 Q&A，可导出 Markdown |
| 🗂 **案例 (Case)** | 三选项菜单：新建 / 打开 / 关闭；列表按"最后修改"倒序；每个案例独立 UI 窗口 |
| 🔐 **多 Azure 账户** | 内置 `az` 账户切换栏，支持 Global / China 双云、多账户共存 |
| ☁️ **归档** | 一键归档当前案例到本地 ZIP 或 Azure DevOps Wiki；带敏感信息脱敏（订阅 ID、Key、Bearer Token、邮箱等） |
| 📁 **可配置案例根目录** | 首次新建案例时弹出目录选择；后续可在"打开案例"对话框中切换；持久化到 `~/.copilot/notepad-copilot/settings.json` |

---

## v0.1.3 新增功能

### 笔记 / 结果区查找

- 点击区域标题栏「查找」，或在对应区域按 `Ctrl+F`；选中文字可自动填入。
- 实时、不区分大小写搜索；全部匹配高亮，当前匹配以橙色标记，并显示位置/总数。
- `Enter` / `F3` 下一个，`Shift+Enter` / `Shift+F3` 上一个，首尾循环；`Esc` 关闭。
- 两个区域独立查找；高亮不会改变保存内容、富文本格式或撤销记录。

### 运行中插嘴

默认开启「运行中插嘴」。任务运行时继续输入，按 Enter 或点击「补充要求」，
即可向同一任务发送新条件或问题。GPT-5.6 和 GPT-6 均可使用，不要求 GPT-6 专属 API。

- 界面区分提交中、CLI 已接收、未送达，并根据运行时事件提示当前任务或后续轮次。
- SDK 使用 `mode="immediate"`：补充在下一次模型请求前加入；到达太晚会接续下一轮。
- 原问题与已接收的补充一起保留在结果和案例历史；未送达内容保留在输入框或「取回未发送」中。
- 首条自动携带笔记和图片；开启「自动同步笔记更新」时，后续发送/补充会携带更新内容。
- 「停止」取消任务并清理队列；「清除会话」创建新会话。切换会话模式也会重置 AI 上下文，但不删除笔记或已归档结果。
- 支持用户默认、GPT-5.6 Sol / Sol Fast、GPT-6 Astra；实际可用模型以 Copilot 账户权限为准。

**边界**：这是 Copilot CLI 任务循环层的 steering，不会改写已发出的单次推理请求，
也不会即时中断或撤销已提交的工具操作。紧急情况应点击「停止」，不要依赖补充消息阻止操作。
关闭「运行中插嘴」后回到传统 `copilot -p` 模式，不支持运行中追加。

### 升级要求

插嘴需要 **Python 3.11+（推荐 3.12）**；Python 3.10 仍可使用传统模式。
已有安装请在应用目录使用对应的 Python 更新依赖，然后重新打开应用：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

实时模式固定使用 `github-copilot-sdk==1.0.11`，复用 CLI 登录，显式启用本地配置、Skill 和指令发现。
优先使用已完整安装的最新 CLI 缓存版本，避免旧 npm 引导包启动过时运行时；不会自动下载另一份 SDK 运行时。
需要指定位置时，将 `NOTEPAD_COPILOT_RUNTIME` 设置为已安装的 `index.js` 或原生 `.exe` 绝对路径。
第三方 MCP 的认证行为可能与交互 CLI 不完全相同；认证/兼容性错误会明确显示，不会静默降级。

参考：[Copilot SDK steering 与排队](https://github.com/github/copilot-sdk/blob/v1.0.11/docs/features/steering-and-queueing.md)。

---

## 🚀 快速开始

### 1️⃣ 获取代码

> ⚠️ 本仓库还有其它子目录（如 `eBPF分析器/`），如果你 **只想要这个工具**，建议用「方式 A」或「方式 B」按需下载。

**方式 A · 一行命令只下载 `notepad-copilot/`**（推荐，最快、不拉其它内容）

Windows 10/11 自带 `curl.exe` 和 `tar.exe`，可在 PowerShell 里直接执行：

```powershell
curl.exe -L -o repo.tar.gz https://github.com/chpa820818/chpa-oclaw/archive/refs/heads/main.tar.gz
tar -xzf repo.tar.gz --strip-components=1 chpa-oclaw-main/notepad-copilot
del repo.tar.gz
cd notepad-copilot
```

完成后当前目录下只会有 `notepad-copilot\`，没有其它子目录。

**方式 B · `git sparse-checkout` 只检出本子目录**（保留 git 历史，方便 `git pull`）

```powershell
git clone --filter=blob:none --no-checkout --depth=1 https://github.com/chpa820818/chpa-oclaw.git
cd chpa-oclaw
git sparse-checkout init --cone
git sparse-checkout set notepad-copilot
git checkout main
cd notepad-copilot
```

工作区只展开 `notepad-copilot/`；以后想拉新版直接 `git pull` 即可。

**方式 C · 完整 clone**（如果你也想看 repo 里的其它项目）

```powershell
git clone https://github.com/chpa820818/chpa-oclaw.git
cd chpa-oclaw\notepad-copilot
```

**方式 D · 下载发布包 ZIP**（无需任何命令行工具，推荐给普通用户）

1. 打开最新发布页：https://github.com/chpa820818/chpa-oclaw/releases/tag/v0.1.3
2. 下载 **`notepad-copilot-v0.1.3.zip`**
3. 解压后进入 `notepad-copilot\` 目录

---

### 2️⃣ 一键安装（推荐）

进入 `notepad-copilot\` 目录后，右键 → 「以管理员身份运行」`install.bat`，脚本会：

1. 通过 `winget` 自动检测/安装 Python 3.12、Node.js LTS、GitHub Copilot CLI、Azure CLI
2. 创建独立 `.venv` 并安装 `requirements.txt`
3. 生成 `launch.bat`（用 `pythonw.exe`，无黑窗）
4. 在桌面 + 开始菜单创建快捷方式

完成后双击桌面的 **Notepad + Copilot** 即可启动。

> 详细步骤、可选参数、排障表请见 **[INSTALL.md](INSTALL.md)**。

### 3️⃣ 手动安装（不想用一键脚本时）

```powershell
# 前置：Python 3.11+、Node.js LTS、Copilot CLI (npm i -g @github/copilot)、Azure CLI
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
├── core/                    # 业务逻辑与 Qt 信号适配
│   ├── case_store.py        # 案例 CRUD + 案例根目录设置
│   ├── copilot_runner.py    # Copilot CLI 子进程封装（per-window session）
│   ├── conversation_runner.py # 实时/传统模式的统一接口
│   ├── live_runner.py       # SDK 持久会话、运行中补充及生命周期
│   ├── runtime_launcher.py  # 已安装 CLI 运行时定位
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
    ├── find_bar.py          # 笔记/结果区共享查找栏
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
- **AI 后端**：`@github/copilot` CLI + `github-copilot-sdk`（每窗口独立 session）
- **Azure 集成**：`az` CLI（Global + China 双云）
- **打包脚本**：纯 PowerShell + winget，无需额外构建工具

---

## 🙋 作者

[@chpa820818](https://github.com/chpa820818) · 内部排障工作流自用工具，欢迎 fork & 改造。

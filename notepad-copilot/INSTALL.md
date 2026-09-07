# Notepad + Copilot — 安装与配置指南

桌面工具：上半部分富文本笔记（OneNote 风格，文字+截图+日志/文件夹附件），
下半部分 Copilot CLI 对话区。每个案例独立窗口，独立 AI 会话。

---

## 0. 一键安装（推荐）

**最简单做法**：在工具目录下双击 `install.bat`。

或在 PowerShell 中执行：

```powershell
cd "<...>\copilot-workspace\tools\notepad-copilot"
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

`install.ps1` 会自动：

1. 检测 / 安装 Python 3.11+（缺失时通过 `winget` 装 Python 3.12）
2. 检测 / 安装 Node.js LTS + GitHub Copilot CLI (`npm i -g @github/copilot`)
3. 检测 / 安装 Azure CLI（`-SkipAzureCli` 可跳过）
4. 在工具目录创建 `.venv` 虚拟环境并安装 PySide6 等依赖
5. 生成无控制台窗口启动脚本 `launch.bat`（用 `pythonw.exe`）
6. 在桌面 + 开始菜单创建 **Notepad + Copilot** 快捷方式

可选参数：

| 参数 | 作用 |
| --- | --- |
| `-SkipAzureCli` | 跳过 Azure CLI 检测/安装 |
| `-NoShortcut`   | 不创建桌面/开始菜单快捷方式 |
| `-Force`        | 强制重建 `.venv`（依赖出错时使用） |

安装完成后双击桌面快捷方式即可启动；首次使用 AI 对话前需执行一次
`copilot` 在终端完成浏览器登录。

> 如系统受组策略限制无法运行 PowerShell 脚本，请改走下面的「手动安装」流程。

---

## 1. 系统要求

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Windows 10 / 11（PowerShell 5+ 或 7+） |
| Python | **3.11+**（推荐 3.12；3.10 仅支持传统模式） |
| GitHub Copilot CLI | `copilot` 命令需在 `PATH` 中 |
| Git | 仅在使用云端归档（Azure DevOps Wiki）时需要 |
| Azure CLI | 仅当使用云端归档/账户切换功能时需要 |

---

## 2. 获取代码

工具位于本地仓库的 `copilot-workspace\tools\notepad-copilot\` 目录。
如果是新机器从零部署：

```powershell
# 假设仓库已 clone 到 OneDrive
cd "C:\Users\<你的用户名>\OneDrive - Microsoft\Documents\VS-Code-Workspace\copilot-workspace\tools\notepad-copilot"
```

---

## 3. 安装 Python（如未安装）

推荐使用官方安装包（**不要**用 Microsoft Store 版，会被 WindowsApps 重定向，
无法被 PySide6 正确加载）：

1. 下载：<https://www.python.org/downloads/windows/>
2. 安装时勾选 **“Add python.exe to PATH”**
3. 默认安装路径：`C:\Users\<你>\AppData\Local\Programs\Python\Python312\`

验证：

```powershell
python --version           # 插嘴功能需要 3.11 或更高
python -c "import sys; print(sys.executable)"
```

如果 `python` 走的是 `WindowsApps\python.exe`，请改用绝对路径：

```powershell
$py = "C:\Users\$env:USERNAME\AppData\Local\Programs\Python\Python312\python.exe"
& $py --version
```

---

## 4. 安装依赖

```powershell
cd "<...>\copilot-workspace\tools\notepad-copilot"
pip install -r requirements.txt
```

`requirements.txt`：

```
PySide6>=6.6
markdown>=3.5
github-copilot-sdk==1.0.11; python_version >= "3.11"
```

如使用绝对路径的 Python，请用对应的 pip：

```powershell
& $py -m pip install -r requirements.txt
```

---

## 5. 安装 / 配置 Copilot CLI

```powershell
# 通过 npm 安装（需 Node.js 18+）
npm install -g @github/copilot

# 验证
copilot --version
copilot --help | Select-String "session|resume"
```

首次运行 `copilot` 会弹出登录流程（浏览器认证）。
默认开启「运行中插嘴」：SDK 为每窗口创建独立 UUID 会话，复用 CLI 已登录账户。
任务进行中可以输入新要求，按 Enter 或点击「补充要求」发送；界面显示提交和接收状态。
补充在下一次模型请求前生效，若本轮已结束则接续下一轮，不会撤销已执行操作。

关闭该选项使用传统 `--name <session>` / `--resume=<session>` 模式，
不支持运行中追加。切换模式会重置 AI 会话，不删除笔记或已归档结果。

实时模式优先使用已完整安装的最新缓存运行时（本机已接通 CLI 1.0.84-1），
不以旧 npm 引导包的版本作为当前 CLI 版本。若握手或任务完成检查报版本不兼容，
先更新 CLI；也可将 `NOTEPAD_COPILOT_RUNTIME` 设置为已安装的 `index.js` / 原生 `.exe` 绝对路径。
不要将它设置为 VS Code 的 `.ps1` / `.bat` 引导脚本。

---

## 6. （可选）安装 Azure CLI

仅当需要使用「账户/订阅栏」「云端归档」时：

```powershell
winget install -e --id Microsoft.AzureCLI
az --version
```

默认在 Azure China 环境下工作；如需切换：

```powershell
az cloud set --name AzureChinaCloud   # 或 AzureCloud
az login
```

---

## 7. 启动工具

任选其一：

**方式 A — 双击批处理**

直接双击目录下的 `run.bat`。

**方式 B — PowerShell 启动**

```powershell
cd "<...>\copilot-workspace\tools\notepad-copilot"
python main.py
```

**方式 C — 使用绝对路径 Python（推荐，避免 WindowsApps 重定向问题）**

```powershell
$py = "C:\Users\$env:USERNAME\AppData\Local\Programs\Python\Python312\python.exe"
Set-Location "<...>\copilot-workspace\tools\notepad-copilot"
Start-Process -FilePath $py -ArgumentList "main.py" -WorkingDirectory (Get-Location)
```

---

## 8. 数据目录

| 目录 | 路径 | 说明 |
| --- | --- | --- |
| 案例数据根 | `<repo>\copilot-workspace\reports\cases\` | 每个案例一个子目录 |
| 单个案例结构 | `<case>\note.md`、`.attachments\`、`chat-history.jsonl`、`archives\`、`meta.json` | 笔记/附件/对话/归档/元数据 |
| Wiki 配置 | `%USERPROFILE%\.copilot\notepad-copilot\wiki.json` | 云端归档 Profile |
| 运行日志 | `<repo>\copilot-workspace\tools\notepad-copilot\copilot-runner.log` | Copilot CLI 启动诊断 |

---

## 9. 云端归档（Azure DevOps Wiki）配置

首次使用时通过「☁ 云端归档…」按钮触发；或手动编辑：

**`%USERPROFILE%\.copilot\notepad-copilot\wiki.json`**

```json
{
  "profiles": [
    {
      "name": "Mooncake Networking Pod",
      "organization": "https://dev.azure.com/CSS-Mooncake",
      "project": "MCVKB",
      "wiki_identifier": "Mooncake-Networking-PoD.wiki",
      "parent_path": "/Cases",
      "api_version": "7.0"
    }
  ],
  "default": "Mooncake Networking Pod"
}
```

身份认证使用本机 `az` CLI 当前登录的账户（DevOps 需有写权限）。
如果云端归档对话框里支持粘贴完整 Wiki URL，会自动解析出
`organization / project / wiki_identifier / parent_path` 并填入。

---

## 10. UI 概览

顶部菜单栏只有三个动作：

| 按钮 | 快捷键 | 行为 |
| --- | --- | --- |
| 📂 新建案例 | `Ctrl+Shift+N` | 输入 case_id + 标题，新建并打开新窗口 |
| 📁 打开案例 | `Ctrl+Shift+O` | 弹列表（按最后修改/保存时间倒序），新窗口打开 |
| ✖ 关闭案例 | `Ctrl+Shift+W` | 保存笔记 → 关闭当前窗口 |

其他操作分布于：

- **账户栏**（顶部）：Az 账户切换、云环境切换、订阅、登入/登出/刷新
- **笔记卡片头部**：📎 上传…（文件 / 文件夹 / 混合）
- **结果卡片头部**：📦 本地归档 / ☁ 云端归档 / 清空
- **对话栏底部**：发送、停止、清除会话、自动同步笔记

---

## 11. 常用故障排查

| 现象 | 排查 |
| --- | --- |
| `ModuleNotFoundError: PySide6` | 用了 WindowsApps 的 python；改绝对路径 python，或重装非 Store 版 |
| 启动后窗口空白/无响应 | 检查 `copilot-runner.log`；确认 `copilot --version` 正常 |
| Copilot 回答串台 / 上下文混淆 | 已修复（每窗口独立 session name）。如复现，确认 CLI 支持 `--name` / `--resume=` |
| 云端归档失败 | 确认 `az login` 当前账户；Wiki Profile 中的 `wiki_identifier` 必须是 `<repo>.wiki` 形式 |
| 截图保存后再打开变成 URL | 已修复；如复现请提供 `note.md` 片段 |
| 「在文件管理器中打开案例目录」无反应 | 已改用 `QDesktopServices.openUrl` + `explorer.exe` 后备路径 |

---

## 12. 卸载

直接删除工具目录即可。如需彻底清理：

```powershell
Remove-Item -Recurse -Force "$env:USERPROFILE\.copilot\notepad-copilot"
# 注意：reports\cases 下的案例数据请先备份
```

---

## 13. 升级

工具是源代码运行，无需"安装"。直接 `git pull`（或拷贝新文件）后重启工具即可。
依赖如有变动：

```powershell
& $py -m pip install -r requirements.txt --upgrade
```

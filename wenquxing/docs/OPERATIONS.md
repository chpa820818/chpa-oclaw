# 运维与排障

## 运行位置

| 项目 | 默认路径 |
|---|---|
| 程序 | `%LOCALAPPDATA%\Programs\WenquxingV02` |
| 私有配置 | `%LOCALAPPDATA%\Wenquxing\secrets\v02.env` |
| SQLite | `%LOCALAPPDATA%\Wenquxing\v02\wenquxing-v02.sqlite3` |
| 日志 | `%LOCALAPPDATA%\Wenquxing\v02\wenquxing-v02.log` |
| 会话文件目录 | `%LOCALAPPDATA%\Wenquxing\v02\sessions\<内部会话标识>` |
| Copilot 会话 | `%USERPROFILE%\.copilot` |

日志自动轮转，单文件上限 5 MB，保留 3 份。SDK 使用 WARNING 级别，避免把临时 WebSocket
ticket 写入日志。

## 健康检查

```powershell
$env:WX_V2_CONFIG="$env:LOCALAPPDATA\Wenquxing\secrets\v02.env"
& "$env:LOCALAPPDATA\Programs\WenquxingV02\.venv\Scripts\python.exe" `
  -m wenquxing_v2.cli doctor
```

正常输出包含 `"status": "ready"`、SQLite 路径、Copilot CLI 版本和任务状态统计。

## 常见问题

| 现象/错误 | 处理 |
|---|---|
| 卡片错误 `200340` | 配置 `card.action.trigger` 长连接回调，并发布新应用版本 |
| 卡片错误 `200341` | 回调超过 3 秒；检查本机负载和日志 |
| 收不到消息 | 检查 `im.message.receive_v1`、应用版本、机器人可用范围和长连接进程 |
| 消息被拒绝 | 核对 `tenant_key`、当前应用对应的 `open_id`，并确认是机器人单聊 |
| Copilot CLI 退出码非 0 | 运行 `copilot -p "测试" --silent` 检查登录、网络和额度 |
| `文曲星 v02 已在运行` | 单实例锁正常；不要重复启动 |
| 永久删除被拒绝 | 目标会话仍在处理消息，等待回复完成后重试 |
| 附件下载错误 `234009` | 开通 `im:message` 权限并发布新应用版本 |
| 视频只返回画面结论 | 当前仅提取视觉关键帧，不包含音轨转录 |
| ZIP 被拒绝 | 检查体积、文件数、加密、路径穿越或压缩炸弹限制 |

## 升级

下载新版发布包并再次运行 `install.bat`。安装器停止该安装目录下的旧实例、更新程序和依赖，
保留配置、SQLite 与 Copilot 会话，然后重新启动。

升级前建议备份：

```powershell
Copy-Item "$env:LOCALAPPDATA\Wenquxing\v02" `
  "$env:USERPROFILE\Documents\Wenquxing-v02-backup" -Recurse
```

凭据应单独使用受保护介质备份，不要放入普通源码压缩包。

## 卸载

默认只移除程序和自启动项，保留数据及凭据：

```powershell
.\uninstall.ps1
```

同时删除文曲星数据或配置：

```powershell
.\uninstall.ps1 -RemoveData -RemoveCredentials
```

卸载不会批量删除 `%USERPROFILE%\.copilot` 中的其他 Copilot CLI 会话。

# 架构与安全

## 数据流

```text
飞书本人单聊
    │  WebSocket: im.message.receive_v1 / card.action.trigger
    ▼
接入处理器 ── 身份校验 ── message_id 去重 ── SQLite 持久化
    │
    ▼
后台任务调度器 ── 下载到当前会话目录 ── 类型/大小校验
    │
    ▼
Copilot CLI -C <全部会话根目录> --session-id=<专属 UUID>
            ├─ 文件增删改查 + 原生 attachment
            └─ 受限 wenquxing-file 图片处理
    │
    ▼
飞书文字回复 + 图片/文件上传与发送 API
```

## 会话模型

SQLite 保存用户可见名称、内部标识、Copilot session UUID、当前选择和任务状态。内部标识
不会显示在飞书卡片中，但用于防止旧卡片误操作同名或新建的会话。

每次调用均显式传递 `--session-id`，禁止使用 `--continue`，因此不同会话不会串入全局最近
会话。由于任一 Copilot 会话都可以修改全部文曲星会话目录，涉及 Copilot 的任务在进程内
全局串行，避免两个会话同时修改同一文件。

每个会话同时拥有 `%LOCALAPPDATA%\Wenquxing\v02\sessions\<内部标识>` 工作目录。飞书
上传的原始文件按消息存放其中；后续普通消息仍以全部会话目录为工作根，因此 Copilot 可以
继续引用当前会话或其他文曲星会话的文件。改名不移动目录，隐藏不删除目录，永久删除会同步
清理目录。

## 删除语义

- **隐藏**：设置归档时间，从活动列表排除；保留 SQLite 消息、任务和 Copilot 记忆。
- **恢复**：清除归档时间并设为当前会话，继续使用原 Copilot session。
- **永久删除**：二次确认后，删除会话关联任务与消息、SQLite 映射、
  `%USERPROFILE%\.copilot\session-state\<UUID>` 以及 `session-store.db` 中该 UUID 的记录。

永久删除不影响其他 Copilot CLI 会话。若目标会话仍在处理消息，操作会被拒绝并要求稍后重试。

## 信任边界

- 仅接受配置的 `tenant_key + owner_open_id + p2p`。
- 飞书事件处理线程只做校验和持久化，不等待模型。
- Copilot 子进程不会继承 `WX_APP_SECRET` 等应用环境变量。
- Copilot 禁用工具、内置 MCP、自定义指令、ask-user、远程控制和远程导出。
- Copilot 对 `%LOCALAPPDATA%\Wenquxing\v02\sessions` 及其子目录拥有完整文件增删改查
  权限，但不开放 `--allow-all-paths`，根目录外访问会被拒绝。
- 文件写入使用 Copilot 的受控 `write` 权限；Shell、网络、MCP、远程控制及飞书凭据继续
  禁用。唯一允许的命令是受限的 `wenquxing-file`，其输入输出路径均强制限制在会话根目录。
- 图片和原生文档可显式附加；文本和代码由 Copilot 文件工具自行读取或修改。
- 用户明确要求“发给我”时，最终文件写入该任务独立的 `outbox\job-<id>`；Worker 最多
  回传 5 个、单个不超过 30 MB 的普通文件。图片走飞书图片消息，其他类型走文件消息。
- 视频仅通过受限 FFmpeg 进程生成视觉帧；ZIP 只做安全展开，不执行内容，并防护路径穿越
  和压缩炸弹；实际理解和回答仍由 Copilot 完成。
- Copilot 仍会接触用户消息文本；它不是本地离线模型。
- SQLite、日志和凭据目录通过 Windows ACL 限制为当前用户与 SYSTEM。

## 可靠性边界

飞书接收按 `message_id` 唯一约束去重。任务在 SQLite 中持久化，进程重启会恢复运行中的任务。
回复使用基于消息 ID 的稳定 UUID，降低平台重试导致的重复。跨飞书与本机数据库无法提供严格的
分布式“恰好一次”，目标是持久化恢复和尽量减少重复。

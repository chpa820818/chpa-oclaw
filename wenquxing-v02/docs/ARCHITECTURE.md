# 架构与安全

## 数据流

```text
飞书本人单聊
    │  WebSocket: im.message.receive_v1 / card.action.trigger
    ▼
接入处理器 ── 身份校验 ── message_id 去重 ── SQLite 持久化
    │
    ▼
后台任务调度器 ── 同会话串行 / 不同会话并行
    │
    ▼
Copilot CLI --session-id=<专属 UUID>
    │
    ▼
飞书回复 API
```

## 会话模型

SQLite 保存用户可见名称、内部标识、Copilot session UUID、当前选择和任务状态。内部标识
不会显示在飞书卡片中，但用于防止旧卡片误操作同名或新建的会话。

每次调用均显式传递 `--session-id`，禁止使用 `--continue`，因此不同会话不会串入全局最近
会话。一个会话只允许一个任务运行，确保消息顺序和记忆顺序一致。

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
- Copilot 仍会接触用户消息文本；它不是本地离线模型。
- SQLite、日志和凭据目录通过 Windows ACL 限制为当前用户与 SYSTEM。

## 可靠性边界

飞书接收按 `message_id` 唯一约束去重。任务在 SQLite 中持久化，进程重启会恢复运行中的任务。
回复使用基于消息 ID 的稳定 UUID，降低平台重试导致的重复。跨飞书与本机数据库无法提供严格的
分布式“恰好一次”，目标是持久化恢复和尽量减少重复。

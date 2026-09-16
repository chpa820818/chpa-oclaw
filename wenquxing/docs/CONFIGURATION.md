# 安装与飞书配置

## 1. 前置条件

- Windows 10/11，能够长期登录运行。
- Python 3.12 或更高版本，并在安装时加入 PATH。
- GitHub Copilot CLI 1.0.83 或兼容版本，已完成登录并能执行：

```powershell
copilot --version
copilot -p "只回复 OK" --silent
```

- 飞书组织内企业自建应用的创建和发布权限。

## 2. 创建企业自建应用

1. 使用管理员或开发者账号进入[飞书开放平台](https://open.feishu.cn/app)。
2. 点击 **创建企业自建应用**，填写应用名称（建议 `文曲星`）、描述和图标。
3. 打开应用详情页，在 **添加应用能力** 中选择 **机器人**，启用机器人能力。
4. 在机器人设置中填写机器人名称和说明；首版只用于本人单聊，不需要群聊能力。
5. 在 **应用发布 → 版本管理与发布** 中，将可用范围限制为本人或专用测试成员。

## 3. 获取 App ID 和 App Secret

进入应用详情页的 **基础信息 → 凭证与基础信息**：

1. 复制 **App ID**，安装时填入 `WX_APP_ID`。
2. 点击查看并复制 **App Secret**，安装时填入 `WX_APP_SECRET`。
3. 不要把 App Secret 放进聊天、截图、GitHub、Issue 或普通日志；泄露后应立即在该页面
   重置，并重新执行 `.\install.ps1 -ForceConfigure`。

App ID 通常以 `cli_` 开头。App Secret 只用于本机换取应用访问令牌，不会传给 Copilot CLI。

## 4. 开通最小权限

进入 **开发配置 → 权限管理**，搜索并开通以下应用身份权限：

| 权限标识 | 飞书页面名称 | 用途 |
|---|---|---|
| `im:message.p2p_msg:readonly` | 读取用户发给机器人的单聊消息 | 接收本人私聊消息 |
| `im:message:send_as_bot` | 以应用的身份发消息 | 回复文本、卡片和主动上线通知 |
| `im:message` | 获取与发送单聊、群组消息 | 下载消息中的图片、视频和文件资源 |

`im:message` 是飞书“获取消息中的资源文件”接口要求的权限之一；文曲星虽然具备该接口
权限，程序仍会在本地强制拒绝群聊和非本人消息。无需申请通讯录、云文档或云盘权限。若组织
启用了审批，提交权限申请并等待管理员批准。

权限变更必须随新的应用版本发布才会对线上机器人生效。文曲星不需要
`im:message.group_at_msg:readonly`，因为当前版本拒绝群聊消息。

## 5. 配置消息事件长连接

进入 **开发配置 → 事件与回调 → 事件配置**：

1. 订阅方式选择 **使用长连接接收事件**。
2. 点击 **添加事件**，搜索并添加 `im.message.receive_v1`（接收消息）。
3. 确认事件使用的是 **应用身份订阅**。
4. 保存。

文曲星使用官方 `lark-oapi` WebSocket SDK 主动连接飞书，不需要公网 IP、域名或 HTTPS
回调地址。若页面要求填写“请求地址”，说明仍处于 Webhook 模式。

## 6. 配置卡片回调长连接

进入同一页面的 **回调配置**：

1. 订阅方式选择 **使用长连接接收回调**。
2. 点击 **添加回调**，搜索并添加 `card.action.trigger`（卡片回传交互）。
3. 保存。

若卡片提示错误码 `200340`，通常是未配置该回调，或修改后没有发布新应用版本。
错误码 `200341` 表示回调未在 3 秒内响应。

## 7. 获取 tenant_key 和本人 open_id

`tenant_key` 是当前飞书组织的租户标识；`open_id` 是用户在**当前应用**下的身份标识。
不要填写手机号、邮箱、`user_id` 或 `union_id`。

推荐通过一条真实的单聊消息获取：

1. 完成长连接事件配置，并创建、发布一个测试版本。
2. 确保文曲星已经启动并建立长连接，然后在飞书中向机器人发送一条单聊测试消息。
3. 回到开放平台，在 **事件与回调** 的事件记录/调试数据中打开这条
   `im.message.receive_v1` 事件。
4. 切换到原始 JSON，复制以下两个字段：

```text
event.sender.tenant_key          → WX_TENANT_KEY
event.sender.sender_id.open_id   → WX_OWNER_OPEN_ID
```

如果事件记录尚未出现，先确认应用版本已发布、机器人在可用范围内、权限审批已通过，
并且文曲星长连接进程正在运行。也可以在飞书开放平台 API 调试台查看同一应用产生的事件
数据，但不要使用其他应用返回的 open_id。

身份字段是文曲星的访问白名单：只有同时匹配该 `tenant_key`、`open_id` 且来自单聊的
消息才会处理。

如果首次安装前还不知道这两个值，可先运行安装器：App ID 和 App Secret 填真实值，
`tenant_key` 与 `open_id` 暂填 `pending`。服务仍能建立长连接，但会拒绝处理测试消息；
从事件记录取得真实字段后，运行 `.\install.ps1 -ForceConfigure` 重新填写并启动。不要长期
保留占位值。

## 8. 执行安装

解压发布包，双击 `install.bat`。安装器会：

1. 检查 Python 和 Copilot CLI。
2. 安装到 `%LOCALAPPDATA%\Programs\WenquxingV02`。
3. 将运行数据放在 `%LOCALAPPDATA%\Wenquxing\v02`。
4. 将私有配置放在 `%LOCALAPPDATA%\Wenquxing\secrets\v02.env`。
5. 移除配置目录的继承权限，仅保留当前用户和 SYSTEM。
6. 写入当前用户登录自启动项并启动长连接。

如需重新录入配置：

```powershell
.\install.ps1 -ForceConfigure
```

## 9. 发布并验证

1. 打开 **应用发布 → 版本管理与发布**。
2. 创建版本，确认机器人能力、权限、事件 `im.message.receive_v1`、回调
   `card.action.trigger` 和可用范围均已包含。
3. 提交审核并发布；仅保存开发配置不会影响线上版本。
4. 等待飞书显示版本已发布后再测试。

发布后向机器人发送：

```text
会话
```

应收到会话管理卡片。点击“新建并命名会话”，输入名称并保存，再发送普通问题验证回复。

## 10. 配置文件

```dotenv
WX_APP_ID=
WX_APP_SECRET=
WX_TENANT_KEY=
WX_OWNER_OPEN_ID=
WX_V2_DATA_DIR=
WX_COPILOT_CLI=
WX_COPILOT_TIMEOUT_SECONDS=180
WX_V2_WORKER_COUNT=4
WX_ATTACHMENT_MAX_MB=50
WX_ATTACHMENT_EXTRACT_MAX_MB=100
WX_ATTACHMENT_ARCHIVE_MAX_FILES=12
WX_V2_LOG_LEVEL=INFO
```

禁止将真实配置复制到源码目录、网盘共享目录、Issue 或日志。

## 11. 附件支持与限制

| 类型 | 处理方式 |
|---|---|
| PNG、JPEG、GIF、WEBP、BMP | 保存到会话目录，并作为原生图片附件交给 Copilot |
| PDF、DOCX、PPTX、XLSX | 保存到会话目录，使用 Copilot CLI 原生文档附件 |
| TXT、Markdown、CSV、JSON、日志及常见代码 | 保存后由 Copilot 文件工具自行处理 |
| MP4、MOV、AVI、MKV、WEBM、M4V | 提取前 120 秒内最多 8 张视觉关键帧 |
| ZIP | 原包保留在会话目录，安全解压后由 Copilot 分析支持的文件 |

默认单个下载文件不超过 50 MB，ZIP 解压后不超过 100 MB、最多 12 个文件。ZIP 中的
可执行文件、符号链接、嵌套压缩包、路径穿越和疑似压缩炸弹会被拒绝或跳过；任何内容都不会
执行。视频目前只分析画面，不转录音轨；RAR、7Z 和加密 ZIP 暂不支持。

每个文曲星会话对应一个稳定目录：

```text
%LOCALAPPDATA%\Wenquxing\v02\sessions\<内部会话标识>\
└─ files\<消息哈希>\
   ├─ 用户上传的原始文件
   └─ extracted / *-frames-*（需要时生成）
```

会话改名不会改变目录；隐藏会话会保留目录；永久删除会一并删除该会话目录。Copilot 每次
调用都以 `sessions` 作为受管工作根，可对所有文曲星会话目录执行读取、创建、修改、移动
和删除。目录外路径、Shell、网络、飞书凭据及其他本机文件仍不可访问。

飞书资源下载接口若返回 `234009`，表示 `im:message` 等必要权限未生效；检查权限审批，
创建新版本并重新发布应用。

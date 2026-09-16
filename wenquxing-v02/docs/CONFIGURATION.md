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

## 2. 创建飞书应用

1. 进入[飞书开放平台](https://open.feishu.cn/app)，创建企业自建应用。
2. 在 **添加应用能力** 中启用 **机器人**。
3. 在 **凭证与基础信息** 复制 App ID 和 App Secret。
4. 在 **权限管理** 开通机器人接收单聊消息、获取单聊消息和发送消息所需权限。
   飞书页面会展示权限的准确名称；若组织启用了审批，先完成审批。

## 3. 配置事件长连接

进入 **开发配置 → 事件与回调 → 事件配置**：

1. 订阅方式选择 **使用长连接接收事件**。
2. 添加 `im.message.receive_v1`（接收消息）。
3. 保存。

文曲星使用官方 `lark-oapi` WebSocket SDK 主动连接飞书，不需要公网 IP、域名或 HTTPS
回调地址。若页面要求填写“请求地址”，说明仍处于 Webhook 模式。

## 4. 配置卡片回调长连接

进入同一页面的 **回调配置**：

1. 订阅方式选择 **使用长连接接收回调**。
2. 添加 `card.action.trigger`（卡片回传交互）。
3. 保存。

若卡片提示错误码 `200340`，通常是未配置该回调，或修改后没有发布新应用版本。
错误码 `200341` 表示回调未在 3 秒内响应。

## 5. 获取 tenant_key 和 open_id

`tenant_key` 是租户标识，`open_id` 是当前应用下用户的身份标识。可以从飞书开放平台
事件调试数据或一次 `im.message.receive_v1` 事件中读取：

```text
event.sender.tenant_key
event.sender.sender_id.open_id
```

`open_id` 与应用绑定，不要使用其他应用返回的 open_id。文曲星只接受配置的租户和
用户发送的单聊消息。

## 6. 执行安装

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

## 7. 发布并验证

在飞书开放平台 **版本管理与发布** 中创建版本并发布。仅保存开发配置但不发布，线上机器人
可能仍使用旧配置。

发布后向机器人发送：

```text
会话
```

应收到会话管理卡片。点击“新建并命名会话”，输入名称并保存，再发送普通问题验证回复。

## 8. 配置文件

```dotenv
WX_APP_ID=
WX_APP_SECRET=
WX_TENANT_KEY=
WX_OWNER_OPEN_ID=
WX_V2_DATA_DIR=
WX_COPILOT_CLI=
WX_COPILOT_TIMEOUT_SECONDS=180
WX_V2_WORKER_COUNT=4
WX_V2_LOG_LEVEL=INFO
```

禁止将真实配置复制到源码目录、网盘共享目录、Issue 或日志。

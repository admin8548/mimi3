# OpenClaw 协议字段/事件/参数字典（2026-06-01）

## 目标

本文件把当前 sandbox 实测到的 OpenClaw WebSocket 协议固化为可实现的字段、事件、参数说明，避免继续依赖猜测或散落日志。代码侧对应实现为：

- `mimo2api/openclaw_protocol.py`
- `GET /api/openclaw/protocol`
- `GET /api/openclaw/features`
- `GET /api/openclaw/events`
- `/api/stats.openclaw_features` 和 `/api/stats.recent_openclaw_events`

## 1. WebSocket envelope 通用字段

| 字段 | 出现位置 | 含义 | mimo2api 用法 |
|---|---|---|---|
| `type` | 所有消息 | envelope 类型：`req`/`res`/`event` | 用于分流请求、响应、事件 |
| `id` | `req`/`res` | RPC 关联 ID，客户端发起请求时生成，服务端响应原样返回 | `NativeClawClient.responses[id]` 等待响应 |
| `method` | `req` | RPC 方法名，以 `hello-ok.features.methods` 为准 | 当前使用 `connect/sessions.list/chat.history/chat.send/agent/agent.wait` |
| `params` | `req` | RPC 参数对象，不同 method schema 不同 | 由 `openclaw_protocol.PARAMETER_HINTS` 记录已确认 schema |
| `ok` | `res` | 响应是否成功 | `False` 时读取 `error.message` 并抛错 |
| `payload` | `res/event` | 响应或事件负载 | hello-ok/features、agent/chat 事件均在此处 |
| `error` | `res` | 错误对象 | 记录/抛出错误，避免误判成功 |
| `event` | `event` | 事件名，以 `hello-ok.features.events` 为准 | `agent/chat/connect.challenge` 已进入执行闭环 |
| `seq` | `event` | 连接级或事件级递增序号 | 用于排序/审计；当前不作为业务判定唯一条件 |
| `stateVersion` | event/snapshot | 状态快照版本 | 预留给 presence/health 状态变更判断 |

## 2. 建联与 hello-ok

### 2.1 `connect.challenge` 事件

服务端首先发送：

```json
{"type":"event","event":"connect.challenge","payload":{...}}
```

含义：服务端要求客户端发送 `connect` RPC，协商协议版本、角色、scope 和能力。

### 2.2 `connect` 参数

当前已验证参数：

```json
{
  "minProtocol": 3,
  "maxProtocol": 3,
  "client": {"id": "cli", "version": "mimo-claw-ui", "platform": "Linux x86_64", "mode": "cli"},
  "role": "operator",
  "scopes": ["operator.admin", "operator.read", "operator.write", "operator.approvals", "operator.pairing"],
  "caps": ["tool-events"],
  "userAgent": "Mozilla/5.0",
  "locale": "zh-CN"
}
```

字段含义：

| 字段 | 含义 |
|---|---|
| `minProtocol/maxProtocol` | 客户端可接受的协议版本范围；当前实测为 `3` |
| `client.id/version/platform/mode` | 客户端标识；用于服务端审计与兼容分支 |
| `role` | 连接角色；当前为 `operator` |
| `scopes` | 请求的 operator 权限范围；影响 `skills.bins` 等方法是否可用 |
| `caps` | 客户端能力声明；`tool-events` 用于接收工具流事件 |
| `userAgent/locale` | Web UI 兼容字段 |

### 2.3 `hello-ok` payload

成功响应关键字段：

| 字段 | 含义 | 实测/处理 |
|---|---|---|
| `type` | 应为 `hello-ok` | 作为连接完成标志 |
| `protocol` | 服务端选择的协议版本 | 当前为 `3` |
| `server.version` | OpenClaw 服务端版本 | 当前观察到 `2026.3.12` |
| `server.connId` | 服务端连接 ID | 只记录“存在”，不持久化原值 |
| `features.methods` | 当前服务端声明 RPC 方法全集 | 存入 `/api/openclaw/features` |
| `features.events` | 当前服务端声明事件全集 | 存入 `/api/openclaw/features` |
| `snapshot.health.defaultAgentId` | 默认 agent ID | 当前为 `main` |
| `sessionDefaults.mainKey` | UI 主会话短 key | 常见为 `main` |
| `sessionDefaults.mainSessionKey` | agent 主 session key | 当前为 `agent:main:main` |
| `canvasHostUrl` | browser/canvas host URL | 只记录存在性，避免泄露内部地址 |
| `authMode` | 鉴权模式 | 当前观察为 `token` |

## 3. 核心执行链路方法

### 3.1 `sessions.list`

- 作用：列出 session，恢复官方 Web UI 实际上下文。
- 已验证参数：

```json
{"includeGlobal": true, "includeUnknown": false, "limit": 120}
```

- 返回：`sessions[]`，每项至少包含 `key/updatedAt/age` 等字段。
- 关键结论：应优先使用 `sessions[0].key`，当前通常为 `agent:main:main`，不要硬编码 `main`。

### 3.2 `chat.history`

- 作用：加载指定 session 的聊天历史，完成上下文初始化。
- 参数：

```json
{"sessionKey": "agent:main:main", "limit": 200}
```

- 失败不应阻断 bridge 注入，但应记录 warning。

### 3.3 `chat.send`

- 作用：普通 Web Chat/UI 通道。
- 参数：

```json
{"sessionKey":"agent:main:main","message":"...","deliver":false,"idempotencyKey":"uuid"}
```

- 事件：主要产生 `events.chat`。
- 重要限制：`chat.send` 不是工具执行入口，不能用来判断 bridge 注入成功。

### 3.4 `agent`

- 作用：真正的 agent/tool 执行入口。
- 参数：

```json
{"agentId":"main","message":"...","idempotencyKey":"uuid-run-id"}
```

- 返回：通常为 accepted/run 相关 payload。
- 事件：核心证据在 `events.agent`。

### 3.5 `agent.wait`

- 作用：等待 agent run 完成。
- 参数：

```json
{"runId":"uuid-run-id"}
```

- 返回：`status/startedAt/endedAt` 等。
- 注意：`agent.wait` 完成只能证明 run 结束，bridge 成功必须再以 gateway 看到 `/ws?uid=<uid>` 为准。

## 4. `events.agent` 事件结构

实测结构：

```json
{
  "type": "event",
  "event": "agent",
  "payload": {
    "runId": "...",
    "stream": "lifecycle|assistant|tool",
    "data": {},
    "sessionKey": "agent:main:main",
    "seq": 1,
    "ts": 1780324533688
  },
  "seq": 250
}
```

字段含义：

| 字段 | 含义 |
|---|---|
| `payload.runId` | agent run 唯一 ID，应与 `agent.wait.runId` 对齐 |
| `payload.stream` | 子流类型：`lifecycle`、`assistant`、`tool` 等 |
| `payload.data` | 子流负载，schema 随 stream 变化 |
| `payload.sessionKey` | run 绑定会话 |
| `payload.seq` | run 内事件序号 |
| `payload.ts` | 服务端毫秒时间戳 |
| 顶层 `seq` | 连接级事件序号 |

子流：

| stream | 已知字段 | 含义 |
|---|---|---|
| `lifecycle` | `data.phase=start/end`, `startedAt`, `endedAt` | run 生命周期 |
| `assistant` | `data.delta`, `data.text` | 文本流；`delta` 是增量，`text` 是累计全文 |
| `tool` | `data.phase`, `data.isError`, `data.meta` | 工具流；见到该流说明工具链已触发。完整字段仍需在真实工具运行中继续采样 |

mimo2api 当前判定：

1. `events.agent.stream == tool` → agent 工具链已触发；
2. `agent.wait` 完成 → run 已结束；
3. gateway 观察到新的 `/ws?uid=<uid>` → bridge 真的成功接入。

## 5. `events.chat` 事件结构

实测结构：

```json
{
  "type": "event",
  "event": "chat",
  "payload": {
    "runId": "...",
    "sessionKey": "agent:main:main",
    "seq": 1,
    "state": "delta|final",
    "message": {"role":"assistant","content":[...],"timestamp":...}
  }
}
```

含义：UI/审计消息事件。它可作为 assistant 文本 fallback，但不能覆盖 `events.agent` 的工具执行证据。

## 6. 方法能力分类

代码中已用 `METHOD_CATEGORIES` 固化分类。重点含义如下：

| 类别 | 方法示例 | 作用 | 当前接入状态 |
|---|---|---|---|
| health/status | `health`, `status` | 服务状态 | 只读验证通过 |
| sessions | `sessions.list/preview/patch/reset/delete/compact` | 会话管理/压缩 | list 已接入，preview 参数已明确为 `keys` |
| chat | `chat.history/chat.send/chat.abort` | UI chat/历史/中止候选 | history/send 已接入，abort 待验证 |
| agent_run | `agent`, `agent.wait` | 执行型任务 | 已接入 |
| approval | `exec.approvals.*`, `exec.approval.*` | exec 审批策略/审批流 | 尚未接入 |
| config | `config.get/schema/set/apply/patch` | 配置读写 | 只读验证通过，写操作未启用 |
| agent_files | `agents.files.*` | agent 文件读写 | list 只读验证通过 |
| node/node_pending/node_invoke | `node.*` | 节点配对、pending 队列、调用 | 只读验证通过，写/调用未启用 |
| cron | `cron.*` | 计划任务 | 只读验证通过 |
| browser | `browser.request` | 浏览器/canvas 请求 | 未深入 |
| models_tools | `models.list`, `tools.catalog` | 模型/工具目录 | 只读验证通过 |

## 7. 事件全集与用途

| 事件 | 含义 |
|---|---|
| `connect.challenge` | 建联挑战，触发 `connect` |
| `agent` | agent run 实时事件，核心执行证据 |
| `chat` | UI/审计消息事件 |
| `presence` | gateway/node presence 变化 |
| `tick` | 周期 tick |
| `talk.mode` | 语音/对话模式变化 |
| `shutdown` | 服务端关闭通知 |
| `health` | 健康状态广播 |
| `heartbeat` | 心跳/保活 |
| `cron` | cron 状态/运行事件 |
| `node.pair.requested/resolved` | node 配对流程 |
| `node.invoke.request` | node 调用请求 |
| `device.pair.requested/resolved` | device 配对流程 |
| `voicewake.changed` | voicewake 配置变化 |
| `exec.approval.requested/resolved` | exec 审批请求/完成 |
| `update.available` | 更新可用通知 |

## 8. 当前已补齐的实现能力

- 新增 `mimo2api/openclaw_protocol.py`：
  - 方法/事件全集；
  - 字段字典；
  - 参数 hints；
  - 方法分类、只读/写入风险分类；
  - hello-ok 安全摘要；
  - agent/chat 事件摘要。
- manager：
  - 使用统一 `build_connect_params()`；
  - 采集 `hello-ok.features` 到 `state.openclaw_features_by_uid`；
  - 继续维持 `agent + agent.wait + /ws?uid` 三段式成功判定。
- Web/API：
  - `/api/openclaw/protocol` 输出本地协议字典；
  - `/api/openclaw/features` 输出运行期观察到的 features；
  - `/api/stats.openclaw_features` 与 `/api/stats.recent_openclaw_events` 纳入总体观测。
- 测试：
  - 协议字典字段、方法分类、事件 hints；
  - hello-ok 摘要不落敏感 connId/canvas URL；
  - `/api/openclaw/protocol` 可用性。

## 9. 仍需实测补齐的部分

这些不是核心 bridge/API 转发阻断项，但属于“完整 OpenClaw 协议实现”的下一层：

1. `events.agent.stream=tool` 的完整 payload schema：需要在真实成功工具调用中采集。
2. `chat.abort`：需要低影响验证它是否能中止 chat run、agent run，或只作用于 UI chat。
3. `exec.approval.*`：需要构造审批事件，明确 request/waitDecision/resolve 的参数与状态机。
4. `browser.request`：需要单独验证 canvas/browser 能力边界和返回结构。
5. `node.invoke.*`：需要确认 node pending 队列字段、ack/result 语义。
6. `sessions.compact`：可与 `/v1/responses/compact` 进一步对齐，判断是否能直接使用 OpenClaw 内置 compact。

## 10. 第二轮低影响验证补充（2026-06-01 22:49 Asia/Shanghai）

本轮用一个已 AVAILABLE 的 sandbox 账号额外验证了以下只读/低影响方法，结果写入本地运行证据 `data/openclaw_deeper_probe_result.json`（`data/` 默认不提交）：

| 方法 | 参数 | 结果 | 关键返回结构 |
|---|---|---|---|
| `sessions.preview` | `{"keys":["agent:main:main"]}` | OK | `ts`, `previews[]`, preview item 含 `key/status/items` |
| `agent.identity.get` | `{}` | OK | `agentId`, `name`, `avatar` |
| `gateway.identity.get` | `{}` | OK | `deviceId`, `publicKey` |
| `models.list` | `{}` | OK | `models[]`, item 含 `id/name/provider/contextWindow/reasoning/input` |
| `tools.catalog` | `{}` | OK | `agentId`, `profiles[]`, `groups[]` |
| `config.schema.lookup` | `{"path":"browser"}` | OK | `path`, `schema`, `hint`, `children[]` |
| `doctor.memory.status` | `{}` | OK | `agentId`, `provider`, `embedding` 状态 |

因此代码侧已把这些方法加入 `READ_ONLY_VERIFIED` 或参数 hints。

## 11. `events.agent.stream=tool` 第二轮字段补充

重启后 manager 自动注入 bridge 时捕获到真实 tool stream 样本，已确认 tool 子流的安全 schema：

| 字段 | 出现阶段 | 含义 |
|---|---|---|
| `data.phase` | `start/update/result` | 工具调用阶段 |
| `data.name` | 全阶段 | 工具名，例如 exec/process/write 等，具体由 OpenClaw 工具目录决定 |
| `data.toolCallId` | 全阶段 | 工具调用 ID；只记录是否存在，不落原值 |
| `data.args` | `start` | 工具入参；只记录 key 列表，不落参数内容 |
| `data.isError` | `result` | 工具结果是否错误 |
| `data.meta` | `result` | 工具结果元数据；只记录 key 列表，不落内容 |

`mimo2api.openclaw_protocol.summarize_openclaw_event()` 现在会额外输出：

```json
{
  "tool_name": "...",
  "tool_call_id_present": true,
  "args_keys": ["..."],
  "meta_keys": ["..."]
}
```

注意：为避免泄露命令、文件内容或工具结果，摘要不会保存 `args`、`meta`、`toolCallId` 原文。

## 12. 当前回退点

本轮继续深入前已设置本地回退 tag：

```text
rollback-openclaw-protocol-20260601 -> 5a19c4b
```

若新变更导致问题，可回退：

```bash
git reset --hard rollback-openclaw-protocol-20260601
docker compose up -d --build
```

## 13. 第三轮剩余方法验证补充（2026-06-01 22:54-22:56 Asia/Shanghai）

本轮继续验证剩余非阻断能力，回退点为：

```text
rollback-openclaw-deeper-20260601 -> 3a53a89
```

运行证据写入本地 `data/openclaw_remaining_probe_result.json`、`data/openclaw_remaining_probe2_result.json`、`data/openclaw_approval_probe_result.json`、`data/openclaw_approval_decision_probe_result.json`（`data/` 不提交）。

### 13.1 `chat.abort`

验证调用：

```json
{"sessionKey":"agent:main:main","runId":"nonexistent-probe-run"}
{"sessionKey":"agent:main:main"}
```

结果均成功返回：

```json
{"ok":true,"aborted":false,"runIds":[]}
```

结论：

- 参数至少需要 `sessionKey`；
- `runId` 可选；
- 无活跃 run 时不会报错，返回 `aborted=false`；
- 它是当前协议中替代不存在的 `agent.cancel/agent.interrupt` 的候选中止入口，但还需要在真实长 run 中验证是否能中止 agent/tool run。

### 13.2 `sessions.compact`

错误参数验证：

```json
{"sessionKey":"probe:nonexistent"}
{"keys":["probe:nonexistent"]}
{"key":"probe:nonexistent","dryRun":true}
```

结果显示：

- 必填字段是 `key`；
- 不接受 `sessionKey`；
- 不接受 `keys`；
- 不接受 `dryRun`。

使用不存在 key：

```json
{"key":"probe:nonexistent"}
```

返回：

```json
{"ok":true,"key":"...","compacted":false,"reason":"no sessionId"}
```

结论：`sessions.compact` 的真实参数是 `key`，对不存在 session 是无副作用返回。

### 13.3 `browser.request`

错误参数：

```json
{}
{"url":"about:blank"}
```

返回：

```text
method and path are required
```

有效只读请求：

```json
{"method":"GET","path":"/"}
```

返回 browser runtime 状态，关键字段包括：

```text
enabled, profile, running, cdpReady, cdpHttp, pid, cdpPort, cdpUrl,
chosenBrowser, detectedBrowser, detectedExecutablePath, userDataDir,
headless, noSandbox, executablePath, attachOnly
```

`GET /json/version`、`GET /status` 在 browser 未运行时返回 `Not Found`。

结论：`browser.request` 是 OpenClaw browser/CDP 管理端请求代理，参数为 `method + path`。

### 13.4 `exec.approvals.*` / `exec.approval.*`

`exec.approvals.get` 成功返回全局审批配置：

```text
path, exists, hash, file.version, file.socket.path, file.defaults, file.agents
```

`exec.approvals.node.get`：

- 缺少 `nodeId` 返回 schema 错误；
- 未知 nodeId 返回 `NOT_CONNECTED: node not connected`。

`exec.approval.waitDecision`：

- 参数名是 `id`，不是 `approvalId`；
- 未知/过期 id 返回 `approval expired or not found`。

`exec.approval.resolve`：

- 参数为 `id + decision`；
- 实测 `decision=deny` 是有效枚举，未知 id 返回 `unknown or expired approval id`；
- `approve/allow/reject/yes/no` 等均返回 `invalid decision`；
- 允许/批准侧枚举仍需等待真实 `exec.approval.requested` 事件后再确认。

## 14. 有状态验证补充（approval / browser / session compact）

### 14.1 真实 approval 事件

已在授权 sandbox 中真实触发并清理一条审批：

- 请求方法：`exec.approval.request`
- 请求内容：`command=echo stateful_approval_probe`, `cwd=/tmp`
- 观察到事件：
  - `exec.approval.requested`
  - `exec.approval.resolved`
- 解析方式：`exec.approval.resolve`，`decision=deny`
- 清理结果：审批请求已显式 deny，未残留未处理状态

实际 `exec.approval.requested` payload 关键字段：

```text
id, request, createdAtMs, expiresAtMs
```

实际 `exec.approval.resolved` payload 关键字段：

```text
id, decision, resolvedBy, ts, request
```

其中 `request` 中可见：

```text
command, cwd, nodeId, host, security, ask, agentId, resolvedPath, sessionKey, turnSource*
```

结论：approval 流已可真实触发、观察、解析并清理。

### 14.2 真实 browser 进程状态切换

已验证 `browser.request` 支持状态切换：

```json
{"method":"POST","path":"/start"}
{"method":"POST","path":"/stop"}
```

观察到：

- `GET /`：返回 runtime 状态；
- `POST /start`：返回 `ok=true, profile=openclaw`；
- `GET /` 之后显示：
  - `running=true`
  - `cdpReady=true`
  - `cdpHttp=true`
  - `pid` 非空
  - `userDataDir` 已创建
- `POST /stop`：返回 `ok=true, stopped=true`；
- 之后 runtime 恢复为 `running=false`。

`/json/version` 与 `/json/list` 在本轮未启动出可访问的浏览器 CDP HTTP 路径，仍返回 `Not Found`，说明 browser 运行管理和 CDP 暴露是两层不同状态。

### 14.3 `sessions.compact` 的真实行为边界

本轮建立临时 session 并执行：

```json
{"key":"agent:main:tmp:stateful-compact-probe2"}
```

结果：

- `sessions.patch` 可创建/更新 session 入口；
- `sessions.compact` 在该临时 session 上返回：
  - `ok=true`
  - `compacted=false`
  - `reason=no transcript`
- `sessions.delete` 可清理临时 session。

同时，`sessions.compact` 的真实参数仍然只有 `key`，不接受 `sessionKey/keys/dryRun`。

结论：`sessions.compact` 是 OpenClaw 内部 session 管理接口，不是 Codex `/v1/responses/compact` 的直接替代；它只能作为内部历史维护工具参考。

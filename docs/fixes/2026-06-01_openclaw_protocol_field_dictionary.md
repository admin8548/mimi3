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

## 15. 下一轮推进中补齐的 schema 结果（agents.files / cron / config / node）

### 15.1 `agents.files.*`

真实验证结果：

- `agents.files.list` 需要 `agentId`
- `agents.files.get` 需要 `agentId + name`
- `agents.files.set` 需要 `agentId + name + content`
- `name` 是固定文件名，不是 `path`
- 任意临时文件名如 `STATEFUL_PROBE.tmp` 会返回 `unsupported file`

实测可读取：

- `AGENTS.md`
- `HEARTBEAT.md`
- `TOOLS.md`
- `IDENTITY.md`

结论：`agents.files.get`/`set` 的目标是预定义 workspace 文档文件，不是任意文件系统路径。

### 15.2 `cron.*`

`cron.add` 真实 schema：

```text
name, schedule, sessionTarget, payload
```

`schedule` 支持：

- `{kind: "cron", expr: "..."}`
- `{kind: "every", everyMs: ...}`

真实返回会补充：

- `id`
- `enabled`
- `createdAtMs`
- `updatedAtMs`
- `wakeMode`
- `delivery.mode`
- `state.nextRunAtMs`
- `payload.kind=agentTurn`

`cron.remove`：

- 需要 `jobId`
- 删除成功返回 `removed=true`
- 不存在返回 `removed=false`

`cron.run`：

- 需要 `id`
- 未知 id 返回 `unknown cron job id`

`cron.update`：

- 需要 `jobId + patch`

### 15.3 `config.patch/apply/set`

已确认：

- 三者都需要 `raw`
- `config.patch` 还需要 `baseHash`
- `config.apply` 也需要 `baseHash`
- `config.set` 也需要 `baseHash`

行为：

- 仅改同内容也会更新 `meta.lastTouchedAt`
- 还原时需使用备份 `raw` + 当前 `baseHash`

### 15.4 `node.invoke.*`

已确认：

- `node.invoke` 需要 `nodeId + command + idempotencyKey`
- 不是 `method` 参数
- `node.pending.enqueue` 需要 `nodeId + type`
- `node.pending.pull/ack/invoke.result/node.event` 在 operator role 下会触发 `unauthorized role: operator`
- 当前 `node.list` 结果为空，因此尚未跑通真实 node invoke 状态机

### 15.5 `agents.files` / `cron` / `config` / `node` 的处理状态

- `agents.files.get`：已发现；尚未做写入恢复，因为受限于支持文件白名单。
- `cron.add/run/remove`：已确认可创建临时 cron 并删除。
- `config.patch/apply`：已确认 baseHash 机制，恢复策略已掌握。
- `node.invoke.*`：仍未有真实 node 目标，因此未完成完整闭环。

## 16. 继续推进补充：approval 枚举、HEARTBEAT 写入恢复、cron.run 事件与 session 污染

### 16.1 Approval 批准枚举候选

对真实 approval id 逐个测试了以下候选：

```text
allow, approve, approved, accept, yes,
approveOnce, approve_once, allowOnce, allow_once, allow_once_for_command,
allowAlways, allow_always, always, permit, grant, ok, allowlist, trusted
```

结果均为：

```text
invalid decision
```

每个无效候选都随后使用 `decision=deny` 清理。因此目前只确认 `deny` 是有效决策枚举，批准枚举仍未发现，可能需要从前端源码/配置 schema 反推而不是盲猜。

### 16.2 `agents.files.set` 固定文件写入恢复

已对 `HEARTBEAT.md` 做最小有状态验证：

1. `agents.files.get agentId=main name=HEARTBEAT.md` 备份原内容；
2. `agents.files.set` 写入临时注释行；
3. 再次 `get` 确认 marker 存在；
4. `agents.files.set` 恢复原内容；
5. 最终 `get` 确认 `restored_equal=true`。

结论：`agents.files.set` 对固定白名单文件可用，写入结果返回文件 metadata 与 content。

### 16.3 `cron.run` 真实事件

创建临时 cron job 后执行 `cron.run`，观察到 `cron` 事件：

- `action=started`
- `action=finished`

finished payload 关键字段：

```text
jobId, action, status, error, summary, deliveryStatus,
sessionId, sessionKey, runAtMs, durationMs, model, provider, usage
```

本次由于未配置 channel，finished 事件 `status=error`，但 `summary=CRON_PROBE_OK` 说明 agent turn 实际执行完成，只是 delivery 阶段失败。

### 16.4 cron.run 造成 session 排序污染

真实 `cron.run` 会创建：

```text
agent:main:cron:<jobId>:run:<sessionId>
```

该 cron session 一度排在 `sessions.list` 第一项，导致旧 manager 逻辑会把 `session_key` 选成 cron session，而不是 `agent:main:main`。

已清理该 cron session，并修复 manager：

- 优先选择 `agent:main:main`；
- 其次选择 `main`；
- 再选择第一个非 `:cron:` / `:run:` session；
- 最后才 fallback。

这避免后续 bridge 注入、chat.history 或关机指令落入 cron 临时上下文。

## 17. 继续推进补充：approval allow-once 与 browser 管理路径

### 17.1 Approval 批准枚举最终确认

继续用真实 approval id 探测后，已确认：

```text
allow-once
```

是有效批准枚举。调用后：

```json
{"ok": true}
```

`exec.approval.request` 返回：

```text
decision = allow-once
```

同时已确认以下候选无效：

```text
allow, approve, approved, accept, yes,
approveOnce, approve_once, allowOnce, allow_once, allow_once_for_command,
allowAlways, allow_always, always, permit, grant, ok, allowlist, trusted,
approve-once, approve-always, allow-always, approveOnceForCommand,
allowAlwaysForCommand, trusted_command, trust_command, ALLOW, APPROVE, ACCEPT
```

当前有效决策枚举：

```text
deny
allow-once
```

### 17.2 Browser 管理路径继续发现

`browser.request` 除已知：

```text
GET /
POST /start
POST /stop
```

还确认：

```text
GET /profiles
GET /tabs
```

`/profiles` 返回 profile 列表，字段包括：

```text
name, cdpPort, cdpUrl, color, running, tabCount, isDefault, isRemote, reconcileReason
```

`/tabs` 返回 tab/target 列表，字段包括：

```text
targetId, title, url, wsUrl, type
```

仍为 Not Found 的路径包括：

```text
/json/version, /json/list, /devtools/browser,
/sessions, /pages, /targets, /status, /health,
/cdp/json/version, /devtools/json/version
```

说明 OpenClaw 暴露的是自己的 browser 管理 API，不是裸 Chrome CDP HTTP API。

### 17.3 AGENTS.md / SOUL.md 注意事项

用户已说明当前 `AGENTS.md` / `SOUL.md` 可能是被当前提示词恢复到最初版本，不是真实现有实时文件。因此：

- 当前仅允许读取/记录白名单与元信息；
- 不写入 `AGENTS.md` / `SOUL.md`；
- 若后续要验证这些核心文件的 `agents.files.set`，需要用户先提供真实最新内容，再按备份/写入/验证/恢复流程处理。

## 18. 继续推进补充（browser snapshot / cron delivery / node pairing-device auth）

### 18.1 Browser 深层管理路径

继续实测 `browser.request` 后确认：

```text
POST /navigate
GET /snapshot
```

关键参数与行为：

- `POST /navigate` 需要 `body: {"url":"about:blank"}`；把 url 放在顶层 `json`、`data` 或 query 中都不会被识别。
- `data:` URL 被明确拒绝，错误为 `Navigation blocked: unsupported protocol "data:"`。
- `GET /snapshot` 返回 `ok/format/targetId/url/snapshot/refs`，当前 `about:blank` 下 snapshot 为空字符串、refs 为空对象。
- `/tabs` 的 `wsUrl` 形如 `ws://127.0.0.1:<cdpPort>/devtools/page/<targetId>`，说明页面级 CDP 能力应从该 wsUrl 继续连接验证，而不是期待 `browser.request` 暴露额外 HTTP 代理端点。

本轮仍确认为 `Not Found` 的页面操作候选包括：

```text
GET /html, GET /content, GET /dom, GET /tree, GET /accessibility, GET /screenshot
POST /reload, POST /back, POST /forward, POST /evaluate, POST /click, POST /type, POST /press, POST /new, POST /close
```

结论：`browser.request` 已能管理 browser/profile/tab，并能 navigate/snapshot；更深的 evaluate/click/type/screenshot 等应走 `/tabs` 暴露的 CDP websocket。

### 18.2 Cron delivery 成功路径

已验证可复现成功路径：

```json
{
  "sessionTarget": "isolated",
  "delivery": {"mode": "none"},
  "payload": {"message": "请只回复 CRON_DEEP_OK"}
}
```

手动 `cron.run` 后事件序列为：

```text
action=started
action=finished, status=ok, summary=CRON_DEEP_OK, delivered=false, deliveryStatus=not-delivered
```

同时对比验证：`delivery.mode=announce` 在没有 channel 配置时，agent turn 仍会完成并生成 summary，但最终 finished 为：

```text
status=error, deliveryStatus=unknown, error=Channel is required ...
```

结论：cron 的“执行成功”和“投递成功”需要分开判断；若只要求定时 agent turn 完成，`delivery.mode=none + sessionTarget=isolated` 是当前最稳定、最低副作用路径。

### 18.3 Node 配对与官方 node 客户端阻断点

新增确认的 node 配对 schema：

| 方法 | 必填 | 结论 |
|---|---|---|
| `node.pair.request` | `nodeId` | 返回 pending request，包含 `requestId/nodeId/isRepair/ts` |
| `node.pair.approve` | `requestId` | 返回 paired node 与 pairing token（需脱敏保存） |
| `node.pair.reject` | `requestId` | 只处理 pending request；不能删除已批准 paired node |
| `node.pair.verify` | `nodeId + token` | schema 已确认，不能用 `requestId` 替代 |

官方 node 客户端入口与连接形态已进一步收敛：

```text
openclaw node run
default gateway: 127.0.0.1:18789
default tls: false
token source: OPENCLAW_GATEWAY_TOKEN or config
identity source: ~/.openclaw/identity/device.json
client.id: cli
client.mode: node
client.instanceId: nodeId
role: node
scopes: []
caps: ["system"]
deviceFamily: required
```

官方设备签名 payload 格式已从源码片段确认：

```text
["v3", deviceId, clientId, clientMode, role, scopes.join(","), signedAtMs, token, nonce, platform, deviceFamily].join("|")
```

签名算法为 Ed25519，输出为 base64url。

当前真实 node role 连接仍卡在：

```text
DEVICE_AUTH_SIGNATURE_INVALID
```

因此 `node.invoke -> node.invoke.request -> node.invoke.result` 的真实闭环仍未完成；这是当前 OpenClaw 协议目录中最重要的未验证缺口。

### 18.4 状态清理注意事项

本轮通过 `node.pair.request/approve` 创建过临时 paired node。已确认以下 RPC 不能清理已批准 paired node：

```text
node.pair.remove, node.remove, node.delete, node.unpair => unknown method
node.pair.reject => only pending request
node.pair.verify => verify only
```

因此后续 node 闭环验证必须控制创建数量，并优先找到官方 paired-store 清理路径或复用既有 paired node，避免继续污染持久配对状态。

## 19. 继续推进补充（official node run 与 pairing required）

### 19.1 官方 node 客户端真实 client 形态修正

通过 npm 官方包 `openclaw@2026.3.12` 与远端 sandbox 内 `openclaw node run` 实测，修正 node host 连接形态：

```text
command: openclaw node run
protocol: 3
client.id: node-host
client.mode: node
client.instanceId: <node config nodeId>
role: node
scopes: []
caps: ["system"]
commands: system.run.prepare, system.run, system.which
```

这修正了前一轮“client.id=cli”的不完整结论：`cli` 是 operator/普通 CLI 客户端名，官方 node host 使用 `node-host`。

### 19.2 自定义云端 ws/proxy node 连接与官方本地 node run 的差异

本轮对比两条路径：

1. 本地自定义 node role 客户端连接 `wss://.../ws/proxy?ticket=...`：
   - 即使使用 Ed25519、raw publicKey base64url、v3/v2 payload、`client.id=node-host`，仍返回 `DEVICE_AUTH_SIGNATURE_INVALID`。
2. 远端 sandbox 内执行官方命令：
   - `timeout 12s openclaw node run --node-id deep-official-live-probe --display-name deep-official-live-probe`
   - 返回 `pairing required`，说明官方客户端已通过 signature/nonce 校验，阻断点推进到了 pairing 层。

结论：node role 真实闭环应优先走远端 sandbox 内官方 `openclaw node run`/local gateway 路径，而不是本地构造客户端经云端 `ws/proxy` 直接连入。

### 19.3 Pairing 分层：先 device.pair，再 node.pair

官方 node run 首次连接后，`device.pair.list` 出现 pending：

```text
displayName=deep-official-live-probe
clientId=node-host
clientMode=node
role=node
scopes=[]
isRepair=true
```

这说明真实 node host 首先触发的是 **device pairing**（给同一 device/publicKey 增加 node role 权限），不是直接进入 `node.pair`。

本轮没有批准该 pending，而是用：

```text
device.pair.reject {requestId}
```

完成恢复，最终 `device.pair.list.pending=[]`，paired device 仍保持原 operator 角色，未追加 node role。

### 19.4 为什么还不继续批准到 node.invoke 闭环

批准 `device.pair.approve` 后很可能继续进入 `node.pair` 阶段；但当前 live server `2026.3.12` 暴露方法中仍没有 `node.pair.remove`，已验证调用会返回：

```text
unknown method: node.pair.remove
```

虽然 npm 新版 `openclaw@2026.5.28` 源码已经包含 `node.pair.remove`，当前 live sandbox 版本尚不支持。因此继续批准 node pairing 可能留下无法通过 RPC 清理的 paired node 状态。

结论：如果要完成 `node.invoke -> node.invoke.request -> node.invoke.result` 闭环，推荐下一步先获得明确授权：允许在一个非关键 uid 上留下一个临时 paired node，或先找到 live `2026.3.12` 的 paired-node 恢复/清理路径。

## 20. 继续推进补充（node.invoke 真实闭环完成）

### 20.1 这个问题要实现的功能

`node.invoke.*` 不是 mimo2api 普通聊天转发链路的必需项，而是 OpenClaw 的 **远端 node host 执行/扩展能力**：

- operator 侧通过 `node.invoke` 向已连接 node 下发命令；
- node host 收到 `node.invoke.request`；
- node host 执行本地 capability/command，例如 `system.which`、`system.run`、`browser.proxy`；
- node host 通过 `node.invoke.result` 回传结构化结果。

这类能力可用于后续实现跨节点执行、远端 shell/工具能力发现、远端浏览器代理、node host health check，以及把 OpenClaw 官方 node 生态纳入 mimo2api 观测/调度。

### 20.2 uid=6875021188 上的闭环验证

用户指定可使用 `6875021188` 作为 node 深测目标。本轮按最小影响路径完成：

1. operator 连接 `uid=6875021188`；
2. 远端 sandbox 内执行官方 node host：

```text
openclaw node run --node-id mimo2api-deep-node-6875021188 --display-name mimo2api-deep-node-6875021188
```

3. 首次触发 `device.pair` pending：

```text
clientId=node-host
clientMode=node
role=node
scopes=[]
isRepair=true
```

4. 批准该 device pairing 后，后台启动官方 node host；
5. `node.list` 出现 connected node：

```json
{
  "nodeId": "860d729a39a5ed0ddbec50af1076a90e7c8af38e253597bcbe83668520e20d1a",
  "displayName": "mimo2api-deep-node-6875021188",
  "version": "2026.3.12",
  "caps": ["browser", "system"],
  "commands": ["browser.proxy", "system.run", "system.run.prepare", "system.which"],
  "paired": true,
  "connected": true
}
```

6. operator 调用：

```json
{
  "method": "node.invoke",
  "params": {
    "nodeId": "860d729a39a5ed0ddbec50af1076a90e7c8af38e253597bcbe83668520e20d1a",
    "command": "system.which",
    "params": {"bins": ["sh"]},
    "idempotencyKey": "<uuid>"
  }
}
```

7. 成功返回：

```json
{
  "ok": true,
  "command": "system.which",
  "payload": {"bins": {"sh": "/usr/bin/sh"}},
  "payloadJSON": "{\"bins\":{\"sh\":\"/usr/bin/sh\"}}"
}
```

结论：`node.invoke -> node.invoke.request -> node.invoke.result` 的真实 closed-loop 已完成。

### 20.3 状态与恢复说明

本轮已停止后台 node host 进程；停止后 `node.list` 显示该 node：

```text
paired=true
connected=false
caps=[]
commands=[]
```

注意：当前 live server `2026.3.12` 没有可用的 `node.pair.remove` RPC；本轮闭环通过 `device.pair.approve` 给 `uid=6875021188` 的既有 paired device 增加了 node role/token。该 uid 是用户指定可用于 node 深测的目标，因此保留该授权以便后续复测 `system.run` / `browser.proxy` 等 node command。

## 21. 继续推进补充（system.run.prepare/system.run 真实闭环完成）

### 21.1 本轮目标

在 `system.which` 已证明 `node.invoke.request/result` 基础链路可用后，继续按最小副作用策略验证官方 node host 的系统执行链路：

- `system.run.prepare`：无副作用，生成审批/执行计划；
- `system.run`：真实执行命令，但需要先经过 exec approval；
- `exec.approval.request(twoPhase)` + `exec.approval.resolve(allow-once)`：确认批准枚举与 node system.run 审批绑定。

本轮仍使用用户指定的 `uid=6875021188` 与既有 paired node：

```text
nodeId=860d729a39a5ed0ddbec50af1076a90e7c8af38e253597bcbe83668520e20d1a
displayName=mimo2api-deep-node-6875021188
```

### 21.2 源码/schema 修正

基于官方 npm 包 `openclaw@2026.3.12` 的运行时代码确认：

- `system.run.prepare` 的参数使用 `params.command`，类型为 argv 数组；
- `system.run` 也使用 `params.command` argv 数组，可带 `rawCommand/cwd/env/timeoutMs/sessionKey/runId`；
- 直接向 `system.run` 塞 `approved=true` 不会绕过审批，gateway 会要求真实 `exec.approval.*` 记录；
- 有效批准枚举为：`allow-once`、`allow-always`、`deny`。

### 21.3 实测链路

1. operator 连接成功，初始 `node.list` 显示该 node 已 paired 但 offline：

```json
{
  "paired": true,
  "connected": false,
  "caps": [],
  "commands": []
}
```

2. 通过远端 sandbox 内官方 node host 启动后，`node.list` 显示 connected：

```json
{
  "nodeId": "860d729a39a5ed0ddbec50af1076a90e7c8af38e253597bcbe83668520e20d1a",
  "version": "2026.3.12",
  "caps": ["browser", "system"],
  "commands": ["browser.proxy", "system.run", "system.run.prepare", "system.which"],
  "paired": true,
  "connected": true
}
```

3. `system.run.prepare` 请求：

```json
{
  "nodeId": "860d729a39a5ed0ddbec50af1076a90e7c8af38e253597bcbe83668520e20d1a",
  "command": "system.run.prepare",
  "params": {
    "command": ["/usr/bin/printf", "mimo2api-system-run-ok\\n"],
    "timeoutMs": 5000,
    "sessionKey": "agent:main:main"
  }
}
```

4. `system.run.prepare` 成功返回 plan：

```json
{
  "ok": true,
  "command": "system.run.prepare",
  "payload": {
    "plan": {
      "argv": ["/usr/bin/printf", "mimo2api-system-run-ok\\n"],
      "cwd": null,
      "commandText": "/usr/bin/printf mimo2api-system-run-ok\\n",
      "commandPreview": null,
      "agentId": null,
      "sessionKey": "agent:main:main"
    }
  }
}
```

5. 未批准直接执行 `system.run` 会被正确拦截：

```text
UNAVAILABLE: SYSTEM_RUN_DENIED: approval required
```

6. 创建 two-phase approval：

```json
{
  "method": "exec.approval.request",
  "params": {
    "id": "<approval uuid>",
    "twoPhase": true,
    "systemRunPlan": "<prepare 返回的 plan>",
    "nodeId": "860d729a39a5ed0ddbec50af1076a90e7c8af38e253597bcbe83668520e20d1a",
    "host": "node",
    "sessionKey": "agent:main:main",
    "timeoutMs": 120000
  }
}
```

返回：

```json
{
  "status": "accepted",
  "id": "<approval uuid>",
  "createdAtMs": 1780399686521,
  "expiresAtMs": 1780399806521
}
```

7. 批准：

```json
{"method":"exec.approval.resolve","params":{"id":"<approval uuid>","decision":"allow-once"}}
```

返回：

```json
{"ok": true}
```

8. 携带批准 id 执行 `system.run`：

```json
{
  "nodeId": "860d729a39a5ed0ddbec50af1076a90e7c8af38e253597bcbe83668520e20d1a",
  "command": "system.run",
  "params": {
    "command": ["/usr/bin/printf", "mimo2api-system-run-ok\\n"],
    "rawCommand": "/usr/bin/printf mimo2api-system-run-ok\\n",
    "runId": "<approval uuid>",
    "approved": true,
    "approvalDecision": "allow-once",
    "timeoutMs": 5000,
    "sessionKey": "agent:main:main",
    "suppressNotifyOnExit": true
  }
}
```

成功返回：

```json
{
  "ok": true,
  "command": "system.run",
  "payload": {
    "exitCode": 0,
    "timedOut": false,
    "success": true,
    "stdout": "mimo2api-system-run-ok\n",
    "stderr": "",
    "error": null
  },
  "payloadJSON": "{\"exitCode\":0,\"timedOut\":false,\"success\":true,\"stdout\":\"mimo2api-system-run-ok\\n\",\"stderr\":\"\",\"error\":null}"
}
```

### 21.4 状态与恢复

本轮验证结束后已停止后台 node host；停止后 `node.list` 恢复为：

```json
{
  "paired": true,
  "connected": false,
  "caps": [],
  "commands": []
}
```

完整本地证据（忽略提交，避免数据目录进入仓库）：

```text
data/stateful_backups5/openclaw_node_system_run_probe.json
```

结论：`node.invoke(system.run.prepare)` 与带真实 approval 的 `node.invoke(system.run)` 均已完成真实闭环；当前 node 方向剩余核心缺口收敛为 `browser.proxy` 的低副作用验证。

## 22. 继续推进补充（browser.proxy 低副作用闭环完成）

### 22.1 本轮目标

在 `system.which` 与 `system.run.prepare/system.run` 已闭环后，继续验证官方 node host 暴露的剩余 node command：

```text
browser.proxy
```

为控制副作用，本轮只验证只读 profile 枚举路径：

```json
{"method":"GET","path":"/profiles","timeoutMs":3000}
```

该路径不执行页面导航、点击、截图、PDF、文件下载等动作，仅确认 node-hosted browser proxy 能经 `node.invoke` 返回浏览器 profile 状态。

### 22.2 源码/schema 修正

官方 `openclaw@2026.3.12` node host 中 `browser.proxy` 处理逻辑确认：

- `params.path` 必填；
- `params.method` 默认为 `GET`，支持 `GET/POST/DELETE` 映射；
- `params.timeoutMs` 可控制代理请求超时；
- 可带 `params.profile/query/body`；
- 返回值是 raw JSON string，gateway 会归一化为 `payload.result` / `payloadJSON`。

### 22.3 实测链路

1. node host 启动后，`node.list` 显示：

```json
{
  "caps": ["browser", "system"],
  "commands": ["browser.proxy", "system.run", "system.run.prepare", "system.which"],
  "paired": true,
  "connected": true
}
```

2. operator 调用：

```json
{
  "method": "node.invoke",
  "params": {
    "nodeId": "860d729a39a5ed0ddbec50af1076a90e7c8af38e253597bcbe83668520e20d1a",
    "command": "browser.proxy",
    "params": {
      "method": "GET",
      "path": "/profiles",
      "timeoutMs": 3000
    },
    "idempotencyKey": "<uuid>"
  }
}
```

3. 成功返回：

```json
{
  "ok": true,
  "command": "browser.proxy",
  "payload": {
    "result": {
      "profiles": [
        {
          "name": "openclaw",
          "cdpPort": 18800,
          "cdpUrl": "http://127.0.0.1:18800",
          "running": false,
          "tabCount": 0,
          "isDefault": true,
          "isRemote": false
        },
        {
          "name": "chrome",
          "cdpPort": 18792,
          "cdpUrl": "http://127.0.0.1:18792",
          "running": true,
          "tabCount": 0,
          "isDefault": false,
          "isRemote": false
        }
      ]
    }
  }
}
```

### 22.4 状态与恢复

本轮结束后已停止后台 node host；`node.list` 恢复为：

```json
{
  "paired": true,
  "connected": false,
  "caps": [],
  "commands": []
}
```

完整本地证据：

```text
data/stateful_backups5/openclaw_node_browser_proxy_probe.json
```

结论：官方 node host 当前公开的四个 command 已全部完成真实闭环或低副作用闭环：

- `system.which`
- `system.run.prepare`
- `system.run`
- `browser.proxy`

后续若继续深入，应聚焦 `browser.proxy` 的页面级动作路径，例如 `/snapshot`、`/navigate`、`/screenshot`，但这些会引入浏览器状态变化，应另行制定更严格的最小副作用与清理策略。

# OpenClaw 协议深度研究最终报告（2026-06-01）

## 研究范围

本轮研究基于当前授权 sandbox 中的 mimo2api/OpenClaw 运行环境，目标是确认 OpenClaw 调用协议是否完整、哪些能力已经可用、哪些能力仍需补齐，以及后续如何继续扩展。

## 本轮产物

- 计划文档：`docs/fixes/2026-06-01_openclaw_protocol_deep_research_plan.md`
- 初步发现：`docs/fixes/2026-06-01_openclaw_protocol_research_findings.md`
- 最终报告：`docs/fixes/2026-06-01_openclaw_protocol_final_report.md`
- hello-ok 原始特征：`data/openclaw_hello_features.json`
- 只读矩阵结果：`data/openclaw_readonly_matrix_result.json`
- 协议 trace 样例：`data/openclaw_protocol_probe_trace.jsonl`, `data/openclaw_readonly_matrix_trace.jsonl`
- 字段字典：`docs/fixes/2026-06-01_openclaw_protocol_field_dictionary.md`

## 结论总览

当前项目对 OpenClaw 协议的实现已经满足 bridge 注入、节点接入、请求转发和基础自愈的生产闭环，但在“完整协议实现”层面尚未完全覆盖。通过 hello-ok 已确认 OpenClaw 暴露了大量 RPC 方法与事件，当前 manager 只使用其中极小一部分。

关键结论：

1. 执行型任务入口不是 `chat.send`，而是 `agent + agent.wait`。
2. `events.agent` 是工具执行证据，`events.chat` 只是 UI/审计事件，不能作为成功唯一判断。
3. 成功注入 bridge 的最终证据必须是 gateway 观察到 `/ws?uid=<uid>`。
4. `sessionKey=agent:main:main` 是当前环境实际 session；之前假设的 `main` 只是 sessionDefaults.mainKey，不等于实际 agent session key。
5. OpenClaw 服务端明确暴露了 RPC feature list；不需要盲猜方法名。
6. 许多之前猜测的 `agent.status/agent.cancel/files.list/workspace.info/process.list/artifacts.list` 等不是当前协议方法，调用返回 `unknown method`。
7. `uid=<none>` legacy 连接当前已被 gateway 拒绝，调度层保持 uid-only。

## 已确认握手流程

```text
WebSocket /ws/proxy?ticket=...
  <- event: connect.challenge
  -> req: connect
  <- res: hello-ok
  <- event: health
  -> req: sessions.list
  -> req: chat.history
  -> req: agent
  -> req: agent.wait
  <- event: agent
  <- event: chat
  -> bridge connects /ws?uid=<uid>
```

## connect 参数

当前使用：

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

服务端返回：

- protocol: `3`
- server.version: `2026.3.12`
- authMode: `token`
- defaultAgentId: `main`
- mainSessionKey: `agent:main:main`
- canvasHostUrl: `http://10.75.193.201:18789`

## 服务端声明的 RPC 方法全集

```text
health
doctor.memory.status
logs.tail
channels.status
channels.logout
status
usage.status
usage.cost
tts.status
tts.providers
tts.enable
tts.disable
tts.convert
tts.setProvider
config.get
config.set
config.apply
config.patch
config.schema
config.schema.lookup
exec.approvals.get
exec.approvals.set
exec.approvals.node.get
exec.approvals.node.set
exec.approval.request
exec.approval.waitDecision
exec.approval.resolve
wizard.start
wizard.next
wizard.cancel
wizard.status
talk.config
talk.mode
models.list
tools.catalog
agents.list
agents.create
agents.update
agents.delete
agents.files.list
agents.files.get
agents.files.set
skills.status
skills.bins
skills.install
skills.update
update.run
voicewake.get
voicewake.set
secrets.reload
secrets.resolve
sessions.list
sessions.preview
sessions.patch
sessions.reset
sessions.delete
sessions.compact
last-heartbeat
set-heartbeats
wake
node.pair.request
node.pair.list
node.pair.approve
node.pair.reject
node.pair.verify
device.pair.list
device.pair.approve
device.pair.reject
device.pair.remove
device.token.rotate
device.token.revoke
node.rename
node.list
node.describe
node.pending.drain
node.pending.enqueue
node.invoke
node.pending.pull
node.pending.ack
node.invoke.result
node.event
node.canvas.capability.refresh
cron.list
cron.status
cron.add
cron.update
cron.remove
cron.run
cron.runs
gateway.identity.get
system-presence
system-event
send
agent
agent.identity.get
agent.wait
browser.request
chat.history
chat.abort
chat.send
```

## 服务端声明的事件全集

```text
connect.challenge
agent
chat
presence
tick
talk.mode
shutdown
health
heartbeat
cron
node.pair.requested
node.pair.resolved
node.invoke.request
device.pair.requested
device.pair.resolved
voicewake.changed
exec.approval.requested
exec.approval.resolved
update.available
```

## 当前 manager 已使用方法

| 方法 | 用途 | 状态 |
|---|---|---|
| `connect` | 完成 handshake | 稳定 |
| `sessions.list` | 获取实际 sessionKey | 稳定 |
| `chat.history` | 初始化上下文 | 稳定 |
| `chat.send` | UI/关机确认类消息 | 可用但非执行入口 |
| `agent` | 执行型任务入口 | 稳定 |
| `agent.wait` | 等待 agent run | 稳定 |

## 只读/低影响 RPC 验证矩阵

| 方法 | 结果 | latency_ms | 摘要/错误 |
|---|---:|---:|---|
| `health` | OK | 51 | keys: ok, ts, durationMs, channels, channelOrder, channelLabels, heartbeatSeconds, defaultAgentId |
| `status` | OK | 51 | keys: runtimeVersion, heartbeat, channelSummary, queuedSystemEvents, sessions |
| `usage.status` | OK | 50 | keys: updatedAt, providers |
| `usage.cost` | OK | 51 | keys: updatedAt, days, daily, totals |
| `tts.status` | OK | 50 | keys: enabled, auto, provider, fallbackProvider, fallbackProviders, prefsPath, hasOpenAIKey, hasElevenLabsKey |
| `tts.providers` | OK | 51 | keys: providers, active |
| `config.get` | OK | 1106 | keys: path, exists, raw, parsed, resolved, valid, config, hash |
| `config.schema` | OK | 161 | keys: schema, uiHints, version, generatedAt |
| `models.list` | OK | 52 | keys: models |
| `tools.catalog` | OK | 51 | keys: agentId, profiles, groups |
| `agents.list` | OK | 51 | keys: defaultId, mainKey, scope, agents |
| `agents.files.list` | OK | 52 | keys: agentId, workspace, files |
| `skills.status` | OK | 251 | keys: workspaceDir, managedSkillsDir, skills |
| `skills.bins` | FAIL | 51 | `unauthorized role: operator` |
| `voicewake.get` | OK | 51 | keys: triggers |
| `sessions.list` | OK | 51 | keys: ts, path, count, defaults, sessions |
| `sessions.preview` | FAIL | 51 | `invalid sessions.preview params: must have required property 'keys'; at root: unexpected property 'sessionKey'` |
| `last-heartbeat` | OK | 51 | keys: ts, status, reason, durationMs |
| `node.pair.list` | OK | 51 | keys: pending, paired |
| `device.pair.list` | OK | 52 | keys: pending, paired |
| `node.list` | OK | 50 | keys: ts, nodes |
| `node.describe` | FAIL | 51 | `invalid node.describe params: must have required property 'nodeId'` |
| `cron.list` | OK | 51 | keys: jobs, total, offset, limit, hasMore, nextOffset |
| `cron.status` | OK | 51 | keys: enabled, storePath, jobs, nextWakeAtMs |
| `cron.runs` | OK | 52 | keys: entries, total, offset, limit, hasMore, nextOffset |
| `gateway.identity.get` | OK | 51 | keys: deviceId, publicKey |
| `system-presence` | OK | 51 | keys: type, len, sample |
| `agent.identity.get` | FAIL | 0 | `Websocket 未连接` |

## 明确不存在/不适用的方法

以下猜测方法已验证为 `unknown method`：

- `agent.status`
- `agent.cancel`
- `agent.interrupt`
- `approvals.list`
- `files.list`
- `workspace.info`
- `terminal.list`
- `process.list`
- `artifacts.list`

对应的真实能力应使用 hello-ok 中声明的命名，例如：

- exec approval: `exec.approvals.*`, `exec.approval.*`
- file/config/agent files: `agents.files.*`, `config.*`
- node: `node.*`
- cron: `cron.*`
- tools catalog: `tools.catalog`

## Session 研究结果

实际环境中：

- `sessionDefaults.mainKey = main`
- `sessionDefaults.mainSessionKey = agent:main:main`
- `sessions.list` 返回 key 为 `agent:main:main`
- `chat.history` 使用 `agent:main:main` 成功

因此 manager 当前“使用 sessions.list 第一项作为 sessionKey”的策略比硬编码 `main` 更可靠。

## Agent run / event 研究结果

现有日志与 trace 证明：

- `agent` 请求返回 `runId/status=accepted`
- `agent.wait` 返回 `status=ok/startedAt/endedAt`
- `events.agent` 中存在 assistant/tool stream
- tool stream 出现即可说明工具链触发
- 但最终 bridge 成功仍必须以 `/ws?uid=<uid>` 为准

本轮已新增：

- `mimo2api/openclaw_protocol.py`
- `/api/openclaw/protocol`
- `/api/openclaw/features`
- `/api/openclaw/events`
- `state.recent_openclaw_events`
- `state.recent_agent_runs`
- `/api/agent-runs`
- `/api/stats.recent_agent_runs`
- `/api/stats.recent_openclaw_events`
- 默认关闭的协议 trace

## Legacy 重连研究结果

当前 gateway 状态：

- `uid_coverage=100%`
- `legacy_no_uid_clients=0`
- 新的 `/ws` 无 uid 连接会被拒绝
- 旧 bridge 仍可能持续重连，但不会进入节点池，也不会参与调度

后续若要彻底消除日志噪声，需要从远端实例清理旧 bridge 进程或增加拒绝日志限流。

## 当前协议覆盖程度评估

| 能力 | 当前覆盖 | 备注 |
|---|---|---|
| 建联/认证 | 完整 | connect.challenge/connect/hello-ok |
| session 初始化 | 基本完整 | sessions.list/chat.history |
| 执行型 agent run | 基本完整 | agent/agent.wait/events.agent |
| bridge 成功判定 | 完整 | /ws?uid=<uid> |
| 节点池调度 | 完整 | uid-only |
| RPC 方法全集 | 已发现，未全部实现 | hello-ok features 已记录 |
| 事件全集 | 已发现，未全部建模 | events list 已记录 |
| approvals | 未实现 | 需要研究 exec.approval.* |
| config/agent files | 只读已验证 | 写操作未启用 |
| cron/node/device | 只读已验证 | 写操作未启用 |
| browser/canvas | 未深入 | browser.request 未探测 |
| agent cancel/interrupt | 当前命名不存在 | 需研究 chat.abort 或其它真实中止语义 |

## 建议后续实现优先级

1. 将 `hello-ok.features.methods/events` 保存到 diagnostics，便于版本差异对比。
2. 完整解析 `events.agent`，建立 run state machine。
3. 增加 `/api/openclaw/features` 和 `/api/openclaw/runs`。
4. 研究 `chat.abort` 是否可作为 agent/chat run 中止入口。
5. 研究 `exec.approval.*`，明确审批流事件与自动处理边界。
6. 对 legacy 拒绝日志做限流，避免旧 bridge 重连风暴污染日志。
7. 对 `sessions.preview` 使用正确参数 `keys` 再验证。
8. 对 `browser.request` 做单独低影响研究。

## 最终判断

- 对当前 mimo2api 的核心目标（bridge 注入、uid 节点池、API 转发）来说，OpenClaw 调用协议已经足够且稳定。
- 对“完整 OpenClaw 协议实现”来说，已新增字段字典与协议目录，但 `tool` 子流、审批流、browser/node invoke 等仍需要继续实测补齐。
- 本轮已经从猜测进入到基于 `hello-ok.features` 的协议矩阵阶段，后续应避免盲猜方法名，严格以服务端声明为准。

## 第二轮深入验证补充（2026-06-01 22:49 Asia/Shanghai）

在 `5a19c4b` 稳定点上已创建本地回退 tag：`rollback-openclaw-protocol-20260601`。随后继续验证：

- `sessions.preview` 使用 `keys` 参数确认成功；
- `agent.identity.get` 重新验证成功，之前失败确认为连接关闭时机问题，不是方法不存在；
- `gateway.identity.get/models.list/tools.catalog/config.schema.lookup/doctor.memory.status` 均通过低影响验证；
- 自动注入过程中捕获到真实 `events.agent.stream=tool` 样本，确认 tool 子流字段包括 `phase/name/toolCallId/args/isError/meta`；
- 代码已只保存工具事件 schema 摘要，不保存工具参数、命令正文、meta 原文或 toolCallId 原值。

新增/更新实现：

- `READ_ONLY_VERIFIED` 补充 `doctor.memory.status/config.schema.lookup/agent.identity.get`；
- `PARAMETER_HINTS` 补充 `agent.identity.get/gateway.identity.get/config.schema.lookup/doctor.memory.status/models.list/tools.catalog`；
- `summarize_openclaw_event()` 增强 tool stream 摘要：`tool_name/tool_call_id_present/args_keys/meta_keys`；
- 单元测试增加 tool event schema 防泄露验证。

## 第三轮剩余方法验证补充（2026-06-01 22:54-22:56 Asia/Shanghai）

在 `3a53a89` 上设置新的回退 tag：`rollback-openclaw-deeper-20260601`，然后继续验证剩余非阻断能力：

- `chat.abort`：`sessionKey` 必填、`runId` 可选；无活跃 run 时返回 `ok=true/aborted=false/runIds=[]`。
- `sessions.compact`：必填字段是 `key`；不接受 `sessionKey/keys/dryRun`；不存在 key 返回 `compacted=false/reason=no sessionId`。
- `browser.request`：必填字段是 `method/path`；`GET /` 返回 browser runtime 状态；`/json/version` 在 browser 未运行时为 `Not Found`。
- `exec.approvals.get`：只读返回全局审批配置。
- `exec.approvals.node.get`：必填 `nodeId`；未知 node 返回 `NOT_CONNECTED`。
- `exec.approval.waitDecision`：必填 `id`，不是 `approvalId`。
- `exec.approval.resolve`：参数为 `id/decision`；`decision=deny` 已确认为有效拒绝枚举，批准枚举仍需真实审批事件确认。

代码已补充相应 `PARAMETER_HINTS` 与测试，`exec.approvals.get` 标记为只读已验证。

## 有状态验证补充（approval / browser / session compact）

本轮已按“先备份、后验证、再恢复”原则继续进行有状态验证：

- 备份了 `exec.approvals.get`、`config.get`、`browser.request GET /`、`sessions.preview`、`agents.list`、`cron.list`、`node.list` 等状态到 `data/stateful_backups/openclaw_stateful_backup.json`。
- 真实触发并清理了一条 approval：
  - `exec.approval.request` 请求 `command=echo stateful_approval_probe`, `cwd=/tmp`
  - 观察到 `exec.approval.requested` / `exec.approval.resolved`
  - 使用 `exec.approval.resolve` + `decision=deny` 完成清理
- 真实验证 browser 状态切换：
  - `browser.request POST /start`
  - `browser.request POST /stop`
  - 启停状态恢复正常
- 建立临时 session，验证 `sessions.compact` 的边界行为：
  - `sessions.patch` 创建临时 session
  - `sessions.compact` 返回 `compacted=false/reason=no transcript`
  - `sessions.delete` 删除临时 session

因此剩余结论进一步明确：

1. `exec.approval.requested/resolved` 已真实可验证，并已补齐事件字段字典。
2. `browser.request` 不只是状态查询，也支持 start/stop 控制。
3. `sessions.compact` 仍不是 `/v1/responses/compact` 的直接后端对齐接口；它是内部 session 管理接口，只适合做 session 压缩维护，不适合替代 Codex Responses compact 输出。

## 继续推进补充：agents.files / cron / config / node

本轮继续做了更多可恢复验证：

- `agents.files.list/get/set`：确认目标是固定 workspace 文件名，`AGENTS.md` / `HEARTBEAT.md` / `TOOLS.md` / `IDENTITY.md` 可读；临时文件名会返回 `unsupported file`，因此不做任意文件写入。
- `cron.add`：确认真实 schema 为 `name + schedule + sessionTarget + payload`；`schedule` 支持 `kind=cron|every` 两类；已成功创建并删除临时 cron job。
- `config.patch/apply/set`：确认都需要 `raw` 和 `baseHash`；仅改同内容会更新 `meta.lastTouchedAt`，因此恢复时需接受时间戳变化。
- `node.invoke.*`：确认 `node.invoke` 真正参数是 `nodeId + command + idempotencyKey`；`node.pending.pull/ack/invoke.result/node.event` 在 operator role 下被拒绝，说明完整 node 状态机需要 node role 或真实 node 连接，当前暂未跑通。

这些结论已同步到 `mimo2api/openclaw_protocol.py` 和字段字典文档。

## 继续推进补充：approval 枚举、agents.files.set、cron.run 与 session 选择修复

继续推进时新增发现：

- 真实 approval id 上测试多组批准候选，均返回 `invalid decision`；所有测试 approval 均已用 `deny` 清理。当前只确认 `deny` 有效，批准枚举仍需源码/schema 反推。
- `agents.files.set` 已对 `HEARTBEAT.md` 完成备份、写入 marker、读取确认、恢复、最终一致性确认。
- `cron.run` 已真实触发 `cron` started/finished 事件；finished 包含 `summary/sessionId/sessionKey/usage` 等字段。因未配置 delivery channel，最终 `status=error`，但 agent turn 已产出 `CRON_PROBE_OK`。
- 重要稳定性修复：`cron.run` 生成的 `agent:main:cron:...` session 会排在 `sessions.list` 首位，旧 manager 会误选它。已清理临时 cron session，并修复 session 选择逻辑为优先 `agent:main:main`。

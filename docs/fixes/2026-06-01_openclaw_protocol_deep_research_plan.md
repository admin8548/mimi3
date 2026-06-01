# OpenClaw 调用协议深度研究计划

## 目标

在现有 mimo2api 已可稳定注入 bridge、维持 uid 节点池、提供 OpenAI/Responses 兼容 API 的基础上，进一步系统化研究 OpenClaw WebSocket/RPC 协议，形成可观测、可复现、可扩展的协议层能力。

## 当前已确认协议链

```text
connect.challenge -> connect
sessions.list     -> session discovery
chat.history      -> history loading
chat.send         -> Web UI 聊天/审计通道
agent             -> 真正 agent 工具执行入口
agent.wait        -> agent run 等待
events.agent      -> 工具链执行证据
events.chat       -> UI 展示/审计事件
/ws?uid=<uid>     -> bridge 实际落地成功证据
```

## 已知结论

- `chat.send` 不能作为执行型任务入口，只能作为 Web Chat/UI 审计通道。
- bridge 注入必须使用 `agent + agent.wait`。
- `events.agent` 的工具流是执行证据。
- `events.chat` 可能出现审计/拒答文本，不能覆盖 `events.agent` 成功证据。
- 最终成功标准必须是 gateway 观察到新的 `/ws?uid=<uid>` 接入。

## 深研阶段划分

### Phase 1：协议追踪基础设施

- 增加可开关协议追踪：`MIMO_CLAW_PROTOCOL_TRACE=true`。
- 输出 JSONL trace 到 `MIMO_CLAW_PROTOCOL_TRACE_PATH`。
- 记录 RPC 请求/响应与事件 schema：
  - uid/logger/account label
  - direction: request / response / event
  - method/event
  - request id / runId / sessionKey
  - 脱敏后的 payload keys/type summary
  - timestamp
- 默认关闭，避免日志量和敏感信息风险。

### Phase 2：events.agent 结构化建模

- 解析并记录 agent run：
  - runId
  - uid
  - assistant text presence
  - tool_seen
  - tool phase
  - tool error
  - started/completed/failed/timeout
- 在 `/api/stats` 或独立 `/api/agent-runs` 暴露最近 run 摘要。

### Phase 3：RPC 方法表探索

基于 trace 和最小主动探测，研究是否存在/可用：

```text
sessions.create / sessions.delete / sessions.rename
agent.cancel / agent.interrupt / agent.status
approvals.*
files.*
workspace.*
terminal.*
process.*
artifacts.*
```

### Phase 4：session 策略验证

- 对比 `main`、`agent:main:main`、`sessions.list[0]`。
- 观察不同 sessionKey 对 `chat.send`、`chat.history`、`agent` 的影响。
- 评估是否需要为 bridge 注入创建专用 session。

### Phase 5：connect capabilities/scopes 验证

- 记录实际 hello-ok payload。
- 对比 caps/scopes 增减后事件覆盖率。
- 明确是否缺失 approvals/tool/artifact 事件。

### Phase 6：legacy 重连源头治理

- 当前 gateway 已拒绝/清理 `uid=<none>`。
- 后续研究旧 bridge 为什么还会持续重连：
  - 是否来自旧实例未销毁；
  - 是否旧 bridge 代码仍在远端进程；
  - 是否需要 manager 主动销毁/重建对应实例；
  - 是否需要拒绝日志限流。

## 验收标准

- 能通过 trace 文件复盘一次完整 connect + session + agent + bridge 注入流程。
- 能从结构化 agent run 摘要判断注入失败阶段。
- uid 节点池保持 100% uid coverage。
- legacy `uid=<none>` 不参与调度且不会污染主要健康判断。
- 形成 OpenClaw 协议方法/事件矩阵。

## 风险控制

- trace 默认关闭。
- payload 只记录 schema/摘要，不记录 cookies/token/完整代码。
- 所有主动探测只针对授权 CTF sandbox 账号与当前项目节点。

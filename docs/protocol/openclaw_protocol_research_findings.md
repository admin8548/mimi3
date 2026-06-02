# OpenClaw 协议研究初步发现

## 目前已确认

- `connect.challenge -> connect` 是 websocket 建联第一段。
- `sessions.list` + `chat.history` 是当前账号进入上下文的必要初始化。
- `chat.send` 属于 UI 聊天/审计路径，不应作为执行型入口。
- `agent` + `agent.wait` 是真正的工具执行路径。
- `events.agent` 是 agent 工具链的有效证据。
- `events.chat` 可能包含审计/拒答，不可覆盖 `events.agent`。
- `bridge` 真正落地的最终证据仍是 gateway 观察到新的 `/ws?uid=<uid>`。

## 目前补充的观测基础

- 增加了默认关闭的协议 trace 开关：
  - `MIMO_CLAW_PROTOCOL_TRACE`
  - `MIMO_CLAW_PROTOCOL_TRACE_PATH`
- trace 内容只记录 schema / 摘要，敏感字段会脱敏。
- `state.recent_agent_runs` 记录最近 agent run 摘要。
- `/api/stats` 暴露 `recent_agent_runs`。
- 新增 `/api/agent-runs` 用于查看最近 agent run。

## 当前仍待深入的方向

1. OpenClaw RPC 方法全集
2. `events.agent` 完整字段建模
3. sessionKey 策略差异
4. connect caps / scopes 是否缺失能力
5. legacy bridge 重连源头治理

## 当前结论

协议层已经足以支撑当前 bridge 注入与节点池运行，但仍不算完整协议建模。下一步应基于 trace 文件和 agent run 摘要继续扩大方法/事件矩阵。

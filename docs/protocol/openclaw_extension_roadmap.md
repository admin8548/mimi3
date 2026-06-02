# OpenClaw 协议点位扩展功能路线图

## 目标

本文件把前期在授权 sandbox 中确认的 OpenClaw 协议点位，整理成可在 `mimo2api` 中落地的功能规划。重点回答三个问题：

1. 已掌握哪些协议点位？
2. 这些点位能做什么？
3. 它们可以在当前项目中构建或丰富哪些功能？

关联资料：

- `docs/protocol/openclaw_protocol_final_report.md`
- `docs/protocol/openclaw_protocol_field_dictionary.md`
- `mimo2api/openclaw_protocol.py`
- `GET /api/openclaw/protocol`
- `GET /api/openclaw/features`
- `GET /api/openclaw/events`

## 总体结论

当前已掌握的 OpenClaw 协议能力，已经足够将 `mimo2api` 从单纯的 OpenAI-compatible API 转发网关，扩展成以下几类能力组合：

1. **OpenClaw 只读诊断面板**：健康、模型、工具、用量、TTS、voicewake、memory、能力矩阵。
2. **Agent Run 观测系统**：展示 agent/tool run 生命周期、工具事件、文本流、失败原因、耗时。
3. **会话/压缩管理**：session list/preview/history/compact，与 Responses compaction 兼容能力结合。
4. **节点池调度与自愈增强**：uid 节点、legacy 策略、manager 状态、bridge 注入闭环、节点冷却原因。
5. **Cron 编排**：定时探活、定时 compact、定时 bridge 重注入、定时自检。
6. **Browser 管理**：profiles/tabs/navigate/snapshot，后续可探索 tabs.wsUrl 的 CDP 能力。
7. **Node/Device 管理与受控执行**：pairing、token 生命周期、node.invoke、approval 流程。
8. **配置与 Agent 文件管理**：config get/schema/patch、agents.files list/get/set，配合备份/恢复。
9. **审批控制台**：exec approval queue、allow-once/deny、node 执行前审批。

建议落地顺序是：

```text
只读诊断 → 观测面板 → 会话/压缩 → Cron 自愈 → Browser 管理 → Approval/Node 执行 → Config/Agent 文件写入
```

写入类和执行类功能必须默认带备份、确认、审计、回滚，不应作为第一阶段默认开放能力。

---

## 协议点位与可扩展功能矩阵

### 1. 握手、能力发现、协议目录

| 点位 | 能力 | 当前项目状态 | 可扩展功能 | 风险 |
|---|---|---|---|---|
| `connect.challenge` | 服务端要求客户端发起 `connect` | Manager 已实现 | 建联失败诊断、协议版本检查 | 低 |
| `connect` | 协商 protocol/role/scopes/caps | Manager 已实现 | 在 diagnostics 展示实际 connect 参数 | 低 |
| `hello-ok.features.methods` | 服务端 RPC 方法全集 | 已暴露 `/api/openclaw/features` | WebUI 能力矩阵、方法分类、版本差异提示 | 低 |
| `hello-ok.features.events` | 服务端事件全集 | 已暴露 `/api/openclaw/features` | WebUI 事件矩阵、事件流筛选 | 低 |
| `gateway.identity.get` | gateway/device identity | 已验证只读 | Gateway 身份页、pairing 诊断 | 低 |

可建设功能：

- `OpenClawProtocolPanel`
- 方法/事件搜索
- feature diff：当前 hello-ok vs 本地 `KNOWN_METHODS`
- protocol version / authMode / defaultAgentId 展示

---

### 2. Agent 执行链路

| 点位 | 能力 | 当前项目状态 | 可扩展功能 | 风险 |
|---|---|---|---|---|
| `sessions.list` | 列出 session，恢复真实 sessionKey | Manager 已用 | 会话列表、sessionKey 诊断 | 低 |
| `chat.history` | 拉取指定 session 历史 | Manager 已用 | 初始化诊断、历史预览 | 低-中 |
| `chat.send` | UI chat / 审计通道 | Manager 仅用于关机/确认类文本 | 管理面低风险文本消息 | 中 |
| `agent` | 真正 agent/tool 执行入口 | bridge 注入已用 | 远端任务运行器、Agent Run 控制台 | 中-高 |
| `agent.wait` | 等待 run 完成 | bridge 注入已用 | run 超时统计、失败恢复 | 中 |
| `events.agent` | lifecycle/assistant/tool 事件 | 已摘要记录 | 实时 Agent Run 面板、工具调用审计 | 低 |
| `events.chat` | UI/审计消息事件 | 已作为 fallback | Chat 审计视图 | 低 |

可建设功能：

- `/api/agent-runs` 已有，继续丰富：
  - `run_id`
  - `uid`
  - `status`
  - `tool_seen`
  - `tool_event_count`
  - `tool_error_count`
  - `assistant_text_len`
  - `duration_ms`
- WebUI Agent Runs 页：按 uid、状态、时间筛选。
- Bridge 注入闭环可视化：`agent accepted → tool seen → agent.wait done → /ws?uid connected`。

注意：

- `chat.send` 不是工具执行入口。
- `events.chat` 可能包含审计拒答，不能覆盖 `events.agent` 的工具执行证据。
- bridge 成功最终以 gateway 观测到 `/ws?uid=<uid>` 为准。

---

### 3. Sessions 与 Compaction

| 点位 | 能力 | 当前项目状态 | 可扩展功能 | 风险 |
|---|---|---|---|---|
| `sessions.list` | 列出会话 | 已用 | WebUI 会话列表 | 低 |
| `sessions.preview` | 批量预览，会用 `keys` | 已确认参数 | 会话预览、摘要列表 | 低 |
| `sessions.compact` | 压缩指定 session，参数是 `key` | 已确认参数 | OpenClaw 原生 compact | 中 |
| `sessions.reset/delete/patch` | 修改/删除会话 | 未接入 | 高级会话管理 | 高 |

可建设功能：

- 会话列表与最近更新时间。
- 手动 session compact。
- 自动 compact 策略：结合当前 `/v1/responses/compact`。
- 会话操作审计与回滚提示。

建议阶段：第二阶段。

---

### 4. 只读诊断能力

| 点位 | 能力 | 可扩展功能 | 风险 |
|---|---|---|---|
| `health` | OpenClaw 健康状态 | 健康面板、心跳状态 | 低 |
| `status` | runtime/channel/sessions 状态 | Runtime 状态页 | 低 |
| `usage.status` | provider 用量状态 | 用量状态 | 低 |
| `usage.cost` | daily/totals 成本 | 成本面板 | 低 |
| `models.list` | 模型列表 | 自动模型发现、模型映射建议 | 低 |
| `tools.catalog` | 工具目录 | 工具能力页 | 低 |
| `tts.status/providers` | TTS 状态和 provider | TTS 管理只读页 | 低 |
| `voicewake.get` | voicewake triggers | 语音唤醒诊断 | 低 |
| `doctor.memory.status` | memory/embedding 状态 | Agent memory 健康 | 低 |
| `logs.tail` | 日志尾部 | WebUI 日志增强 | 低-中 |

推荐优先落地：

```text
/api/openclaw/health
/api/openclaw/status
/api/openclaw/models
/api/openclaw/tools
/api/openclaw/usage
```

也可以先实现一个通用只读 RPC wrapper，但必须限制 allowlist，避免误暴露 mutating 方法。

---

### 5. Cron 编排

| 点位 | 能力 | 当前结论 | 可扩展功能 | 风险 |
|---|---|---|---|---|
| `cron.list` | 列出任务 | 已验证 | Cron 管理页 | 低 |
| `cron.status` | Cron runtime 状态 | 已验证 | 调度器健康 | 低 |
| `cron.runs` | 历史运行 | 已验证 | Cron run 历史 | 低 |
| `cron.add` | 创建任务 | 已确认 schema | 定时自愈任务 | 中 |
| `cron.update` | 更新任务 | 已确认 | 启停/改 schedule | 中 |
| `cron.remove` | 删除任务 | 已确认 | 清理任务 | 中 |
| `cron.run` | 手动触发 | 已确认 | 立即运行探活/自愈 | 中 |
| `events.cron` | started/finished/delivery | 已建模 | 实时任务事件 | 低 |

已验证可靠组合：

```json
{
  "schedule": {"kind": "cron", "expr": "0 0 1 1 *"},
  "sessionTarget": "isolated",
  "payload": {"message": "..."},
  "delivery": {"mode": "none"}
}
```

可建设功能：

- 定时 bridge 重注入。
- 定时健康检查。
- 定时 session compact。
- 定时节点状态采集。
- Cron runs 历史与失败摘要。

建议阶段：第二/第三阶段之间，先只读，再开放 add/run。

---

### 6. Browser 管理

| 点位 | 能力 | 当前结论 | 可扩展功能 | 风险 |
|---|---|---|---|---|
| `browser.request GET /` | Browser runtime 状态 | 已确认 | Browser 状态页 | 低 |
| `POST /start` | 启动 browser | 已确认 | 手动启动 | 中 |
| `POST /stop` | 停止 browser | 已确认 | 手动停止 | 中 |
| `GET /profiles` | Profile 状态 | 已确认 | Profile 列表 | 低 |
| `GET /tabs` | Tab/target/wsUrl | 已确认 | Tab 管理、CDP 探索入口 | 中 |
| `POST /navigate` | 导航 URL | body.url，data: 被拒 | 简单页面导航 | 中 |
| `GET /snapshot` | AI snapshot/refs | 已确认 | 页面状态快照 | 低-中 |

明确未确认/不可直接使用：

- `/screenshot`
- `/evaluate`
- `/click`
- `/json/list`
- 常见 CDP HTTP 路径

可建设功能：

- Browser 状态页。
- Profiles/Tabs 只读页。
- Navigate 表单。
- Snapshot Viewer。
- 后续基于 `tabs.wsUrl` 探索 CDP，但应单独立项。

---

### 7. Node / Device / 远端节点调用

| 点位 | 能力 | 当前结论 | 可扩展功能 | 风险 |
|---|---|---|---|---|
| `node.pair.list` | node 配对列表 | 已验证 | Node pairing 页 | 低 |
| `device.pair.list` | device 配对列表 | 已验证 | Device 管理页 | 低 |
| `node.pair.request` | 创建 node 配对请求 | 已确认 | 节点接入流程 | 中 |
| `node.pair.approve/reject` | 批准/拒绝 node | 已确认 | WebUI 配对审批 | 中 |
| `node.pair.verify` | 验证 node token | schema 是 `nodeId + token` | Token 校验 | 中 |
| `device.token.rotate/revoke` | token 生命周期 | 已知方法 | 凭据轮换/吊销 | 高 |
| `node.list` | 列出 connected node | 已验证 | 节点列表 | 低 |
| `node.describe` | 节点详情 | 需 nodeId | 节点详情页 | 低 |
| `node.invoke` | 调用 node 命令 | 已完成闭环 | 远端执行器 | 高 |

已验证 `node.invoke` 命令：

- `system.which`
- `system.run.prepare`
- `system.run`
- `browser.proxy`

`system.run` 需要 approval：

```text
node.invoke(system.run) → SYSTEM_RUN_DENIED
exec.approval.request → exec.approval.resolve(decision=allow-once)
node.invoke(system.run approved) → stdout/stderr/exitCode
```

可建设功能：

- Node 管理页。
- Node pairing 向导。
- Approval-gated command runner。
- Browser proxy 调试。
- Node health check。

必须配套：

- 二次确认。
- 操作日志。
- approval 审计。
- token 脱敏。
- 默认关闭 mutating 操作。

---

### 8. Exec Approval

| 点位 | 能力 | 当前结论 | 可扩展功能 | 风险 |
|---|---|---|---|---|
| `exec.approvals.get` | 全局审批配置 | 已验证 | 审批配置页 | 低 |
| `exec.approvals.node.get` | node 审批配置 | 需 nodeId | Node 审批策略 | 低-中 |
| `exec.approval.request` | 创建审批请求 | 参数是 `command` | 命令执行前置审批 | 中 |
| `exec.approval.waitDecision` | 等待审批 | 参数是 `id` | 异步等待 | 中 |
| `exec.approval.resolve` | 决策审批 | 有效 `deny` / `allow-once` | Approval 控制台 | 高 |
| `events.exec.approval.*` | 审批事件 | 已建模 | 实时审批事件流 | 低 |

可建设功能：

- Approval Queue。
- allow-once / deny 按钮。
- 审批过期状态。
- 与 node.invoke/system.run 串联。

建议：第三阶段，且必须有 WebUI 确认和审计。

---

### 9. Config 与 Agent 文件

| 点位 | 能力 | 当前结论 | 可扩展功能 | 风险 |
|---|---|---|---|---|
| `config.get` | 读取 raw/config/hash | 已验证 | 配置只读页、备份 | 低 |
| `config.schema` | schema/ui hints | 已验证 | 动态配置表单 | 低 |
| `config.schema.lookup` | 子路径 schema | path=browser 已验证 | 局部配置 UI | 低 |
| `config.patch/apply/set` | 写入配置，需 baseHash | 已确认 | 安全配置编辑器 | 高 |
| `agents.files.list` | 文件列表 | 已验证 | Agent 文件页 | 低 |
| `agents.files.get` | 读取固定文件 | 已验证 | AGENTS/SOUL/TOOLS 查看 | 低-中 |
| `agents.files.set` | 写入固定文件 | HEARTBEAT.md 已验证 | Agent 文件编辑 | 高 |

可建设功能：

- Config Viewer。
- Schema-based Form。
- Agent Files Viewer。
- Backup/restore。
- Controlled editor：先备份，再写入，再验证，再恢复。

建议：

- 第一阶段只做 read-only。
- 写入类单独做 feature flag。

---

## 项目功能落地分期

### Phase 1：只读诊断增强，优先落地

目标：不改变远端状态，最大化可观测性。

建议 API：

```text
GET /api/openclaw/health
GET /api/openclaw/status
GET /api/openclaw/models
GET /api/openclaw/tools
GET /api/openclaw/usage
GET /api/openclaw/tts
GET /api/openclaw/sessions
GET /api/openclaw/cron/status
GET /api/openclaw/cron/runs
GET /api/openclaw/browser/status
GET /api/openclaw/browser/profiles
GET /api/openclaw/browser/tabs
```

建议 WebUI：

- OpenClaw Diagnostics 总览页。
- Feature Matrix。
- Agent Runs 页。
- Node/Browser/Cron 只读页。

### Phase 2：可控管理能力

目标：允许低风险操作或有明确回滚的操作。

建议功能：

- sessions.preview / sessions.compact。
- browser.start / browser.stop / browser.navigate。
- cron.run。
- config.get + schema viewer。
- agents.files.get。

必须：

- 操作确认。
- recent_errors 分类。
- recent_openclaw_events 摘要。

### Phase 3：高级编排与执行

目标：完成高价值 mutating 能力，但默认关闭。

建议功能：

- cron.add/update/remove。
- exec approval queue。
- node pairing。
- node.invoke(system.which/system.run/browser.proxy)。
- config.patch/apply/set。
- agents.files.set。

必须：

- Feature flag。
- WebUI 二次确认。
- 审计日志。
- token/secret 脱敏。
- 备份/恢复。

---

## 推荐优先级

| 优先级 | 功能 | 原因 |
|---|---|---|
| P0 | OpenClaw Diagnostics 只读页 | 低风险、高收益，能立即提升运维可见性 |
| P0 | Agent Runs 实时观测 | 当前 bridge 注入和自愈最需要 |
| P1 | Sessions/Compaction 管理 | 与 Responses API 兼容主线高度相关 |
| P1 | Cron 只读 + cron.run | 可支撑后续自愈编排 |
| P1 | Browser profiles/tabs/snapshot | 可增强远端运行态诊断 |
| P2 | Approval Queue | node.invoke/system.run 前置条件 |
| P2 | Node pairing/list/describe | 为远端执行闭环铺路 |
| P3 | node.invoke/system.run | 高价值但风险高，必须审批/审计 |
| P3 | config.patch / agents.files.set | 高风险写入，必须备份/恢复 |

---

## 下一步建议

建议从 **Phase 1：OpenClaw Diagnostics 只读 API + WebUI 展示** 开始。

最小可落地切片：

1. 新增通用只读 RPC allowlist。
2. 实现：
   - `GET /api/openclaw/health`
   - `GET /api/openclaw/status`
   - `GET /api/openclaw/models`
   - `GET /api/openclaw/tools`
   - `GET /api/openclaw/sessions`
3. WebUI 增加 OpenClaw Diagnostics tab。
4. 所有返回值做 schema 摘要和敏感字段脱敏。
5. 写回测试与文档。

该切片不涉及 mutating RPC，适合作为下一阶段开发起点。

# 可复用项目分析说明：运行时证据优先的功能/协议闭环验证

本文档是一份通用提示词与执行规范，可用于让另一个代码项目获得类似本项目的深度分析结果。它**不依赖 OpenClaw 或任何特定框架**，适用于 Web/API、CLI、agent、worker、队列、浏览器自动化、RPC、插件系统、微服务、数据管道等项目。

核心目标：让分析者不要停留在“读源码后猜测功能”，而是通过可复现证据确认：项目在当前环境中到底能做什么、哪些接口/命令/事件真实可用、哪些链路已经闭环、哪些仍是缺口。

---

## 1. 一句话版本

可直接复制给另一个项目：

```text
请像做协议逆向和功能闭环验证一样分析这个项目：以运行时证据优先，建立每个接口/事件/命令/任务的字段字典、真实可用性、复现步骤、已验证闭环和剩余缺口；所有结论必须有文件、日志、trace、测试、命令输出或运行时状态变化支撑，不要只基于源码或注释猜测。
```

---

## 2. 完整任务描述模板

```text
这是一个我拥有或被授权分析的项目。请按“运行时证据优先”的方式，系统分析它的真实能力边界、接口/协议行为和端到端闭环。

总体目标：
1. 不只读源码，要确认哪些功能在当前运行环境中真实可用。
2. 建立从入口 → 请求/事件/命令 → 服务端或 worker 处理 → 状态变化 → 返回结果的完整证据链。
3. 把已验证能力、未验证能力、阻塞点、风险点和下一步验证方法整理成能力目录或协议字段字典。
4. 所有结论必须能用命令、日志、trace、测试、数据库状态、文件变更或可复现最小实验支撑。
5. 优先做低副作用验证；需要修改时先备份、最小 diff、可恢复。
6. 如果源码、文档、注释和运行结果冲突，以当前运行时证据为准。

工作方式：
- 先做被动检查：仓库结构、配置、依赖、最近提交、测试、日志、缓存、数据文件、构建产物。
- 再识别真实入口：HTTP route、WebSocket、RPC、CLI、worker、cron、队列、agent、插件、数据库任务、前端资源等。
- 对每个能力按固定链路验证：入口/方法名 → 参数 schema → 实际请求 → 实际响应 → 事件/日志 → 状态变化 → 是否闭环。
- 不要把注释、死代码或未注册 handler 当成事实；必须用 runtime 行为确认。
- 每次只改变一个变量，记录输入、输出和环境状态。
- 完成后跑测试，更新文档/目录，并明确说明还有哪些未完成。

输出格式：
- Outcome：当前已经确认了什么。
- Key Evidence：关键文件、日志、trace、命令、响应片段。
- Verification Steps：如何从干净状态复现。
- Known Gaps：还有哪些能力未验证或只在源码中存在。
- Recommended Next Steps：下一步最值得做什么。
```

---

## 3. 推荐交付物

分析结束后，建议至少生成这些交付物：

1. **能力目录 / 协议目录**
   - 项目支持哪些接口、命令、事件、任务或能力。
   - 哪些只是源码存在，哪些是运行时可见，哪些已闭环验证。

2. **字段字典**
   - 每个 method/event/command 的 required params、optional params、返回结构、错误结构。

3. **验证记录**
   - 每个关键结论对应的命令、输入、响应、日志、trace、文件路径或数据库状态。

4. **剩余缺口清单**
   - 明确哪些还没有验证。
   - 为什么没验证：缺少权限、缺少环境、会产生副作用、没有入口、schema 不明等。

5. **复现步骤**
   - 从干净环境如何重新得到同样结论。

6. **测试或探针**
   - 能自动验证关键行为的单元测试、集成测试、小脚本或只读探针。

---

## 4. 能力目录字段模板

适用于 API、RPC、CLI、agent command、worker job、插件 hook 等。

```json
{
  "name": "能力/接口/命令名称",
  "category": "api | rpc | cli | event | worker | cron | plugin | database | browser | other",
  "entrypoint": "入口路径、route、命令、事件名或函数名",
  "source_location": [
    "src/server/routes.ts:120",
    "src/worker/jobs.ts:88"
  ],
  "runtime_evidence": [
    "logs/app.log 中观察到请求",
    "trace/xxx.jsonl 中观察到事件",
    "curl 命令返回 200",
    "数据库表 task_runs 增加记录"
  ],
  "required_params": {},
  "optional_params": {},
  "known_good_request": {},
  "success_response_shape": {},
  "error_response_shape": {},
  "state_changes": [
    "写入数据库",
    "生成文件",
    "发布事件",
    "启动后台任务"
  ],
  "verified_level": "not_seen | advertised | source_only | observed | request_ok | state_changed | closed_loop",
  "side_effect_level": "none | low | medium | high",
  "cleanup_method": "如何恢复或清理",
  "known_gaps": [
    "仍未确认某个字段",
    "仍未验证失败路径"
  ],
  "next_step": "下一步最小验证动作"
}
```

---

## 5. 验证等级定义

为了避免“看起来支持”和“真的可用”混在一起，建议给每个能力标一个等级：

| 等级 | 含义 |
|---|---|
| `not_seen` | 没在源码、配置、日志或运行时看到 |
| `source_only` | 源码中存在，但没有证明被注册或执行 |
| `advertised` | hello/features/docs/config 中宣称支持 |
| `observed` | 日志、trace 或事件中看到过，但未主动复现 |
| `request_ok` | 能主动请求并得到可解释响应 |
| `state_changed` | 请求后产生了可验证状态变化 |
| `closed_loop` | 请求方 → 执行方 → 状态变化/动作 → 结果回传 全链路已验证 |

只有 `closed_loop` 才能写成“已跑通”。其它等级都应说明证据边界。

---

## 6. 推荐分析流程

### Step 1：被动盘点

先不改代码、不主动探测外部服务，只做本地被动检查。

建议命令：

```bash
git status --short
git log --oneline -10
find . -maxdepth 3 -type f | sort | head -300
find . -maxdepth 3 -type f \( -name '*.py' -o -name '*.js' -o -name '*.ts' -o -name '*.go' -o -name '*.rs' -o -name '*.json' -o -name '*.yaml' -o -name '*.yml' \) | sort
```

重点看：

- README / docs
- package manifest / requirements / lock file
- route 注册
- CLI 入口
- worker / cron / scheduler
- WebSocket / RPC handler
- database schema / migration
- logs / trace / cache / snapshots
- tests
- recent commits

### Step 2：确认运行入口

找出项目实际如何运行：

- HTTP server：端口、路由、middleware。
- CLI：命令入口、参数解析。
- worker：消费哪个队列、任务 schema。
- agent：事件流、工具调用、审批流。
- browser/frontend：入口 HTML、JS bundle、API 调用。
- database：哪些表会被读写。

不要只看“有这个函数”，要确认它是否被注册、是否被当前配置启用。

### Step 3：建立最小闭环

对一个能力，只验证最小成功路径：

```text
输入 → 入口接受 → handler 执行 → 状态变化/动作发生 → 返回结果 → 日志/trace 可见
```

示例：

- API：`curl` 请求 → HTTP 200 → DB 行增加 → response 返回 ID。
- CLI：执行命令 → stdout 有结果 → 输出文件存在。
- worker：入队 job → worker 消费 → job 状态变为 done。
- RPC：发送 method → 收到 event/result → 对端确实执行。
- frontend：点击按钮 → network request → UI 状态变化。

### Step 4：记录失败路径

失败也有价值。记录：

- 缺少字段时报什么错。
- 错误类型和错误码。
- 是否有 rate limit / auth / permission。
- 是否有 silent failure。
- 是否只是前端拒绝，后端仍接受。

### Step 5：清理与恢复

每个主动验证都应回答：

- 是否写了数据库？
- 是否生成了文件？
- 是否创建了远端/后台资源？
- 是否能删除？
- 如果不能删除，是否记录为残留状态？

### Step 6：测试与提交

最后至少执行：

```bash
python3 -m unittest discover -s tests -v  # Python 项目示例
npm test                                 # Node 项目示例
go test ./...                            # Go 项目示例
cargo test                               # Rust 项目示例
```

如果测试环境缺依赖，也要记录原因。

---

## 7. 证据优先级

当证据冲突时，建议按以下优先级判断：

```text
当前运行时行为
> 主动复现结果
> 实时日志/trace
> 数据库/文件状态变化
> 当前进程配置
> 构建产物
> 源码
> 文档
> 注释
> 历史报告
```

典型规则：

- 源码里有但没注册：不能算可用。
- docs 说支持但 runtime 返回 unknown method：以 runtime 为准。
- 配置关闭的功能：标记为 source_only 或 disabled。
- 旧报告说未完成，但新证据跑通了：保留历史描述时必须新增“后续补充”说明。

---

## 8. 常见项目类型的关注点

### 8.1 Web / API 项目

重点：

- route table
- middleware 顺序
- auth/session
- request body schema
- response schema
- error code
- database writes
- background task
- streaming/SSE/WebSocket

闭环示例：

```text
POST /api/tasks
→ handler 接收 JSON
→ 插入 task 表
→ worker 消费
→ task.status=done
→ GET /api/tasks/:id 返回结果
```

### 8.2 CLI 项目

重点：

- CLI parser
- subcommand
- config loading
- env var
- filesystem side effect
- exit code
- stdout/stderr

闭环示例：

```text
cli build --input x
→ 解析参数
→ 读取配置
→ 生成 dist 文件
→ exit code 0
→ 输出文件 hash 可复现
```

### 8.3 Worker / Queue / Cron 项目

重点：

- job schema
- enqueue path
- consumer handler
- retry/dead-letter
- idempotency
- state transition

闭环示例：

```text
enqueue job
→ queue length 增加
→ worker 日志出现 job id
→ DB 状态 running → done
→ result 字段写入
```

### 8.4 Agent / Tool 项目

重点：

- agent run lifecycle
- tool call schema
- approval / policy
- event stream
- cancellation
- result aggregation

闭环示例：

```text
创建 run
→ assistant 请求 tool
→ tool 执行
→ tool result event
→ assistant 汇总
→ run completed
```

### 8.5 Browser / Frontend 项目

重点：

- served HTML
- JS bundle
- API initiator
- browser storage
- network request
- UI state
- WebSocket/SSE

闭环示例：

```text
点击按钮
→ fetch /api/action
→ response ok
→ localStorage/session 更新
→ DOM 出现结果
```

### 8.6 Plugin / Extension 项目

重点：

- plugin discovery
- manifest
- load order
- trust/config allowlist
- hook registration
- runtime invocation

闭环示例：

```text
plugin manifest 被发现
→ plugin loaded 日志出现
→ hook registered
→ 触发 hook
→ plugin 返回结果
```

---

## 9. 输出报告模板

```md
# 项目运行时闭环分析报告

## Outcome

- 已确认能力：
- 已跑通闭环：
- 未跑通/未验证能力：
- 当前最大阻塞：

## Environment

- 分支：
- commit：
- 运行命令：
- 测试命令：
- 关键配置：

## Capability Catalog

| 能力 | 类型 | 验证等级 | 成功证据 | 副作用 | 缺口 |
|---|---|---|---|---|---|
| example.run | CLI | closed_loop | logs/x + output/y | low | 无 |

## Field Dictionary

### example.run

- required params:
- optional params:
- known good input:
- success response:
- error response:
- state changes:
- cleanup:

## Key Evidence

```text
命令、日志、trace、文件路径、响应片段
```

## Verification Steps

```bash
# 从干净状态复现
```

## Known Gaps

1. xxx 只在源码中存在，尚未 runtime 验证。
2. yyy 会产生高副作用，暂未测试。
3. zzz 缺少清理接口。

## Recommended Next Steps

1. 最小副作用验证 xxx。
2. 给 yyy 增加只读探针。
3. 把 zzz 写入测试。
```

---

## 10. 让分析更可靠的附加要求

可以在提示词里补充：

```text
请遵守以下约束：

1. 不要一次性大范围修改代码。
2. 不要用源码推翻运行结果。
3. 不要把旧文档当成当前事实。
4. 所有敏感字段如 token、cookie、authorization、secret、private key 必须打码。
5. 每个结论必须标注证据路径。
6. 如果只能推断，必须写明“这是推断”。
7. 如果验证失败，记录失败输入、失败输出和下一步替代方案。
8. 如果某个操作可能产生残留状态，先说明风险，再选择低影响路径。
```

---

## 11. 更强的“闭环验证”提示词

如果希望分析者像本项目一样深挖，可以直接使用下面这一段：

```text
请不要停留在静态代码审计。我要的是“真实闭环验证”。

某个功能只有在以下链路全部成立时，才算完成：
1. 调用方能发起请求/命令/事件；
2. 接收方接受参数并进入正确 handler；
3. 执行方确实执行动作；
4. 产生可观测状态变化或结果；
5. 结果通过预期通道返回；
6. 日志、trace、测试或状态能证明链路；
7. 能从干净状态复现；
8. 结论被写入 docs/tests/catalog。

如果任何一步缺失，请标记为未完成，而不是猜测成功。
```

---

## 12. 最终检查清单

分析结束前逐项确认：

- [ ] 是否检查了当前 git 状态和最近提交？
- [ ] 是否识别了真实运行入口？
- [ ] 是否区分 source_only / observed / closed_loop？
- [ ] 是否至少跑通一个最小闭环？
- [ ] 是否记录了参数 schema？
- [ ] 是否记录了成功和失败响应？
- [ ] 是否记录了副作用和清理方式？
- [ ] 是否跑了测试或说明测试无法运行原因？
- [ ] 是否更新了文档或能力目录？
- [ ] 是否明确列出剩余缺口？

---

## 13. 简短交接模板

给下一个分析者的 handoff 可以这样写：

```text
当前项目已完成：
- xxx 能力 closed_loop，证据在 path/to/evidence.json。
- yyy 只达到 observed，尚未主动复现。
- zzz source_only，当前配置未启用。

当前工作区状态：
- branch:
- commit:
- git status:
- tests:

下一步建议：
1. 先验证 aaa，因为副作用最低且能补齐最大缺口。
2. 再验证 bbb，但需要清理策略。
3. 暂不做 ccc，因为会产生不可恢复状态。
```

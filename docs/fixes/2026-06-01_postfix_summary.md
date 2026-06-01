# 2026-06-01 Post-fix Summary

## 已修复内容

### 1. `/v1/responses/compact` 兼容性
- 保证 `POST /v1/responses/compact` 正常返回。
- 增加 `POST /v1/responses/compact/` 尾斜杠兼容。
- 统一非法 JSON / 转换失败为 OpenAI 风格错误体。

### 2. Responses compact / stream 兼容
- `/v1/responses` 遇到 `compaction_trigger` 时走专用 compact 流程。
- SSE 输出补齐 `response.created` / `response.output_item.done` / `response.completed`。

### 3. 路由可诊断性
- 新增 `/api/diagnostics/routes`。
- 启动时记录关键路由存在性，便于快速判断是否命中旧实例/错端口/反代问题。

### 4. 调度策略修复
- 移除 `MIMO_PREFERRED_UID=6875021188`。
- 移除 `MIMO_MANAGER_EXCLUDE_USER_IDS=6875021188`。
- 默认切换为 uid 节点优先，legacy `uid=<none>` 不再参与调度。
- 允许通过配置显式恢复 legacy fallback（默认关闭）。

### 5. 节点池卫生
- `uid=<none>` 连接会被后台清理。
- 新的 legacy 连接在 uid 节点存在时直接拒绝。
- 注入成功判定改为：必须观察到 gateway 侧 `/ws?uid=<uid>` 新连接。

### 6. 观测增强
- `/api/stats` 增加：
  - `uid_coverage`
  - `dispatch_policy`
  - `preferred_uid`
  - `manager_excluded_user_ids`
- 节点标识改为 `uid@host`，避免同 IP 多账号混淆。

## 仍需关注
- 历史 metrics 中仍保留旧 401/404/502 统计，这是历史累计，不代表当前仍在发生。
- `api/errors` 属于 WebUI 登录态接口，Bearer AI key 访问会返回 401，属于预期行为。

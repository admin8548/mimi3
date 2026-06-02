# 2026-06-02 主线稳定性 / API 兼容 / 调度 / 错误恢复推进记录

## Outcome

本轮从 OpenClaw 协议研究收束后回到 `mimo2api` 主线，优先修复了网关与 bridge 在高噪声、流式响应、错误恢复场景下的稳定性问题。

## Key Changes

- `mimo2api/web_service.py`
  - 对严格模式下的 legacy `uid=<none>` bridge 拒绝日志做限频，避免旧 bridge 快速重连时刷爆 `gateway.log`。
  - 对被拒绝的 legacy websocket 增加短暂 hold 后再关闭，降低旧远端脚本固定短周期重连造成的连接风暴。
  - 修复 Responses SSE 与通用转发流中 keepalive/data 同时就绪时优先处理 keepalive 导致真实 chunk/finish/error 丢失的竞态。
  - 通用转发流现在会处理节点中途 `error` 帧，避免客户端挂住并等待 stale sweeper 兜底。
- `mimo2api/bridge.py`
  - bridge 升级到 `BRIDGE_VERSION: 2.2`。
  - websocket 重连从固定 3 秒改为指数退避 + jitter，并设置 open/ping/close timeout。
- `mimo2api/responses_converter.py`
  - `_sse_event()` 不再原地修改输入 payload，降低复用 payload 时的隐性副作用。
- `mimo2api/config.py`
  - 新增 `get_env_bool/get_env_int/get_env_float`，对关键环境变量做容错解析与上下界钳制。
  - 已接入 `main.py`、`web_service.py`、`metrics_store.py`、`manager.py` 的端口/超时/指标窗口/开关配置。
- `tests/test_gateway_diagnostics.py`
  - 增加 legacy 拒绝日志限频测试。
  - 增加 Responses SSE payload 不被原地污染测试。
  - 增加非法数值/布尔环境变量回退测试。

## Verification

```bash
python3 -m py_compile main.py mimo2api/*.py
python3 -m unittest discover -s tests -v
```

结果：18 个单元测试全部通过。

## Recommended Next Steps

1. API 兼容：补 Responses API 的更多非流式/工具调用/多模态历史项回归测试。
2. 调度：增加节点选择策略的可观测字段，例如 preferred uid fallback 原因、冷却原因、近 N 次失败状态。
3. 错误恢复：继续扩大容错范围到剩余外部输入，例如用户 JSON 文件 schema、模型映射 JSON 与 WebUI 用户导入内容。
4. 运行期：观察 `gateway.log` 中 legacy 拒绝风暴是否降噪；必要时调大 `MIMO_LEGACY_REJECT_HOLD_SECONDS` 或临时允许 legacy fallback。

## Follow-up Batch: Dispatch Observability + Responses Tool Compatibility

### Outcome

- `/api/stats` 的 `nodes[]` 明细新增 `cooldown_reason`，能直接看到节点处于冷却的原因，例如 `401 Unauthorized`。
- `GatewayState` 新增 `client_cooldown_reasons`，在节点连接、断开、退休、发送失败清理时同步维护，避免冷却原因残留。
- Responses -> Chat 转换新增函数工具标准化逻辑：
  - 支持 Responses 风格：`{"type":"function","name":"...","parameters":{...}}`
  - 支持 Chat 风格：`{"type":"function","function":{"name":"..."}}`
  - 跳过无法透传到 Chat Completions 的非函数内置工具，避免 `KeyError`。

### Verification

新增回归测试：

- `DispatchObservabilityTests.test_stats_exposes_node_cooldown_reason`
- `ResponsesToolCompatibilityTests.test_convert_request_accepts_responses_and_chat_style_function_tools`

当前测试数：20。

## Follow-up Batch: Config/File Input Recovery

### Outcome

- 模型映射路径新增 `MIMO_MODEL_MAPPING_PATH`，Docker 默认指向 `/app/data/model_mapping.json`，与挂载目录保持一致。
- `PUT /api/model_mapping` 现在要求映射必须是“非空字符串 -> 非空字符串”的 JSON 对象，避免非字符串 model 值污染上游请求。
- 保存模型映射前会自动创建父目录。
- WebUI 用户导入/删除新增 userId 校验：仅允许字母、数字、下划线、点、短横线，长度 1-128，避免异常路径/非法 uid 进入文件名。
- `README.md`、`env.example`、`Dockerfile` 同步补充 `MIMO_MODEL_MAPPING_PATH`。

### Verification

新增回归测试：

- `ModelMappingValidationTests.test_model_mapping_requires_string_to_string_entries`
- `ModelMappingValidationTests.test_model_mapping_put_rejects_invalid_schema`
- `UserIdValidationTests.test_user_id_validation_rejects_path_like_values`

当前测试数：23。

## Follow-up Batch: Responses SSE Parity + Dispatch Pool Explanation

### Outcome

- Responses 流式转换器新增 `response.in_progress` 事件，提升与官方 Responses SSE 序列的兼容性。
- 文本输出结束时新增 `response.output_text.done`，客户端可在 content part done 前拿到完整文本事件。
- Responses 输入中的 `input_image.image_url` 支持对象形态：`{"image_url":{"url":"..."}}`。
- `/api/stats` 新增顶层 `dispatchable_clients`，区分 raw available clients 与实际可调度节点数。
- `/api/stats` 新增 `dispatch_pool`：
  - `effective_pool`
  - `dispatchable_clients`
  - `available_clients_raw`
  - `available_uid_clients`
  - `available_legacy_clients`
  - `preferred_uid_available_clients`
  - `fallback_reason`
  - `available_uids`
- `preferred_uid` 明细新增 `available_count`，`fallback_active` 改为基于 preferred 可用节点数计算。

### Verification

新增回归测试：

- `DispatchPoolStatsTests.test_dispatch_pool_explains_preferred_uid_fallback`
- `ResponsesStreamingCompatibilityTests.test_stream_converter_emits_in_progress_and_output_text_done`
- `ResponsesStreamingCompatibilityTests.test_response_input_image_url_object_is_normalized`

当前测试数：26。

# Retrospective: "Unexpected content after document" 400 错误修复

**日期**: 2026-05-30
**问题**: 调用 mimo2api Responses API 时，上游 Mimo API 返回 HTTP 400，错误信息为 `"unexpected content after document"`
**涉及文件**: `mimo2api/responses_converter.py`、`docker-compose.yml`、`Dockerfile`

---

## 1. 问题现象

通过 mimo2api 的 OpenAI Responses API 兼容层发送请求时，无论是否携带 reasoning 字段，上游 Mimo Chat Completions API 均返回 400 错误。错误信息 `"unexpected content after document"` 表明请求体 JSON 格式本身合法，但包含了上游 API 无法识别的未知字段。

## 2. 根因分析

### 2.1 第一层：顶层未知字段（已在前轮修复）

`convert_request()` 直接返回 `chat_req.model_dump(exclude_none=True)` 的完整字典，其中包含了 Responses API 特有的顶层字段（如 `reasoning`），这些字段不在 Chat Completions API 的参数白名单中，导致上游拒绝。

**修复方式**: 对 `req` 字典做白名单过滤，仅保留 Chat Completions API 认识的顶层字段。

### 2.2 第二层：消息级别未知字段 `reasoning_content`（本轮修复）

即使顶层字段已过滤，`ChatMessage` 模型上定义了 `reasoning_content` 字段（用于在转换过程中暂存推理内容）。调用 `model_dump(exclude_none=True)` 时，如果消息包含推理内容，该字段会随序列化结果一起输出到最终请求体中。上游 API 同样不认识消息级别的 `reasoning_content` 字段，因此继续返回 400。

**修复方式**: 在 `model_dump()` 之后、`return` 之前，遍历 `result["messages"]` 并 `pop` 掉 `reasoning_content`：

```python
result = chat_req.model_dump(exclude_none=True)
for msg in result.get("messages", []):
    msg.pop("reasoning_content", None)
return result
```

### 2.3 第三层：Docker 容器未重建

`Dockerfile` 使用 `COPY mimo2api ./mimo2api` 在构建时将源码复制进镜像。即使宿主机上的代码已修改，运行中的容器仍使用旧代码，直到执行 `docker compose up --build` 重建镜像并重启容器。

## 3. 修复过程

| 步骤 | 操作 | 结果 |
|------|------|------|
| 1 | 定位 `convert_request()` 返回值处 (`responses_converter.py:272`) | 确认 `chat_req.model_dump(exclude_none=True)` 直接返回 |
| 2 | 在返回前插入消息字段清理逻辑，`pop("reasoning_content")` | 代码变更就绪 |
| 3 | `docker compose up --build -d` | 镜像重建成功，容器 `mimi3` 启动且 health check 通过 |

## 4. 经验教训

### 4.1 Pydantic `model_dump` 的隐含风险

`model_dump(exclude_none=True)` 只排除 `None` 值，不会排除**非标准但有值的内部字段**。当模型类既承载内部转换逻辑又用于序列化输出时，内部字段会意外泄露。更好的做法：

- **方案 A（本次采用）**: 序列化后手动清理——简单直接，但容易遗漏。
- **方案 B（推荐长期方案）**: 为内部转换和外部输出分别定义模型类，内部模型含 `reasoning_content`，输出模型不含。
- **方案 C**: 使用 Pydantic 的 `exclude` 参数：`model_dump(exclude_none=True, exclude={"messages": {"__all__": {"reasoning_content"}}})`。

### 4.2 Docker 构建上下文与代码热更新

`COPY` 指令在构建时将代码固化到镜像中。开发调试时容易忘记重建，导致修改无效。建议：

- 开发阶段使用 `volumes` 挂载源码目录（`./mimo2api:/app/mimo2api`），避免频繁重建。
- 生产部署时再用 `COPY` 固化，并确保 CI/CD 流程包含 `--build`。

### 4.3 分层排查的必要性

`400 unexpected content after document` 这类错误可能由**多个未知字段**共同导致。第一次只修复了顶层字段，但消息级别仍有一个未知字段。教训：修复 API 字段兼容性问题时，应该一次性检查所有序列化层级（顶层参数、消息内容、工具定义等），而不是逐层修复、逐层部署验证。

### 4.4 验证应覆盖序列化全链路

有效的验证方式是直接打印或日志记录 `convert_request()` 的最终返回值（而非只看代码逻辑），确认发往上游的 JSON 中不包含任何非标准字段。

## 5. 当前状态

- ✅ `reasoning_content` 已从序列化输出中移除
- ✅ 顶层字段白名单过滤生效
- ✅ Docker 容器已重建并运行 (`mimi3` healthy)
- ✅ `reasoning_content` 的内部转换逻辑未受影响（仅在序列化输出时剥离）

## 6. 后续建议

1. **增加集成测试**: 构造包含 reasoning 历史的多轮请求，验证 `convert_request()` 输出不含非标准字段。
2. **考虑模型分离**: 将 `ChatMessage` 拆分为内部转换用模型和输出序列化用模型，从根本上避免字段泄露。
3. **文档化字段白名单**: 在代码中明确注释 Chat Completions API 接受的标准字段列表，便于后续维护。

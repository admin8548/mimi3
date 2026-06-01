# "unexpected content after document" 400 错误根因分析

**日期**: 2026-05-30  
**错误信息**: `{"error":{"code":"400","message":"unexpected content after document: line 1 column 281 (char 280)","param":"","type":"BadRequest"}}`  
**状态**: 仅分析，未修改代码

---

## 1. 错误性质

`"unexpected content after document"` 是 **RapidJSON 解析器**的标准错误信息，含义为：
- 解析器成功解析了一个完整的 JSON 文档
- 在该文档之后（char 280 起）发现了非空白字符
- 即：上游 Mimo API 收到的请求体**不是合法的单一 JSON 对象**

这与"未知字段"导致的 400 错误（通常报 `invalid_request` 或 `unknown parameter`）性质完全不同。此错误说明请求体在 JSON 格式层面就不合法。

---

## 2. 已排除的因素

### 2.1 容器未重建 ❌ 已排除
`diff` 确认容器内代码与宿主机一致：
```
$ diff responses_converter.py <(docker exec mimi3 cat ...)
# 无差异
```

### 2.2 `reasoning_content` 泄露 ❌ 已排除
```python
result = chat_req.model_dump(exclude_none=True)
for msg in result.get("messages", []):
    msg.pop("reasoning_content", None)
return result
```
逻辑正确，所有测试用例的 `convert_request()` 输出均不含 `reasoning_content`。

### 2.3 顶层字段泄露 ❌ 已排除
`ALLOWED_CHAT_KEYS` 白名单过滤生效，`reasoning`、`text`、`include`、`metadata` 等 Responses API 专有字段均被移除。

### 2.4 序列化管道验证
通过完整管道模拟（`convert_request` → `json.dumps` → `apply_model_mapping` → `build_ws_payload` → bridge 解包 → httpx 发送），每个环节均保持 JSON 合法性。

---

## 3. 疑似根因

### 根因 A（最高概率）：多轮对话历史中 assistant 消息包含多个 content parts，产生 `content: [...]` 数组

**触发条件**: Responses API 的多轮对话中，assistant 的输出包含**多个 `output_text` 部分**。

**转换过程**:
```
输入: {"type": "message", "role": "assistant", "content": [
    {"type": "output_text", "text": "Part 1."},
    {"type": "output_text", "text": "Part 2."}
]}

_extract_message_content() 检测到 parts 数量 > 1，返回列表而非字符串

输出: {"role": "assistant", "content": [
    {"type": "text", "text": "Part 1."},
    {"type": "text", "text": "Part 2."}
]}
```

**问题**: 上游 Mimo Chat Completions API 可能**不支持 `content` 为数组格式**，将数组 `[{"type":"text","text":"Part 1."},{"type":"text","text":"Part 2."}]` 作为一个独立的 JSON 值解析后，发现后续还有 `"role":"user","content":"..."` 等内容，报告 "unexpected content after document"。

**复现场景**:
```json
{
  "model": "mimo-v2.5-pro",
  "messages": [
    {"role": "user", "content": "Hello"},
    {"role": "assistant", "content": [
      {"type": "text", "text": "Hi!"},
      {"type": "text", "text": "How can I help you today?"}
    ]},
    {"role": "user", "content": "I need help."}
  ],
  "stream": true
}
```

此 body 长度为 286 字符，错误报告的 char 280 恰好落在 `"stream":` 附近，与 body 结构吻合。

**触发频率**: 单轮对话不触发（assistant 只有一个 content part → 转换为字符串），多轮对话中 assistant 有多个 output_text 时触发。

### 根因 B（中等概率）：直接 `/v1/chat/completions` 转发路径未过滤字段

`_forward_request()` 对 `/v1/chat/completions` 请求**原样转发**，仅做模型映射。如果客户端（如 Cursor、Continue 等 IDE 插件）在 Chat Completions 请求中携带了非标准字段（如 `reasoning`、`input`），这些字段会直接传递到上游。

但此路径的问题通常报 `unknown_parameter` 而非 `unexpected content after document`，因此概率较低。

### 根因 C（低概率）：客户端发送了格式异常的请求体

客户端发送的请求体本身存在 JSON 格式问题（如 BOM、尾部多余内容、编码问题）。`_forward_request()` 中的 `body_text = body.decode("utf-8", "ignore").lstrip("\ufeff")` 仅处理 BOM，不校验 JSON 完整性。

---

## 4. 验证方法

### 验证根因 A
在 `responses_handler()` 中添加临时日志，打印发往上游的 body：
```python
# 在 chat_body_text = json.dumps(chat_req, ensure_ascii=False) 之后
logger.info(f"[DEBUG] 发往上游的 body ({len(chat_body_text)} chars): {chat_body_text[:500]}")
```

观察触发 400 错误时的 body 内容，确认是否包含 `"content": [{...}]` 数组格式。

### 验证根因 B
检查 `/v1/chat/completions` 的请求日志，确认是否有客户端携带非标准字段。

---

## 5. 修复方向（供后续实施参考）

### 修复根因 A：将多 part content 合并为单个字符串

在 `_extract_message_content()` 中，当所有 parts 都是 text 类型时，将其合并为单个字符串而非返回列表：

```python
def _extract_message_content(content: Any) -> Union[str, list[dict[str, Any]]]:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    parts: list[dict[str, Any]] = []
    for part in content:
        # ... 现有的 part 处理逻辑 ...

    if len(parts) == 0:
        return ""
    if len(parts) == 1 and parts[0].get("type") == "text":
        return parts[0]["text"]
    
    # 新增：如果所有 parts 都是 text，合并为单个字符串
    if all(p.get("type") == "text" for p in parts):
        return "\n".join(p["text"] for p in parts)
    
    return parts  # 仅 image_url 等非 text 类型保留列表格式
```

### 修复根因 B：对直接转发路径也做字段过滤

在 `_forward_request()` 中对 `body_text` 做类似的字段白名单过滤。

---

## 6. 结论

| 因素 | 状态 | 说明 |
|------|------|------|
| 容器未重建 | ✅ 已排除 | 代码一致 |
| `reasoning_content` 泄露 | ✅ 已排除 | 修复已生效 |
| 顶层字段泄露 | ✅ 已排除 | 白名单生效 |
| **content 数组格式** | **⚠️ 最高嫌疑** | 多轮对话多 part 输出产生 content 数组 |
| 直接转发路径 | ⚠️ 中等嫌疑 | 未做过滤，但报错类型不太匹配 |
| 客户端格式问题 | ❓ 低概率 | 需要客户端侧确认 |

**下一步**: 添加上游 body 日志，捕获触发 400 时的实际请求体，确认具体是哪个原因。

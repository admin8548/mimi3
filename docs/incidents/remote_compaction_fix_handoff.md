# mimo2api Codex Remote Compaction 兼容修复交接文档

生成时间：2026-05-31  
适用目标：另一台运行同类 `mimo2api` 网关的服务器  
核心文件：

- `mimo2api/web_service.py`
- `mimo2api/responses_converter.py`

当前已修复文件校验值：

```bash
sha256sum mimo2api/web_service.py mimo2api/responses_converter.py
# d9de33852cd28417d54abed34e6df43ecd4c6a8c14a763e27cde45ffacce61df  mimo2api/web_service.py
# 9648087d3d6db7f3c0339af0667eecaee865c66abd3f24fd055f39b278e79a30  mimo2api/responses_converter.py
```

---

## 1. 问题现象

Codex 使用 remote compaction 时，`mimo2api` 可能出现以下错误：

```text
stream disconnected before completion
expected value at line 1 column 1
remote compaction v2 stream closed before response.completed
remote compaction v2 expected exactly one compaction output item
```

或客户端表现为：

- compaction 后卡住；
- SSE 流提前断开；
- 客户端尝试解析空响应，报 JSON parse error；
- compact 后历史上下文丢失。

---

## 2. 根因

这不是普通 JSON 语法错误，而是 Responses API 兼容层缺少 Codex compaction 协议兼容。

Codex remote compaction 有两种入口：

### 2.1 非流式 compact endpoint

路径通常为：

```text
POST /v1/responses/compact
```

Codex 期望返回：

```json
{
  "output": [
    {
      "type": "compaction",
      "encrypted_content": "..."
    }
  ]
}
```

旧实现把该接口简单转发给普通 `/v1/responses` handler，最后返回普通 assistant `message`，不是 `compaction` item。

### 2.2 Remote compaction v2 流式入口

路径为：

```text
POST /v1/responses
```

请求体的 `input` 数组中会包含：

```json
{"type":"compaction_trigger"}
```

Codex 期望 SSE 流中至少包含：

```text
event: response.created

event: response.output_item.done
# data.item.type 必须是 compaction 或 compaction_summary

event: response.completed
```

旧实现会把它当普通 Responses 请求转换，最终输出普通 `message`，导致 Codex 认为 compaction 失败。

---

## 3. 修复目标

本次修复实现以下兼容：

1. `/v1/responses/compact` 专用处理，不再复用普通 `/v1/responses`。
2. `/v1/responses` 检测 `compaction_trigger` 后走 Codex remote compaction v2 专用流程。
3. 网关通过上游 Chat Completions 生成摘要，但由网关合成 Responses compaction item。
4. 后续普通对话能识别历史中的 `compaction` / `compaction_summary` / `context_compaction`。
5. 上游空响应、非法 JSON、超时、断流时返回明确错误，不让客户端解析空 body。

---

## 4. 推荐迁移方式

如果另一台服务器代码版本基本一致，最稳妥方式是直接同步这两个文件。

### 4.1 在源服务器打包修复文件

在已修复服务器执行：

```bash
cd /home/ubuntu/mimo2api
mkdir -p /tmp/mimo2api_compaction_fix/mimo2api
cp mimo2api/web_service.py /tmp/mimo2api_compaction_fix/mimo2api/web_service.py
cp mimo2api/responses_converter.py /tmp/mimo2api_compaction_fix/mimo2api/responses_converter.py
cp docs/incidents/remote_compaction_fix_handoff.md /tmp/mimo2api_compaction_fix/
tar -C /tmp -czf /tmp/mimo2api_compaction_fix_20260531.tar.gz mimo2api_compaction_fix
sha256sum /tmp/mimo2api_compaction_fix_20260531.tar.gz
```

### 4.2 在目标服务器备份原文件

在另一台服务器执行：

```bash
cd /home/ubuntu/mimo2api
mkdir -p backup_compaction_fix_$(date +%Y%m%d_%H%M%S)
cp mimo2api/web_service.py backup_compaction_fix_*/web_service.py.bak
cp mimo2api/responses_converter.py backup_compaction_fix_*/responses_converter.py.bak
```

如果 shell 不支持上面的通配写法，可用：

```bash
cd /home/ubuntu/mimo2api
BACKUP_DIR="backup_compaction_fix_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"
cp mimo2api/web_service.py "$BACKUP_DIR/web_service.py.bak"
cp mimo2api/responses_converter.py "$BACKUP_DIR/responses_converter.py.bak"
```

### 4.3 覆盖文件

将压缩包上传到目标服务器后：

```bash
cd /home/ubuntu/mimo2api
tar -xzf /tmp/mimo2api_compaction_fix_20260531.tar.gz -C /tmp
cp /tmp/mimo2api_compaction_fix/mimo2api/web_service.py mimo2api/web_service.py
cp /tmp/mimo2api_compaction_fix/mimo2api/responses_converter.py mimo2api/responses_converter.py
```

### 4.4 校验文件

```bash
cd /home/ubuntu/mimo2api
sha256sum mimo2api/web_service.py mimo2api/responses_converter.py
```

期望输出：

```text
d9de33852cd28417d54abed34e6df43ecd4c6a8c14a763e27cde45ffacce61df  mimo2api/web_service.py
9648087d3d6db7f3c0339af0667eecaee865c66abd3f24fd055f39b278e79a30  mimo2api/responses_converter.py
```

---

## 5. 手工修复清单

如果另一台服务器版本不同，不建议直接覆盖，则按以下清单手工改。

### 5.1 `responses_converter.py`

#### 5.1.1 新增 compaction item model

在 Responses item model 区域新增：

```python
class RespCompactionItem(BaseModel):
    type: Literal["compaction", "compaction_summary", "context_compaction"] = "compaction"
    encrypted_content: Optional[str] = None
```

并把 `RespItem` 从：

```python
RespItem = Union[RespReasoningItem, RespMessageItem, RespFunctionCallItem, RespFunctionOutputItem]
```

改为：

```python
RespItem = Union[RespReasoningItem, RespMessageItem, RespFunctionCallItem, RespFunctionOutputItem, RespCompactionItem]
```

#### 5.1.2 新增 compaction 内容提取函数

```python
def _extract_compaction_content(item: RespCompactionItem) -> str:
    return item.encrypted_content or ""
```

#### 5.1.3 解析 Responses input 中的 compaction item

在 `_parse_response_input_item()` 中增加：

```python
if item_type in {"compaction", "compaction_summary", "context_compaction"}:
    item = dict(raw_item)
    if item.get("encrypted_content") is None:
        item["encrypted_content"] = ""
    return RespCompactionItem.model_validate(item)

if item_type == "compaction_trigger":
    return None
```

#### 5.1.4 普通对话转换时保留 compact 后历史

在 `convert_request()` 遍历 `items` 时，在 `RespMessageItem` 处理之前增加：

```python
if isinstance(item, RespCompactionItem):
    compaction_content = _extract_compaction_content(item).strip()
    if compaction_content:
        chat_messages.append(ChatMessage(
            role="system",
            content=(
                "The conversation before this point was compacted. "
                "Use the following compacted context as prior conversation state:\n\n"
                f"{compaction_content}"
            ),
        ))
    continue
```

这样 compact 后的 `encrypted_content` 不会被丢弃。

---

### 5.2 `web_service.py`

#### 5.2.1 修改 import

把：

```python
from .responses_converter import ResponsesStreamConverter
```

改为：

```python
from .responses_converter import ResponsesStreamConverter, RespCompactionItem
```

#### 5.2.2 新增 compact helper 函数

建议放在 `record_error()` 后、常量定义前。

功能包括：

- `_is_compaction_trigger_request(req_body)`：检测 `input` 中是否存在 `compaction_trigger`。
- `_extract_text_from_chat_message(message)`：从 Chat Completions 响应 message 中抽取文本。
- `_extract_response_text(chat_resp)`：从上游响应抽取摘要文本。
- `_build_compaction_chat_request(req_body)`：把 compact 请求转成非流式 Chat Completions 摘要请求。
- `_build_compaction_output(summary_text)`：生成 `{"output":[{"type":"compaction", ...}]}`。
- `_compact_sse_event(event_type, payload)`：生成 Responses SSE。

核心逻辑：

```python
def _is_compaction_trigger_request(req_body: dict[str, Any]) -> bool:
    input_data = req_body.get("input")
    if not isinstance(input_data, list):
        return False
    return any(isinstance(item, dict) and item.get("type") == "compaction_trigger" for item in input_data)
```

```python
def _build_compaction_output(summary_text: str) -> dict[str, Any]:
    item = RespCompactionItem(type="compaction", encrypted_content=summary_text)
    return {"output": [item.model_dump(exclude_none=True)]}
```

#### 5.2.3 新增上游非流式收集函数

新增 `collect_upstream_chat_response(...)`，用于 compact handler：

- 固定请求 `/v1/chat/completions`
- 收集完整 body
- 空 body 返回明确错误
- 非法 JSON 记录 `raw_body[:500]`
- 透传/包装上游错误

重点判断：

```python
if not raw_body.strip():
    record_error(route_key, 502, "上游返回空响应", detail="")
    raise RuntimeError("上游返回空响应")

try:
    chat_resp = json.loads(raw_body)
except json.JSONDecodeError:
    record_error(route_key, 502, "上游返回了非法 JSON", detail=raw_body[:500])
    raise RuntimeError("上游返回了非法 JSON")
```

#### 5.2.4 新增 `handle_compaction_request()`

该函数统一处理：

- `/v1/responses/compact` 非流式 compact；
- `/v1/responses` 中带 `compaction_trigger` 的 remote compaction v2 stream。

非流式返回：

```python
return JSONResponse(content={
    "output": [
        {"type": "compaction", "encrypted_content": summary_text}
    ]
})
```

流式返回必须按顺序输出：

```text
event: response.created

event: response.output_item.done

event: response.completed
```

`response.output_item.done` 的 payload 必须包含：

```json
{
  "output_index": 0,
  "item": {
    "type": "compaction",
    "encrypted_content": "..."
  }
}
```

这是 Codex remote compaction v2 成功的关键。

#### 5.2.5 修改 `/v1/responses`

在 `responses_handler()` 解析 JSON 后、普通 `responses_convert_request(req_body)` 前插入：

```python
if _is_compaction_trigger_request(req_body):
    return await handle_compaction_request(
        req_body,
        route_key="/v1/responses",
        stream=req_body.get("stream", True) is True,
    )
```

这样带 `compaction_trigger` 的请求不会误走普通 ResponsesStreamConverter。

#### 5.2.6 修改 `/v1/responses/compact`

旧逻辑通常是：

```python
@app.post("/v1/responses/compact")
async def responses_compact_handler(request: Request):
    return await responses_handler(request)
```

必须改为专用 handler：

```python
@app.post("/v1/responses/compact")
async def responses_compact_handler(request: Request):
    body = await request.body()
    try:
        req_body = json.loads(body.decode("utf-8", "ignore").lstrip("\ufeff"))
    except Exception as exc:
        record_error("/v1/responses/compact", 400, f"请求解析失败: {exc}")
        return JSONResponse({"error": {"message": f"请求解析失败: {exc}"}}, status_code=400)
    return await handle_compaction_request(req_body, route_key="/v1/responses/compact", stream=False)
```

#### 5.2.7 补强普通 Responses 非流式错误处理

在普通 `/v1/responses` 非流式解析 `json.loads(raw_body)` 前加：

```python
if not raw_body.strip():
    record_error(route_key, 502, "上游返回空响应", detail="")
    return JSONResponse({"error": {"message": "上游返回空响应", "type": "upstream_error", "code": 502}}, status_code=502)
```

`json.JSONDecodeError` 时记录：

```python
record_error(route_key, 502, "上游返回了非法 JSON", detail=raw_body[:500])
```

#### 5.2.8 补强流式超时错误

普通 Responses SSE 流中，如果 keepalive 检测到超时，不要静默 `break`，先发送：

```python
err_evt = f"event: error\ndata: {json.dumps({'type': 'error', 'message': '上游流式响应超时或断开，未收到 response.completed'})}\n\n"
yield err_evt.encode("utf-8")
```

直接转发流 `_forward_request()` 中同理发送 error event。

---

## 6. 验证步骤

### 6.1 语法检查

```bash
cd /home/ubuntu/mimo2api
python3 -m py_compile mimo2api/web_service.py mimo2api/responses_converter.py
```

无输出即通过。

### 6.2 单元级转换验证

```bash
cd /home/ubuntu/mimo2api
python3 - <<'PY'
from mimo2api.responses_converter import convert_request

req = {
    "model": "mimo-v2.5-pro",
    "input": [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}]},
        {"type": "compaction", "encrypted_content": "prior summary"},
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "next"}]},
        {"type": "compaction_trigger"},
    ],
    "stream": True,
    "reasoning": {"effort": "high"},
}

out = convert_request(req)
print(out)
assert out["stream"] is True
assert "reasoning" not in out
assert all("reasoning_content" not in m for m in out["messages"])
assert any(m["role"] == "system" and "prior summary" in m["content"] for m in out["messages"])
PY
```

### 6.3 compact helper 验证

```bash
cd /home/ubuntu/mimo2api
python3 - <<'PY'
from mimo2api.web_service import _build_compaction_chat_request, _is_compaction_trigger_request

req = {
    "model": "mimo-v2.5-pro",
    "input": [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}]},
        {"type": "compaction_trigger"},
    ],
    "stream": True,
    "tools": [{"type": "function", "name": "x"}],
}

assert _is_compaction_trigger_request(req)
out = _build_compaction_chat_request(req)
print(out)
assert out["stream"] is False
assert "tools" not in out
assert out["messages"][0]["role"] == "system"
PY
```

### 6.4 HTTP 验证：`/v1/responses/compact`

服务启动后执行：

```bash
curl -sS http://127.0.0.1:8000/v1/responses/compact \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -d '{
    "model":"mimo-v2.5-pro",
    "input":[
      {"type":"message","role":"user","content":[{"type":"input_text","text":"hello"}]}
    ]
  }' | python3 -m json.tool
```

期望至少包含：

```json
{
  "output": [
    {
      "type": "compaction",
      "encrypted_content": "..."
    }
  ]
}
```

### 6.5 HTTP 验证：`/v1/responses` compact v2 stream

```bash
curl -N http://127.0.0.1:8000/v1/responses \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -d '{
    "model":"mimo-v2.5-pro",
    "stream":true,
    "input":[
      {"type":"message","role":"user","content":[{"type":"input_text","text":"hello"}]},
      {"type":"compaction_trigger"}
    ]
  }'
```

期望输出中依次出现：

```text
event: response.created
```

```text
event: response.output_item.done
```

且 `response.output_item.done` 的 data 中有：

```json
"item":{"type":"compaction","encrypted_content":"..."}
```

最后必须出现：

```text
event: response.completed
```

如果没有 `response.completed`，Codex 仍会认为 stream 未完成。

---

## 7. 部署重启

### Docker Compose 部署

```bash
cd /home/ubuntu/mimo2api
docker compose up -d --build
```

查看状态：

```bash
docker compose ps
docker compose logs -f --tail=200
```

### 裸进程部署

如果是直接运行：

```bash
cd /home/ubuntu/mimo2api
pkill -f 'python.*main.py' || true
nohup python3 main.py > logs/gateway.log 2>&1 &
```

或按实际 systemd/supervisor 配置重启。

---

## 8. 回滚方案

如果目标服务器部署后出现异常，可回滚备份：

```bash
cd /home/ubuntu/mimo2api
cp backup_compaction_fix_YYYYMMDD_HHMMSS/web_service.py.bak mimo2api/web_service.py
cp backup_compaction_fix_YYYYMMDD_HHMMSS/responses_converter.py.bak mimo2api/responses_converter.py
python3 -m py_compile mimo2api/web_service.py mimo2api/responses_converter.py
docker compose up -d --build
```

将 `backup_compaction_fix_YYYYMMDD_HHMMSS` 替换为实际备份目录。

---

## 9. 排查要点

### 9.1 仍然报 `expected value at line 1 column 1`

优先检查：

1. `/v1/responses/compact` 是否返回空 body；
2. 目标服务是否真的加载了新代码；
3. Docker 是否忘记 `--build`；
4. 是否有反向代理吞掉 SSE 或返回 HTML 错误页；
5. `/api/errors` 中是否记录 `上游返回空响应` 或 `上游返回了非法 JSON`。

### 9.2 仍然报 `stream disconnected before completion`

优先检查：

1. SSE 是否输出了 `response.completed`；
2. `response.output_item.done` 的 `item.type` 是否为 `compaction`；
3. 是否进入了普通 `ResponsesStreamConverter` 而不是 compact handler；
4. 请求 `input` 中是否确实有 `{"type":"compaction_trigger"}`。

### 9.3 compact 后历史丢失

检查 `responses_converter.py` 是否包含：

- `RespCompactionItem`
- `_extract_compaction_content()`
- `_parse_response_input_item()` 对 `compaction` / `compaction_summary` / `context_compaction` 的处理
- `convert_request()` 中将 compact 内容转为 system message 的逻辑

---

## 10. 本次修复的核心结论

关键点不是让底层 MIMO 模型原生支持 OpenAI/Codex compaction item，而是在 gateway 层完成协议适配：

1. 上游模型只负责生成摘要文本；
2. gateway 把摘要文本包装成 Codex 需要的 `compaction` item；
3. gateway 对 compact v2 stream 合成完整 Responses SSE；
4. gateway 在后续普通对话中重新注入 compact 历史。

这样可以在不修改上游模型能力的情况下，兼容 Codex remote compaction。

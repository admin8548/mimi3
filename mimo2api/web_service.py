import asyncio
import base64
import binascii
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, TextIO
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Response
from fastapi.responses import StreamingResponse, JSONResponse
import uvicorn
import os
from pathlib import Path

try:
    import fcntl
except ImportError:
    fcntl = None

try:
    import msvcrt
except ImportError:
    msvcrt = None

MODEL_MAPPING_FILE = Path(os.getenv("MIMO_MODEL_MAPPING_PATH", Path(__file__).parent.parent / "model_mapping.json"))

# 引入 Manager 长驻协程任务
from .manager import start_manager_tasks, trigger_rebuild

# Responses API 转换器
from .responses_converter import convert_request as responses_convert_request
from .responses_converter import convert_response as responses_convert_response
from .responses_converter import ResponsesStreamConverter, RespCompactionItem
from .audio_helpers import (
    AudioSpeechRequest,
    audio_media_type,
    extract_audio_payload,
    map_openai_tts_model,
    map_openai_tts_voice,
)
from .auth import (
    get_webui_username,
    is_ai_auth_enabled,
    is_web_auth_enabled,
    require_ai_request,
    require_webui_request,
)
from .config import get_env_float, get_env_int
from .openclaw_protocol import build_protocol_catalog
from .metrics_store import (
    METRICS_BUCKET_SECONDS,
    METRICS_RETENTION_DAYS,
    build_gateway_stats,
    extract_usage_from_sse_chunk,
    init_metrics_db,
    load_status_history,
    metrics_history_worker,
    node_label,
    reclassify_history,
    record_attempt_finished,
    record_attempt_started,
    record_request_finished,
    record_request_started,
)

# 配置基础日志
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def _json_error(message: str, status_code: int, error_type: str = "server_error") -> JSONResponse:
    normalized = message if isinstance(message, str) and message else f"Gateway Error: HTTP {status_code}"
    return JSONResponse(
        content={
            "error": {
                "message": normalized,
                "type": error_type,
                "code": status_code,
            }
        },
        status_code=status_code,
    )

manager_bg_task = None
metrics_persist_task = None
sweeper_bg_task = None
legacy_sweeper_task = None
single_process_lock_file = None
STALE_QUEUE_TTL = 300
SHUTDOWN_TASK_TIMEOUT = get_env_float("MIMO_SHUTDOWN_TASK_TIMEOUT", 5.0, min_value=1.0)

def sweep_stale_queues_once(now: float | None = None) -> int:
    now = time.time() if now is None else now
    stale_count = 0
    for req_id, last_activity_at in list(state.req_id_timestamps.items()):
        if now - last_activity_at > STALE_QUEUE_TTL:
            logger.error(f"💀 发现长时间无活动的悬挂队列，强制回收: [{req_id[:8]}]")
            cleanup_pending_request(req_id)
            stale_count += 1
    if stale_count > 0:
        logger.info(f"🧹 垃圾回收周期结束，共清理了 {stale_count} 个泄露队列。当前活跃队列数: {len(state.pending_queues)}")
    return stale_count

async def sweep_stale_queues():
    """后台巡检任务，清理长时间无活动的悬挂请求队列。"""
    while True:
        try:
            await asyncio.sleep(60)
            sweep_stale_queues_once()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"清理死锁队列任务发生异常: {e}")


async def sweep_legacy_uidless_clients():
    """Strict-mode cleanup: once uid-bearing nodes exist, retire legacy uid=<none> sockets."""
    while True:
        try:
            await asyncio.sleep(30)
            if ALLOW_LEGACY_WS_FALLBACK:
                continue
            if not state.active_clients:
                continue
            uid_clients = [client for client in state.active_clients if state.client_uids.get(id(client))]
            if not uid_clients:
                continue
            legacy_clients = [client for client in list(state.active_clients) if not state.client_uids.get(id(client))]
            if not legacy_clients:
                continue
            for client in legacy_clients:
                await retire_client(client, "legacy uid=<none> connection retired; uid pool available")
            logger.info(f"🧹 已清退 {len(legacy_clients)} 个 legacy uid=<none> 连接，当前仅保留 uid 节点")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"清理 legacy 节点任务发生异常: {e}")


async def close_active_clients() -> None:
    clients = list(state.active_clients)
    if not clients:
        return

    logger.info(f"🛑 正在关闭 {len(clients)} 个内网节点连接...")
    for client in clients:
        try:
            await client.close()
        except Exception as exc:
            logger.debug(f"关闭内网节点连接失败: {exc}")


async def cancel_and_wait_tasks(tasks: list[asyncio.Task | None], *, label: str) -> None:
    pending = [task for task in tasks if task is not None and not task.done()]
    if not pending:
        return

    for task in pending:
        task.cancel()

    try:
        await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=SHUTDOWN_TASK_TIMEOUT)
    except asyncio.TimeoutError:
        still_running = [task for task in pending if not task.done()]
        logger.warning(
            f"⚠️ 关闭 {label} 超时，{len(still_running)} 个任务在 {SHUTDOWN_TASK_TIMEOUT}s 内未退出"
        )

@asynccontextmanager
async def lifespan(app: FastAPI):
    global manager_bg_task, metrics_persist_task, sweeper_bg_task, legacy_sweeper_task
    logger.info("🚀 正在拉起挂后台的 Claw 账号守护线程...")
    acquire_single_process_lock()
    log_key_routes()

    await asyncio.to_thread(init_metrics_db)
    fixed = await asyncio.to_thread(reclassify_history)
    if fixed:
        logger.info(f"🔧 重新分类了 {fixed} 条历史状态记录")
        
    manager_bg_task = asyncio.create_task(start_manager_tasks(), name="mimo-manager")
    metrics_persist_task = asyncio.create_task(metrics_history_worker(), name="mimo-metrics")
    sweeper_bg_task = asyncio.create_task(sweep_stale_queues(), name="mimo-sweeper") # 启动巡检死神
    legacy_sweeper_task = asyncio.create_task(sweep_legacy_uidless_clients(), name="mimo-legacy-sweeper")
    
    yield

    try:
        await close_active_clients()
        await cancel_and_wait_tasks(
            [manager_bg_task, metrics_persist_task, sweeper_bg_task, legacy_sweeper_task],
            label="核心后台任务",
        )
        await cancel_and_wait_tasks(list(_background_tasks), label="转发清理任务")
    finally:
        manager_bg_task = None
        metrics_persist_task = None
        sweeper_bg_task = None
        legacy_sweeper_task = None
        release_single_process_lock()

app = FastAPI(lifespan=lifespan)

# 全局状态从 gateway_state 引入
from .gateway_state import state

# 注入前面拆分出的 WebUI 独立路由
from .ui_router import router as ui_router
app.include_router(ui_router)

RETRYABLE_STATUS_CODES = {401, 403, 429}
NODE_RESPONSE_TIMEOUT = 30
MAX_RETRIES = 3
MAX_PENDING_QUEUES = 2000
AI_ROUTE_PREFIXES = ("/v1/", "/anthropic/v1/")
WEBUI_PUBLIC_PATHS = {
    "/",
    "/api/auth/session",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/stats",
    "/api/diagnostics/routes",
    "/api/openclaw/protocol",
    "/api/status/history",
    "/webui",
}
PREFERRED_UID = os.getenv("MIMO_PREFERRED_UID", "").strip()
ALLOW_LEGACY_WS_FALLBACK = os.getenv("MIMO_ALLOW_LEGACY_WS_FALLBACK", "false").strip().lower() in {"1", "true", "yes", "on"}
KEY_DIAGNOSTIC_ROUTES = {
    "/v1/chat/completions",
    "/v1/responses",
    "/v1/responses/compact",
    "/v1/responses/compact/",
    "/anthropic/v1/messages",
    "/v1/models",
}

if is_ai_auth_enabled():
    logger.info("🔐 AI API 鉴权已启用")
if is_web_auth_enabled():
    logger.info(f"🔐 WebUI 鉴权已启用，登录用户: {get_webui_username()}")


def is_ai_route(path: str) -> bool:
    return path.startswith(AI_ROUTE_PREFIXES)


def is_webui_route(path: str) -> bool:
    return path.startswith("/api/") and path not in WEBUI_PUBLIC_PATHS


def build_route_diagnostics() -> dict[str, Any]:
    routes: list[dict[str, Any]] = []
    present: dict[str, list[str]] = {}
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = sorted(getattr(route, "methods", []) or [])
        name = getattr(route, "name", "")
        if not path:
            continue
        routes.append({"path": path, "methods": methods, "name": name})
        present.setdefault(path, [])
        present[path] = sorted(set(present[path]) | set(methods))
    return {
        "generated_at": int(time.time()),
        "key_routes": {
            path: {"present": path in present, "methods": present.get(path, [])}
            for path in sorted(KEY_DIAGNOSTIC_ROUTES)
        },
        "routes": sorted(routes, key=lambda item: (item["path"], item["methods"])),
    }


def log_key_routes() -> None:
    diagnostics = build_route_diagnostics()
    summary = "; ".join(
        f"{path}={','.join(info['methods']) if info['present'] else 'MISSING'}"
        for path, info in diagnostics["key_routes"].items()
    )
    logger.info(f"🧭 关键路由诊断: {summary}")


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path

    if is_ai_route(path):
        auth_error = require_ai_request(request)
        if auth_error is not None:
            return auth_error

    if is_webui_route(path):
        auth_error = require_webui_request(request)
        if auth_error is not None:
            return auth_error

    return await call_next(request)


def diagnose_request(body_text: str) -> str:
    """从请求体中提取关键诊断信息，用于 400 错误追踪"""
    try:
        req = json.loads(body_text)
    except Exception:
        return "body=非法JSON"
    msgs = req.get("messages", [])
    model = req.get("model", "未指定")
    stream = req.get("stream", False)
    total_chars = sum(len(str(m.get("content", ""))) for m in msgs)
    est_tokens = total_chars // 3
    tools = req.get("tools", [])
    return (
        f"model={model}, stream={stream}, msgs={len(msgs)}, "
        f"est_tokens≈{est_tokens}, chars={total_chars}, tools={len(tools)}"
    )


def record_error(route: str, status_code: int, reason: str, model: str = "", detail: str = "", request_body: str = ""):
    """记录错误到环形缓冲区，可通过 /api/errors 查询"""
    state.recent_errors.append({
        "ts": int(time.time()),
        "route": route,
        "status": status_code,
        "reason": reason[:200],
        "model": model,
        "detail": detail[:500],
        "request": request_body[:2000] if request_body else "",
    })


def no_available_nodes_error() -> JSONResponse:
    """避免把“全部节点因上游 401 冷却”误报成单纯无节点。"""
    if not state.active_clients:
        return _json_error("没有可用的内网节点", 503)

    now = time.time()
    cooldown_count = sum(1 for c in state.active_clients if state.client_cooldowns.get(id(c), 0) > now)
    if cooldown_count >= len(state.active_clients):
        recent_401 = None
        for err in reversed(state.recent_errors):
            if now - int(err.get("ts", 0)) > max(300, NODE_401_COOLDOWN_SECONDS + 60):
                break
            if int(err.get("status", 0)) == 401:
                recent_401 = err
                break
        if recent_401:
            detail = recent_401.get("detail") or recent_401.get("reason") or ""
            if "Invalid API Key" in detail or "invalid_key" in detail:
                return _json_error(
                    "内网节点在线，但上游返回 Invalid API Key；请刷新/修复远端节点环境变量 MIMO_API_KEY 后重建节点",
                    401,
                    "invalid_request_error",
                )
            return _json_error(
                "内网节点在线，但节点到上游鉴权失败，节点已短暂冷却；请检查远端 MIMO_API_KEY / MIMO_API_ENDPOINT",
                401,
                "invalid_request_error",
            )

    return _json_error("没有可用的内网节点", 503)


def _is_compaction_trigger_request(req_body: dict[str, Any]) -> bool:
    input_data = req_body.get("input")
    if not isinstance(input_data, list):
        return False
    return any(isinstance(item, dict) and item.get("type") == "compaction_trigger" for item in input_data)


def _extract_text_from_chat_message(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") in {"text", "output_text", "input_text"}:
                text = part.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
        if parts:
            return "\n".join(parts)
    reasoning_content = message.get("reasoning_content")
    if isinstance(reasoning_content, str) and reasoning_content:
        return reasoning_content
    return ""


def _extract_response_text(chat_resp: dict[str, Any]) -> str:
    choice = (chat_resp.get("choices") or [{}])[0]
    message = choice.get("message", {}) if isinstance(choice, dict) else {}
    text = _extract_text_from_chat_message(message) if isinstance(message, dict) else ""
    if text.strip():
        return text.strip()
    if isinstance(message, dict):
        refusal = message.get("refusal")
        if isinstance(refusal, str) and refusal.strip():
            return refusal.strip()
    return "(no summary returned)"


def _build_compaction_chat_request(req_body: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(req_body)
    input_data = cleaned.get("input")
    if isinstance(input_data, list):
        cleaned["input"] = [item for item in input_data if not (isinstance(item, dict) and item.get("type") == "compaction_trigger")]
    chat_req = responses_convert_request(cleaned)
    chat_req["stream"] = False
    chat_req.pop("tools", None)
    chat_req.pop("tool_choice", None)
    chat_req.pop("response_format", None)
    chat_req.pop("parallel_tool_calls", None)
    chat_req.pop("temperature", None)
    chat_req.pop("top_p", None)
    chat_req["messages"] = [
        {
            "role": "system",
            "content": (
                "Summarize the conversation compactly for future continuation. "
                "Preserve important facts, decisions, constraints, names, identifiers, and unresolved tasks."
            ),
        },
        *chat_req.get("messages", []),
    ]
    return chat_req


def _build_compaction_output(summary_text: str) -> dict[str, Any]:
    item = RespCompactionItem(type="compaction", encrypted_content=summary_text)
    return {"output": [item.model_dump(exclude_none=True)]}


def _compact_sse_event(event_type: str, payload: dict[str, Any]) -> str:
    payload = dict(payload)
    payload["type"] = event_type
    return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

STREAM_CHUNK_TIMEOUT = 60
STREAM_KEEPALIVE_INTERVAL = 25  # 秒，需小于 Cloudflare 超时 (~100s)
QUEUE_DRAIN_TIMEOUT = 5
DEFAULT_GATEWAY_ERROR = "Gateway Error: 所有节点请求失败"
NODE_401_COOLDOWN_SECONDS = get_env_int("MIMO_NODE_401_COOLDOWN_SECONDS", 60, min_value=0)
LEGACY_REJECT_HOLD_SECONDS = get_env_int("MIMO_LEGACY_REJECT_HOLD_SECONDS", 30, min_value=0)
LEGACY_REJECT_LOG_INTERVAL_SECONDS = get_env_int("MIMO_LEGACY_REJECT_LOG_INTERVAL_SECONDS", 30, min_value=1)
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROCESS_LOCK_PATH = os.getenv("MIMO_PROCESS_LOCK_PATH", os.path.join(ROOT_DIR, "mimo2api.lock"))

# 后台 fire-and-forget 任务集合
_background_tasks: set[asyncio.Task] = set()
PROCESS_LOCK_SIZE = 1
_legacy_reject_last_log_at = 0.0
_legacy_reject_suppressed = 0

def _track_task(task: asyncio.Task) -> None:
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def should_log_legacy_reject(now: float | None = None) -> tuple[bool, int]:
    """Rate-limit strict-mode legacy bridge rejection logs.

    Old injected bridge.py copies may reconnect every few seconds without a uid.
    Strict mode must still reject them, but logging every reconnect can drown out
    actionable gateway errors.  Return whether to log and how many rejections
    were suppressed since the previous log line.
    """
    global _legacy_reject_last_log_at, _legacy_reject_suppressed
    now = time.time() if now is None else now
    if _legacy_reject_last_log_at == 0 or now - _legacy_reject_last_log_at >= LEGACY_REJECT_LOG_INTERVAL_SECONDS:
        suppressed = _legacy_reject_suppressed
        _legacy_reject_suppressed = 0
        _legacy_reject_last_log_at = now
        return True, suppressed
    _legacy_reject_suppressed += 1
    return False, 0


async def reject_legacy_ws(ws: WebSocket, client_addr: str) -> None:
    """Reject uid-less bridges in strict mode without causing tight reconnect storms."""
    should_log, suppressed = should_log_legacy_reject()
    if should_log:
        suffix = f"，上个窗口已抑制 {suppressed} 条同类日志" if suppressed else ""
        logger.warning(
            f"🚫 拒绝 legacy uid=<none> 连接: addr={client_addr}，因 uid 节点已存在{suffix}；"
            f"hold={LEGACY_REJECT_HOLD_SECONDS}s"
        )
    if LEGACY_REJECT_HOLD_SECONDS > 0:
        try:
            await asyncio.sleep(LEGACY_REJECT_HOLD_SECONDS)
        except asyncio.CancelledError:
            raise
    try:
        await ws.close(code=1008, reason="uid required while uid pool is active")
    except Exception:
        pass

def _lock_file_nonblocking(lock_file: TextIO) -> None:
    if os.name == "nt":
        if msvcrt is None:
            raise OSError("当前平台缺少 msvcrt，无法加锁。")
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, PROCESS_LOCK_SIZE)
        return

    if fcntl is None:
        raise OSError("当前平台缺少 fcntl，无法加锁。")
    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

def _unlock_file(lock_file: TextIO) -> None:
    if os.name == "nt":
        if msvcrt is None:
            raise OSError("当前平台缺少 msvcrt，无法解锁。")
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, PROCESS_LOCK_SIZE)
        return

    if fcntl is None:
        raise OSError("当前平台缺少 fcntl，无法解锁。")
    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

def acquire_single_process_lock() -> None:
    global single_process_lock_file
    if single_process_lock_file is not None:
        return

    try:
        lock_path = Path(PROCESS_LOCK_PATH)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.touch(exist_ok=True)
        lock_file = lock_path.open("r+", encoding="utf-8")
        if lock_path.stat().st_size < PROCESS_LOCK_SIZE:
            lock_file.write("\n")
            lock_file.flush()
        _lock_file_nonblocking(lock_file)
    except (BlockingIOError, OSError) as exc:
        if 'lock_file' in locals():
            lock_file.close()
        raise RuntimeError("当前进程锁被占用。") from exc

    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    single_process_lock_file = lock_file

def release_single_process_lock() -> None:
    global single_process_lock_file
    if single_process_lock_file is None:
        return
    try:
        _unlock_file(single_process_lock_file)
    finally:
        single_process_lock_file.close()
        single_process_lock_file = None

@dataclass(slots=True)
class RetryState:
    status_code: int = 502
    response_text: str = DEFAULT_GATEWAY_ERROR

@dataclass(slots=True)
class ForwardAttempt:
    req_id: str
    queue: asyncio.Queue
    target_ws: WebSocket
    first_msg: dict[str, Any]
    attempt_number: int

@app.post("/api/rebuild")
async def api_rebuild():
    trigger_rebuild()
    return JSONResponse(content={"ok": True, "message": "重建信号已发送，所有节点将在当前循环结束后立即重建"})

@app.get("/api/stats")
async def api_stats():
    return JSONResponse(content=build_gateway_stats(len(_background_tasks)))

@app.get("/api/diagnostics/routes")
async def api_diagnostics_routes():
    return JSONResponse(content=build_route_diagnostics())

@app.get("/api/status/history")
async def api_status_history(hours: int = 24):
    hours = max(1, min(hours, 24 * METRICS_RETENTION_DAYS))
    return JSONResponse(content=await asyncio.to_thread(load_status_history, hours))

@app.get("/api/errors")
async def api_errors(limit: int = 50):
    limit = max(1, min(limit, 200))
    errors = list(state.recent_errors)[-limit:]
    errors.reverse()  # 最新的在前
    return JSONResponse(content={"count": len(errors), "errors": errors})

@app.get("/api/agent-runs")
async def api_agent_runs(limit: int = 50):
    limit = max(1, min(limit, 200))
    runs = list(state.recent_agent_runs)[-limit:]
    runs.reverse()
    return JSONResponse(content={"count": len(runs), "runs": runs})

@app.get("/api/openclaw/protocol")
async def api_openclaw_protocol():
    """Return the local OpenClaw protocol catalog and field dictionary."""
    return JSONResponse(content=build_protocol_catalog())

@app.get("/api/openclaw/features")
async def api_openclaw_features():
    """Return sanitized hello-ok features observed from active manager sessions."""
    features = dict(state.openclaw_features_by_uid)
    return JSONResponse(content={"count": len(features), "by_uid": features})

@app.get("/api/openclaw/events")
async def api_openclaw_events(limit: int = 100):
    """Return recent sanitized OpenClaw event summaries for protocol research."""
    limit = max(1, min(limit, 1000))
    events = list(state.recent_openclaw_events)[-limit:]
    events.reverse()
    return JSONResponse(content={"count": len(events), "events": events})

def normalize_model_mapping(raw_mapping: Any) -> dict[str, str] | None:
    if not isinstance(raw_mapping, dict):
        return None
    normalized: dict[str, str] = {}
    for source_model, target_model in raw_mapping.items():
        if not isinstance(source_model, str) or not isinstance(target_model, str):
            return None
        source_model = source_model.strip()
        target_model = target_model.strip()
        if not source_model or not target_model:
            return None
        normalized[source_model] = target_model
    return normalized


def load_model_mapping() -> dict[str, str]:
    if not MODEL_MAPPING_FILE.exists():
        return {}
    try:
        loaded = json.loads(MODEL_MAPPING_FILE.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    normalized = normalize_model_mapping(loaded)
    return normalized or {}

def save_model_mapping(mapping: dict[str, str]) -> None:
    MODEL_MAPPING_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = MODEL_MAPPING_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), "utf-8")
    tmp.rename(MODEL_MAPPING_FILE)

def apply_model_mapping(body_text: str) -> str:
    mapping = load_model_mapping()
    if not mapping:
        return body_text
    try:
        data = json.loads(body_text)
    except (json.JSONDecodeError, AttributeError):
        return body_text
    original_model = data.get("model")
    if original_model and original_model in mapping:
        data["model"] = mapping[original_model]
        logger.info(f"🔀 模型映射: {original_model} → {data['model']}")
        return json.dumps(data, ensure_ascii=False)
    return body_text

@app.get("/api/model_mapping")
async def api_get_model_mapping():
    return JSONResponse(content=load_model_mapping())

@app.put("/api/model_mapping")
async def api_put_model_mapping(request: Request):
    body = await request.body()
    try:
        new_mapping = json.loads(body.decode("utf-8", "ignore").lstrip("\ufeff"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"error": "请求体不是合法 JSON"}, status_code=400)
    normalized_mapping = normalize_model_mapping(new_mapping)
    if normalized_mapping is None:
        return JSONResponse({"error": "映射必须是非空字符串到非空字符串的 JSON 对象"}, status_code=400)
    save_model_mapping(normalized_mapping)
    return JSONResponse(content=normalized_mapping)

@app.delete("/api/model_mapping/{model_name:path}")
async def api_delete_model_mapping(model_name: str):
    mapping = load_model_mapping()
    if model_name in mapping:
        del mapping[model_name]
        save_model_mapping(mapping)
        return JSONResponse({"ok": True, "deleted": model_name})
    return JSONResponse({"error": f"模型 {model_name} 不在映射中"}, status_code=404)

@app.websocket("/ws")
async def ws_tunnel(ws: WebSocket):
    await ws.accept()
    client_addr = f"{ws.client.host}:{ws.client.port}" if ws.client else "Unknown"
    uid = (ws.query_params.get("uid") or "").strip()
    if not uid and not ALLOW_LEGACY_WS_FALLBACK and any(state.client_uids.values()):
        await reject_legacy_ws(ws, client_addr)
        return
    state.active_clients.append(ws)
    state.client_connected_at[id(ws)] = time.time()
    if uid:
        state.client_uids[id(ws)] = uid
    state.client_cooldowns.pop(id(ws), None)
    state.client_cooldown_reasons.pop(id(ws), None)
    uid_part = f" uid={uid}" if uid else " uid=<none>"
    logger.info(f"✅ 内网节点已接入:{uid_part} addr={client_addr}。当前在线节点数: {len(state.active_clients)}")
    
    try:
        while True:
            msg = await ws.receive_text()
            data = json.loads(msg)
            req_id = data.get("req_id")
            if req_id and req_id in state.pending_queues:
                touch_pending_request(req_id)
                state.pending_queues[req_id].put_nowait(data)
    except WebSocketDisconnect:
        logger.warning(f"❌ 内网节点主动断开: {client_addr}")
    except Exception as e:
        logger.error(f"❌ 内网节点异常断开: {client_addr}, 错误: {e}")
    finally:
        if ws in state.active_clients:
            state.active_clients.remove(ws)
        state.client_uids.pop(id(ws), None)
        state.client_connected_at.pop(id(ws), None)
        state.client_cooldowns.pop(id(ws), None)
        state.client_cooldown_reasons.pop(id(ws), None)
        
        # 清理该节点的所有孤儿队列
        orphan_ids = state.ws_to_req_ids.pop(id(ws), set())
        for orphan_id in orphan_ids:
            q = state.pending_queues.pop(orphan_id, None)
            state.req_id_to_ws_id.pop(orphan_id, None)
            state.req_id_timestamps.pop(orphan_id, None)
            if q is not None:
                try:
                    q.put_nowait({"type": "error", "body": "节点断开连接"})
                except asyncio.QueueFull:
                    pass
        if orphan_ids:
            logger.warning(f"🧹 节点断开，已清理 {len(orphan_ids)} 个孤儿请求队列")
            
        if state.current_client_index >= len(state.active_clients):
            state.current_client_index = 0
        logger.info(f"当前在线节点数: {len(state.active_clients)}")


def get_available_dispatch_clients(preferred_uid: str | None = None) -> list[WebSocket]:
    now = time.time()
    available_clients: list[WebSocket] = []
    for client in state.active_clients:
        if state.client_cooldowns.get(id(client), 0) <= now:
            available_clients.append(client)
    if not available_clients:
        return []

    preferred_uid = (preferred_uid or "").strip()
    if preferred_uid:
        preferred_clients = [
            client for client in available_clients
            if state.client_uids.get(id(client)) == preferred_uid
        ]
        if preferred_clients:
            return preferred_clients

    uid_clients = [client for client in available_clients if state.client_uids.get(id(client))]
    if uid_clients:
        return uid_clients

    if ALLOW_LEGACY_WS_FALLBACK:
        return available_clients

    return []


def get_next_client(preferred_uid: str | None = None) -> WebSocket | None:
    dispatch_clients = get_available_dispatch_clients(preferred_uid)
    if not dispatch_clients:
        return None

    if state.current_client_index >= len(dispatch_clients):
        state.current_client_index = 0
    client = dispatch_clients[state.current_client_index]
    state.current_client_index = (state.current_client_index + 1) % len(dispatch_clients)
    return client


def get_available_client_count() -> int:
    return len(get_available_dispatch_clients(PREFERRED_UID))


def touch_pending_request(req_id: str) -> None:
    if req_id in state.pending_queues:
        state.req_id_timestamps[req_id] = time.time()


def create_pending_request() -> tuple[str, asyncio.Queue]:
    if len(state.pending_queues) >= MAX_PENDING_QUEUES:
        raise RuntimeError("pending queue 已满")
    req_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    state.pending_queues[req_id] = queue
    state.req_id_timestamps[req_id] = time.time()
    return req_id, queue


def cleanup_pending_request(req_id: str) -> None:
    state.pending_queues.pop(req_id, None)
    state.req_id_timestamps.pop(req_id, None)
    ws_id = state.req_id_to_ws_id.pop(req_id, None)
    if ws_id is not None:
        req_ids = state.ws_to_req_ids.get(ws_id)
        if req_ids is not None:
            req_ids.discard(req_id)
            if not req_ids:
                state.ws_to_req_ids.pop(ws_id, None)


def cooldown_client(ws: WebSocket, seconds: int, reason: str) -> None:
    cooldown_until = time.time() + max(seconds, 0)
    state.client_cooldowns[id(ws)] = cooldown_until
    state.client_cooldown_reasons[id(ws)] = reason
    logger.warning(
        f"⛔ 节点 {node_label(ws)} 因 {reason} 进入冷却 {seconds}s，"
        f"冷却结束时间戳: {int(cooldown_until)}"
    )


async def retire_client(ws: WebSocket, reason: str) -> None:
    """Remove a definitely bad bridge connection from the pool."""
    label = node_label(ws)
    if ws in state.active_clients:
        state.active_clients.remove(ws)
    state.client_uids.pop(id(ws), None)
    state.client_connected_at.pop(id(ws), None)
    state.client_cooldowns.pop(id(ws), None)
    state.client_cooldown_reasons.pop(id(ws), None)
    for req_id in list(state.ws_to_req_ids.get(id(ws), set())):
        q = state.pending_queues.get(req_id)
        if q:
            try:
                q.put_nowait({"type": "error", "body": reason})
            except asyncio.QueueFull:
                pass
        cleanup_pending_request(req_id)
    try:
        await ws.close()
    except Exception:
        pass
    logger.warning(f"🧹 已移除失效节点 {label}: {reason}。当前在线节点数: {len(state.active_clients)}")

async def drain_and_close(req_id: str, queue: asyncio.Queue) -> None:
    try:
        while True:
            msg = await asyncio.wait_for(queue.get(), timeout=QUEUE_DRAIN_TIMEOUT)
            if msg.get("type") in ["finish", "error"]:
                break
    except Exception:
        pass
    finally:
        cleanup_pending_request(req_id)

def should_retry_status(status_code: int) -> bool:
    return status_code in RETRYABLE_STATUS_CODES or status_code >= 500

def build_ws_payload(req_id: str, method: str, path: str, body: str) -> str:
    return json.dumps({"req_id": req_id, "method": method, "path": path, "body": body})

async def dispatch_to_node(*, method: str, path: str, body: str, log_label: str, attempt_number: int) -> ForwardAttempt | None:
    try:
        req_id, queue = create_pending_request()
    except RuntimeError:
        logger.warning("⚠️ pending queue 已满，拒绝新请求")
        return None
        
    target_ws = get_next_client(PREFERRED_UID)
    if not target_ws:
        cleanup_pending_request(req_id)
        return None

    # 🌟 修复内存泄漏的双向绑定：既知道 WS 管哪些 req_id，也知道 req_id 归属于哪个 WS
    state.req_id_to_ws_id[req_id] = id(target_ws)
    state.ws_to_req_ids.setdefault(id(target_ws), set()).add(req_id)

    ws_payload = build_ws_payload(req_id, method, path, body)
    attempt_started_at = time.monotonic()
    record_attempt_started(target_ws)

    try:
        await target_ws.send_text(ws_payload)
        target_uid = state.client_uids.get(id(target_ws), "")
        uid_part = f", uid={target_uid}" if target_uid else ""
        logger.debug(f"👉 {log_label} [{req_id[:8]}] ({method} {path}) -> 节点: {node_label(target_ws)}{uid_part} (尝试 {attempt_number})")
    except RuntimeError:
        record_attempt_finished(target_ws=target_ws, status_code=0, first_byte_latency_ms=(time.monotonic() - attempt_started_at) * 1000, success=False)
        logger.warning(f"⚠️ {log_label} 转发失败，节点状态异常，尝试切换...")
        cleanup_pending_request(req_id) # 内部会自动解绑 target_ws
        if target_ws in state.active_clients:
            state.active_clients.remove(target_ws)
        state.client_uids.pop(id(target_ws), None)
        state.client_connected_at.pop(id(target_ws), None)
        state.client_cooldowns.pop(id(target_ws), None)
        state.client_cooldown_reasons.pop(id(target_ws), None)
        return None

    try:
        first_msg = await asyncio.wait_for(queue.get(), timeout=NODE_RESPONSE_TIMEOUT)
    except asyncio.TimeoutError:
        record_attempt_finished(target_ws=target_ws, status_code=504, first_byte_latency_ms=(time.monotonic() - attempt_started_at) * 1000, success=False)
        cleanup_pending_request(req_id)
        raise

    record_attempt_finished(
        target_ws=target_ws,
        status_code=int(first_msg.get("status", 200)),
        first_byte_latency_ms=(time.monotonic() - attempt_started_at) * 1000,
        success=first_msg.get("type") != "error" and not should_retry_status(int(first_msg.get("status", 200))),
    )
    return ForwardAttempt(req_id=req_id, queue=queue, target_ws=target_ws, first_msg=first_msg, attempt_number=attempt_number)


async def prepare_forward_attempt(*, method: str, path: str, body: str, log_label: str, retry_state: RetryState, attempt_number: int) -> ForwardAttempt | None:
    attempt = await dispatch_to_node(method=method, path=path, body=body, log_label=log_label, attempt_number=attempt_number)
    if attempt is None:
        return None

    first_msg = attempt.first_msg
    if first_msg.get("type") == "error":
        error_text = first_msg.get("body") or "节点返回错误"
        logger.warning(f"⚠️ {log_label} 节点返回内部错误: {error_text}，尝试切换...")
        retry_state.response_text = f"Gateway Error: {error_text}"
        cleanup_pending_request(attempt.req_id)
        return None

    status_code = first_msg.get("status", 200)
    if status_code == 401:
        body_preview_parts: list[str] = []
        try:
            while len("".join(body_preview_parts)) < 512:
                msg = await asyncio.wait_for(attempt.queue.get(), timeout=1.5)
                if msg.get("type") == "chunk":
                    body_preview_parts.append(str(msg.get("body", "")))
                if msg.get("type") in {"finish", "error"}:
                    break
        except asyncio.TimeoutError:
            pass
        finally:
            cleanup_pending_request(attempt.req_id)
        preview = "".join(body_preview_parts).replace("\n", "\\n")[:512]
        if preview:
            logger.warning(f"⚠️ {log_label} 节点 401 响应体摘要: {preview}")
            record_error(path, 401, "节点到上游鉴权失败", detail=preview)
        if "Invalid API Key" in preview or "invalid_key" in preview:
            await retire_client(attempt.target_ws, "上游 Invalid API Key")
            retry_state.status_code = 401
            retry_state.response_text = "Gateway Error: 节点上游 MIMO_API_KEY 无效，已移除该失效节点"
            return None
        cooldown_client(attempt.target_ws, NODE_401_COOLDOWN_SECONDS, "401 Unauthorized")
        retry_state.status_code = 401
        retry_state.response_text = "Gateway Error: 节点鉴权失败 (401)，已临时跳过该节点"
        return None

    if should_retry_status(status_code):
        logger.warning(f"⚠️ {log_label} 节点返回状态码 {status_code}，触发自动重试 (当前 attempt={attempt_number})...")
        retry_state.status_code = status_code
        _track_task(asyncio.create_task(drain_and_close(attempt.req_id, attempt.queue)))
        return None

    return attempt


def normalize_response_headers(headers: dict | None) -> tuple[str, dict]:
    response_headers = dict(headers or {})
    content_type = response_headers.pop("content-type", "application/json")
    for key in ["content-length", "transfer-encoding", "content-encoding", "connection"]:
        response_headers.pop(key, None)
    return content_type, response_headers


async def collect_response_body(current_req_id: str, current_queue: asyncio.Queue, timeout: int = 120) -> str:
    chunks: list[str] = []
    try:
        while True:
            msg = await asyncio.wait_for(current_queue.get(), timeout=timeout)
            if msg.get("type") == "finish":
                break
            if msg.get("type") == "error":
                raise RuntimeError(msg.get("body") or "节点返回错误")
            if msg.get("type") == "chunk":
                chunks.append(msg.get("body", ""))
    finally:
        cleanup_pending_request(current_req_id)
    return "".join(chunks)


async def collect_upstream_chat_response(
    *,
    route_key: str,
    request_started_at: float,
    body_text: str,
    log_label: str,
    retry_state: RetryState,
) -> tuple[dict[str, Any], int, float]:
    max_retries = min(MAX_RETRIES, get_available_client_count())
    for attempt in range(max_retries):
        req_id = "unknown"
        try:
            prepared = await prepare_forward_attempt(
                method="POST",
                path="/v1/chat/completions",
                body=body_text,
                log_label=log_label,
                retry_state=retry_state,
                attempt_number=attempt + 1,
            )
            if prepared is None:
                continue
            req_id = prepared.req_id
            queue = prepared.queue
            first_msg = prepared.first_msg
            status_code = first_msg.get("status", 200)
            first_byte_at = time.monotonic()
            raw_body = await collect_response_body(req_id, queue)

            if status_code >= 400:
                record_error(route_key, status_code, f"上游返回 {status_code}", detail=raw_body[:500])
                record_request_finished(
                    route_key=route_key,
                    status_code=status_code,
                    started_at=request_started_at,
                    first_byte_at=first_byte_at,
                    success=False,
                )
                try:
                    parsed = json.loads(raw_body) if raw_body else None
                except (json.JSONDecodeError, ValueError):
                    parsed = None
                if isinstance(parsed, dict) and "error" in parsed:
                    raise RuntimeError(json.dumps(parsed, ensure_ascii=False))
                raise RuntimeError(raw_body or f"上游返回 {status_code}")

            if not raw_body.strip():
                record_error(route_key, 502, "上游返回空响应", detail="")
                record_request_finished(
                    route_key=route_key,
                    status_code=502,
                    started_at=request_started_at,
                    first_byte_at=first_byte_at,
                    success=False,
                )
                raise RuntimeError("上游返回空响应")

            try:
                chat_resp = json.loads(raw_body)
            except json.JSONDecodeError:
                record_error(route_key, 502, "上游返回了非法 JSON", detail=raw_body[:500])
                record_request_finished(
                    route_key=route_key,
                    status_code=502,
                    started_at=request_started_at,
                    first_byte_at=first_byte_at,
                    success=False,
                )
                raise RuntimeError("上游返回了非法 JSON")

            return chat_resp, status_code, first_byte_at
        except asyncio.TimeoutError:
            retry_state.status_code = 504
            retry_state.response_text = "Gateway Error: 请求内网节点超时"
            cleanup_pending_request(req_id)
            continue
        except RuntimeError as exc:
            cleanup_pending_request(req_id)
            if "上游返回" in str(exc) and str(exc).startswith("{"):
                raise
            if str(exc) in {"上游返回空响应", "上游返回了非法 JSON"}:
                raise
            raise
        except Exception:
            cleanup_pending_request(req_id)
            raise
    raise TimeoutError(retry_state.response_text)


async def handle_compaction_request(req_body: dict[str, Any], *, route_key: str, stream: bool) -> JSONResponse | StreamingResponse:
    if not state.active_clients:
        return no_available_nodes_error()

    if get_available_client_count() == 0:
        return no_available_nodes_error()

    request_started_at = time.monotonic()
    record_request_started(route_key, is_streaming=stream)
    retry_state = RetryState()

    try:
        chat_req = _build_compaction_chat_request(req_body)
    except Exception as exc:
        record_error(route_key, 400, f"compact 请求转换失败: {exc}")
        record_request_finished(route_key=route_key, status_code=400, started_at=request_started_at, first_byte_at=None, success=False)
        return _json_error(f"compact 请求转换失败: {exc}", 400, "invalid_request_error")

    model = chat_req.get("model", "")
    body_text = apply_model_mapping(json.dumps(chat_req, ensure_ascii=False))

    try:
        chat_resp, status_code, first_byte_at = await collect_upstream_chat_response(
            route_key=route_key,
            request_started_at=request_started_at,
            body_text=body_text,
            log_label="Responses Compact 请求",
            retry_state=retry_state,
        )
    except TimeoutError:
        record_request_finished(route_key=route_key, status_code=retry_state.status_code, started_at=request_started_at, first_byte_at=None, success=False)
        return _json_error(retry_state.response_text, retry_state.status_code)
    except RuntimeError as exc:
        message = str(exc)
        status = 502
        try:
            parsed = json.loads(message)
        except (json.JSONDecodeError, ValueError):
            parsed = None
        if isinstance(parsed, dict) and "error" in parsed:
            return JSONResponse(parsed, status_code=status)
        return _json_error(f"compact 上游失败: {message}", status)

    summary_text = _extract_response_text(chat_resp)
    compaction_output = _build_compaction_output(summary_text)
    usage = chat_resp.get("usage")

    if not stream:
        record_request_finished(
            route_key=route_key,
            status_code=status_code,
            started_at=request_started_at,
            first_byte_at=first_byte_at,
            success=True,
            usage=usage,
        )
        return JSONResponse(content=compaction_output, status_code=200)

    resp_id = f"resp_{uuid.uuid4().hex[:24]}"
    created_at = int(time.time())
    item = compaction_output["output"][0]
    response_in_progress = {
        "id": resp_id,
        "object": "response",
        "created_at": created_at,
        "model": model,
        "status": "in_progress",
        "output": [],
    }
    response_completed = {
        "id": resp_id,
        "object": "response",
        "created_at": created_at,
        "model": model,
        "status": "completed",
        "output": [item],
    }
    if usage:
        response_completed["usage"] = {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        }

    async def compaction_stream_generator():
        try:
            yield _compact_sse_event("response.created", {"response": response_in_progress}).encode("utf-8")
            yield _compact_sse_event("response.output_item.done", {"output_index": 0, "item": item}).encode("utf-8")
            yield _compact_sse_event("response.completed", {"response": response_completed}).encode("utf-8")
        finally:
            record_request_finished(
                route_key=route_key,
                status_code=200,
                started_at=request_started_at,
                first_byte_at=first_byte_at,
                success=True,
                usage=usage,
            )

    return StreamingResponse(
        compaction_stream_generator(),
        status_code=200,
        media_type="text/event-stream",
        headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
    )

# -------------- API 路由定义 --------------

@app.post("/v1/audio/speech")
async def audio_speech_handler(payload: AudioSpeechRequest):
    if not state.active_clients:
        return no_available_nodes_error()

    input_text = payload.input.strip()
    if not input_text:
        return JSONResponse({"error": {"message": "`input` 不能为空"}}, status_code=400)

    messages = []
    if isinstance(payload.instructions, str) and payload.instructions.strip():
        messages.append({"role": "user", "content": payload.instructions})
    messages.append({"role": "assistant", "content": input_text})

    mimo_payload = {
        "model": map_openai_tts_model(payload.model),
        "messages": messages,
        "audio": {"format": payload.response_format.lower(), "voice": map_openai_tts_voice(payload.voice)},
    }
    body_text = json.dumps(mimo_payload, ensure_ascii=False)
    
    max_retries = min(MAX_RETRIES, get_available_client_count())
    if max_retries == 0:
        return no_available_nodes_error()

    retry_state = RetryState()
    route_key = "/v1/audio/speech"
    request_started_at = time.monotonic()
    record_request_started(route_key, is_streaming=False)

    for attempt in range(max_retries):
        req_id = "unknown"
        try:
            prepared = await prepare_forward_attempt(method="POST", path="/v1/chat/completions", body=body_text, log_label="TTS 映射请求", retry_state=retry_state, attempt_number=attempt + 1)
            if prepared is None:
                continue
            req_id = prepared.req_id
            queue = prepared.queue
            first_msg = prepared.first_msg
            first_byte_at = time.monotonic()

            raw_body = await collect_response_body(req_id, queue)
            status_code = first_msg.get("status", 200)
            
            if status_code >= 400:
                record_error(route_key, status_code, f"上游返回 {status_code}", detail=raw_body[:500])
                content_type, response_headers = normalize_response_headers(first_msg.get("headers", {}))
                record_request_finished(route_key=route_key, status_code=status_code, started_at=request_started_at, first_byte_at=first_byte_at, success=False)

                try:
                    parsed = json.loads(raw_body) if raw_body else None
                except (json.JSONDecodeError, ValueError):
                    parsed = None

                if isinstance(parsed, dict) and "error" in parsed:
                    return JSONResponse(parsed, status_code=status_code, headers=response_headers)

                error_payload = {"error": {"message": raw_body or f"上游返回 {status_code}", "type": "upstream_error", "code": status_code}}
                return JSONResponse(error_payload, status_code=status_code, headers=response_headers)

            try:
                response_json = json.loads(raw_body)
            except json.JSONDecodeError:
                record_request_finished(route_key=route_key, status_code=502, started_at=request_started_at, first_byte_at=first_byte_at, success=False)
                return JSONResponse({"error": {"message": "上游 TTS 返回了非法 JSON"}}, status_code=502)

            audio_b64, actual_format = extract_audio_payload(response_json)
            if not audio_b64:
                record_request_finished(route_key=route_key, status_code=502, started_at=request_started_at, first_byte_at=first_byte_at, success=False)
                return JSONResponse({"error": {"message": "上游 TTS 响应里没有音频数据"}}, status_code=502)

            try:
                audio_bytes = base64.b64decode(audio_b64, validate=True)
            except binascii.Error:
                try:
                    audio_bytes = base64.b64decode(audio_b64)
                except (binascii.Error, TypeError):
                    record_request_finished(route_key=route_key, status_code=502, started_at=request_started_at, first_byte_at=first_byte_at, success=False)
                    return JSONResponse({"error": {"message": "上游 TTS 音频数据损坏"}}, status_code=502)

            record_request_finished(route_key=route_key, status_code=200, started_at=request_started_at, first_byte_at=first_byte_at, success=True)
            return Response(audio_bytes, media_type=audio_media_type((actual_format or payload.response_format).lower()))

        except asyncio.TimeoutError:
            retry_state.status_code = 504
            retry_state.response_text = "Gateway Error: 请求内网节点超时 (30s)"
            cleanup_pending_request(req_id)
            continue
        except RuntimeError as exc:
            retry_state.status_code = 502
            retry_state.response_text = f"Gateway Error: {exc}"
            cleanup_pending_request(req_id)
            continue
        except Exception as e:
            cleanup_pending_request(req_id)
            raise e

    record_request_finished(route_key=route_key, status_code=retry_state.status_code, started_at=request_started_at, first_byte_at=None, success=False)
    return _json_error(retry_state.response_text, retry_state.status_code)

@app.post("/v1/responses")
async def responses_handler(request: Request):
    if not state.active_clients:
        return no_available_nodes_error()

    body = await request.body()
    try:
        req_body = json.loads(body.decode("utf-8", "ignore").lstrip("\ufeff"))
    except Exception as exc:
        record_error("/v1/responses", 400, f"请求解析失败: {exc}")
        return _json_error(f"请求解析失败: {exc}", 400, "invalid_request_error")

    if _is_compaction_trigger_request(req_body):
        # Codex remote compaction v2 sends a normal /v1/responses stream with a
        # trailing compaction_trigger item.  The upstream Chat Completions model
        # cannot emit Responses compaction items, so synthesize the required
        # Responses SSE envelope here after a non-streaming summarization call.
        return await handle_compaction_request(
            req_body,
            route_key="/v1/responses",
            stream=req_body.get("stream", True) is True,
        )

    try:
        chat_req = responses_convert_request(req_body)
    except Exception as exc:
        record_error("/v1/responses", 400, f"请求解析/转换失败: {exc}")
        return _json_error(f"请求解析失败: {exc}", 400, "invalid_request_error")

    model = chat_req.get("model", "")
    is_streaming = chat_req.get("stream", False) is True
    if "stream" not in req_body:
        is_streaming = True
        chat_req["stream"] = True

    chat_body_text = json.dumps(chat_req, ensure_ascii=False)
    chat_body_text = apply_model_mapping(chat_body_text)
    max_retries = min(MAX_RETRIES, get_available_client_count())
    if max_retries == 0:
        return no_available_nodes_error()

    retry_state = RetryState()
    route_key = "/v1/responses"
    request_started_at = time.monotonic()
    record_request_started(route_key, is_streaming=is_streaming)

    for attempt in range(max_retries):
        req_id = "unknown"
        try:
            prepared = await prepare_forward_attempt(method="POST", path="/v1/chat/completions", body=chat_body_text, log_label="Responses 映射请求", retry_state=retry_state, attempt_number=attempt + 1)
            if prepared is None:
                continue
            req_id = prepared.req_id
            queue = prepared.queue
            first_msg = prepared.first_msg
            status_code = first_msg.get("status", 200)
            first_byte_at = time.monotonic()

            if status_code >= 400:
                content_type, response_headers = normalize_response_headers(first_msg.get("headers", {}))
                raw_body = await collect_response_body(req_id, queue)
                record_error("/v1/responses", status_code, f"上游返回 {status_code}", detail=raw_body[:500])
                record_request_finished(route_key=route_key, status_code=status_code, started_at=request_started_at, first_byte_at=first_byte_at, success=False)

                try:
                    parsed = json.loads(raw_body) if raw_body else None
                except (json.JSONDecodeError, ValueError):
                    parsed = None

                if isinstance(parsed, dict) and "error" in parsed:
                    return JSONResponse(parsed, status_code=status_code, headers=response_headers)

                error_payload = {"error": {"message": raw_body or f"上游返回 {status_code}", "type": "upstream_error", "code": status_code}}
                return JSONResponse(error_payload, status_code=status_code, headers=response_headers)

            if is_streaming:
                converter = ResponsesStreamConverter(model=model)

                async def responses_stream_generator(current_req_id, current_queue):
                    last_data_time = time.monotonic()
                    stream_succeeded = False
                    data_task = asyncio.ensure_future(current_queue.get())

                    async def _do_keepalive():
                        await asyncio.sleep(STREAM_KEEPALIVE_INTERVAL)
                        return b": keep-alive\n\n"
                    keepalive_task = asyncio.ensure_future(_do_keepalive())

                    try:
                        while True:
                            done, _ = await asyncio.wait({data_task, keepalive_task}, return_when=asyncio.FIRST_COMPLETED)

                            # Prefer real data over keepalive if both complete
                            # in the same event-loop tick; otherwise a chunk can
                            # be dropped and the stream may never emit completed.
                            if data_task in done:
                                last_data_time = time.monotonic()
                                msg = data_task.result()
                                data_task = asyncio.ensure_future(current_queue.get())
                                if msg.get("type") == "finish":
                                    stream_succeeded = True
                                    for evt in converter.finalize():
                                        yield evt.encode("utf-8")
                                    break
                                elif msg.get("type") == "error":
                                    err_evt = f"event: error\ndata: {json.dumps({'type': 'error', 'message': msg.get('body')})}\n\n"
                                    yield err_evt.encode("utf-8")
                                    break
                                elif msg.get("type") == "chunk":
                                    for line in msg.get("body", "").split("\n"):
                                        for evt in converter.process_chunk(line):
                                            yield evt.encode("utf-8")
                                continue

                            if keepalive_task in done:
                                elapsed = time.monotonic() - last_data_time
                                if elapsed > STREAM_CHUNK_TIMEOUT:
                                    logger.warning(f"⚠️ Responses 流式 {elapsed:.0f}s 无数据，节点可能已断开 [{current_req_id[:8]}]")
                                    err_evt = f"event: error\ndata: {json.dumps({'type': 'error', 'message': '上游流式响应超时或断开，未收到 response.completed'})}\n\n"
                                    yield err_evt.encode("utf-8")
                                    break
                                yield keepalive_task.result()
                                keepalive_task = asyncio.ensure_future(_do_keepalive())
                    finally:
                        data_task.cancel()
                        keepalive_task.cancel()
                        await asyncio.gather(data_task, keepalive_task, return_exceptions=True)
                        cleanup_pending_request(current_req_id)
                        usage_obj = getattr(converter, "_usage", None)
                        record_request_finished(route_key=route_key, status_code=status_code if stream_succeeded else 502, started_at=request_started_at, first_byte_at=first_byte_at, success=stream_succeeded, usage=usage_obj.model_dump() if usage_obj else None)

                return StreamingResponse(
                    responses_stream_generator(req_id, queue),
                    status_code=status_code,
                    media_type="text/event-stream",
                    headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
                )
            else:
                raw_body = await collect_response_body(req_id, queue)
                if not raw_body.strip():
                    record_error(route_key, 502, "上游返回空响应", detail="")
                    record_request_finished(route_key=route_key, status_code=502, started_at=request_started_at, first_byte_at=first_byte_at, success=False)
                    return JSONResponse({"error": {"message": "上游返回空响应", "type": "upstream_error", "code": 502}}, status_code=502)
                try:
                    chat_resp = json.loads(raw_body)
                except json.JSONDecodeError:
                    record_error(route_key, 502, "上游返回了非法 JSON", detail=raw_body[:500])
                    record_request_finished(route_key=route_key, status_code=502, started_at=request_started_at, first_byte_at=first_byte_at, success=False)
                    return JSONResponse({"error": {"message": "上游返回了非法 JSON", "type": "upstream_error", "code": 502}}, status_code=502)

                responses_resp = responses_convert_response(chat_resp)
                record_request_finished(route_key=route_key, status_code=status_code, started_at=request_started_at, first_byte_at=first_byte_at, success=True, usage=chat_resp.get("usage"))
                return JSONResponse(content=responses_resp)

        except asyncio.TimeoutError:
            retry_state.status_code = 504
            retry_state.response_text = "Gateway Error: 请求内网节点超时"
            cleanup_pending_request(req_id)
            continue
        except Exception as e:
            cleanup_pending_request(req_id)
            raise e

    record_request_finished(route_key=route_key, status_code=retry_state.status_code, started_at=request_started_at, first_byte_at=None, success=False)
    return _json_error(retry_state.response_text, retry_state.status_code)


@app.post("/v1/responses/compact")
@app.post("/v1/responses/compact/", include_in_schema=False)
async def responses_compact_handler(request: Request):
    """Codex/OpenAI Responses compact endpoint compatibility."""
    body = await request.body()
    try:
        req_body = json.loads(body.decode("utf-8", "ignore").lstrip("\ufeff"))
    except Exception as exc:
        record_error("/v1/responses/compact", 400, f"请求解析失败: {exc}")
        return _json_error(f"请求解析失败: {exc}", 400, "invalid_request_error")
    # The compact sub-endpoint is non-streaming and must return:
    # {"output":[{"type":"compaction","encrypted_content":"..."}]}
    return await handle_compaction_request(req_body, route_key="/v1/responses/compact", stream=False)


_MODELS = [
    ("mimo-v2.5-pro", "MiMo V2.5 Pro", 1048576, 131072),
    ("mimo-v2.5", "MiMo V2.5", 1048576, 131072),
    ("mimo-v2.5-tts", "MiMo V2.5 TTS", 8192, 8192),
    ("mimo-v2-pro", "MiMo V2 Pro", 1048576, 131072),
    ("mimo-v2-flash", "MiMo V2 Flash", 256000, 131072),
    ("mimo-v2-omni", "MiMo V2 Omni", 256000, 131072),
    ("mimo-v2.5-tts-voicedesign", "MiMo V2.5 TTS VoiceDesign", 8192, 8192),
    ("mimo-v2.5-tts-voiceclone", "MiMo V2.5 TTS VoiceClone", 8192, 8192),
    ("mimo-v2-tts", "MiMo V2 TTS", 8192, 8192),
]


@app.get("/v1/models")
async def get_models():
    data = [{"id": m[0], "object": "model", "created": 1700000000, "owned_by": "mimo", "context_length": m[2], "max_tokens": m[2]} for m in _MODELS]
    return JSONResponse(content={"object": "list", "data": data})

@app.get("/anthropic/v1/models")
async def get_anthropic_models():
    data = [
        {
            "id": model_id,
            "display_name": display_name,
            "created_at": "2025-01-01T00:00:00Z",
            "type": "model",
            "max_input_tokens": context_length,
            "max_tokens": max_output_tokens,
        }
        for model_id, display_name, context_length, max_output_tokens in _MODELS
    ]
    return JSONResponse(content={"data": data, "has_more": False, "first_id": data[0]["id"], "last_id": data[-1]["id"]})

@app.post("/v1/chat/completions")
async def chat_completions_handler(request: Request):
    return await _forward_request(request, "/v1/chat/completions")

@app.post("/anthropic/v1/messages")
async def anthropic_messages_handler(request: Request):
    return await _forward_request(request, "/anthropic/v1/messages")

async def _forward_request(request: Request, path: str):
    if not state.active_clients:
        return no_available_nodes_error()

    body = await request.body()
    method = request.method
    max_retries = min(MAX_RETRIES, get_available_client_count())
    if max_retries == 0:
        return no_available_nodes_error()

    retry_state = RetryState()
    body_text = body.decode("utf-8", "ignore").lstrip("\ufeff")
    body_text = apply_model_mapping(body_text)
    route_key = path
    request_started_at = time.monotonic()

    is_streaming = False
    try:
        is_streaming = json.loads(body_text).get("stream", False) is True
    except (json.JSONDecodeError, AttributeError):
        pass
    record_request_started(route_key, is_streaming=is_streaming)

    for attempt in range(max_retries):
        req_id = "unknown"
        try:
            prepared = await prepare_forward_attempt(method=method, path=path, body=body_text, log_label="转发请求", retry_state=retry_state, attempt_number=attempt + 1)
            if prepared is None:
                continue
            req_id = prepared.req_id
            queue = prepared.queue
            first_msg = prepared.first_msg
            status_code = first_msg.get("status", 200)
            first_byte_at = time.monotonic()
            content_type, response_headers = normalize_response_headers(first_msg.get("headers", {}))

            async def stream_generator(current_req_id, current_queue, use_keepalive):
                last_data_time = time.monotonic()
                data_task = asyncio.ensure_future(current_queue.get())
                keepalive_task = None
                stream_succeeded = False
                usage_data = None

                async def _do_keepalive():
                    await asyncio.sleep(STREAM_KEEPALIVE_INTERVAL)
                    return b": keep-alive\n\n"
                if use_keepalive:
                    keepalive_task = asyncio.ensure_future(_do_keepalive())

                def _format_stream_error(message: str) -> bytes:
                    if use_keepalive or str(content_type).startswith("text/event-stream"):
                        return f"event: error\ndata: {json.dumps({'type': 'error', 'message': message})}\n\n".encode("utf-8")
                    return json.dumps({"error": {"message": message, "type": "upstream_error", "code": 502}}, ensure_ascii=False).encode("utf-8")

                try:
                    while True:
                        pending = {data_task}
                        if keepalive_task is not None:
                            pending.add(keepalive_task)
                        done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)

                        # Real upstream data wins over keepalive if both are
                        # ready.  Dropping a finish/error frame here leaves the
                        # client hanging and leaks the pending request until the
                        # stale sweeper catches it.
                        if data_task in done:
                            last_data_time = time.monotonic()
                            msg = data_task.result()
                            data_task = asyncio.ensure_future(current_queue.get())
                            if msg.get("type") == "finish":
                                stream_succeeded = True
                                break
                            elif msg.get("type") == "error":
                                error_text = str(msg.get("body") or "节点返回错误")
                                record_error(route_key, 502, "节点流式转发中途返回错误", detail=error_text[:500])
                                yield _format_stream_error(error_text)
                                break
                            elif msg.get("type") == "chunk":
                                chunk_body = msg.get("body", "")
                                if usage_data is None:
                                    usage_data = extract_usage_from_sse_chunk(chunk_body)
                                yield chunk_body.encode("utf-8")
                            continue

                        if keepalive_task is not None and keepalive_task in done:
                            elapsed = time.monotonic() - last_data_time
                            if elapsed > STREAM_CHUNK_TIMEOUT:
                                logger.warning(f"⚠️ 流式 {elapsed:.0f}s 无数据，节点可能已断开 [{current_req_id[:8]}]")
                                yield _format_stream_error("上游流式响应超时或断开，未收到完成事件")
                                break
                            yield keepalive_task.result()
                            keepalive_task = asyncio.ensure_future(_do_keepalive())
                finally:
                    data_task.cancel()
                    if keepalive_task is not None:
                        keepalive_task.cancel()
                    await asyncio.gather(*[t for t in (data_task, keepalive_task) if t is not None], return_exceptions=True)
                    cleanup_pending_request(current_req_id)
                    record_request_finished(route_key=route_key, status_code=status_code if stream_succeeded else 502, started_at=request_started_at, first_byte_at=first_byte_at, success=stream_succeeded and status_code < 400, usage=usage_data)

            if status_code >= 400:
                record_error(route_key, status_code, f"上游返回 {status_code}", detail=first_msg.get("body", "")[:300])

            return StreamingResponse(stream_generator(req_id, queue, use_keepalive=is_streaming), status_code=status_code, media_type=content_type, headers=response_headers)

        except asyncio.TimeoutError:
            retry_state.status_code = 504
            retry_state.response_text = "Gateway Error: 请求所有节点超时 (30s)"
            cleanup_pending_request(req_id)
            continue
        except Exception as e:
            cleanup_pending_request(req_id)
            raise e

    record_request_finished(route_key=route_key, status_code=retry_state.status_code, started_at=request_started_at, first_byte_at=None, success=False)
    return _json_error(retry_state.response_text, retry_state.status_code)

if __name__ == "__main__":
    logger.info("🚀 启动支持多节点的公网网关...")
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        ws_max_size=10**8,
        timeout_graceful_shutdown=int(SHUTDOWN_TASK_TIMEOUT),
    )

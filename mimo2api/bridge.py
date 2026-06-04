# BRIDGE_VERSION: 2.4 - singleton bridge, hello, bad-key self-exit and lease-aware probing
import asyncio, websockets, httpx, json, os, random, time, uuid, socket, hashlib, signal, atexit, re

KEY = os.getenv("MIMO_API_KEY")
URL = os.getenv("MIMO_API_ENDPOINT")
BASE = URL.split("/v1/")[0] if URL and "/v1/" in URL else URL

WS_URL = "__WS_URL__"
USER_ID = "__USER_ID__"
BRIDGE_VERSION = "2.4"
BRIDGE_BOOT_ID = str(uuid.uuid4())
BRIDGE_EPOCH = "__BRIDGE_EPOCH__"

def _hash_text(value):
    if not value:
        return ""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]

def _safe_uid(value):
    return re.sub(r"[^0-9A-Za-z_.-]+", "_", str(value or "default"))[:80] or "default"

def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False

def _ensure_singleton_bridge():
    """Keep only one bridge process per UID in the remote sandbox.

    Gateway-side epoch rejection protects dispatch correctness, but the remote
    process should also avoid accumulating old reconnect loops when a container
    is reused.  New bridge generations therefore terminate the previous PID
    recorded for the same UID before connecting.
    """
    uid = _safe_uid(USER_ID)
    pid_file = f"/tmp/mimo2api_bridge_{uid}.pid"
    current_pid = os.getpid()
    try:
        old_raw = ""
        if os.path.exists(pid_file):
            with open(pid_file, "r", encoding="utf-8") as f:
                old_raw = f.read().strip()
        old_pid = int(old_raw) if old_raw.isdigit() else 0
        if old_pid and old_pid != current_pid and _pid_alive(old_pid):
            print(f"[bridge] terminating previous bridge pid={old_pid} for uid={USER_ID}", flush=True)
            try:
                os.kill(old_pid, signal.SIGTERM)
            except Exception as exc:
                print(f"[bridge] SIGTERM old pid failed: {exc}", flush=True)
            deadline = time.time() + 3
            while time.time() < deadline and _pid_alive(old_pid):
                time.sleep(0.1)
            if _pid_alive(old_pid):
                try:
                    os.kill(old_pid, signal.SIGKILL)
                except Exception as exc:
                    print(f"[bridge] SIGKILL old pid failed: {exc}", flush=True)
        with open(pid_file, "w", encoding="utf-8") as f:
            f.write(str(current_pid))
        def _cleanup_pid_file():
            try:
                if os.path.exists(pid_file):
                    with open(pid_file, "r", encoding="utf-8") as f:
                        if f.read().strip() == str(current_pid):
                            os.remove(pid_file)
            except Exception:
                pass
        atexit.register(_cleanup_pid_file)
    except Exception as exc:
        print(f"[bridge] singleton setup warning: {exc}", flush=True)

def env_float(name, default, minimum=None):
    raw = os.getenv(name)
    try:
        value = float(raw) if raw not in (None, "") else float(default)
    except ValueError:
        print(f"[bridge] invalid {name}={raw!r}; using default {default}", flush=True)
        value = float(default)
    if minimum is not None:
        value = max(float(minimum), value)
    return value

async def safe_send(ws, lock, data):
    async with lock:
        await ws.send(json.dumps(data))

async def handle_request(ws, req, client, lock):
    req_id = req.get("req_id") 
    try:
        body_raw = req.get("body", "")
        if isinstance(body_raw, str):
            body_bytes = body_raw.encode("utf-8")
        else:
            body_bytes = body_raw or b""

        if not KEY or not URL:
            await safe_send(ws, lock, {
                "req_id": req_id,
                "type": "error",
                "body": (
                    "bridge 环境变量缺失: "
                    f"MIMO_API_KEY={'set' if KEY else 'missing'}, "
                    f"MIMO_API_ENDPOINT={'set' if URL else 'missing'}"
                )
            })
            return

        _headers = {
            "api-key": KEY,
            "Content-Type": "application/json",
            "Content-Length": str(len(body_bytes)),
        }
        _url = f"{BASE}/anthropic/v1/messages" if "/anthropic/" in req.get("path", "") else URL

        _sent_len = len(body_bytes)
        _cl_header = _headers.get("Content-Length", "MISSING")
        print(f"[bridge-diag] method={req.get('method','?')} path={req.get('path','?')} body_bytes={_sent_len} Content-Length={_cl_header}", flush=True)

        async with client.stream(
            method=req.get("method", "GET"), 
            url=_url, 
            headers=_headers, 
            content=body_bytes
        ) as r:
            await safe_send(ws, lock, {
                "req_id": req_id, "type": "start", 
                "status": r.status_code, "headers": dict(r.headers)
            })
            body_preview = []
            async for chunk in r.aiter_text():
                if chunk:
                    if r.status_code == 401 and len("".join(body_preview)) < 512:
                        body_preview.append(chunk)
                    await safe_send(ws, lock, {
                        "req_id": req_id, "type": "chunk", "body": chunk
                    })
            await safe_send(ws, lock, {"req_id": req_id, "type": "finish"})
            if r.status_code == 401:
                preview = "".join(body_preview)
                if "Invalid API Key" in preview or "invalid_key" in preview:
                    await safe_send(ws, lock, {
                        "type": "bridge.status",
                        "status": "bad_key",
                        "uid": USER_ID,
                        "epoch": BRIDGE_EPOCH,
                        "reason": "upstream invalid_key",
                    })
                    await asyncio.sleep(0.2)
                    os._exit(12)
        
    except Exception as e:
        await safe_send(ws, lock, {"req_id": req_id, "type": "error", "body": str(e)})

async def main():
    _ensure_singleton_bridge()
    if USER_ID:
        sep = "&" if "?" in WS_URL else "?"
        ws_url = f"{WS_URL}{sep}uid={USER_ID}&epoch={BRIDGE_EPOCH}"
    else:
        ws_url = WS_URL
    print(f"[bridge] connecting to {ws_url}", flush=True)
    reconnect_delay = 1.0
    reconnect_delay_max = env_float("MIMO_BRIDGE_RECONNECT_DELAY_MAX", 60, minimum=1)
    
    async with httpx.AsyncClient(timeout=None) as client:
        while True:
            try:
                async with websockets.connect(
                    ws_url,
                    max_size=10**8,
                    open_timeout=20,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                ) as ws:
                    reconnect_delay = 1.0
                    send_lock = asyncio.Lock()
                    await safe_send(ws, send_lock, {
                        "type": "bridge.hello",
                        "uid": USER_ID,
                        "bridge_version": BRIDGE_VERSION,
                        "boot_id": BRIDGE_BOOT_ID,
                        "epoch": BRIDGE_EPOCH,
                        "pid": os.getpid(),
                        "hostname": socket.gethostname(),
                        "started_at": int(time.time()),
                        "api_key_present": bool(KEY),
                        "api_endpoint_present": bool(URL),
                        "api_endpoint_hash": _hash_text(URL),
                    })
                    async for msg in ws:
                        asyncio.create_task(handle_request(ws, json.loads(msg), client, send_lock))
            except Exception as e:
                jitter = random.uniform(0, min(1.0, reconnect_delay * 0.1))
                sleep_for = min(reconnect_delay_max, reconnect_delay) + jitter
                print(f"[bridge] websocket disconnected/error: {type(e).__name__}: {e}; reconnect in {sleep_for:.1f}s", flush=True)
                await asyncio.sleep(sleep_for)
                reconnect_delay = min(reconnect_delay_max, reconnect_delay * 2)

if __name__ == "__main__":
    asyncio.run(main())

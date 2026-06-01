# BRIDGE_VERSION: 2.1 - Content-Length fix for chunked encoding
import asyncio, websockets, httpx, json, os

KEY = os.getenv("MIMO_API_KEY")
URL = os.getenv("MIMO_API_ENDPOINT")
BASE = URL.split("/v1/")[0] if URL and "/v1/" in URL else URL

WS_URL = "__WS_URL__"
USER_ID = "__USER_ID__"

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
            async for chunk in r.aiter_text():
                if chunk:
                    await safe_send(ws, lock, {
                        "req_id": req_id, "type": "chunk", "body": chunk
                    })
            await safe_send(ws, lock, {"req_id": req_id, "type": "finish"})
        
    except Exception as e:
        await safe_send(ws, lock, {"req_id": req_id, "type": "error", "body": str(e)})

async def main():
    ws_url = f"{WS_URL}?uid={USER_ID}" if USER_ID else WS_URL
    print(f"[bridge] connecting to {ws_url}", flush=True)
    
    async with httpx.AsyncClient(timeout=None) as client:
        while True:
            try:
                async with websockets.connect(ws_url, max_size=10**8) as ws:
                    send_lock = asyncio.Lock()
                    async for msg in ws:
                        asyncio.create_task(handle_request(ws, json.loads(msg), client, send_lock))
            except Exception:
                await asyncio.sleep(3)

if __name__ == "__main__":
    asyncio.run(main())

import os
import json
import re
import time
import asyncio
import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from .auth import (
    create_webui_session_token,
    get_webui_cookie_name,
    get_webui_session_ttl,
    get_webui_username,
    is_ai_auth_enabled,
    is_web_auth_enabled,
    is_webui_authenticated,
    verify_webui_login,
    webui_cookie_secure,
)
from .gateway_state import state
from .user_store import USERS_DIR, build_user_file_path, is_valid_user_id, load_user_records

router = APIRouter()

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def admin_error(message: str, status_code: int, error_type: str = "invalid_request_error") -> JSONResponse:
    return JSONResponse(
        {
            "detail": message,
            "error": {
                "message": message,
                "type": error_type,
                "code": status_code,
            },
        },
        status_code=status_code,
    )


@router.get("/")
async def root_page():
    return RedirectResponse(url="/webui", status_code=307)

@router.get("/webui")
async def webui_page():
    ui_path = os.path.join(os.path.dirname(__file__), "webui.html")
    if os.path.exists(ui_path):
        with open(ui_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return Response("webui.html not found", status_code=404)

@router.get("/api/system/status")
async def api_status():
    now = time.time()
    healthy_clients = 0
    dispatchable_clients = 0
    for client in state.active_clients:
        ws_id = id(client)
        health = state.client_health.get(ws_id, {})
        health_status = health.get("status", "unknown") if isinstance(health, dict) else "unknown"
        uid = state.client_uids.get(ws_id, "")
        bad_key_until = float(state.bad_key_uids.get(uid, 0) or 0) if uid else 0
        cooldown_until = float(state.client_cooldowns.get(ws_id, 0) or 0)
        if health_status in {"healthy", "unknown"}:
            healthy_clients += 1
        if health_status in {"healthy", "unknown"} and bad_key_until <= now and cooldown_until <= now:
            dispatchable_clients += 1
    return JSONResponse({
        "active_clients": len(state.active_clients),
        "healthy_clients": healthy_clients,
        "dispatchable_clients": dispatchable_clients,
        "bad_key_uid_count": sum(1 for until in state.bad_key_uids.values() if until > now),
    })


@router.get("/api/auth/session")
async def api_auth_session(request: Request):
    auth_enabled = is_web_auth_enabled()
    authenticated = is_webui_authenticated(request)
    return JSONResponse({
        "enabled": auth_enabled,
        "authenticated": authenticated,
        "username": get_webui_username(),
        "ai_auth_enabled": is_ai_auth_enabled(),
    })


@router.post("/api/auth/login")
async def api_auth_login(request: Request):
    if not is_web_auth_enabled():
        return JSONResponse({"ok": True, "enabled": False, "username": get_webui_username()})

    try:
        body = await request.json()
    except Exception:
        return admin_error("请求体不是合法 JSON", 400)

    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    if not verify_webui_login(username, password):
        return admin_error("用户名或密码错误", 401, "authentication_error")

    response = JSONResponse({"ok": True, "enabled": True, "username": get_webui_username()})
    response.set_cookie(
        key=get_webui_cookie_name(),
        value=create_webui_session_token(get_webui_username()),
        max_age=get_webui_session_ttl(),
        httponly=True,
        samesite="lax",
        secure=webui_cookie_secure(),
        path="/",
    )
    return response


@router.post("/api/auth/logout")
async def api_auth_logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(key=get_webui_cookie_name(), path="/")
    return response

async def fetch_user_status(data: dict) -> dict:
    uid = data.get("userId")
    cookies = {
        "serviceToken": data.get("serviceToken", ""),
        "userId": uid,
        "xiaomichatbot_ph": data.get("xiaomichatbot_ph", "")
    }
    url = "https://aistudio.xiaomimimo.com/open-apis/user/mimo-claw/status"
    headers = {
        "Accept": "*/*",
        "Content-Type": "application/json",
        "Origin": "https://aistudio.xiaomimimo.com",
        "Referer": "https://aistudio.xiaomimimo.com/",
        "User-Agent": "Mozilla/5.0"
    }
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(url, cookies=cookies, headers=headers, timeout=5)
            if r.status_code == 401:
                return {**data, "claw_status": "EXPIRED(401)", "remain_sec": 0}
            r_data = r.json()
            st = r_data.get("data", {}).get("status", "")
            expire_ms = r_data.get("data", {}).get("expireTime")
            expire_at = int(int(expire_ms) / 1000) if expire_ms else 0
            remain_sec = max(0, int(expire_at - time.time())) if expire_at else 0
            return {**data, "claw_status": st, "remain_sec": remain_sec, "expire_at": expire_at}
    except Exception:
        return {**data, "claw_status": "ERROR", "remain_sec": 0}

@router.get("/api/users/list")
async def api_users_list():
    raw_users, invalid_users = load_user_records(USERS_DIR)

    # 并发查询所有用户的实例状态
    tasks = [fetch_user_status(rd) for rd in raw_users]
    results = await asyncio.gather(*tasks) if raw_users else []

    users = []
    for data in results:
        users.append({
            "userId": data.get("userId"),
            "name": data.get("name"),
            "serviceToken": data.get("serviceToken"),
            "claw_status": data.get("claw_status", ""),
            "remain_sec": data.get("remain_sec", 0),
            "expire_at": data.get("expire_at", 0)
        })
    return JSONResponse({"users": users, "invalid_count": len(invalid_users), "invalid_users": invalid_users})

@router.post("/api/users/add")
async def api_users_add(request: Request):
    try:
        body = await request.json()
        raw_text = body.get("raw_text", "")
        # 解析正则提取
        parsed = {}
        for match in re.finditer(r'([a-zA-Z0-9_]+)="?([^;"]+)"?', raw_text):
            parsed[match.group(1)] = match.group(2)
            
        uid = parsed.get("userId")
        st = parsed.get("serviceToken")
        ph = parsed.get("xiaomichatbot_ph")
        
        if not uid or not st or not ph:
            return admin_error("缺少必要字段 userId, serviceToken 或 xiaomichatbot_ph", 400)
        uid = uid.strip()
        if not is_valid_user_id(uid):
            return admin_error("userId 只能包含字母、数字、下划线、点和短横线，长度 1-128", 400)
            
        os.makedirs(USERS_DIR, exist_ok=True)
        target_file = build_user_file_path(uid, USERS_DIR)
        
        user_data = {
            "userId": uid,
            "serviceToken": st,
            "xiaomichatbot_ph": ph,
            "name": f"Imported_{uid}"
        }
        with open(target_file, "w", encoding="utf-8") as f:
            json.dump(user_data, f, ensure_ascii=False, indent=2)
            
        return JSONResponse({"status": "ok", "userId": uid})
    except Exception as e:
        return admin_error(str(e), 500, "server_error")

@router.delete("/api/users/delete/{uid}")
async def api_users_delete(uid: str):
    uid = uid.strip()
    if not is_valid_user_id(uid):
        return admin_error("非法 userId", 400)
    target_file = build_user_file_path(uid, USERS_DIR)
    if target_file.exists():
        target_file.unlink()
        return JSONResponse({"status": "ok"})
    return admin_error("User not found", 404, "not_found")

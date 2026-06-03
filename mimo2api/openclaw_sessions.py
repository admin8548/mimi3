"""Read-only OpenClaw session overview helpers."""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable

from .manager import NativeClawClient
from .openclaw_diagnostics import (
    DIAGNOSTICS_RPC_TIMEOUT_SECONDS,
    sanitize_payload,
    readonly_rpc,
    select_user_record,
)
from .user_store import USERS_DIR

logger = logging.getLogger(__name__)


def _extract_session_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        sessions = payload.get("sessions")
    else:
        sessions = payload
    return [item for item in sessions if isinstance(item, dict)] if isinstance(sessions, list) else []


def _extract_session_keys(payload: Any, limit: int) -> list[str]:
    keys: list[str] = []
    for session in _extract_session_list(payload):
        key = session.get("key")
        if isinstance(key, str) and key.strip():
            keys.append(key.strip())
        if len(keys) >= limit:
            break
    return keys


async def build_openclaw_sessions_overview(
    *,
    uid: str | None = None,
    limit: int = 50,
    include_preview: bool = True,
    users_dir: str | os.PathLike[str] = USERS_DIR,
    client_factory: Callable[..., NativeClawClient] = NativeClawClient,
) -> dict[str, Any]:
    """Return a read-only sessions.list + optional sessions.preview snapshot."""

    limit = max(1, min(int(limit or 50), 120))
    selected_user, selection_errors = select_user_record(uid, users_dir=users_dir)
    generated_at = int(time.time())

    if selected_user is None:
        return {
            "ok": False,
            "generated_at": generated_at,
            "selected_uid": str(uid or ""),
            "requested": {"limit": limit, "include_preview": bool(include_preview)},
            "sessions": {"ok": False, "error": "no valid user"},
            "preview": {"ok": False, "error": "no valid user"},
            "errors": selection_errors,
        }

    selected_uid = str(selected_user["userId"])
    cookies = {
        "serviceToken": selected_user.get("serviceToken", ""),
        "userId": selected_uid,
        "xiaomichatbot_ph": selected_user.get("xiaomichatbot_ph", ""),
    }
    client = client_factory(
        selected_user.get("xiaomichatbot_ph", ""),
        cookies,
        logger,
        uid=selected_uid,
    )

    response: dict[str, Any] = {
        "ok": False,
        "generated_at": generated_at,
        "selected_uid": selected_uid,
        "requested": {"limit": limit, "include_preview": bool(include_preview)},
        "sessions": {"ok": False, "error": "not started"},
        "preview": None,
        "errors": [],
    }

    connected = False
    try:
        connected = await client.connect(wait_available=False, initialize_context=False)
        if not connected:
            response["sessions"] = {"ok": False, "error": "OpenClaw websocket connect failed"}
            response["preview"] = {"ok": False, "error": "OpenClaw websocket connect failed"} if include_preview else None
            response["errors"].append({"message": "OpenClaw websocket connect failed", "uid": selected_uid})
            return response

        sessions_payload = await readonly_rpc(
            client,
            "sessions.list",
            {"includeGlobal": True, "includeUnknown": False, "limit": limit},
            timeout=DIAGNOSTICS_RPC_TIMEOUT_SECONDS,
        )
        session_keys = _extract_session_keys(sessions_payload, limit)
        response["sessions"] = {
            "ok": True,
            "count": len(session_keys),
            "keys": sanitize_payload(session_keys),
            "payload": sanitize_payload(sessions_payload),
        }

        if include_preview:
            if session_keys:
                try:
                    preview_payload = await readonly_rpc(
                        client,
                        "sessions.preview",
                        {"keys": session_keys},
                        timeout=DIAGNOSTICS_RPC_TIMEOUT_SECONDS,
                    )
                    response["preview"] = {"ok": True, "payload": sanitize_payload(preview_payload)}
                except Exception as exc:
                    error_text = str(exc) or type(exc).__name__
                    response["preview"] = {"ok": False, "error": error_text}
                    response["errors"].append({"section": "preview", "message": error_text})
            else:
                response["preview"] = {"ok": True, "payload": {"message": "no sessions to preview"}}

        response["ok"] = True
        return response
    except Exception as exc:
        error_text = str(exc) or type(exc).__name__
        response["sessions"] = {"ok": False, "error": error_text}
        response["preview"] = {"ok": False, "error": error_text} if include_preview else response.get("preview")
        response["errors"].append({"section": "sessions", "message": error_text})
        response["ok"] = bool(connected)
        return response
    finally:
        try:
            await client.close()
        except Exception:
            logger.debug("OpenClaw sessions overview transient client close failed", exc_info=True)


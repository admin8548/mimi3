"""Controlled OpenClaw session mutation operations."""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Callable

from .manager import NativeClawClient
from .openclaw_diagnostics import sanitize_payload, select_user_record
from .openclaw_mutation_safety import (
    MUTATIONS_ENABLED_ENV,
    mutations_enabled,
    record_mutation_audit,
    verify_confirmation_token,
)
from .user_store import USERS_DIR

logger = logging.getLogger(__name__)

SESSION_KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")


async def execute_sessions_compact(
    *,
    uid: str | None = None,
    key: str,
    confirmation_token: str,
    users_dir: str | os.PathLike[str] = USERS_DIR,
    client_factory: Callable[..., NativeClawClient] = NativeClawClient,
) -> dict[str, Any]:
    """Execute sessions.compact only after feature flag and confirmation token checks."""

    key = str(key or "").strip()
    uid = str(uid or "").strip()
    params = {"key": key}
    generated_at = int(time.time())
    action = "sessions.compact"

    if not mutations_enabled():
        audit = record_mutation_audit(action=action, uid=uid, stage="blocked", params=params, result=f"{MUTATIONS_ENABLED_ENV}=false", ok=False)
        return {
            "ok": False,
            "generated_at": generated_at,
            "action": action,
            "selected_uid": uid,
            "payload": {},
            "audit_id": audit["id"],
            "errors": [{"message": f"{MUTATIONS_ENABLED_ENV} is disabled"}],
        }

    if not SESSION_KEY_RE.fullmatch(key):
        audit = record_mutation_audit(action=action, uid=uid, stage="blocked", params=params, result="invalid session key", ok=False)
        return {
            "ok": False,
            "generated_at": generated_at,
            "action": action,
            "selected_uid": uid,
            "payload": {},
            "audit_id": audit["id"],
            "errors": [{"message": "invalid session key"}],
        }

    if not verify_confirmation_token(confirmation_token, action=action, uid=uid, params=params):
        audit = record_mutation_audit(action=action, uid=uid, stage="blocked", params=params, result="invalid confirmation token", ok=False)
        return {
            "ok": False,
            "generated_at": generated_at,
            "action": action,
            "selected_uid": uid,
            "payload": {},
            "audit_id": audit["id"],
            "errors": [{"message": "invalid confirmation token"}],
        }

    selected_user, selection_errors = select_user_record(uid, users_dir=users_dir)
    if selected_user is None:
        audit = record_mutation_audit(action=action, uid=uid, stage="blocked", params=params, result="no valid user", ok=False)
        return {
            "ok": False,
            "generated_at": generated_at,
            "action": action,
            "selected_uid": uid,
            "payload": {},
            "audit_id": audit["id"],
            "errors": selection_errors,
        }

    selected_uid = str(selected_user["userId"])
    cookies = {
        "serviceToken": selected_user.get("serviceToken", ""),
        "userId": selected_uid,
        "xiaomichatbot_ph": selected_user.get("xiaomichatbot_ph", ""),
    }
    client = client_factory(selected_user.get("xiaomichatbot_ph", ""), cookies, logger, uid=selected_uid)
    record_mutation_audit(action=action, uid=selected_uid, stage="execute_start", params=params, result="connecting", ok=True)
    try:
        connected = await client.connect(wait_available=False, initialize_context=False)
        if not connected:
            audit = record_mutation_audit(action=action, uid=selected_uid, stage="execute_error", params=params, result="OpenClaw websocket connect failed", ok=False)
            return {
                "ok": False,
                "generated_at": generated_at,
                "action": action,
                "selected_uid": selected_uid,
                "payload": {},
                "audit_id": audit["id"],
                "errors": [{"message": "OpenClaw websocket connect failed"}],
            }
        payload = await client.request(action, params, timeout=30)
        audit = record_mutation_audit(action=action, uid=selected_uid, stage="execute_done", params=params, result="ok", ok=True)
        return {
            "ok": True,
            "generated_at": generated_at,
            "action": action,
            "selected_uid": selected_uid,
            "payload": sanitize_payload(payload),
            "audit_id": audit["id"],
            "errors": [],
        }
    except Exception as exc:
        audit = record_mutation_audit(action=action, uid=selected_uid, stage="execute_error", params=params, result=str(exc), ok=False)
        return {
            "ok": False,
            "generated_at": generated_at,
            "action": action,
            "selected_uid": selected_uid,
            "payload": {},
            "audit_id": audit["id"],
            "errors": [{"message": str(exc) or type(exc).__name__}],
        }
    finally:
        try:
            await client.close()
        except Exception:
            logger.debug("OpenClaw sessions.compact transient client close failed", exc_info=True)


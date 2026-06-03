"""Controlled OpenClaw browser mutation operations."""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable
from urllib.parse import urlparse

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


def validate_navigate_url(url: str) -> tuple[bool, str]:
    url = str(url or "").strip()
    if not url:
        return False, "empty url"
    if url == "about:blank":
        return True, ""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False, "unsupported url scheme"
    if not parsed.netloc:
        return False, "missing url host"
    return True, ""


async def execute_browser_navigate(
    *,
    uid: str | None = None,
    url: str,
    confirmation_token: str,
    users_dir: str | os.PathLike[str] = USERS_DIR,
    client_factory: Callable[..., NativeClawClient] = NativeClawClient,
) -> dict[str, Any]:
    """Execute browser.request POST /navigate after feature flag and confirmation token checks."""

    url = str(url or "").strip()
    uid = str(uid or "").strip()
    params = {"url": url}
    generated_at = int(time.time())
    action = "browser.navigate"

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

    valid_url, reason = validate_navigate_url(url)
    if not valid_url:
        audit = record_mutation_audit(action=action, uid=uid, stage="blocked", params=params, result=reason, ok=False)
        return {
            "ok": False,
            "generated_at": generated_at,
            "action": action,
            "selected_uid": uid,
            "payload": {},
            "audit_id": audit["id"],
            "errors": [{"message": reason}],
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
        payload = await client.request(
            "browser.request",
            {"method": "POST", "path": "/navigate", "body": {"url": url}},
            timeout=30,
        )
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
            logger.debug("OpenClaw browser.navigate transient client close failed", exc_info=True)


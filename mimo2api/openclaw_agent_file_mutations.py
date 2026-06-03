"""Controlled OpenClaw agent file mutation operations."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from typing import Any, Callable

from .manager import NativeClawClient
from .openclaw_backup_diff import find_backup_diff_record
from .openclaw_config_files import SAFE_AGENT_ID_RE
from .openclaw_diagnostics import sanitize_payload, select_user_record
from .openclaw_mutation_safety import (
    MUTATIONS_ENABLED_ENV,
    mutations_enabled,
    record_mutation_audit,
    verify_confirmation_token,
)
from .user_store import USERS_DIR

logger = logging.getLogger(__name__)

ALLOWED_AGENT_FILE_NAMES: frozenset[str] = frozenset(
    {"AGENTS.md", "SOUL.md", "TOOLS.md", "USER.md", "HEARTBEAT.md"}
)


def content_sha256(content: str) -> str:
    return hashlib.sha256(str(content or "").encode("utf-8")).hexdigest()


def validate_agent_file_set_request(agent_id: str, file_name: str, content: str, backup_id: str) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if not SAFE_AGENT_ID_RE.fullmatch(str(agent_id or "")):
        errors.append({"message": "invalid agent_id"})
    if str(file_name or "") not in ALLOWED_AGENT_FILE_NAMES:
        errors.append({"message": "file_name not allowed"})
    if not str(backup_id or "").strip():
        errors.append({"message": "backup_id required"})
    if not isinstance(content, str):
        errors.append({"message": "content must be string"})
    return errors


def _token_params(agent_id: str, file_name: str, content: str, backup_id: str) -> dict[str, Any]:
    return {
        "agentId": agent_id,
        "name": file_name,
        "content_sha256": content_sha256(content),
        "backup_id": backup_id,
    }


def _validate_backup_record(*, uid: str, agent_id: str, file_name: str, content: str, backup_id: str) -> list[dict[str, str]]:
    record = find_backup_diff_record(backup_id)
    if not record:
        return [{"message": "backup metadata not found"}]
    target = record.get("target") if isinstance(record.get("target"), dict) else {}
    errors: list[dict[str, str]] = []
    if str(record.get("uid") or "") != str(uid or ""):
        errors.append({"message": "backup uid mismatch"})
    if target.get("type") != "agent_file":
        errors.append({"message": "backup target type mismatch"})
    if target.get("agent_id") != agent_id:
        errors.append({"message": "backup agent_id mismatch"})
    if target.get("file_name") != file_name:
        errors.append({"message": "backup file_name mismatch"})
    if record.get("proposed_sha256") != content_sha256(content):
        errors.append({"message": "backup proposed content mismatch"})
    return errors


async def execute_agents_files_set(
    *,
    uid: str | None = None,
    agent_id: str,
    file_name: str,
    content: str,
    backup_id: str,
    confirmation_token: str,
    users_dir: str | os.PathLike[str] = USERS_DIR,
    client_factory: Callable[..., NativeClawClient] = NativeClawClient,
) -> dict[str, Any]:
    """Execute agents.files.set only after feature flag, backup metadata, and token checks."""

    uid = str(uid or "").strip()
    agent_id = str(agent_id or "main").strip()
    file_name = str(file_name or "").strip()
    content = str(content or "")
    backup_id = str(backup_id or "").strip()
    action = "agents.files.set"
    generated_at = int(time.time())
    params = _token_params(agent_id, file_name, content, backup_id)

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

    errors = validate_agent_file_set_request(agent_id, file_name, content, backup_id)
    if errors:
        audit = record_mutation_audit(action=action, uid=uid, stage="blocked", params=params, result=errors[0]["message"], ok=False)
        return {
            "ok": False,
            "generated_at": generated_at,
            "action": action,
            "selected_uid": uid,
            "payload": {},
            "audit_id": audit["id"],
            "errors": errors,
        }

    backup_errors = _validate_backup_record(uid=uid, agent_id=agent_id, file_name=file_name, content=content, backup_id=backup_id)
    if backup_errors:
        audit = record_mutation_audit(action=action, uid=uid, stage="blocked", params=params, result=backup_errors[0]["message"], ok=False)
        return {
            "ok": False,
            "generated_at": generated_at,
            "action": action,
            "selected_uid": uid,
            "payload": {},
            "audit_id": audit["id"],
            "errors": backup_errors,
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
            action,
            {"agentId": agent_id, "name": file_name, "content": content},
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
            logger.debug("OpenClaw agents.files.set transient client close failed", exc_info=True)


"""Controlled OpenClaw config mutation operations."""

from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Any, Callable

from .manager import NativeClawClient
from .openclaw_backup_diff import find_backup_diff_record
from .openclaw_diagnostics import sanitize_payload, select_user_record
from .openclaw_mutation_safety import (
    MUTATIONS_ENABLED_ENV,
    mutations_enabled,
    record_mutation_audit,
    verify_confirmation_token,
)
from .user_store import USERS_DIR

logger = logging.getLogger(__name__)


def raw_sha256(raw: str) -> str:
    return hashlib.sha256(str(raw or "").encode("utf-8")).hexdigest()


def _token_params(raw: str, base_hash: str, backup_id: str) -> dict[str, Any]:
    return {
        "raw_sha256": raw_sha256(raw),
        "baseHash": str(base_hash or ""),
        "backup_id": str(backup_id or ""),
    }


def _validate_backup_record(*, uid: str, raw: str, base_hash: str, backup_id: str) -> list[dict[str, str]]:
    record = find_backup_diff_record(backup_id)
    if not record:
        return [{"message": "backup metadata not found"}]
    target = record.get("target") if isinstance(record.get("target"), dict) else {}
    errors: list[dict[str, str]] = []
    if str(record.get("uid") or "") != str(uid or ""):
        errors.append({"message": "backup uid mismatch"})
    if target.get("type") != "config":
        errors.append({"message": "backup target type mismatch"})
    if str(target.get("baseHash") or "") != str(base_hash or ""):
        errors.append({"message": "backup baseHash mismatch"})
    if record.get("proposed_sha256") != raw_sha256(raw):
        errors.append({"message": "backup proposed content mismatch"})
    return errors


async def execute_config_patch(
    *,
    uid: str | None = None,
    raw: str,
    base_hash: str,
    backup_id: str,
    confirmation_token: str,
    users_dir: str | os.PathLike[str] = USERS_DIR,
    client_factory: Callable[..., NativeClawClient] = NativeClawClient,
) -> dict[str, Any]:
    """Execute config.patch only after feature flag, backup metadata, baseHash, and token checks."""

    uid = str(uid or "").strip()
    raw = str(raw or "")
    base_hash = str(base_hash or "").strip()
    backup_id = str(backup_id or "").strip()
    action = "config.patch"
    generated_at = int(time.time())
    params = _token_params(raw, base_hash, backup_id)

    if not mutations_enabled():
        audit = record_mutation_audit(action=action, uid=uid, stage="blocked", params=params, result=f"{MUTATIONS_ENABLED_ENV}=false", ok=False)
        return {
            "ok": False,
            "generated_at": generated_at,
            "action": action,
            "selected_uid": uid,
            "payload": {},
            "verify": {},
            "audit_id": audit["id"],
            "errors": [{"message": f"{MUTATIONS_ENABLED_ENV} is disabled"}],
        }

    if not raw:
        audit = record_mutation_audit(action=action, uid=uid, stage="blocked", params=params, result="raw required", ok=False)
        return {
            "ok": False,
            "generated_at": generated_at,
            "action": action,
            "selected_uid": uid,
            "payload": {},
            "verify": {},
            "audit_id": audit["id"],
            "errors": [{"message": "raw required"}],
        }
    if not base_hash:
        audit = record_mutation_audit(action=action, uid=uid, stage="blocked", params=params, result="baseHash required", ok=False)
        return {
            "ok": False,
            "generated_at": generated_at,
            "action": action,
            "selected_uid": uid,
            "payload": {},
            "verify": {},
            "audit_id": audit["id"],
            "errors": [{"message": "baseHash required"}],
        }

    backup_errors = _validate_backup_record(uid=uid, raw=raw, base_hash=base_hash, backup_id=backup_id)
    if backup_errors:
        audit = record_mutation_audit(action=action, uid=uid, stage="blocked", params=params, result=backup_errors[0]["message"], ok=False)
        return {
            "ok": False,
            "generated_at": generated_at,
            "action": action,
            "selected_uid": uid,
            "payload": {},
            "verify": {},
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
            "verify": {},
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
            "verify": {},
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
                "verify": {},
                "audit_id": audit["id"],
                "errors": [{"message": "OpenClaw websocket connect failed"}],
            }

        payload = await client.request(action, {"raw": raw, "baseHash": base_hash}, timeout=30)
        verify_payload = await client.request("config.get", {}, timeout=30)
        verify_raw = verify_payload.get("raw") if isinstance(verify_payload, dict) else ""
        proposed_hash = raw_sha256(raw)
        verified_hash = raw_sha256(verify_raw)
        verify = {
            "raw_sha256": verified_hash,
            "proposed_sha256": proposed_hash,
            "matches_proposed": verified_hash == proposed_hash,
            "chars": len(str(verify_raw or "")),
            "lines": len(str(verify_raw or "").splitlines()),
            "baseHash": verify_payload.get("baseHash") if isinstance(verify_payload, dict) else None,
            "payload": sanitize_payload(verify_payload),
        }
        if not verify["matches_proposed"]:
            audit = record_mutation_audit(action=action, uid=selected_uid, stage="verify_error", params=params, result="post-write config.get hash mismatch", ok=False)
            return {
                "ok": False,
                "generated_at": generated_at,
                "action": action,
                "selected_uid": selected_uid,
                "payload": sanitize_payload(payload),
                "verify": verify,
                "audit_id": audit["id"],
                "errors": [{"message": "post-write config.get hash mismatch"}],
            }

        audit = record_mutation_audit(action=action, uid=selected_uid, stage="execute_done", params=params, result="ok", ok=True)
        return {
            "ok": True,
            "generated_at": generated_at,
            "action": action,
            "selected_uid": selected_uid,
            "payload": sanitize_payload(payload),
            "verify": verify,
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
            "verify": {},
            "audit_id": audit["id"],
            "errors": [{"message": str(exc) or type(exc).__name__}],
        }
    finally:
        try:
            await client.close()
        except Exception:
            logger.debug("OpenClaw config.patch transient client close failed", exc_info=True)


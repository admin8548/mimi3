"""Safety primitives for future OpenClaw mutating operations.

This module does not execute any OpenClaw mutating RPC.  It only provides a
feature flag, preview/audit records, and confirmation-token primitives that
future write operations must use.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from collections import deque
from typing import Any

from .config import get_env_bool, get_env_int
from .openclaw_diagnostics import sanitize_payload

MUTATIONS_ENABLED_ENV = "MIMO_OPENCLAW_MUTATIONS_ENABLED"
CONFIRMATION_SECRET_ENV = "MIMO_OPENCLAW_CONFIRMATION_SECRET"
CONFIRMATION_TTL_ENV = "MIMO_OPENCLAW_CONFIRMATION_TTL_SECONDS"

DEFAULT_CONFIRMATION_TTL_SECONDS = 300
MAX_AUDIT_RECORDS = 500

SUPPORTED_MUTATION_ACTIONS: tuple[str, ...] = (
    "sessions.compact",
    "cron.run",
    "browser.navigate",
    "config.patch",
    "agents.files.set",
)

_audit_records: deque[dict[str, Any]] = deque(maxlen=MAX_AUDIT_RECORDS)
_confirmation_tokens: dict[str, dict[str, Any]] = {}


def mutations_enabled() -> bool:
    return get_env_bool(MUTATIONS_ENABLED_ENV, False)


def confirmation_ttl_seconds() -> int:
    return get_env_int(CONFIRMATION_TTL_ENV, DEFAULT_CONFIRMATION_TTL_SECONDS, min_value=60, max_value=3600)


def _confirmation_secret() -> bytes:
    secret = os.getenv(CONFIRMATION_SECRET_ENV, "").strip()
    if not secret:
        secret = os.getenv("MIMO_WEBUI_SECRET", "").strip() or os.getenv("MIMO_WEBUI_PASSWORD", "").strip()
    return (secret or "mimo-openclaw-confirmation-development-secret").encode("utf-8")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def params_digest(params: Any) -> str:
    return hashlib.sha256(_canonical_json(sanitize_payload(params)).encode("utf-8")).hexdigest()


def _hash_token(token: str) -> str:
    return hmac.new(_confirmation_secret(), token.encode("utf-8"), hashlib.sha256).hexdigest()


def record_mutation_audit(
    *,
    action: str,
    uid: str = "",
    stage: str,
    params: Any = None,
    result: str = "",
    ok: bool = True,
) -> dict[str, Any]:
    record = {
        "id": secrets.token_hex(8),
        "ts": int(time.time()),
        "action": str(action or ""),
        "uid": str(uid or ""),
        "stage": str(stage or ""),
        "params": sanitize_payload(params or {}),
        "params_digest": params_digest(params or {}),
        "result": str(result or "")[:300],
        "ok": bool(ok),
    }
    _audit_records.append(record)
    return record


def list_mutation_audit(limit: int = 50) -> dict[str, Any]:
    limit = max(1, min(int(limit or 50), MAX_AUDIT_RECORDS))
    records = list(_audit_records)[-limit:]
    records.reverse()
    return {"count": len(records), "records": records}


def validate_mutation_action(action: str) -> bool:
    return str(action or "") in SUPPORTED_MUTATION_ACTIONS


def build_mutation_preview(action: str, uid: str = "", params: Any = None) -> dict[str, Any]:
    action = str(action or "")
    uid = str(uid or "")
    params = params or {}
    valid_action = validate_mutation_action(action)
    enabled = mutations_enabled()
    audit = record_mutation_audit(
        action=action,
        uid=uid,
        stage="preview",
        params=params,
        result="valid_action" if valid_action else "invalid_action",
        ok=valid_action,
    )
    return {
        "ok": valid_action,
        "generated_at": int(time.time()),
        "mutations_enabled": enabled,
        "action": action,
        "uid": uid,
        "supported_actions": list(SUPPORTED_MUTATION_ACTIONS),
        "confirmation_required": True,
        "confirmation_available": enabled and valid_action,
        "would_execute": False,
        "params": sanitize_payload(params),
        "params_digest": params_digest(params),
        "audit_id": audit["id"],
        "errors": [] if valid_action else [{"message": "unsupported mutation action", "action": action}],
    }


def create_confirmation_token(action: str, uid: str = "", params: Any = None) -> dict[str, Any]:
    preview = build_mutation_preview(action, uid, params)
    if not preview["ok"]:
        return {**preview, "token": ""}
    if not mutations_enabled():
        return {
            **preview,
            "ok": False,
            "token": "",
            "errors": [{"message": f"{MUTATIONS_ENABLED_ENV} is disabled"}],
        }

    token = secrets.token_urlsafe(32)
    token_hash = _hash_token(token)
    expires_at = int(time.time()) + confirmation_ttl_seconds()
    _confirmation_tokens[token_hash] = {
        "action": str(action or ""),
        "uid": str(uid or ""),
        "params_digest": params_digest(params or {}),
        "expires_at": expires_at,
        "used": False,
    }
    record_mutation_audit(action=action, uid=uid, stage="confirmation_token", params=params, result="issued", ok=True)
    return {**preview, "ok": True, "token": token, "expires_at": expires_at}


def verify_confirmation_token(token: str, *, action: str, uid: str = "", params: Any = None) -> bool:
    token_hash = _hash_token(str(token or ""))
    record = _confirmation_tokens.get(token_hash)
    if not record or record.get("used"):
        return False
    if int(record.get("expires_at", 0)) < int(time.time()):
        return False
    if record.get("action") != str(action or ""):
        return False
    if record.get("uid") != str(uid or ""):
        return False
    if record.get("params_digest") != params_digest(params or {}):
        return False
    record["used"] = True
    return True


def build_mutation_safety_status() -> dict[str, Any]:
    return {
        "ok": True,
        "generated_at": int(time.time()),
        "mutations_enabled": mutations_enabled(),
        "feature_flag_env": MUTATIONS_ENABLED_ENV,
        "confirmation_ttl_seconds": confirmation_ttl_seconds(),
        "supported_actions": list(SUPPORTED_MUTATION_ACTIONS),
        "audit_count": len(_audit_records),
        "pending_confirmation_count": sum(1 for item in _confirmation_tokens.values() if not item.get("used") and int(item.get("expires_at", 0)) >= int(time.time())),
        "requirements": {
            "feature_flag": "must be true",
            "confirmation_token": "required for every mutating operation",
            "audit": "preview and execution stages must be recorded",
            "dry_run": "preview must be shown before execution",
        },
    }


def clear_mutation_safety_state() -> None:
    _audit_records.clear()
    _confirmation_tokens.clear()


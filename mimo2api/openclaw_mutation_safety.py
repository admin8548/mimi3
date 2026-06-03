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

MUTATION_ACTION_REQUIREMENTS: dict[str, dict[str, Any]] = {
    "sessions.compact": {
        "required_params": ["key"],
        "confirmation_fields": ["uid", "key", "params_digest"],
        "risk": "压缩指定会话上下文，可能改变后续对话可用历史。",
        "safeguards": ["requires confirmation token", "audited execute_start/execute_done"],
    },
    "cron.run": {
        "required_params": ["id"],
        "confirmation_fields": ["uid", "id", "params_digest"],
        "risk": "立即触发指定定时任务运行，可能产生异步副作用。",
        "safeguards": ["requires confirmation token", "audited execute_start/execute_done"],
    },
    "browser.navigate": {
        "required_params": ["url"],
        "constraints": ["only http://, https://, and about:blank are allowed"],
        "confirmation_fields": ["uid", "url", "params_digest"],
        "risk": "改变沙箱浏览器当前导航状态。",
        "safeguards": ["URL scheme allowlist", "requires confirmation token"],
    },
    "config.patch": {
        "required_params": ["raw_sha256", "baseHash", "backup_id"],
        "confirmation_fields": ["uid", "backup_id", "baseHash", "raw_sha256", "params_digest"],
        "risk": "写入 OpenClaw raw 配置；错误 raw 可能影响后续 OpenClaw 行为。",
        "safeguards": ["metadata backup preview required", "baseHash required", "post-write config.get hash verification"],
    },
    "agents.files.set": {
        "required_params": ["agentId", "name", "content_sha256", "backup_id"],
        "constraints": ["allowed names: AGENTS.md, SOUL.md, TOOLS.md, USER.md, HEARTBEAT.md"],
        "confirmation_fields": ["uid", "agentId", "name", "backup_id", "content_sha256", "params_digest"],
        "risk": "写入指定智能体工作区文件；错误内容可能影响智能体行为。",
        "safeguards": ["metadata backup preview required", "allowed filename list", "content hash bound to token"],
    },
}


def mutation_action_requirements() -> dict[str, dict[str, Any]]:
    return json.loads(json.dumps(MUTATION_ACTION_REQUIREMENTS, ensure_ascii=False))


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
        "action_requirements": mutation_action_requirements().get(action, {}),
        "confirmation_required": True,
        "confirmation_available": enabled and valid_action,
        "would_execute": False,
        "params": sanitize_payload(params),
        "params_digest": params_digest(params),
        "audit_id": audit["id"],
        "errors": [] if valid_action else [{"message": "unsupported mutation action", "action": action}],
    }


def create_confirmation_token(action: str, uid: str = "", params: Any = None) -> dict[str, Any]:
    purge_expired_confirmation_tokens()
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
    purge_expired_confirmation_tokens()
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


def purge_expired_confirmation_tokens(now: int | None = None) -> int:
    """Remove expired confirmation-token records and return the number purged."""

    current_ts = int(time.time()) if now is None else int(now)
    expired_hashes = [
        token_hash
        for token_hash, record in _confirmation_tokens.items()
        if int(record.get("expires_at", 0)) < current_ts
    ]
    for token_hash in expired_hashes:
        _confirmation_tokens.pop(token_hash, None)
    return len(expired_hashes)


def confirmation_token_counts(now: int | None = None) -> dict[str, int]:
    current_ts = int(time.time()) if now is None else int(now)
    total = len(_confirmation_tokens)
    used = sum(1 for item in _confirmation_tokens.values() if item.get("used"))
    expired = sum(1 for item in _confirmation_tokens.values() if int(item.get("expires_at", 0)) < current_ts)
    pending = sum(
        1
        for item in _confirmation_tokens.values()
        if not item.get("used") and int(item.get("expires_at", 0)) >= current_ts
    )
    return {
        "total": total,
        "pending": pending,
        "used": used,
        "expired": expired,
    }


def build_mutation_safety_status() -> dict[str, Any]:
    now = int(time.time())
    before_cleanup = confirmation_token_counts(now)
    purged_expired = purge_expired_confirmation_tokens(now)
    token_counts = confirmation_token_counts(now)
    ttl_seconds = confirmation_ttl_seconds()
    return {
        "ok": True,
        "generated_at": now,
        "mutations_enabled": mutations_enabled(),
        "feature_flag_env": MUTATIONS_ENABLED_ENV,
        "confirmation_ttl_seconds": ttl_seconds,
        "confirmation_tokens": {
            **token_counts,
            "expired_before_cleanup": before_cleanup["expired"],
            "purged_expired": purged_expired,
            "ttl_seconds": ttl_seconds,
            "one_time_use": True,
        },
        "supported_actions": list(SUPPORTED_MUTATION_ACTIONS),
        "mutation_action_requirements": mutation_action_requirements(),
        "audit_count": len(_audit_records),
        "pending_confirmation_count": token_counts["pending"],
        "requirements": {
            "feature_flag": "must be true",
            "confirmation_token": "required for every mutating operation",
            "confirmation_token_one_time_use": "tokens are single-use and bound to action, uid, and params digest",
            "confirmation_token_ttl": f"tokens expire after {ttl_seconds} seconds and expired local records are purged from safety status",
            "audit": "preview and execution stages must be recorded",
            "dry_run": "preview must be shown before execution",
        },
    }


def clear_mutation_safety_state() -> None:
    _audit_records.clear()
    _confirmation_tokens.clear()


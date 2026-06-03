"""Read-only backup metadata and diff preview helpers for future writes."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import time
from collections import deque
from typing import Any, Callable

from .manager import NativeClawClient
from .openclaw_diagnostics import DIAGNOSTICS_RPC_TIMEOUT_SECONDS, readonly_rpc, sanitize_payload, select_user_record
from .openclaw_config_files import SAFE_AGENT_ID_RE, SAFE_FILE_NAME_RE
from .user_store import USERS_DIR

logger = logging.getLogger(__name__)

BACKUP_DIFF_RECORDS_MAX = 200
TARGET_TYPES = {"config", "agent_file"}
_backup_diff_records: deque[dict[str, Any]] = deque(maxlen=BACKUP_DIFF_RECORDS_MAX)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _extract_text(payload: Any, preferred_keys: tuple[str, ...]) -> str:
    if isinstance(payload, dict):
        for key in preferred_keys:
            value = payload.get(key)
            if isinstance(value, str):
                return value
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _text_summary(text: str) -> dict[str, Any]:
    lines = text.splitlines()
    return {
        "chars": len(text),
        "lines": len(lines),
        "sha256": _sha256_text(text),
        "prefix": text[:240],
    }


def _validate_request(target_type: str, agent_id: str, file_name: str) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if target_type not in TARGET_TYPES:
        errors.append({"message": "invalid target_type"})
    if target_type == "agent_file":
        if not SAFE_AGENT_ID_RE.fullmatch(agent_id):
            errors.append({"message": "invalid agent_id"})
        if not SAFE_FILE_NAME_RE.fullmatch(file_name):
            errors.append({"message": "invalid file_name"})
    return errors


def list_backup_diff_records(limit: int = 50) -> dict[str, Any]:
    limit = max(1, min(int(limit or 50), BACKUP_DIFF_RECORDS_MAX))
    records = list(_backup_diff_records)[-limit:]
    records.reverse()
    return {"count": len(records), "records": records}


def find_backup_diff_record(backup_id: str) -> dict[str, Any] | None:
    backup_id = str(backup_id or "").strip()
    if not backup_id:
        return None
    for record in reversed(_backup_diff_records):
        if record.get("id") == backup_id:
            return dict(record)
    return None


def clear_backup_diff_records() -> None:
    _backup_diff_records.clear()


async def build_openclaw_backup_diff_preview(
    *,
    uid: str | None = None,
    target_type: str,
    proposed_content: str,
    agent_id: str = "main",
    file_name: str = "",
    users_dir: str | os.PathLike[str] = USERS_DIR,
    client_factory: Callable[..., NativeClawClient] = NativeClawClient,
) -> dict[str, Any]:
    """Read current config/file and compare metadata with proposed content; no writes."""

    target_type = str(target_type or "").strip()
    proposed_content = str(proposed_content or "")
    agent_id = str(agent_id or "main").strip()
    file_name = str(file_name or "").strip()
    generated_at = int(time.time())
    validation_errors = _validate_request(target_type, agent_id, file_name)
    if validation_errors:
        return {
            "ok": False,
            "generated_at": generated_at,
            "target_type": target_type,
            "selected_uid": str(uid or ""),
            "backup": {},
            "diff": {},
            "errors": validation_errors,
        }

    selected_user, selection_errors = select_user_record(uid, users_dir=users_dir)
    if selected_user is None:
        return {
            "ok": False,
            "generated_at": generated_at,
            "target_type": target_type,
            "selected_uid": str(uid or ""),
            "backup": {},
            "diff": {},
            "errors": selection_errors,
        }

    selected_uid = str(selected_user["userId"])
    cookies = {
        "serviceToken": selected_user.get("serviceToken", ""),
        "userId": selected_uid,
        "xiaomichatbot_ph": selected_user.get("xiaomichatbot_ph", ""),
    }
    client = client_factory(selected_user.get("xiaomichatbot_ph", ""), cookies, logger, uid=selected_uid)
    try:
        connected = await client.connect(wait_available=False, initialize_context=False)
        if not connected:
            return {
                "ok": False,
                "generated_at": generated_at,
                "target_type": target_type,
                "selected_uid": selected_uid,
                "backup": {},
                "diff": {},
                "errors": [{"message": "OpenClaw websocket connect failed"}],
            }

        if target_type == "config":
            current_payload = await readonly_rpc(client, "config.get", {}, timeout=DIAGNOSTICS_RPC_TIMEOUT_SECONDS)
            current_text = _extract_text(current_payload, ("raw", "content", "text"))
            base_hash = current_payload.get("baseHash") if isinstance(current_payload, dict) else None
            target = {"type": "config", "baseHash": base_hash}
        else:
            current_payload = await readonly_rpc(
                client,
                "agents.files.get",
                {"agentId": agent_id, "name": file_name},
                timeout=DIAGNOSTICS_RPC_TIMEOUT_SECONDS,
            )
            current_text = _extract_text(current_payload, ("content", "text", "raw"))
            target = {"type": "agent_file", "agent_id": agent_id, "file_name": file_name}

        backup_id = secrets.token_hex(8)
        current_summary = _text_summary(current_text)
        proposed_summary = _text_summary(proposed_content)
        changed = current_summary["sha256"] != proposed_summary["sha256"]
        record = {
            "id": backup_id,
            "ts": generated_at,
            "uid": selected_uid,
            "target": target,
            "current_sha256": current_summary["sha256"],
            "proposed_sha256": proposed_summary["sha256"],
            "changed": changed,
        }
        _backup_diff_records.append(record)
        return {
            "ok": True,
            "generated_at": generated_at,
            "selected_uid": selected_uid,
            "target": target,
            "backup": {
                "id": backup_id,
                "metadata_only": True,
                "current": sanitize_payload(current_summary),
            },
            "diff": {
                "changed": changed,
                "current": sanitize_payload(current_summary),
                "proposed": sanitize_payload(proposed_summary),
                "delta": {
                    "chars": proposed_summary["chars"] - current_summary["chars"],
                    "lines": proposed_summary["lines"] - current_summary["lines"],
                },
            },
            "source_payload": sanitize_payload(current_payload),
            "errors": [],
        }
    except Exception as exc:
        return {
            "ok": False,
            "generated_at": generated_at,
            "target_type": target_type,
            "selected_uid": selected_uid,
            "backup": {},
            "diff": {},
            "errors": [{"section": "backup_diff", "message": str(exc) or type(exc).__name__}],
        }
    finally:
        try:
            await client.close()
        except Exception:
            logger.debug("OpenClaw backup/diff transient client close failed", exc_info=True)

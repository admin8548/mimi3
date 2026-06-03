"""Read-only OpenClaw config and agent-file overview helpers."""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Callable

from .manager import NativeClawClient
from .openclaw_diagnostics import (
    DIAGNOSTICS_RPC_TIMEOUT_SECONDS,
    readonly_rpc,
    sanitize_payload,
    select_user_record,
)
from .user_store import USERS_DIR

logger = logging.getLogger(__name__)

SAFE_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
SAFE_FILE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")


def _validate_agent_file_request(agent_id: str, file_name: str) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if not SAFE_AGENT_ID_RE.fullmatch(agent_id):
        errors.append({"message": "invalid agent_id"})
    if file_name and not SAFE_FILE_NAME_RE.fullmatch(file_name):
        errors.append({"message": "invalid file_name"})
    return errors


def _file_count(payload: Any) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        files = payload.get("files")
        if isinstance(files, list):
            return len(files)
    return 0


async def build_openclaw_config_files_overview(
    *,
    uid: str | None = None,
    agent_id: str = "main",
    file_name: str | None = None,
    users_dir: str | os.PathLike[str] = USERS_DIR,
    client_factory: Callable[..., NativeClawClient] = NativeClawClient,
) -> dict[str, Any]:
    """Return read-only config.get/schema and agents.files list/get data."""

    agent_id = str(agent_id or "main").strip()
    file_name = str(file_name or "").strip()
    generated_at = int(time.time())
    validation_errors = _validate_agent_file_request(agent_id, file_name)
    if validation_errors:
        return {
            "ok": False,
            "generated_at": generated_at,
            "selected_uid": str(uid or ""),
            "requested": {"agent_id": agent_id, "file_name": file_name},
            "config": {},
            "agent_files": {},
            "summary": {},
            "errors": validation_errors,
        }

    selected_user, selection_errors = select_user_record(uid, users_dir=users_dir)
    if selected_user is None:
        return {
            "ok": False,
            "generated_at": generated_at,
            "selected_uid": str(uid or ""),
            "requested": {"agent_id": agent_id, "file_name": file_name},
            "config": {},
            "agent_files": {},
            "summary": {},
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

    try:
        connected = await client.connect(wait_available=False, initialize_context=False)
        if not connected:
            return {
                "ok": False,
                "generated_at": generated_at,
                "selected_uid": selected_uid,
                "requested": {"agent_id": agent_id, "file_name": file_name},
                "config": {},
                "agent_files": {},
                "summary": {},
                "errors": [{"message": "OpenClaw websocket connect failed", "uid": selected_uid}],
            }

        config_payload = await readonly_rpc(client, "config.get", {}, timeout=DIAGNOSTICS_RPC_TIMEOUT_SECONDS)
        schema_payload = await readonly_rpc(client, "config.schema", {}, timeout=DIAGNOSTICS_RPC_TIMEOUT_SECONDS)
        files_payload = await readonly_rpc(
            client,
            "agents.files.list",
            {"agentId": agent_id},
            timeout=DIAGNOSTICS_RPC_TIMEOUT_SECONDS,
        )
        file_payload = None
        if file_name:
            file_payload = await readonly_rpc(
                client,
                "agents.files.get",
                {"agentId": agent_id, "name": file_name},
                timeout=DIAGNOSTICS_RPC_TIMEOUT_SECONDS,
            )

        return {
            "ok": True,
            "generated_at": generated_at,
            "selected_uid": selected_uid,
            "requested": {"agent_id": agent_id, "file_name": file_name},
            "config": {
                "get": {"ok": True, "payload": sanitize_payload(config_payload)},
                "schema": {"ok": True, "payload": sanitize_payload(schema_payload)},
            },
            "agent_files": {
                "list": {"ok": True, "payload": sanitize_payload(files_payload)},
                "get": {"ok": True, "payload": sanitize_payload(file_payload)} if file_name else None,
            },
            "summary": {
                "file_count": _file_count(files_payload),
                "file_loaded": bool(file_name),
            },
            "errors": [],
        }
    except Exception as exc:
        return {
            "ok": False,
            "generated_at": generated_at,
            "selected_uid": selected_uid,
            "requested": {"agent_id": agent_id, "file_name": file_name},
            "config": {},
            "agent_files": {},
            "summary": {},
            "errors": [{"section": "config_files", "message": str(exc) or type(exc).__name__}],
        }
    finally:
        try:
            await client.close()
        except Exception:
            logger.debug("OpenClaw config/files transient client close failed", exc_info=True)


"""Read-only OpenClaw cron overview helpers."""

from __future__ import annotations

import asyncio
import logging
import os
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


def _list_like_count(value: Any, preferred_keys: tuple[str, ...]) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        for key in preferred_keys:
            nested = value.get(key)
            if isinstance(nested, list):
                return len(nested)
        return len(value)
    return 0


def _status_counts_from_runs(value: Any) -> dict[str, int]:
    if isinstance(value, dict):
        runs = None
        for key in ("runs", "items", "history"):
            if isinstance(value.get(key), list):
                runs = value.get(key)
                break
    elif isinstance(value, list):
        runs = value
    else:
        runs = None

    counts: dict[str, int] = {}
    for run in runs if isinstance(runs, list) else []:
        if not isinstance(run, dict):
            continue
        status = str(run.get("status") or run.get("state") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


async def build_openclaw_cron_overview(
    *,
    uid: str | None = None,
    users_dir: str | os.PathLike[str] = USERS_DIR,
    client_factory: Callable[..., NativeClawClient] = NativeClawClient,
) -> dict[str, Any]:
    """Return a read-only cron.status + cron.list + cron.runs snapshot."""

    selected_user, selection_errors = select_user_record(uid, users_dir=users_dir)
    generated_at = int(time.time())

    if selected_user is None:
        return {
            "ok": False,
            "generated_at": generated_at,
            "selected_uid": str(uid or ""),
            "cron": {},
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
                "cron": {},
                "summary": {},
                "errors": [{"message": "OpenClaw websocket connect failed", "uid": selected_uid}],
            }

        status_payload, list_payload, runs_payload = await asyncio.gather(
            readonly_rpc(client, "cron.status", {}, timeout=DIAGNOSTICS_RPC_TIMEOUT_SECONDS),
            readonly_rpc(client, "cron.list", {}, timeout=DIAGNOSTICS_RPC_TIMEOUT_SECONDS),
            readonly_rpc(client, "cron.runs", {}, timeout=DIAGNOSTICS_RPC_TIMEOUT_SECONDS),
        )
        return {
            "ok": True,
            "generated_at": generated_at,
            "selected_uid": selected_uid,
            "cron": {
                "status": {"ok": True, "payload": sanitize_payload(status_payload)},
                "list": {"ok": True, "payload": sanitize_payload(list_payload)},
                "runs": {"ok": True, "payload": sanitize_payload(runs_payload)},
            },
            "summary": {
                "job_count": _list_like_count(list_payload, ("jobs", "items", "crons")),
                "run_count": _list_like_count(runs_payload, ("runs", "items", "history")),
                "run_status_counts": _status_counts_from_runs(runs_payload),
            },
            "errors": [],
        }
    except Exception as exc:
        return {
            "ok": False,
            "generated_at": generated_at,
            "selected_uid": selected_uid,
            "cron": {},
            "summary": {},
            "errors": [{"section": "cron", "message": str(exc) or type(exc).__name__}],
        }
    finally:
        try:
            await client.close()
        except Exception:
            logger.debug("OpenClaw cron overview transient client close failed", exc_info=True)


"""Read-only OpenClaw browser overview helpers."""

from __future__ import annotations

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


def _count_nested_list(value: Any, keys: tuple[str, ...]) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        for key in keys:
            nested = value.get(key)
            if isinstance(nested, list):
                return len(nested)
        return len(value)
    return 0


async def _browser_get(client: NativeClawClient, path: str, *, include_snapshot: bool) -> Any:
    return await readonly_rpc(
        client,
        "browser.request",
        {"method": "GET", "path": path},
        include_snapshot=include_snapshot,
        timeout=DIAGNOSTICS_RPC_TIMEOUT_SECONDS,
    )


async def build_openclaw_browser_overview(
    *,
    uid: str | None = None,
    include_snapshot: bool = False,
    users_dir: str | os.PathLike[str] = USERS_DIR,
    client_factory: Callable[..., NativeClawClient] = NativeClawClient,
) -> dict[str, Any]:
    """Return a read-only browser GET /, /profiles, /tabs, optional /snapshot snapshot."""

    selected_user, selection_errors = select_user_record(uid, users_dir=users_dir)
    generated_at = int(time.time())

    if selected_user is None:
        return {
            "ok": False,
            "generated_at": generated_at,
            "selected_uid": str(uid or ""),
            "requested": {"include_snapshot": bool(include_snapshot)},
            "browser": {},
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
                "requested": {"include_snapshot": bool(include_snapshot)},
                "browser": {},
                "summary": {},
                "errors": [{"message": "OpenClaw websocket connect failed", "uid": selected_uid}],
            }

        root_payload = await _browser_get(client, "/", include_snapshot=include_snapshot)
        profiles_payload = await _browser_get(client, "/profiles", include_snapshot=include_snapshot)
        tabs_payload = await _browser_get(client, "/tabs", include_snapshot=include_snapshot)
        snapshot_payload = None
        snapshot_result = None
        if include_snapshot:
            snapshot_payload = await _browser_get(client, "/snapshot", include_snapshot=True)
            snapshot_result = {"ok": True, "payload": sanitize_payload(snapshot_payload)}

        return {
            "ok": True,
            "generated_at": generated_at,
            "selected_uid": selected_uid,
            "requested": {"include_snapshot": bool(include_snapshot)},
            "browser": {
                "root": {"ok": True, "payload": sanitize_payload(root_payload)},
                "profiles": {"ok": True, "payload": sanitize_payload(profiles_payload)},
                "tabs": {"ok": True, "payload": sanitize_payload(tabs_payload)},
                "snapshot": snapshot_result,
            },
            "summary": {
                "profile_count": _count_nested_list(profiles_payload, ("profiles", "items")),
                "tab_count": _count_nested_list(tabs_payload, ("tabs", "items", "targets")),
                "snapshot_loaded": bool(include_snapshot),
            },
            "errors": [],
        }
    except Exception as exc:
        return {
            "ok": False,
            "generated_at": generated_at,
            "selected_uid": selected_uid,
            "requested": {"include_snapshot": bool(include_snapshot)},
            "browser": {},
            "summary": {},
            "errors": [{"section": "browser", "message": str(exc) or type(exc).__name__}],
        }
    finally:
        try:
            await client.close()
        except Exception:
            logger.debug("OpenClaw browser overview transient client close failed", exc_info=True)


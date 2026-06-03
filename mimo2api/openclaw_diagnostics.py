"""Read-only OpenClaw diagnostics aggregation.

This module deliberately exposes only a small read-only RPC surface for the
WebUI diagnostics page.  It never creates/destroys Claw instances and it never
invokes agent, node, cron mutation, config mutation, chat, or approval methods.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Awaitable, Callable

from .manager import NativeClawClient
from .user_store import USERS_DIR, is_valid_user_id, load_user_records

logger = logging.getLogger(__name__)

DEFAULT_DIAGNOSTIC_SECTIONS: tuple[str, ...] = (
    "health",
    "status",
    "usage",
    "models",
    "tools",
    "sessions",
    "cron",
    "browser",
)

DIAGNOSTICS_CACHE_TTL_SECONDS = 30
DIAGNOSTICS_RPC_TIMEOUT_SECONDS = 15

_RPC_ALLOWLIST: frozenset[str] = frozenset(
    {
        "health",
        "status",
        "usage.status",
        "usage.cost",
        "config.get",
        "config.schema",
        "models.list",
        "tools.catalog",
        "agents.files.list",
        "agents.files.get",
        "sessions.list",
        "sessions.preview",
        "cron.status",
        "cron.list",
        "cron.runs",
        "browser.request",
    }
)
_BROWSER_GET_ALLOWLIST: frozenset[str] = frozenset({"/", "/profiles", "/tabs"})
_BROWSER_SNAPSHOT_PATH = "/snapshot"

_SENSITIVE_KEY_MARKERS = (
    "token",
    "cookie",
    "ticket",
    "secret",
    "authorization",
    "api_key",
    "apikey",
    "password",
    "service",
    "xiaomichatbot_ph",
    "wsurl",
)
_SENSITIVE_VALUE_MARKERS = (
    "authorization:",
    "bearer ",
    "serviceToken=",
    "ticket=",
    "token=",
    "api_key=",
    "apikey=",
)
_SUMMARY_STRING_LIMIT = 240
_SUMMARY_LIST_LIMIT = 8
_SUMMARY_DICT_LIMIT = 40

_diagnostics_cache: dict[tuple[str, tuple[str, ...], bool], tuple[float, dict[str, Any]]] = {}
_cache_lock = asyncio.Lock()


class DiagnosticsRPCDenied(ValueError):
    """Raised when code attempts to use a non-diagnostics RPC."""


def parse_sections(raw_sections: str | None) -> tuple[str, ...]:
    if not raw_sections:
        return DEFAULT_DIAGNOSTIC_SECTIONS

    requested: list[str] = []
    allowed = set(DEFAULT_DIAGNOSTIC_SECTIONS)
    for item in raw_sections.split(","):
        section = item.strip().lower()
        if not section or section not in allowed or section in requested:
            continue
        requested.append(section)
    return tuple(requested) or DEFAULT_DIAGNOSTIC_SECTIONS


def _is_sensitive_key(key: Any) -> bool:
    key_text = str(key or "").lower()
    return any(marker in key_text for marker in _SENSITIVE_KEY_MARKERS)


def _is_sensitive_value(value: str) -> bool:
    value_lower = value.lower()
    return any(marker.lower() in value_lower for marker in _SENSITIVE_VALUE_MARKERS)


def sanitize_payload(value: Any, *, depth: int = 0) -> Any:
    """Return a UI-safe schema/value summary with credential redaction."""

    if depth >= 4:
        return f"<{type(value).__name__}>"

    if isinstance(value, dict):
        result: dict[str, Any] = {}
        items = list(value.items())
        for key, val in items[:_SUMMARY_DICT_LIMIT]:
            key_text = str(key)
            if _is_sensitive_key(key_text):
                result[key_text] = "<redacted>"
            else:
                result[key_text] = sanitize_payload(val, depth=depth + 1)
        if len(items) > _SUMMARY_DICT_LIMIT:
            result["_truncated_keys"] = len(items) - _SUMMARY_DICT_LIMIT
        return result

    if isinstance(value, list):
        return {
            "type": "list",
            "len": len(value),
            "sample": [sanitize_payload(item, depth=depth + 1) for item in value[:_SUMMARY_LIST_LIMIT]],
        }

    if isinstance(value, str):
        if _is_sensitive_value(value):
            return "<redacted>"
        if len(value) > _SUMMARY_STRING_LIMIT:
            return {"type": "str", "len": len(value), "prefix": value[:_SUMMARY_STRING_LIMIT]}
        return value

    if isinstance(value, (int, float, bool)) or value is None:
        return value

    return f"<{type(value).__name__}>"


def validate_readonly_rpc(method: str, params: dict[str, Any] | None = None, *, include_snapshot: bool = False) -> None:
    method = str(method or "")
    params = params or {}
    if method not in _RPC_ALLOWLIST:
        raise DiagnosticsRPCDenied(f"OpenClaw diagnostics RPC not allowed: {method}")

    if method != "browser.request":
        return

    browser_method = str(params.get("method") or "").upper()
    path = str(params.get("path") or "")
    if browser_method != "GET":
        raise DiagnosticsRPCDenied(f"OpenClaw diagnostics browser.request only allows GET, got {browser_method}")
    if path in _BROWSER_GET_ALLOWLIST:
        return
    if include_snapshot and path == _BROWSER_SNAPSHOT_PATH:
        return
    raise DiagnosticsRPCDenied(f"OpenClaw diagnostics browser.request path not allowed: {path}")


async def readonly_rpc(
    client: NativeClawClient,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    include_snapshot: bool = False,
    timeout: int = DIAGNOSTICS_RPC_TIMEOUT_SECONDS,
) -> Any:
    validate_readonly_rpc(method, params, include_snapshot=include_snapshot)
    return await client.request(method, params or {}, timeout=timeout)


async def _run_usage(client: NativeClawClient, include_snapshot: bool) -> dict[str, Any]:
    status, cost = await asyncio.gather(
        readonly_rpc(client, "usage.status", {}, include_snapshot=include_snapshot),
        readonly_rpc(client, "usage.cost", {}, include_snapshot=include_snapshot),
    )
    return {"status": status, "cost": cost}


async def _run_cron(client: NativeClawClient, include_snapshot: bool) -> dict[str, Any]:
    status, jobs, runs = await asyncio.gather(
        readonly_rpc(client, "cron.status", {}, include_snapshot=include_snapshot),
        readonly_rpc(client, "cron.list", {}, include_snapshot=include_snapshot),
        readonly_rpc(client, "cron.runs", {}, include_snapshot=include_snapshot),
    )
    return {"status": status, "list": jobs, "runs": runs}


async def _run_browser(client: NativeClawClient, include_snapshot: bool) -> dict[str, Any]:
    paths = ["/", "/profiles", "/tabs"]
    if include_snapshot:
        paths.append("/snapshot")

    results: dict[str, Any] = {}
    for path in paths:
        results[path] = await readonly_rpc(
            client,
            "browser.request",
            {"method": "GET", "path": path},
            include_snapshot=include_snapshot,
        )
    return results


async def run_section(client: NativeClawClient, section: str, *, include_snapshot: bool = False) -> Any:
    section = section.strip().lower()
    if section == "health":
        return await readonly_rpc(client, "health", {}, include_snapshot=include_snapshot)
    if section == "status":
        return await readonly_rpc(client, "status", {}, include_snapshot=include_snapshot)
    if section == "usage":
        return await _run_usage(client, include_snapshot)
    if section == "models":
        return await readonly_rpc(client, "models.list", {}, include_snapshot=include_snapshot)
    if section == "tools":
        return await readonly_rpc(client, "tools.catalog", {}, include_snapshot=include_snapshot)
    if section == "sessions":
        return await readonly_rpc(
            client,
            "sessions.list",
            {"includeGlobal": True, "includeUnknown": False, "limit": 120},
            include_snapshot=include_snapshot,
        )
    if section == "cron":
        return await _run_cron(client, include_snapshot)
    if section == "browser":
        return await _run_browser(client, include_snapshot)
    raise ValueError(f"unknown diagnostics section: {section}")


def select_user_record(
    uid: str | None = None,
    *,
    users_dir: str | os.PathLike[str] = USERS_DIR,
    preferred_uid: str | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    users, invalid_users = load_user_records(users_dir)
    by_uid = {str(user.get("userId")): user for user in users}

    requested_uid = str(uid or "").strip()
    if requested_uid:
        if not is_valid_user_id(requested_uid):
            return None, [{"message": "invalid uid", "uid": requested_uid}]
        if requested_uid not in by_uid:
            return None, [{"message": "uid not found", "uid": requested_uid}]
        return by_uid[requested_uid], []

    preferred = str(preferred_uid if preferred_uid is not None else os.getenv("MIMO_PREFERRED_UID", "")).strip()
    if preferred and preferred in by_uid:
        return by_uid[preferred], []

    if users:
        return users[0], []

    return None, [{"message": "no valid users", "invalid_count": len(invalid_users), "invalid_users": invalid_users[:20]}]


def clear_diagnostics_cache() -> None:
    _diagnostics_cache.clear()


async def build_openclaw_diagnostics(
    *,
    uid: str | None = None,
    sections: str | None = None,
    refresh: bool = False,
    include_snapshot: bool = False,
    users_dir: str | os.PathLike[str] = USERS_DIR,
    client_factory: Callable[..., NativeClawClient] = NativeClawClient,
) -> dict[str, Any]:
    selected_sections = parse_sections(sections)
    selected_user, selection_errors = select_user_record(uid, users_dir=users_dir)
    generated_at = int(time.time())

    if selected_user is None:
        return {
            "ok": False,
            "generated_at": generated_at,
            "selected_uid": str(uid or ""),
            "cache": {"hit": False, "ttl_seconds": DIAGNOSTICS_CACHE_TTL_SECONDS},
            "sections": {},
            "errors": selection_errors,
        }

    selected_uid = str(selected_user["userId"])
    cache_key = (selected_uid, selected_sections, bool(include_snapshot))
    if not refresh:
        async with _cache_lock:
            cached = _diagnostics_cache.get(cache_key)
            if cached:
                cached_at, cached_payload = cached
                if time.time() - cached_at < DIAGNOSTICS_CACHE_TTL_SECONDS:
                    payload = dict(cached_payload)
                    payload["cache"] = {"hit": True, "ttl_seconds": DIAGNOSTICS_CACHE_TTL_SECONDS}
                    payload["generated_at"] = generated_at
                    return payload

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

    section_results: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    connected = False
    try:
        connected = await client.connect(wait_available=False, initialize_context=False)
        if not connected:
            errors.append({"message": "OpenClaw websocket connect failed", "uid": selected_uid})
            for section in selected_sections:
                section_results[section] = {"ok": False, "error": "OpenClaw websocket connect failed"}
        else:
            for section in selected_sections:
                try:
                    payload = await run_section(client, section, include_snapshot=include_snapshot)
                    section_results[section] = {"ok": True, "payload": sanitize_payload(payload)}
                except Exception as exc:
                    error_text = str(exc) or type(exc).__name__
                    section_results[section] = {"ok": False, "error": error_text}
                    errors.append({"section": section, "message": error_text})
    finally:
        try:
            await client.close()
        except Exception:
            logger.debug("OpenClaw diagnostics transient client close failed", exc_info=True)

    response = {
        "ok": bool(connected),
        "generated_at": generated_at,
        "selected_uid": selected_uid,
        "cache": {"hit": False, "ttl_seconds": DIAGNOSTICS_CACHE_TTL_SECONDS},
        "sections": section_results,
        "errors": errors,
    }
    async with _cache_lock:
        _diagnostics_cache[cache_key] = (time.time(), response)
    return response

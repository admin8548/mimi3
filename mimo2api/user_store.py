import json
import os
import re
from pathlib import Path
from typing import Any

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USERS_DIR = os.path.join(ROOT_DIR, "users")
USER_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
REQUIRED_USER_FIELDS = ("userId", "serviceToken", "xiaomichatbot_ph")


def is_valid_user_id(uid: str | None) -> bool:
    return isinstance(uid, str) and bool(USER_ID_RE.fullmatch(uid.strip()))


def normalize_user_record(raw_data: Any) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(raw_data, dict):
        return None, "not_object"

    normalized = dict(raw_data)
    for field in REQUIRED_USER_FIELDS:
        value = normalized.get(field)
        if not isinstance(value, str) or not value.strip():
            return None, f"missing_or_invalid_{field}"
        normalized[field] = value.strip()

    if not is_valid_user_id(normalized["userId"]):
        return None, "invalid_userId"

    name = normalized.get("name")
    if not isinstance(name, str) or not name.strip():
        normalized["name"] = f"Imported_{normalized['userId']}"
    else:
        normalized["name"] = name.strip()

    return normalized, ""


def load_user_records(users_dir: str | os.PathLike[str] = USERS_DIR) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    users: list[dict[str, Any]] = []
    invalid: list[dict[str, str]] = []
    directory = Path(users_dir)
    if not directory.exists():
        return users, invalid

    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if not path.name.startswith("user_") or path.suffix != ".json":
            continue
        try:
            raw_data = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            invalid.append({"file": path.name, "reason": f"json_error:{type(exc).__name__}"})
            continue

        normalized, reason = normalize_user_record(raw_data)
        if normalized is None:
            invalid.append({"file": path.name, "reason": reason})
            continue
        users.append(normalized)

    return users, invalid


def build_user_file_path(uid: str, users_dir: str | os.PathLike[str] = USERS_DIR) -> Path:
    uid = uid.strip()
    if not is_valid_user_id(uid):
        raise ValueError("invalid_userId")
    return Path(users_dir) / f"user_{uid}.json"

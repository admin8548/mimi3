import logging
import os

logger = logging.getLogger(__name__)


def get_env_bool(name: str, default: bool = False) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    value = raw_value.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    logger.warning("环境变量 %s=%r 不是合法布尔值，使用默认值 %s", name, raw_value, default)
    return default


def get_env_int(name: str, default: int, *, min_value: int | None = None, max_value: int | None = None) -> int:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        value = default
    else:
        try:
            value = int(raw_value.strip())
        except ValueError:
            logger.warning("环境变量 %s=%r 不是合法整数，使用默认值 %s", name, raw_value, default)
            value = default
    if min_value is not None:
        value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value


def get_env_float(name: str, default: float, *, min_value: float | None = None, max_value: float | None = None) -> float:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        value = default
    else:
        try:
            value = float(raw_value.strip())
        except ValueError:
            logger.warning("环境变量 %s=%r 不是合法浮点数，使用默认值 %s", name, raw_value, default)
            value = default
    if min_value is not None:
        value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value

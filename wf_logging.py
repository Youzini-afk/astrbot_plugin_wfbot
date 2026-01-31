from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from astrbot.api import logger  # type: ignore


@dataclass
class LogConfig:
    enabled: bool = True
    cycle_enabled: bool = True
    http_enabled: bool = True
    public_export_enabled: bool = True
    subscription_enabled: bool = True
    cache_enabled: bool = True
    manager_enabled: bool = True
    main_enabled: bool = True
    cycle_success: bool = True
    cycle_failures: bool = True


_cfg = LogConfig()


def configure(
    *,
    enabled: bool | None = None,
    cycle_enabled: bool | None = None,
    http_enabled: bool | None = None,
    public_export_enabled: bool | None = None,
    subscription_enabled: bool | None = None,
    cache_enabled: bool | None = None,
    manager_enabled: bool | None = None,
    main_enabled: bool | None = None,
    cycle_success: bool | None = None,
    cycle_failures: bool | None = None,
) -> None:
    if enabled is not None:
        _cfg.enabled = bool(enabled)
    if cycle_enabled is not None:
        _cfg.cycle_enabled = bool(cycle_enabled)
    if http_enabled is not None:
        _cfg.http_enabled = bool(http_enabled)
    if public_export_enabled is not None:
        _cfg.public_export_enabled = bool(public_export_enabled)
    if subscription_enabled is not None:
        _cfg.subscription_enabled = bool(subscription_enabled)
    if cache_enabled is not None:
        _cfg.cache_enabled = bool(cache_enabled)
    if manager_enabled is not None:
        _cfg.manager_enabled = bool(manager_enabled)
    if main_enabled is not None:
        _cfg.main_enabled = bool(main_enabled)
    if cycle_success is not None:
        _cfg.cycle_success = bool(cycle_success)
    if cycle_failures is not None:
        _cfg.cycle_failures = bool(cycle_failures)


def _allow(category: str | None = None) -> bool:
    if not _cfg.enabled:
        return False
    if category == "cycle" and not _cfg.cycle_enabled:
        return False
    if category == "http" and not _cfg.http_enabled:
        return False
    if category == "public_export" and not _cfg.public_export_enabled:
        return False
    if category == "subscription" and not _cfg.subscription_enabled:
        return False
    if category == "cache" and not _cfg.cache_enabled:
        return False
    if category == "manager" and not _cfg.manager_enabled:
        return False
    if category == "main" and not _cfg.main_enabled:
        return False
    return True


def debug(msg: str, *args: Any, category: str | None = None, **kwargs: Any) -> None:
    if not _allow(category):
        return
    logger.debug(msg, *args, **kwargs)


def info(msg: str, *args: Any, category: str | None = None, **kwargs: Any) -> None:
    if not _allow(category):
        return
    logger.info(msg, *args, **kwargs)


def warning(msg: str, *args: Any, category: str | None = None, **kwargs: Any) -> None:
    if not _allow(category):
        return
    logger.warning(msg, *args, **kwargs)


def error(msg: str, *args: Any, category: str | None = None, **kwargs: Any) -> None:
    if not _allow(category):
        return
    logger.error(msg, *args, **kwargs)


def exception(msg: str, *args: Any, category: str | None = None, **kwargs: Any) -> None:
    if not _allow(category):
        return
    logger.exception(msg, *args, **kwargs)

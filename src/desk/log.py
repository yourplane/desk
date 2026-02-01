"""Logging configuration for desk."""

from __future__ import annotations

import logging
import os

_logger: logging.Logger | None = None


def get_desk_log_path() -> str:
    """Return the desk log file path."""
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(config_home, "desk", "desk.log")


def get_logger(name: str) -> logging.Logger:
    """Get or create the desk logger. Configures file logging on first use."""
    global _logger
    if _logger is not None:
        return _logger.getChild(name) if name else _logger

    log_path = get_desk_log_path()
    log_dir = os.path.dirname(log_path)
    os.makedirs(log_dir, mode=0o700, exist_ok=True)

    logger = logging.getLogger("desk")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(
        logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    )
    logger.addHandler(fh)
    _logger = logger

    return logger.getChild(name) if name else logger

"""NGSAT logging configuration.

Provides structured logging with consistent format across all modules.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_LOG_FORMAT = "%(asctime)s | %(name)-20s | %(levelname)-8s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logger(
    name: str = "ngsat",
    level: int = logging.INFO,
    log_file: str | None = None,
) -> logging.Logger:
    """Set up and return a logger instance.

    Args:
        name: Logger name (usually module name).
        level: Logging level (default INFO).
        log_file: Optional file path for log output.

    Returns:
        Configured logger instance.
    """
    logger = logging.getLogger(name)

    if logger.handlers:
        # Already configured — add file handler if missing but requested
        if log_file:
            has_file = any(isinstance(h, logging.FileHandler) for h in logger.handlers)
            if not has_file:
                log_path = PROJECT_ROOT / log_file
                log_path.parent.mkdir(parents=True, exist_ok=True)
                file_handler = logging.FileHandler(log_path, encoding="utf-8")
                file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
                logger.addHandler(file_handler)
        return logger

    logger.setLevel(level)

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
    logger.addHandler(console)

    # File handler (optional)
    if log_file:
        log_path = PROJECT_ROOT / log_file
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
        logger.addHandler(file_handler)

    return logger


# Default logger
logger = setup_logger()

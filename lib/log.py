"""Logging shared by backup.py and restore.py."""

import logging
import sys

from lib.config import BACKUP_DIR

LOGGER = "backup"

# "none" is not a level restic or logging knows about; it means "stdout only"
LEVELS = ("info", "warning", "error", "none")


def setup_logging(filename: str, level: str = "info") -> logging.Logger:
    """Log to stdout and to <BACKUP_DIR>/filename. Call once, after argument parsing."""
    log = logging.getLogger(LOGGER)
    log.setLevel(logging.INFO if level == "none" else getattr(logging, level.upper()))
    fmt = logging.Formatter("[%(asctime)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    handlers = [logging.StreamHandler(sys.stdout)]
    if level != "none":
        handlers.append(logging.FileHandler(BACKUP_DIR / filename))
    for h in handlers:
        h.setFormatter(fmt)
        log.addHandler(h)
    return log

"""Logging shared by backup.py and restore.py."""

import logging
import sys

from lib.config import BACKUP_DIR

LOGGER = "backup"


def setup_logging(filename: str) -> logging.Logger:
    """Log to stdout and to <BACKUP_DIR>/filename. Call once, after argument parsing."""
    log = logging.getLogger(LOGGER)
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("[%(asctime)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    for h in (
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(BACKUP_DIR / filename),
    ):
        h.setFormatter(fmt)
        log.addHandler(h)
    return log

"""Running restic: environment, process plumbing, and the operations both scripts share."""

import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lib.log import LOGGER

BACKUP_INCOMPLETE = 3  # some source files could not be read; snapshot was still created
REPO_NOT_FOUND = 10  # restic's exit code for "repository does not exist"
REPO_LOCKED = 11  # restic's exit code for "repository is already locked"

STALE_LOCK_AGE = timedelta(hours=1)  # don't touch a lock younger than this


def restic_env(repo: str) -> dict:
    env = dict(os.environ)
    env["RESTIC_REPOSITORY"] = repo
    env.pop("RESTIC_REPOSITORY_FILE", None)
    return env


def find_restic() -> str:
    restic = shutil.which("restic")
    if not restic:
        sys.exit("ERROR: restic not found in PATH")
    return restic


def run(cmd: list, env: dict) -> int:
    """Run cmd, streaming combined output to the log."""
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env
    )
    assert proc.stdout is not None  # stdout=PIPE guarantees a readable stream
    for line in proc.stdout:
        logging.getLogger(LOGGER).info(line.rstrip())
    return proc.wait()


def capture(cmd: list, env: dict) -> str | None:
    """Run cmd quietly, returning its stdout, or None if it failed."""
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)
    return proc.stdout if proc.returncode == 0 else None


def lock_owner_alive(lock: dict) -> bool:
    """Is the restic process that took this lock still running on this machine?

    restic's own staleness check is just `kill(pid, 0)`, which calls a lock live
    whenever the pid has been recycled by an unrelated process (and EPERM, when
    that process belongs to another user, reads as "alive" too). Checking the
    command name and owner as well means a recycled pid no longer pins a lock.
    """
    proc = Path(f"/proc/{lock.get('pid')}")
    try:
        if proc.joinpath("comm").read_text().strip() != "restic":
            return False  # pid recycled by something that is not restic
        status = proc.joinpath("status").read_text()
    except OSError:
        return False  # process is gone
    for line in status.splitlines():
        if line.startswith("Uid:"):
            return int(line.split()[1]) == lock.get("uid")
    return True


def repo_locks(restic: str, env: dict) -> list | None:
    """Return every lock in the repo as (id, parsed lock), or None if unreadable."""
    ids = capture([restic, "list", "locks"], env)
    if ids is None:
        return None
    locks = []
    for lock_id in ids.split():
        body = capture([restic, "cat", "lock", lock_id], env)
        if body is None:
            return None  # vanished or unreadable: too uncertain to act on
        try:
            locks.append((lock_id, json.loads(body)))
        except json.JSONDecodeError:
            return None
    return locks


def clear_stale_locks(restic: str, env: dict, host: str) -> bool:
    """Drop locks left behind by restic processes that died on this host.

    Only removes anything when *every* lock in the repo is ours, old enough, and
    orphaned; a single lock we can't account for leaves the repo untouched. The
    caller must hold the flock, which is what rules out a concurrent backup of
    our own. Returns True if locks were removed.
    """
    log = logging.getLogger(LOGGER)
    locks = repo_locks(restic, env)
    if not locks:
        return False

    now = datetime.now(timezone.utc)
    for lock_id, lock in locks:
        short = lock_id[:8]
        if lock.get("hostname") != host:
            log.warning(f"lock {short} was taken on {lock.get('hostname')}; leaving it alone")
            return False
        age = now - datetime.fromisoformat(lock["time"])
        if age < STALE_LOCK_AGE:
            log.warning(f"lock {short} is only {age} old; leaving it alone")
            return False
        if lock_owner_alive(lock):
            log.warning(f"lock {short} is held by running pid {lock.get('pid')}")
            return False

    log.warning(f"removing {len(locks)} stale lock(s) left by dead restic processes")
    return run([restic, "unlock", "--remove-all"], env) == 0


def list_snapshots(restic: str, host: str, env: dict) -> None:
    """Replace this process with `restic snapshots --json`, scoped to host."""
    os.execve(restic, [restic, "snapshots", "--tag", host, "--json"], env)


def probe_repo(restic: str, env: dict, host: str | None = None) -> int:
    """Return restic's exit code for a snapshots listing: 0 ok, REPO_NOT_FOUND, or an error."""
    cmd = [restic, "snapshots"]
    if host:
        cmd += ["--tag", host]
    return run(cmd, env)

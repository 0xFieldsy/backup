#!/usr/bin/env python3
"""
Restic backup to Cloudflare R2.

Usage:
  backup.py [PATH ...]             folders to back up (absolute, relative to cwd, or ~/...)
  backup.py -b FILE                also include the folders listed in FILE
  backup.py --exclude-file FILE    apply restic exclude patterns from FILE
  backup.py --bucket NAME          R2 bucket (default: backup-<hostname>)
  backup.py --path PATH            local repo path for testing (bypasses CF keys)
  backup.py --keep-days N          keep N daily snapshots (default 7; 0 skips forget/prune)
  backup.py --retry-lock DUR       wait DUR for a locked repo (default 5m)
  backup.py --no-gitignore         don't derive excludes from .gitignore files
  backup.py --list                 list snapshots for this host
  backup.py --dry-run              print resolved config and path list
  backup.py --log-level LEVEL      log verbosity; "none" keeps the run out of backup.log
"""

import argparse
import atexit
import fcntl
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # find lib via a symlink

from lib.config import BACKUP_DIR, HOST, load_env_file, resolve_repo
from lib.gitignore import gitignore_patterns
from lib.log import LEVELS, setup_logging
from lib.restic import (
    BACKUP_INCOMPLETE,
    REPO_LOCKED,
    REPO_NOT_FOUND,
    clear_stale_locks,
    find_restic,
    list_snapshots,
    probe_repo,
    restic_env,
    run,
)

# Exit codes:
# - 2 is argparse's usage error
# - 3 is restic's "snapshot incomplete"
# - 4 means the snapshot is safe; only forget/prune did not run
# - 5 skipped: another backup on this host still holds the flock
RETENTION_FAILED = 4
ALREADY_RUNNING = 5

# Permanent file; only the kernel-held flock signals a running backup
LOCK_NAME = ".lock"


def read_backup_txt(path: Path) -> list:
    lines = (line.strip() for line in path.read_text().splitlines())
    return [line for line in lines if line]


def resolve_paths(items: list) -> tuple:
    paths, skipped = [], []
    for item in items:
        candidate = Path(item).expanduser().resolve()
        if candidate.is_dir():
            paths.append(candidate)
        else:
            skipped.append(candidate)
    if not paths:
        sys.exit("ERROR: nothing to back up (no source path is an existing directory)")
    return paths, skipped


def acquire_lock(path: Path):
    """Take the run lock, returning the open fd, or None if another run holds it.

    Keep the file in place so every run locks the same inode. The kernel releases the lock when the
    descriptor closes or the process exits, including crashes.
    """
    fd = open(path, "a")  # noqa: SIM115
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fd.close()
        return None
    except OSError:
        fd.close()
        raise
    return fd


def retry_past_lock(restic: str, cmd: list, env: dict, log, host: str) -> int:
    """Run cmd; if the repo is locked by a dead run of ours, clear it and retry once."""
    status = run(cmd, env)
    if status != REPO_LOCKED:
        return status
    if not clear_stale_locks(restic, env, host):
        return status
    return run(cmd, env)


def main() -> None:
    ap = argparse.ArgumentParser(description="push-based restic backup to R2")
    ap.add_argument(
        "paths",
        nargs="*",
        metavar="PATH",
        help="folders to back up (absolute, relative to cwd, or ~/...)",
    )
    ap.add_argument("-l", "--list", action="store_true", help="list snapshots and exit")
    ap.add_argument(
        "-b",
        "--backup-file",
        metavar="FILE",
        help="extra folders from FILE, one per line (same path rules as PATH)",
    )
    ap.add_argument(
        "--exclude-file",
        metavar="FILE",
        help="restic exclude patterns from FILE",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="print resolved config and path list, change nothing",
    )
    ap.add_argument(
        "--no-gitignore",
        action="store_true",
        help="don't derive excludes from the .gitignore files in the sources",
    )
    ap.add_argument(
        "--bucket",
        metavar="NAME",
        help="R2 bucket name (default: backup-<hostname>)",
    )
    ap.add_argument(
        "--path",
        metavar="PATH",
        help="local restic repo path (for testing)",
    )
    ap.add_argument(
        "--keep-days",
        type=int,
        metavar="N",
        default=7,
        help="keep N daily snapshots (default: 7); 0 skips forget/prune",
    )
    ap.add_argument(
        "--retry-lock",
        metavar="DURATION",
        default="5m",
        help="wait this long for a locked repo before giving up (default: 5m)",
    )
    ap.add_argument(
        "--log-level",
        metavar="LEVEL",
        choices=LEVELS,
        default="info",
        help=f"log verbosity: {', '.join(LEVELS)} (default: info); "
        "'none' logs to stdout only, leaving backup.log untouched",
    )
    args = ap.parse_args()

    if args.keep_days < 0:
        ap.error("--keep-days must be non-negative")

    if not args.paths and not args.backup_file and not args.list:
        ap.error("no PATHs given and no --backup-file (nothing to back up)")

    load_env_file()
    repo = resolve_repo(args.path, args.bucket)
    env = restic_env(repo)

    exclude_file = Path(args.exclude_file) if args.exclude_file else None
    if exclude_file and not exclude_file.is_file():
        sys.exit(f"ERROR: {exclude_file} does not exist")

    git_patterns, unparseable = [], []
    if not args.list:
        items = list(args.paths)
        if args.backup_file:
            backup_file = Path(args.backup_file)
            if not backup_file.is_file():
                sys.exit(f"ERROR: {backup_file} does not exist")
            items += read_backup_txt(backup_file)
        paths, skipped = resolve_paths(items)
        if not args.no_gitignore:
            git_patterns, unparseable = gitignore_patterns(paths)

    if args.dry_run:
        print(f"repo: {repo}")
        if args.list:
            print(f"operation: restic snapshots --tag {HOST} --json")
            return
        print(f"paths: {[str(p) for p in paths]}")
        print(f"skipped: {[str(p) for p in skipped]}")
        print(f"exclude file: {exclude_file or '(none)'}")
        print(
            f"gitignore excludes: {len(git_patterns)} patterns"
            if not args.no_gitignore
            else "gitignore excludes: (disabled)"
        )
        for pattern in git_patterns:
            print(f"  {pattern}")
        for path in unparseable:
            print(f"  (skipped, glob chars in name) {path}")
        print(
            f"retention: keep-daily {args.keep_days}, grouped by host"
            if args.keep_days
            else "retention: (none — forget skipped)"
        )
        return

    restic = find_restic()

    if args.list:
        list_snapshots(restic, HOST, env)

    log = setup_logging("backup.log", args.log_level)

    lock_fd = acquire_lock(BACKUP_DIR / LOCK_NAME)
    if lock_fd is None:
        log.warning("another backup is still running; skipping this run")
        sys.exit(ALREADY_RUNNING)
    atexit.register(lock_fd.close)

    for path in skipped:
        log.warning(f"skipping {path}: not an existing directory")
    for path in unparseable:
        log.warning(f"not excluding {path}: glob characters in the name")

    if git_patterns:
        log.info(f"excluding {len(git_patterns)} gitignored paths")

    status = probe_repo(restic, env)
    if status == REPO_NOT_FOUND:
        log.info(f"initializing repo {repo}...")
        if run([restic, "init"], env) != 0:
            sys.exit("ERROR: restic init failed")
    elif status == REPO_LOCKED:
        log.warning("repo is locked; continuing (the backup retries the lock itself)")
    elif status != 0:
        sys.exit(f"ERROR: checking repository failed (exit {status})")

    # Sweep orphaned locks up front: waiting out --retry-lock first would stall the run for that
    # long before we ever got around to noticing the lock was dead.
    clear_stale_locks(restic, env, HOST)

    log.info(f"backing up {HOST}: {[str(p) for p in paths]}")
    cmd = [
        restic,
        "backup",
        *(str(p) for p in paths),
        "--tag",
        HOST,
        "--host",
        HOST,
        "--one-file-system",
        "--skip-if-unchanged",
        "--retry-lock",
        args.retry_lock,
    ]
    if exclude_file:
        cmd += ["--exclude-file", str(exclude_file)]
    cmd += [arg for pattern in git_patterns for arg in ("--exclude", pattern)]

    status = retry_past_lock(restic, cmd, env, log, HOST)
    if status == BACKUP_INCOMPLETE:
        log.warning("some files could not be read; the snapshot is incomplete")
    elif status != 0:
        sys.exit("ERROR: restic backup failed")

    if not args.keep_days:
        log.info("no retention flags given; skipping forget")
        log.info("done")
        return

    log.info(f"applying retention (--keep-daily {args.keep_days})...")
    cmd = [
        restic,
        "forget",
        "--tag",
        HOST,
        "--group-by",
        "host",
        "--keep-daily",
        str(args.keep_days),
        "--prune",
        "--retry-lock",
        args.retry_lock,
    ]
    if retry_past_lock(restic, cmd, env, log, HOST) != 0:
        # The snapshot is already saved, so this is not a backup failure: say so plainly and exit
        # distinctly, or cron reports a lost backup every night.
        log.error("retention failed; snapshots will accumulate until the next run")
        sys.exit(RETENTION_FAILED)

    log.info("done")


if __name__ == "__main__":
    main()

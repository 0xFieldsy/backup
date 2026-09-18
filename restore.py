#!/usr/bin/env python3
"""
Restore from the restic repository written by backup.py.

Usage:
  restore.py -t DIR [PATH ...]    restore PATHs (default: everything) into DIR
  restore.py -s ID                snapshot to restore (default: latest for this host)
  restore.py -e PATTERN           exclude PATTERN from the restore (repeatable)
  restore.py --bucket NAME        R2 bucket (default: backup-<host selected by --host>)
  restore.py --path PATH          local repo path for testing (bypasses CF keys)
  restore.py --host NAME          restore another machine's snapshots
  restore.py --force              allow restoring into / or $HOME
  restore.py --list               list snapshots for this host
  restore.py --dry-run            print resolved config and the restic command
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # find lib via a symlink

from lib.config import HOST, load_env_file, resolve_repo
from lib.log import setup_logging
from lib.restic import (
    REPO_NOT_FOUND,
    find_restic,
    list_snapshots,
    probe_repo,
    restic_env,
    run,
)


def check_target(target: Path, force: bool) -> None:
    """Restoring writes absolute snapshot paths under target; keep it off / and $HOME."""
    protected = {Path("/"), Path.home().resolve()}
    if target in protected and not force:
        sys.exit(f"ERROR: refusing to restore into {target} (pass --force to override)")
    if target.exists() and not target.is_dir():
        sys.exit(f"ERROR: {target} exists and is not a directory")


def main() -> None:
    ap = argparse.ArgumentParser(description="restore a restic snapshot written by backup.py")
    ap.add_argument(
        "paths",
        nargs="*",
        metavar="PATH",
        help="paths inside the snapshot to restore (absolute; default: everything)",
    )
    ap.add_argument("-l", "--list", action="store_true", help="list snapshots and exit")
    ap.add_argument(
        "-t",
        "--target",
        metavar="DIR",
        help="directory to restore into (created if missing)",
    )
    ap.add_argument(
        "-s",
        "--snapshot",
        metavar="ID",
        default="latest",
        help="snapshot to restore (default: latest); explicit IDs ignore host/tag filters",
    )
    ap.add_argument(
        "-e",
        "--exclude",
        metavar="PATTERN",
        action="append",
        default=[],
        help="exclude PATTERN from the restore (repeatable)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="print resolved config and the restic command, change nothing",
    )
    ap.add_argument(
        "--host",
        metavar="NAME",
        default=HOST,
        help=f"host whose snapshots to restore (default: {HOST})",
    )
    ap.add_argument(
        "--bucket",
        metavar="NAME",
        help="R2 bucket name (default: backup-<host selected by --host>)",
    )
    ap.add_argument(
        "--path",
        metavar="PATH",
        help="local restic repo path (for testing)",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="allow restoring into / or $HOME",
    )
    ap.add_argument(
        "--verify",
        action="store_true",
        help="re-read restored files and compare them against the snapshot",
    )
    args = ap.parse_args()

    if not args.list and not args.target:
        ap.error("--target is required (restore to a scratch directory, not over $HOME)")

    load_env_file()
    bucket = args.bucket
    if not args.path and bucket is None:
        bucket = f"backup-{args.host}"
    repo = resolve_repo(args.path, bucket)
    env = restic_env(repo)

    if not args.list:
        target = Path(args.target).expanduser().resolve()
        check_target(target, args.force)
        cmd = [
            "restic",
            "restore",
            args.snapshot,
            "--tag",
            args.host,
            "--host",
            args.host,
            "--target",
            str(target),
        ]
        for item in args.paths:
            cmd += ["--include", item]
        for pattern in args.exclude:
            cmd += ["--exclude", pattern]
        if args.verify:
            cmd.append("--verify")

    if args.dry_run:
        print(f"repo: {repo}")
        if args.list:
            print(f"operation: restic snapshots --tag {args.host} --json")
            return
        print(f"target: {target}")
        print(f"snapshot: {args.snapshot} (host {args.host})")
        print(f"include: {args.paths or '(everything)'}")
        print(f"exclude: {args.exclude or '(none)'}")
        print(f"operation: {' '.join(cmd)}")
        return

    restic = find_restic()

    if args.list:
        list_snapshots(restic, args.host, env)

    log = setup_logging("restore.log")
    cmd[0] = restic

    status = probe_repo(restic, env, args.host)
    if status == REPO_NOT_FOUND:
        sys.exit(f"ERROR: repository {repo} does not exist")
    elif status != 0:
        sys.exit(f"ERROR: checking repository failed (exit {status})")

    target.mkdir(parents=True, exist_ok=True)
    if run(cmd, env) != 0:
        sys.exit("ERROR: restic restore failed")

    log.info(f"restored into {target}")


if __name__ == "__main__":
    main()

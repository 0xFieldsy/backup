# AGENTS.md / CLAUDE.md

Push-based `restic` backups to Cloudflare R2. Every VM runs its own backup: this directory can be cloned or copied anywhere on each machine, and each machine lists the folders it wants backed up in `backup.txt` (or as positional arguments).

## Layout

Runtime files live beside `backup.py` using the script's resolved directory, independently of the working directory. Runtime files are git-ignored and kept separately on each host.

- `lib/` — code shared by both scripts, imported as `lib.*`; keep it beside them
  * `lib/config.py` — `BACKUP_DIR`, `ENV_FILE`, `HOST`, `.env` loading, repo/credential resolution
  * `lib/restic.py` — restic environment, subprocess plumbing, snapshot listing and repo probing
  * `lib/gitignore.py` — turns each source's `.gitignore` rules into restic excludes
  * `lib/log.py` — stdout and file logging (`setup_logging("backup.log")` / `("restore.log")`)
- `.env` — `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_API_TOKEN_ID`, and one of `RESTIC_PASSWORD`, `RESTIC_PASSWORD_FILE`, or `RESTIC_PASSWORD_COMMAND`; not required if set in shell environment (useful for cron)
- `.lock` — permanent zero-byte file, created on demand for a `flock` guard
- `backup.log` — appended during backup runs (also streamed to stdout); dry runs and listing do not write it
- `backup.py` — the backup script
- `backup.txt` — optional per-host list of folders to back up (absolute, relative to the working directory, or `~/...`)
- `exclude.txt` — optional restic exclude patterns for one-off folders beyond Git's ignore rules
- `restore.log` — same, for restore runs
- `restore.py` — the restore script

## Usage

### Backup

`backup.py [PATH ...]` — back up the given folders plus anything in `--backup-file`, then optionally apply retention.

- `-b, --backup-file FILE` — also read folders from `FILE`, one per line, using the same path rules as positional arguments
- `--exclude-file FILE` — pass `FILE` unchanged to restic as `--exclude-file`
- `--bucket NAME` — R2 bucket (default `backup-<hostname>`)
- `--path PATH` — use a local repo at `PATH` instead of R2; skips the Cloudflare credentials entirely (useful for testing)
- `--keep-days N` — keep `N` daily snapshots (default `7`); `N` must be non-negative; `--keep-days 0` skips `forget --prune` entirely
- `--retry-lock DURATION` — how long restic waits for a locked repo before giving up (default `5m`; `0` for no retries)
- `-l, --list` — list this host's snapshots as JSON and exit (pipe to `jq`)
- `--dry-run` — print the resolved repo, paths, excludes and retention, then change nothing
- `--no-gitignore` — turn off the `.gitignore`-derived excludes described below

For each source inside a Git repository, we ask Git for ignored entries using `git ls-files --ignored`. Git handles nested `.gitignore` files, negation, `.git/info/exclude`, and global ignore rules; we do not parse ignore files or recursively discover repositories ourselves. A source outside a Git repository contributes no Git-derived excludes, even if it contains repositories.

Ignored entries are passed to restic as absolute-path `--exclude` patterns, in addition to any one-off patterns in `--exclude-file`. Paths whose names contain glob characters (`*?[`) are skipped with a warning instead of being excluded. `--dry-run` prints the generated patterns. `--no-gitignore` disables this integration.

In backup files, blank lines are skipped and surrounding whitespace is stripped. Comments are not supported; `#` is literal. Exclude files are passed unchanged to restic, which handles their parsing. We do not read or expand their contents. Use absolute paths or restic patterns instead of `~/...`. `--dry-run` prints the exclude file's path.

Backup source folders accept absolute paths, relative paths (including `..`), and `~/...` for the user's home directory. Relative source paths resolve from the working directory, not the script's directory. Backup sources **are** expanded and resolved to absolute paths before being passed to restic. A source that is not an existing directory is skipped with a logged warning rather than failing the run. In `backup.txt`, write paths without quotes, even when they contain spaces; shell variables such as `$HOME` are **not** expanded.

### Restore

`restore.py -t DIR [PATH ...]` — restore a snapshot, or just the given folders out of it, into `DIR`. Repo, credentials, and `.env` handling are identical to `backup.py` — both scripts use `lib/`, which must stay beside them.

- `PATH ...` — restore only these paths from the snapshot, passed to restic as `--include`; paths inside a snapshot are absolute, so write them as they appeared on the backed-up machine (`/home/<user>/project`). Default is the whole snapshot
- `-t, --target DIR` — where to restore (created if missing); **required**
- `-s, --snapshot ID` — snapshot to restore (default `latest`); host/tag filters apply only to `latest`
- `-e, --exclude PATTERN` — restic `--exclude` applied to the restore; repeatable
- `--host NAME` — restore another machine's snapshots (default: this host); also selects the default bucket `backup-NAME`
- `--verify` — re-read the restored files and compare them against the snapshot
- `--force` — allow `--target /` or `--target $HOME`, which are refused otherwise
- `--bucket NAME` — override the default bucket selected by `--host`
- `--path` / `-l, --list` / `--dry-run` — as in `backup.py`

```sh
python3 "/path/to/backups/restore.py" -l
python3 "/path/to/backups/restore.py" -t /tmp/restore /home/<user>/project
```

Restored files land under the target with their full original path (`/tmp/restore/home/<user>/project/...`). Restore to a scratch target (`/tmp/restore`) and move files into place rather than restoring over `$HOME`.

## Configuration

Config comes from the environment first, then `.env` next to the script (existing environment variables win). That matters for cron, which runs with a minimal environment.

- `CLOUDFLARE_ACCOUNT_ID` / `CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_API_TOKEN_ID` — required unless `--path` is used
- `RESTIC_PASSWORD` / `RESTIC_PASSWORD_FILE` / `RESTIC_PASSWORD_COMMAND` — optional; if not set, restic prompts for the password interactively (once per `restic` invocation), or fails without a TTY (like cron).
  * Set exactly one: `RESTIC_PASSWORD_FILE` and `RESTIC_PASSWORD_COMMAND` each silently override `RESTIC_PASSWORD`, and setting those two together is a fatal error.

## Cloudflare R2

R2's S3 API is addressed directly; restic's S3 credentials are derived from the Cloudflare API token:

- `AWS_ACCESS_KEY_ID` = `CLOUDFLARE_API_TOKEN_ID` (token id)
- `AWS_SECRET_ACCESS_KEY` = `sha256(CLOUDFLARE_API_TOKEN)` (hex)
- `AWS_DEFAULT_REGION` = `auto`

## How restic works

`restic` is a content-addressed, encrypted backup program. Every snapshot is an immutable point-in-time copy of a directory tree; files are deduplicated, so repeated snapshots cost little.

- `restic init` creates the repository. The script probes with `restic snapshots` and only inits when it returns exit code 10 (repository does not exist); exit code 11 (already locked) is logged and the run continues, since the lock sweep and `--retry-lock` handle it. Other failures stop the backup with restic's diagnostic.
- `restic backup <paths> --tag H --host H --one-file-system --skip-if-unchanged` records one snapshot covering all the host's paths. `--exclude-file` is passed through unchanged; generated `.gitignore` excludes are appended as individual `--exclude` flags.
- Excludes are matched as path suffixes, so an absolute pattern like `/home/x/proj/node_modules` prunes that directory and everything under it. `*` does not cross `/`, `**` matches zero or more directories, a leading `/` anchors to the filesystem root (not the backup root), and there is no negation: `!` patterns are meaningless to restic.
- `restic forget --tag H --group-by host --keep-daily N --prune` drops snapshots outside retention and reclaims space. Retention applies across all backup path sets for each host, so changing paths does not create a separate retention group. Runs by default (7 daily); pass `--keep-days 0` to let snapshots accumulate instead.
- Retention needs an exclusive repo lock, so it is the step that a leftover lock breaks. Because the snapshot is already saved by then, a failed `forget` is **not** a failed backup: it logs `retention failed` and exits `4`, where a failed `backup` exits `1`. Cron should treat the two differently — exit `4` is _look at it tomorrow_, exit `1` is _there is no backup_. Exit `5` means the run was skipped because another backup still held `.lock`; seeing it more than once in a row means a run is wedged. restic's exit `3` (some files unreadable, snapshot still created) is logged as a warning and retention still runs.

### Repository locks

restic writes a lock object per running command and deletes it on exit; a killed run (`kill -9`, OOM, a reboot mid-backup) leaves one behind. restic is supposed to ignore such a lock, but its staleness test is `kill(pid, 0)` plus a 30-minute grace period, which calls a lock live whenever the pid has since been recycled. Every later `forget --prune` fails until someone runs `restic unlock` by hand.

`clear_stale_locks()` in `lib/restic.py` sweeps those locks once per run, before the backup, rather than after burning the `--retry-lock` window. It removes locks only when **every** lock in the repo is simultaneously:

- taken on this host (`hostname` matches),
- older than `STALE_LOCK_AGE` (1 hour),
- and orphaned — `/proc/<pid>` is gone, or it is not a `restic` process, or its real uid differs from the lock's. The comm and uid checks are what catch the recycled pid that restic's own check misses.

Any lock that fails a test, or that cannot be read, aborts the sweep and leaves the repo untouched; a genuinely running restic is never unlocked out from under itself. The liveness check is what makes this safe, not `.lock`. `restore.py` holds no flock, and its lock survives the sweep because the check sees a live `restic` owned by the lock's uid. `--retry-lock` still covers the honestly-concurrent case, and `retry_past_lock()` retries once if a command hits exit `11` anyway.

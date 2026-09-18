# backup

Backup to Cloudflare [R2](https://www.cloudflare.com/products/r2/) using [restic](https://github.com/restic/restic).

Clone this onto a machine, list the folders you want kept, and add a cron job or systemd timer. Each machine gets its own R2 bucket (`backup-<hostname>`).

Snapshots are deduplicated and everything is encrypted before it leaves the machine.

## Requirements

- `git`, `python3`, and `restic` in your `PATH`
- A Cloudflare account with R2 enabled and an R2 API token

## Setup

**1. Clone anywhere on the machine.** Like `~/.backup`:

```sh
gh repo clone 0xFieldsy/backup ~/.backup
```

**2. List what to back up.** One folder per line in `backup.txt`:

```
/home/me/projects
/home/me/Documents
/etc
```

Prefer absolute paths or tilde (`~`) for home. Relative paths are relative to the working directory, not the location of the script. Note that `$HOME` and other shell variables are _not_ expanded.

**3. Add any one-off exclusions.** Git ignore rules are applied automatically when a source is inside a Git repository. Use the optional `exclude.txt` for additional folders you don't want backed up, with patterns passed straight to restic:

```
/home/me/Documents/scratch
/home/me/projects/demo/local-data
```

The exclude file is passed unchanged to restic via `--exclude-file`; restic handles its parsing. Use absolute paths or restic patterns, _not_ `~/...` (home-directory expansion is not performed). `--dry-run` shows the exclude file's path.

We ask Git itself which entries are ignored; Git handles nested `.gitignore` files, negation, repository-local excludes, and global ignore rules. We don't discover repositories under a source directory. For example, backing up `/home/me/projects` when it isn't a Git repository won't apply the ignore rules of repositories beneath it. List `/home/me/projects/app` and your other repositories individually in `backup.txt` to use their rules. Pass `--no-gitignore` to disable Git-derived exclusions.

**4. Add your R2 credentials.** Create an R2 API token, then put it in `~/.backup/.env`:

> [!NOTE]
> The token permission decides whether you have to create the bucket yourself. Each machine backs up to a bucket named `backup-<hostname>`, and on the first run restic tries to create it. Only **Admin Read & Write** can create a bucket.

```sh
CLOUDFLARE_ACCOUNT_ID=...
CLOUDFLARE_API_TOKEN=...
CLOUDFLARE_API_TOKEN_ID=...
```
**5. Create the repository password.**

> [!NOTE]
> Repository refers to a Restic backup, not a Git repo.

```sh
openssl rand -base64 32
```

Add it to the same `.env` file:

```sh
RESTIC_PASSWORD=<the generated password>
```

> [!WARNING]
> **Store a copy of this password somewhere off the machine** like a password manager. If you lose the password, the backups are permanently unreadable.

**6. Check it.** A dry run prints what _would_ happen and changes nothing:

```sh
python3 ~/.backup/backup.py --backup-file ~/.backup/backup.txt --dry-run
```

Then do a real run. The first one creates the repository and uploads everything; later runs only send what changed.

```sh
python3 ~/.backup/backup.py --backup-file ~/.backup/backup.txt --exclude-file ~/.backup/exclude.txt
```

**7. Schedule it.** Using `crontab -e`:

```sh
@daily python3 "/home/<user>/.backup/backup.py" --backup-file "/home/<user>/.backup/backup.txt" --exclude-file "/home/<user>/.backup/exclude.txt"
```

Cron runs with almost no environment, which is why the credentials live in `.env` rather than in your shell profile.

Old snapshots are pruned automatically: seven daily snapshots are kept by default. Change that with `--keep-days N`, or pass `--keep-days 0` to keep everything forever.

Overlapping backup runs are guarded by a lock on `.lock` beside the script; a run that finds the lock held exits with code `5`. The file stays in place between runs. Its presence does not mean a backup is active, and it should not be deleted: the operating system releases the lock automatically when the process exits, even after a crash.

## Restoring

List snapshots to find the one you want:

```sh
python3 ~/.backup/restore.py --list | jq .[]
```

Restore one folder from the most recent snapshot:

```sh
python3 ~/.backup/restore.py --target /tmp/restore /home/me/project
```

Restore everything, from a specific snapshot:

```sh
python3 ~/.backup/restore.py --target /tmp/restore --snapshot 797d68bd
```

Leave parts of a snapshot behind with `-e/--exclude`, which takes a restic pattern and can be repeated:

```sh
python3 ~/.backup/restore.py --target /tmp/restore /home/me/project -e node_modules -e '*.log'
```

Files land under the target with their full original path, so the example above gives you `/tmp/restore/home/me/project/...`.

Quote exclude patterns containing `*`, or the shell expands them against the current directory before restic sees them.

Add `--verify` to have restic re-read the restored files and check them against the snapshot.

To restore another machine's backups, pass `--host NAME`. This selects the default bucket `backup-NAME` and filters `latest` by that host and tag. Use `--bucket NAME` to override the bucket, or `--path PATH` for a local repository.

Host/tag filters apply to `latest`; an explicit `--snapshot ID` restores that snapshot regardless of its host or tags within the selected repository.

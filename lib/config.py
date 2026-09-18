"""Paths, host identity, and configuration shared by backup.py and restore.py."""

import hashlib
import os
import socket
import sys
from pathlib import Path

BACKUP_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BACKUP_DIR / ".env"
HOST = socket.gethostname().split(".")[0]


def load_env_file(path: Path = ENV_FILE) -> None:
    """KEY=VALUE lines; existing environment variables are NOT overridden."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        line = line.removeprefix("export ")
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


def need(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"ERROR: {name} not set (environment or .env)")
    return value


def resolve_repo(path_arg: str | None, bucket_arg: str | None) -> str:
    """Local repo path, or an R2 bucket URL with the S3 credentials put into the environment."""
    if path_arg:
        if bucket_arg:
            sys.exit("ERROR: --bucket is ignored when --path is set")
        return str(Path(path_arg).expanduser())
    account = need("CLOUDFLARE_ACCOUNT_ID")
    token = need("CLOUDFLARE_API_TOKEN")
    token_id = need("CLOUDFLARE_API_TOKEN_ID")
    os.environ["AWS_ACCESS_KEY_ID"] = token_id
    os.environ["AWS_SECRET_ACCESS_KEY"] = hashlib.sha256(token.encode()).hexdigest()
    os.environ["AWS_DEFAULT_REGION"] = "auto"
    return f"s3:https://{account}.r2.cloudflarestorage.com/{bucket_arg or f'backup-{HOST}'}"

"""restic exclude patterns derived from .gitignore files.

git is the source of truth: its `ls-files --others --ignored --exclude-standard --directory` output
already has gitignore negation, nesting and precedence applied, and collapses whole ignored
directories into one entry. Sources that are not inside a git repository contribute nothing.
"""

import subprocess
from pathlib import Path

GLOB_CHARS = set("*?[")


def _run(cmd: list, cwd: Path) -> str:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return ""
    return proc.stdout


def _git_toplevel(src: Path) -> Path | None:
    out = _run(["git", "-C", str(src), "rev-parse", "--show-toplevel"], src)
    out = out.strip()
    return Path(out) if out else None


def _git_ignored(src: Path, top: Path) -> list:
    out = _run(
        [
            "git",
            "-C",
            str(src),
            "ls-files",
            "--others",
            "--ignored",
            "--exclude-standard",
            "--directory",
            "--full-name",
            "-z",
        ],
        src,
    )
    found = []
    for entry in out.split("\0"):
        entry = entry.strip()
        if entry:
            found.append(top / entry)
    return found


def gitignore_patterns(sources: list) -> tuple:
    """Return a tuple of (restic exclude patterns, unparseable paths) for the source dirs."""
    patterns, skipped = [], []
    for src in sources:
        top = _git_toplevel(src)
        if not top:
            continue
        for path in _git_ignored(src, top):
            path = path.resolve()
            if path == src:
                continue
            if not path.is_relative_to(src):
                continue
            if any(c in GLOB_CHARS for c in str(path)):
                skipped.append(path)
                continue
            patterns.append(str(path))
    return sorted(set(patterns)), skipped

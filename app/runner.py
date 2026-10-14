"""Shared entry point for cron jobs: directory setup, a lock against overlapping runs, git commit."""

import fcntl
import subprocess
import sys
from collections.abc import Callable

from .config import Config

# Pending inbox entries are a queue, not content; inbox/failed/ is kept for inspection.
# Obsidian rewrites its workspace file on every click.
GITIGNORE_LINES = [".worker.lock", "*.tmp", "inbox/*.json", ".obsidian/workspace*.json"]


def run_locked(cfg: Config, job: Callable[[], str]) -> int:
    """Run job() under the brain lock; job returns the commit message (empty = no commit)."""
    cfg.ensure_dirs()
    _ensure_gitignore(cfg)
    with open(cfg.brain / ".worker.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("another run is in progress, exiting")
            return 0
        message = job()
        if message:
            commit(cfg, message)
    return 0


def _ensure_gitignore(cfg: Config) -> None:
    path = cfg.brain / ".gitignore"
    lines = path.read_text().splitlines() if path.exists() else []
    missing = [line for line in GITIGNORE_LINES if line not in lines]
    if missing:
        path.write_text("\n".join(lines + missing) + "\n")


def commit(cfg: Config, message: str) -> None:
    """Commit everything in brain/ if it is a git repo and something changed. Never fails the run."""
    if not (cfg.brain / ".git").exists():
        return
    git = ["git", "-C", str(cfg.brain), "-c", "user.name=secondbrain", "-c", "user.email=secondbrain@localhost"]
    try:
        subprocess.run(git + ["add", "-A"], check=True, capture_output=True, text=True)
        if subprocess.run(git + ["diff", "--cached", "--quiet"]).returncode == 0:
            return
        subprocess.run(git + ["commit", "-q", "-m", message], check=True, capture_output=True, text=True)
        print(f"committed: {message}")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        detail = getattr(e, "stderr", "") or str(e)
        print(f"git commit failed: {detail.strip()[:300]}", file=sys.stderr)

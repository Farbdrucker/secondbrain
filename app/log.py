"""log.md: append-only. Every entry starts with `## [YYYY-MM-DD] <action> | <subject>`."""

from datetime import datetime
from pathlib import Path


def append(brain: Path, when: datetime, action: str, subject: str, details: str) -> None:
    subject = " ".join(subject.split())[:120]
    details = " ".join(details.split())
    with (brain / "log.md").open("a", encoding="utf-8") as f:
        f.write(f"## [{when:%Y-%m-%d}] {action} | {subject}\n- {details}\n\n")

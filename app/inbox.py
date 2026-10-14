"""Inbox entries: one JSON file per received message. The directory is the queue."""

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from .fsutil import write_atomic

KINDS = ("youtube", "instagram", "web", "pdf", "note")

URL_RE = re.compile(r"https?://[^\s<>\"']+")


@dataclass
class Entry:
    id: str
    received_at: str  # ISO 8601 with offset; time the message was sent, not processed
    kind: str
    payload: str  # URL, note text, or path of a downloaded PDF (relative to brain/)
    attempts: int = 0
    comment: str = ""  # text sent alongside a link or file, if any
    file_name: str = ""  # original name of an uploaded file

    @property
    def received(self) -> datetime:
        return datetime.fromisoformat(self.received_at)


def make_id(sent: datetime, seq: int | str) -> str:
    return f"{sent:%Y%m%d-%H%M%S}-{seq}"


def kind_for_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.").removeprefix("m.")
    if host in ("youtube.com", "youtu.be", "music.youtube.com") or host.endswith(".youtube.com"):
        return "youtube"
    if host == "instagram.com" or host.endswith(".instagram.com"):
        return "instagram"
    return "web"


def classify_text(text: str) -> list[tuple[str, str, str]]:
    """Split a text message into (kind, payload, comment) items.

    Every URL becomes its own item; the remaining text is attached as comment.
    Text without any URL is a note.
    """
    urls = [u.rstrip(".,;:!?)]}") for u in URL_RE.findall(text)]
    if not urls:
        return [("note", text.strip(), "")]
    comment = URL_RE.sub("", text).strip()
    seen: list[str] = []
    for u in urls:
        if u not in seen:
            seen.append(u)
    return [(kind_for_url(u), u, comment) for u in seen]


def write(inbox: Path, entry: Entry) -> Path:
    path = inbox / f"{entry.id}.json"
    write_atomic(path, json.dumps(asdict(entry), ensure_ascii=False, indent=2) + "\n")
    return path


def read(path: Path) -> Entry:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Entry(**{k: v for k, v in data.items() if k in Entry.__dataclass_fields__})


def pending(inbox: Path) -> list[tuple[Path, Entry]]:
    items = [(p, read(p)) for p in inbox.glob("*.json")]
    return sorted(items, key=lambda pe: (pe[1].received, pe[1].id))

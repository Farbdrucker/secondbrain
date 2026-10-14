"""Links that are saved without fetching or LLM, tagged for reading later.

Used for sites that block automated access (archive.today and its mirrors by default) and as
the fallback when a web page cannot be downloaded, so no link is ever lost.
"""

import os
from urllib.parse import urlparse

DEFAULT_DOMAINS = "archive.ph,archive.today,archive.is,archive.li,archive.md,archive.vn,archive.fo"


def _csv(name: str, default: str) -> list[str]:
    return [s.strip().lower() for s in os.environ.get(name, default).split(",") if s.strip()]


def domains() -> list[str]:
    return _csv("READ_LATER_DOMAINS", DEFAULT_DOMAINS)


def tags() -> list[str]:
    """Status tags, not topics: no topic pages, never merged, not offered to the LLM as vocabulary."""
    return _csv("READ_LATER_TAGS", "lesen,todo")


def matches(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return any(host == d or host.endswith("." + d) for d in domains())


def title_for(url: str, comment: str, known_title: str = "") -> str:
    """The owner's comment wins, then a title known without fetching (HN), then the URL."""
    for text in (comment, known_title):
        if text.strip():
            return " ".join(text.split())[:80]
    p = urlparse(url)
    return f"Read later: {(p.hostname or '').removeprefix('www.')}{p.path.rstrip('/')}"[:80]

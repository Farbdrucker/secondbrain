import re
from pathlib import Path

from slugify import slugify

GERMAN = [("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue"), ("ß", "ss")]


def slug(text: str, max_length: int = 0) -> str:
    """Lowercase ASCII slug with hyphens; German umlauts become ae/oe/ue/ss."""
    for a, b in GERMAN:
        text = text.replace(a, b)
    return slugify(text, max_length=max_length, word_boundary=bool(max_length), separator="-")


def normalize_tags(tags: list[str], limit: int = 6) -> list[str]:
    out: list[str] = []
    for t in tags:
        s = slug(str(t), max_length=40)
        if s and s not in out:
            out.append(s)
    return out[:limit]


def filename(date: str, tags: list[str], title: str) -> str:
    """YYYY-MM-DD_<tag1>-<tag2>_<title-slug>.md"""
    tag_part = "-".join(tags[:2]) or "untagged"
    title_part = slug(title, max_length=50) or "untitled"
    return f"{date}_{tag_part}_{title_part}.md"


def unique_path(directory: Path, name: str, keep: Path | None = None) -> Path:
    """Append -2, -3, ... to avoid clobbering another entry's file. `keep` may be reused."""
    stem = re.sub(r"\.md$", "", name)
    path = directory / name
    n = 2
    while path.exists() and path != keep:
        path = directory / f"{stem}-{n}.md"
        n += 1
    return path

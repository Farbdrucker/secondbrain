"""Wiki notes: Markdown with YAML frontmatter, built by code from the LLM's JSON."""

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class Note:
    path: Path
    meta: dict


def _split(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    data = yaml.safe_load(text[4:end]) or {}
    return (data if isinstance(data, dict) else {}), text[end + 5:]


def read_frontmatter(path: Path) -> dict:
    return _split(path.read_text(encoding="utf-8"))[0]


def read_body(path: Path) -> str:
    return _split(path.read_text(encoding="utf-8"))[1]


def dump_frontmatter(meta: dict) -> str:
    """One key per line; lists inline (tags: [a, b]) as Obsidian and grep expect."""
    fm = "".join(yaml.safe_dump({k: v}, sort_keys=False, allow_unicode=True, width=1000,
                                default_flow_style=None if isinstance(v, list) else False)
                 for k, v in meta.items())
    return f"---\n{fm}---\n"


def all_notes(wiki: Path) -> list[Note]:
    notes = []
    for p in sorted(wiki.glob("*.md")):
        try:
            meta = read_frontmatter(p)
        except yaml.YAMLError:
            continue
        if meta.get("id"):
            notes.append(Note(p, meta))
    return notes


def tag_counts(notes: list[Note]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for n in notes:
        for t in n.meta.get("tags") or []:
            counts[str(t)] = counts.get(str(t), 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def render(meta: dict, summary: str, key_points: list[str], note_text: str = "", comment: str = "") -> str:
    parts = [dump_frontmatter(meta), f"## Summary\n{summary.strip()}\n"]
    if key_points:
        parts.append("## Key points\n" + "\n".join(f"- {p.strip()}" for p in key_points) + "\n")
    if note_text:
        parts.append(f"## Note\n{note_text.strip()}\n")
    if comment:
        parts.append(f"## Comment\n{comment.strip()}\n")
    if meta.get("source"):
        label = "Original link" if str(meta["source"]).startswith("http") else "Original file"
        links = f"[{label}]({meta['source']})"
        if meta.get("article"):
            links += f"\n[Linked article]({meta['article']})"
        parts.append(f"## Source\n{links}\n")
    return "\n".join(parts)

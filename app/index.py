"""index.md is generated from the frontmatter of all wiki notes; never edited by the LLM."""

from datetime import datetime
from pathlib import Path

from .fsutil import write_atomic
from .wiki import Note, all_notes, read_frontmatter


def _line(n: Note, brain: Path) -> tuple[str, str, str, str]:
    m = n.meta
    rel = n.path.relative_to(brain).as_posix()
    title = str(m.get("title") or n.path.stem).replace("[", "(").replace("]", ")")
    date = str(m.get("received_at", ""))[:10]
    return f"[{title}]({rel})", str(m.get("summary_short") or "").strip(), str(m.get("type", "")), date


def build(brain: Path, wiki: Path, now: datetime) -> str:
    notes = sorted(all_notes(wiki), key=lambda n: (str(n.meta.get("received_at", "")), str(n.meta["id"])),
                   reverse=True)
    out = ["# Index", "", f"Last updated: {now:%Y-%m-%d %H:%M} · {len(notes)} entries", ""]

    topics = []
    for page in sorted((brain / "topics").glob("*.md")):
        meta = read_frontmatter(page)
        topics.append(f"- [{meta.get('title') or page.stem}](topics/{page.name}) ({meta.get('entries', '?')})")
    if topics:
        out += ["## Topics", ""] + topics + [""]

    by_tag: dict[str, list[Note]] = {}
    for n in notes:
        for t in n.meta.get("tags") or []:
            by_tag.setdefault(str(t), []).append(n)

    out += ["## By tag", ""]
    for tag in sorted(by_tag):
        out += [f"### {tag}"]
        for n in by_tag[tag]:
            link, short, kind, date = _line(n, brain)
            desc = f": {short}" if short else ""
            out.append(f"- {link}{desc} ({kind}, {date})")
        out.append("")

    out += ["## Chronological (newest first)", ""]
    for n in notes:
        link, _, kind, date = _line(n, brain)
        out.append(f"- {date} · {link} · {kind}")
    return "\n".join(out) + "\n"


def write(brain: Path, wiki: Path, now: datetime) -> None:
    """Rewrite only if something besides the timestamp changed, so idle runs leave git clean."""
    path = brain / "index.md"
    new = build(brain, wiki, now)
    if path.exists():
        strip = lambda t: "\n".join(l for l in t.splitlines() if not l.startswith("Last updated:"))
        if strip(path.read_text(encoding="utf-8")) == strip(new):
            return
    write_atomic(path, new)

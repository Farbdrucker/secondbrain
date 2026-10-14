"""Plain substring search over the wiki, for the bot's /search command."""

from pathlib import Path

from .wiki import Note, all_notes


def search(wiki: Path, query: str, limit: int = 10) -> list[Note]:
    terms = [t for t in query.lower().split() if t]
    if not terms:
        return []
    scored = []
    for n in all_notes(wiki):
        text = n.path.read_text(encoding="utf-8").lower()
        if not all(t in text for t in terms):
            continue
        title = str(n.meta.get("title", "")).lower()
        tags = " ".join(str(t) for t in n.meta.get("tags") or [])
        score = sum(5 * (t in title) + 3 * (t in tags) + min(text.count(t), 10) for t in terms)
        scored.append((score, str(n.meta.get("received_at", "")), n))
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
    return [n for _, _, n in scored[:limit]]


def format_results(query: str, notes: list[Note]) -> str:
    if not notes:
        return f"Nothing found for “{query}”."
    lines = [f"{len(notes)} result(s) for “{query}”:"]
    for i, n in enumerate(notes, 1):
        m = n.meta
        lines.append(f"\n{i}. {m.get('title')} ({str(m.get('received_at', ''))[:10]}, {m.get('type')})")
        if m.get("summary_short"):
            lines.append(f"   {m['summary_short']}")
        lines.append(f"   #{' #'.join(str(t) for t in m.get('tags') or [])}")
        if str(m.get("source", "")).startswith("http"):
            lines.append(f"   {m['source']}")
    return "\n".join(lines)

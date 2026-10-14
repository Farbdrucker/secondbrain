"""Process the inbox once. Run from cron: docker compose run --rm -T worker"""

import shutil
import sys
from datetime import datetime
from pathlib import Path

from . import config, index, inbox, log, naming, notify, readlater, topics, wiki
from .config import Config
from .fsutil import write_atomic
from .llm import LLMUnavailable
from .runner import run_locked
from .sources import FetchError, Source, fetch
from .summarize import summarize

def main() -> int:
    cfg = config.load()

    def job() -> str:
        stats = run(cfg, summarize)
        return f"ingest {_now(cfg):%Y-%m-%d}: {stats['ok']} added, {stats['failed']} failed"

    return run_locked(cfg, job)


def run(cfg: Config, summarize_fn, llm=None) -> dict[str, int]:
    stats = {"ok": 0, "failed": 0, "skipped": 0}
    report: list[str] = []
    llm_down = False
    notes = wiki.all_notes(cfg.wiki)
    by_id = {str(n.meta["id"]): n.path for n in notes}
    tags = wiki.tag_counts(notes)

    for path, entry in inbox.pending(cfg.inbox):
        try:
            if entry.kind == "web" and readlater.matches(entry.payload):
                out = _write_read_later(cfg, entry, "site blocks automated access", by_id.get(entry.id))
            else:
                try:
                    source = _load_source(cfg, entry)
                except FetchError as e:
                    if entry.kind != "web":
                        raise
                    # Paywall, bot protection or a dead server: keep the link instead of retrying.
                    out = _write_read_later(cfg, entry, f"could not download ({e})", by_id.get(entry.id),
                                            title=e.title, article=e.article)
                else:
                    vocabulary = {t: c for t, c in tags.items() if t not in readlater.tags()}
                    result = summarize_fn(entry, source, vocabulary)
                    out = _write_note(cfg, entry, result, existing=by_id.get(entry.id), article=source.article)
        except LLMUnavailable as e:
            # Usage limit or outage: the rest would fail the same way. Retry next run.
            print(f"LLM unavailable, stopping this run: {e}", file=sys.stderr)
            stats["skipped"] += 1
            llm_down = True
            report.append(f"LLM unavailable, remaining entries wait for the next run ({e})")
            break
        except Exception as e:  # one bad entry must not stop the run
            stats["failed"] += 1
            if final := _fail(cfg, path, entry, e):
                report.append(final)
            continue

        by_id[entry.id] = out
        meta = wiki.read_frontmatter(out)
        for t in meta["tags"]:
            tags[t] = tags.get(t, 0) + 1
        log.append(cfg.brain, _now(cfg), "ingest", meta["title"],
                   f"id: {entry.id} · type: {entry.kind} · tags: {', '.join(meta['tags'])} · file: {out.name}")
        path.unlink()
        stats["ok"] += 1
        print(f"ok   {entry.id} -> {out.name}")
        report.insert(stats["ok"] - 1, f"+ {meta['title']} ({', '.join(meta['tags'][:3])})")

    if not llm_down:
        try:
            t = topics.update(cfg.brain, cfg.wiki, _now(cfg), llm)
            if t["updated"]:
                report.append("Topic pages updated: " + ", ".join(t["updated"]))
        except LLMUnavailable as e:
            report.append(f"Topic pages skipped, LLM unavailable ({e})")
    index.write(cfg.brain, cfg.wiki, _now(cfg))
    if report:
        notify.send("\n".join(report))
    print(f"done: {stats}")
    return stats


def _now(cfg: Config) -> datetime:
    return datetime.now(cfg.tz).replace(microsecond=0)


def _load_source(cfg: Config, entry: inbox.Entry) -> Source:
    """Raw text is saved before the LLM call; a retry reuses it instead of fetching again."""
    raw = cfg.raw / f"{entry.id}.txt"
    if raw.exists():
        text = raw.read_text(encoding="utf-8")
    else:
        source = fetch(entry, cfg.brain)
        text = source.text
        header = [f"Article: {source.article}"] if source.article else []
        if source.title_hint and not text.startswith("Title: "):
            header.insert(0, f"Title: {source.title_hint}")
        if header:
            text = "\n".join(header) + "\n\n" + text
        write_atomic(raw, text)
    # Title and article survive in the raw header, so a retry needs no new download.
    fields = {}
    for line in text.split("\n\n", 1)[0].splitlines():
        key, sep, value = line.partition(": ")
        if sep and key in ("Title", "Article"):
            fields.setdefault(key, value.strip())
    return Source(text, fields.get("Title", ""), fields.get("Article", ""))


def _write_note(cfg: Config, entry: inbox.Entry, result: dict, existing: Path | None, article: str = "") -> Path:
    title = " ".join(str(result["title"]).split())[:80] or "Untitled"
    tags = naming.normalize_tags(result.get("tags") or []) or ["untagged"]
    meta = {
        "id": entry.id,
        "type": entry.kind,
        "source": {"note": None, "pdf": f"../{entry.payload}"}.get(entry.kind, entry.payload),
        "article": article or None,
        "file_name": entry.file_name or None,
        "received_at": entry.received_at,
        "processed_at": _now(cfg).isoformat(),
        "title": title,
        "tags": tags,
        "summary_short": " ".join(str(result.get("summary_short", "")).split()),
        "raw": f"../raw/{entry.id}.txt",
    }
    meta = {k: v for k, v in meta.items() if v is not None}
    text = wiki.render(
        meta,
        summary=str(result.get("summary", "")),
        key_points=[str(p) for p in result.get("key_points") or []],
        note_text=entry.payload if entry.kind == "note" else "",
        comment=entry.comment,
    )
    name = naming.filename(entry.received.strftime("%Y-%m-%d"), tags, title)
    # If this id was already written (crash before the inbox file was removed), replace that file.
    target = naming.unique_path(cfg.wiki, name, keep=existing)
    write_atomic(target, text)
    if existing and existing != target:
        existing.unlink(missing_ok=True)
    return target


def _write_read_later(cfg: Config, entry: inbox.Entry, reason: str, existing: Path | None,
                      title: str = "", article: str = "") -> Path:
    """A note for a link that was not fetched or summarized. Raw text keeps lint and dedupe working."""
    write_atomic(cfg.raw / f"{entry.id}.txt", f"URL: {entry.payload}\nNot fetched: {reason}\n")
    title = readlater.title_for(entry.payload, entry.comment, title)
    tags = readlater.tags()
    meta = {
        "id": entry.id,
        "type": entry.kind,
        "source": entry.payload,
        "article": article or None,
        "received_at": entry.received_at,
        "processed_at": _now(cfg).isoformat(),
        "title": title,
        "tags": tags,
        "summary_short": "Saved to read later, not summarized.",
        "raw": f"../raw/{entry.id}.txt",
    }
    meta = {k: v for k, v in meta.items() if v is not None}
    text = wiki.render(meta, summary=f"Saved to read later. Not summarized: {reason}.", key_points=[],
                       comment=entry.comment)
    name = naming.filename(entry.received.strftime("%Y-%m-%d"), tags, title)
    target = naming.unique_path(cfg.wiki, name, keep=existing)
    write_atomic(target, text)
    if existing and existing != target:
        existing.unlink(missing_ok=True)
    return target


def _fail(cfg: Config, path: Path, entry: inbox.Entry, err: Exception) -> str:
    """Count the attempt; returns a message for the run summary once the entry gives up."""
    entry.attempts += 1
    reason = f"{type(err).__name__}: {err}"
    final = entry.attempts >= cfg.max_attempts
    log.append(cfg.brain, _now(cfg), "fail", entry.payload if entry.kind != "note" else entry.payload[:60],
               f"id: {entry.id} · type: {entry.kind} · reason: {reason[:300]} "
               f"(attempt {entry.attempts}/{cfg.max_attempts}{', moved to inbox/failed' if final else ''})")
    print(f"fail {entry.id}: {reason}", file=sys.stderr)
    inbox.write(cfg.inbox, entry)
    if final:
        shutil.move(path, cfg.failed / path.name)
        what = entry.payload if entry.kind != "note" else entry.payload[:80]
        return f"✗ Failed after {entry.attempts} attempts ({entry.kind}): {what} — {reason[:200]}"
    return ""


if __name__ == "__main__":
    sys.exit(main())

"""Weekly maintenance: remove duplicates, merge near-identical tags, report orphans, refresh topics.

Run from cron: docker compose run --rm -T worker python -m app.lint [--dry-run]
"""

import argparse
import hashlib
import sys
from datetime import datetime
from pathlib import Path

from . import config, index, log, naming, notify, readlater, topics, wiki
from .config import Config
from .fsutil import write_atomic
from .llm import LLMUnavailable, llm_json
from .runner import run_locked
from .urls import canonical

MAX_MERGES = 30

MERGE_SCHEMA = {
    "type": "object",
    "properties": {
        "merges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"from": {"type": "string"}, "to": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["from", "to", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["merges"],
    "additionalProperties": False,
}

MERGE_SYSTEM = (
    "You clean up the tag vocabulary of a personal knowledge wiki. Propose merges only for tags that "
    "mean the same thing: synonyms, spelling or language variants, singular/plural, abbreviations "
    "(e.g. ml -> machine-learning), or a tag that is a needlessly specific duplicate of another. "
    "Do NOT merge tags that are merely related (llm and prompt-engineering stay separate; running "
    "and ultramarathon stay separate). Merge into the tag that is more common or more standard. "
    "Tags are lowercase English with hyphens. Return an empty list when nothing should be merged; "
    "that is the normal case."
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")
    args = ap.parse_args()
    cfg = config.load()
    return run_locked(cfg, lambda: run(cfg, llm_json, dry_run=args.dry_run))


def run(cfg: Config, llm=None, dry_run: bool = False) -> str:
    llm = llm or llm_json
    now = datetime.now(cfg.tz).replace(microsecond=0)
    report: list[str] = []

    removed = dedupe(cfg, now, dry_run)
    if removed:
        report.append("Removed duplicates:\n" + "\n".join(f"- {t}" for t in removed))

    merged: list[str] = []
    try:
        merged = merge_tags(cfg, now, llm, dry_run)
        if merged:
            report.append("Merged tags:\n" + "\n".join(f"- {m}" for m in merged))
    except LLMUnavailable as e:
        report.append(f"Tag merge skipped (LLM unavailable: {e})")

    orphans = find_orphans(cfg)
    if orphans:
        report.append("Needs a look:\n" + "\n".join(f"- {o}" for o in orphans[:15])
                      + (f"\n- … and {len(orphans) - 15} more" if len(orphans) > 15 else ""))

    if not dry_run:
        try:
            t = topics.update(cfg.brain, cfg.wiki, now, llm)
            if t["updated"] or t["removed"]:
                report.append(f"Topic pages: {len(t['updated'])} updated, {len(t['removed'])} removed")
        except LLMUnavailable as e:
            report.append(f"Topic pages skipped (LLM unavailable: {e})")
        index.write(cfg.brain, cfg.wiki, now)

    summary = "\n\n".join(report) or "Nothing to do."
    print(("DRY RUN\n\n" if dry_run else "") + summary)
    if dry_run:
        return ""
    log.append(cfg.brain, now, "lint", f"{len(removed)} duplicates, {len(merged)} "
               f"tag merges, {len(orphans)} to check", summary.replace("\n", " · "))
    notify.send("Weekly lint\n\n" + summary)
    return f"lint {now:%Y-%m-%d}"


# --- duplicates ----------------------------------------------------------------

def dup_key(cfg: Config, note: wiki.Note) -> str | None:
    m = note.meta
    if m.get("type") == "pdf":
        pdf = cfg.raw / f"{m['id']}.pdf"
        return "pdf:" + hashlib.sha256(pdf.read_bytes()).hexdigest() if pdf.exists() else None
    if m.get("type") == "note":
        raw = cfg.raw / f"{m['id']}.txt"
        text = raw.read_text(encoding="utf-8") if raw.exists() else ""
        return "note:" + hashlib.sha256(" ".join(text.lower().split()).encode()).hexdigest() if text else None
    return "url:" + canonical(str(m["source"])) if m.get("source") else None


def dedupe(cfg: Config, now: datetime, dry_run: bool) -> list[str]:
    groups: dict[str, list[wiki.Note]] = {}
    for n in wiki.all_notes(cfg.wiki):
        key = dup_key(cfg, n)
        if key:
            groups.setdefault(key, []).append(n)
    removed = []
    for notes in groups.values():
        if len(notes) < 2:
            continue
        keep, *drop = sorted(notes, key=lambda n: (str(n.meta.get("received_at", "")), str(n.meta["id"])))
        # Same id twice is a copy of one entry (e.g. a Nextcloud conflict copy), not a duplicate
        # submission: find_orphans reports it, a person decides which version to keep.
        for n in [n for n in drop if n.meta["id"] != keep.meta["id"]]:
            removed.append(f"{n.meta.get('title')} ({n.meta['id']}, kept {keep.meta['id']})")
            if dry_run:
                continue
            n.path.unlink()
            for raw in cfg.raw.glob(f"{n.meta['id']}.*"):
                raw.unlink()
            log.append(cfg.brain, now, "dedupe", str(n.meta.get("title")),
                       f"removed id: {n.meta['id']} · duplicate of {keep.meta['id']} ({keep.path.name})")
    return removed


# --- tag merges ----------------------------------------------------------------

def merge_tags(cfg: Config, now: datetime, llm, dry_run: bool) -> list[str]:
    notes = wiki.all_notes(cfg.wiki)
    status = set(readlater.tags())
    counts = {t: c for t, c in wiki.tag_counts(notes).items() if t not in status}
    if len(counts) < 2:
        return []
    examples: dict[str, list[str]] = {}
    for n in notes:
        for t in n.meta.get("tags") or []:
            examples.setdefault(str(t), []).append(str(n.meta.get("title")))
    prompt = "Tags (usage count): example titles\n\n" + "\n".join(
        f"{t} ({c}): " + " | ".join(examples[t][:3]) for t, c in counts.items())
    proposals = llm(prompt, MERGE_SCHEMA, MERGE_SYSTEM).get("merges") or []
    mapping = {src: dst for src, dst in resolve(proposals, set(counts)).items() if dst not in status}
    if not mapping:
        return []

    done = []
    affected = {src: 0 for src in mapping}
    for n in notes:
        tags = [str(t) for t in n.meta.get("tags") or []]
        new = naming.normalize_tags([mapping.get(t, t) for t in tags])
        if new == tags:
            continue
        for t in tags:
            if t in mapping:
                affected[t] += 1
        if not dry_run:
            retag(cfg, n, new)
    reasons = {naming.slug(p["from"]): p.get("reason", "") for p in proposals}
    for src, dst in mapping.items():
        done.append(f"{src} → {dst} ({affected[src]} entries){': ' + reasons[src] if reasons.get(src) else ''}")
        if not dry_run:
            log.append(cfg.brain, now, "retag", f"{src} -> {dst}", f"{affected[src]} entries · {reasons.get(src, '')}")
    return done


def resolve(proposals: list[dict], existing: set[str]) -> dict[str, str]:
    """Validate merge proposals and collapse chains (a→b, b→c becomes a→c). Cycles are dropped."""
    raw = {}
    for p in proposals[:MAX_MERGES]:
        src, dst = naming.slug(str(p.get("from", ""))), naming.slug(str(p.get("to", "")))
        if src in existing and dst and src != dst:
            raw[src] = dst
    mapping = {}
    for src in raw:
        dst, seen = raw[src], {src}
        while dst in raw and dst not in seen:
            seen.add(dst)
            dst = raw[dst]
        if dst not in seen:
            mapping[src] = dst
    return mapping


def retag(cfg: Config, note: wiki.Note, tags: list[str]) -> Path:
    """Rewrite a note's tags; rename the file when its two main tags change."""
    meta = {**note.meta, "tags": tags}
    text = wiki.dump_frontmatter(meta) + wiki.read_body(note.path)
    name = naming.filename(str(meta.get("received_at", ""))[:10], tags, str(meta.get("title", "")))
    target = note.path
    if not note.path.name.startswith(name.removesuffix(".md")):
        target = naming.unique_path(cfg.wiki, name, keep=note.path)
    write_atomic(target, text)
    if target != note.path:
        note.path.unlink()
    return target


# --- orphans -------------------------------------------------------------------

def find_orphans(cfg: Config) -> list[str]:
    note_ids = {str(n.meta["id"]) for n in wiki.all_notes(cfg.wiki)}
    queued = {p.stem for p in cfg.inbox.glob("*.json")} | {p.stem for p in cfg.failed.glob("*.json")}
    out = []
    for raw in sorted(cfg.raw.iterdir()):
        if raw.is_file() and raw.stem not in note_ids and raw.stem not in queued:
            out.append(f"raw/{raw.name} has no wiki entry")
    for n in wiki.all_notes(cfg.wiki):
        if not (cfg.raw / f"{n.meta['id']}.txt").exists():
            out.append(f"wiki/{n.path.name} has no raw text")
    for p in sorted(cfg.failed.glob("*.json")):
        out.append(f"inbox/failed/{p.name} failed permanently")
    files_by_id: dict[str, list[str]] = {}
    for n in wiki.all_notes(cfg.wiki):
        files_by_id.setdefault(str(n.meta["id"]), []).append(n.path.name)
    for id_, names in sorted(files_by_id.items()):
        if len(names) > 1:
            out.append(f"id {id_} is in {len(names)} files (sync conflict copy?): {', '.join(sorted(names))}")
    for p in sorted(cfg.wiki.glob("*.md")):
        if not wiki.read_frontmatter(p).get("id"):
            out.append(f"wiki/{p.name} has no id in its frontmatter")
    return out


if __name__ == "__main__":
    sys.exit(main())

import json
import subprocess

from app import index, lint, runner, topics, wiki, worker
from app.search import format_results, search
from app.urls import canonical

from conftest import add, fake


def ingest(cfg, items, calls=None):
    """items: (id, kind, payload, title, tags, when)"""
    calls = [] if calls is None else calls
    for id_, kind, payload, title, tags, when in items:
        add(cfg, id_, kind, payload, when=when)
        if kind != "note":  # pretend the source was fetched already
            (cfg.raw / f"{id_}.txt").write_text(f"Title: {title}\n\ncontent")
        worker.run(cfg, fake(title=title, tags=tags), llm=topic_llm(calls))


def topic_llm(calls):
    def f(prompt, schema, system):
        calls.append(prompt)
        return {"title": "Topic", "overview": "Covers [1] and [2].", "themes": ["Theme from [1]"],
                "open_questions": []}
    return f


def test_canonical_urls():
    assert canonical("https://www.instagram.com/reel/DeV56CHMhSR/?obrf=abc") == "instagram:DeV56CHMhSR"
    assert canonical("https://instagram.com/reel/DeV56CHMhSR") == "instagram:DeV56CHMhSR"
    assert canonical("https://youtu.be/abc123?si=x") == canonical("https://www.youtube.com/watch?v=abc123&t=5")
    assert canonical("https://m.youtube.com/shorts/xyz") == "youtube:xyz"
    assert canonical("https://Example.org/a/?utm_source=x&id=2") == "example.org/a?id=2"


def test_topic_pages_cached_and_relinked(cfg):
    T = "2026-10-1{}T10:00:00+02:00"
    calls = []
    # The worker builds topic pages as soon as a tag reaches 3 entries.
    ingest(cfg, [(f"t-{i}", "note", f"text {i}", f"Note {i}", ["running", f"x{i}", "gear"], T.format(i))
                 for i in range(3)], calls)
    assert len(calls) == 2
    now = worker._now(cfg)
    page = (cfg.brain / "topics" / "running.md").read_text()
    assert "[\\[1\\]](../wiki/2026-10-12_running-x2_note-2.md)" in page and "[[" not in page
    assert "\ntag: running\n" in page  # newest first, citation linked
    assert "3. 2026-10-10 · [Note 0]" in page
    assert "Note 2" in calls[0] and "<entries>" in calls[0]

    # Unchanged entries: no LLM call.
    assert topics.update(cfg.brain, cfg.wiki, now, topic_llm(calls))["updated"] == [] and len(calls) == 2

    # Renamed file (e.g. after a tag merge): links follow without an LLM call.
    n = next(n for n in wiki.all_notes(cfg.wiki) if n.meta["id"] == "t-0")
    lint.retag(cfg, n, ["running", "new-main", "gear"])
    topics.update(cfg.brain, cfg.wiki, now, topic_llm(calls))
    assert len(calls) == 2
    assert "2026-10-10_running-new-main_note-0.md" in (cfg.brain / "topics" / "running.md").read_text()

    # Below the threshold the page disappears.
    n.path.parent.joinpath("2026-10-10_running-new-main_note-0.md").unlink()
    stats = topics.update(cfg.brain, cfg.wiki, now, topic_llm(calls))
    assert set(stats["removed"]) == {"running", "gear"} and not list((cfg.brain / "topics").glob("*.md"))


def test_index_lists_topics_and_skips_idle_rewrite(cfg):
    T = "2026-10-1{}T10:00:00+02:00"
    ingest(cfg, [(f"t-{i}", "note", f"text {i}", f"Note {i}", ["running", "a", "b"], T.format(i)) for i in range(3)])
    topics.update(cfg.brain, cfg.wiki, worker._now(cfg), topic_llm([]))
    index.write(cfg.brain, cfg.wiki, worker._now(cfg))
    idx = cfg.brain / "index.md"
    assert "## Topics" in idx.read_text() and "(topics/running.md) (3)" in idx.read_text()
    before = idx.stat().st_mtime_ns
    from datetime import timedelta
    index.write(cfg.brain, cfg.wiki, worker._now(cfg) + timedelta(hours=5))
    assert idx.stat().st_mtime_ns == before


def test_dedupe_keeps_oldest(cfg):
    ingest(cfg, [
        ("d-1", "instagram", "https://www.instagram.com/reel/AAA/?obrf=1", "Reel", ["a", "b", "c"], "2026-10-14T10:07:00+02:00"),
        ("d-2", "instagram", "https://www.instagram.com/reel/AAA/?igsh=2", "Reel", ["a", "b", "c"], "2026-10-14T10:20:00+02:00"),
        ("d-3", "note", "Same  text", "N", ["a", "b", "c"], "2026-10-14T11:00:00+02:00"),
        ("d-4", "note", "same text", "N", ["a", "b", "c"], "2026-10-14T12:00:00+02:00"),
        ("d-5", "web", "https://example.org/other", "Other", ["a", "b", "c"], "2026-10-14T13:00:00+02:00"),
    ])
    assert len(lint.dedupe(cfg, worker._now(cfg), dry_run=True)) == 2 and len(list(cfg.wiki.glob("*.md"))) == 5
    removed = lint.dedupe(cfg, worker._now(cfg), dry_run=False)
    ids = {n.meta["id"] for n in wiki.all_notes(cfg.wiki)}
    assert len(removed) == 2 and ids == {"d-1", "d-3", "d-5"}
    assert not list(cfg.raw.glob("d-2.*")) and not list(cfg.raw.glob("d-4.*"))


def test_resolve_merges():
    existing = {"ml", "machine-learning", "ai-tool", "ai-tools", "llm"}
    props = [{"from": "ml", "to": "machine-learning"}, {"from": "ai-tools", "to": "ai-tool"},
             {"from": "ai-tool", "to": "ai-tools"},  # cycle with the previous one: both dropped
             {"from": "unknown", "to": "llm"}, {"from": "llm", "to": "llm"}]
    assert lint.resolve(props, existing) == {"ml": "machine-learning"}
    assert lint.resolve([{"from": "a", "to": "b"}, {"from": "b", "to": "c"}], {"a", "b"}) == {"a": "c", "b": "c"}


def test_merge_tags_rewrites_and_renames(cfg):
    ingest(cfg, [("m-1", "note", "x", "First", ["ai-tools", "llm", "z"], "2026-10-14T10:00:00+02:00"),
                 ("m-2", "note", "y", "Second", ["llm", "ai-tool", "ai-tools"], "2026-10-14T11:00:00+02:00")])

    def llm(prompt, schema, system):
        assert "ai-tools (2)" in prompt
        return {"merges": [{"from": "ai-tools", "to": "ai-tool", "reason": "plural"}]}

    done = lint.merge_tags(cfg, worker._now(cfg), llm, dry_run=False)
    assert done == ["ai-tools → ai-tool (2 entries): plural"]
    notes = {n.meta["id"]: n for n in wiki.all_notes(cfg.wiki)}
    assert notes["m-1"].meta["tags"] == ["ai-tool", "llm", "z"]
    assert notes["m-1"].path.name == "2026-10-14_ai-tool-llm_first.md"
    assert notes["m-2"].meta["tags"] == ["llm", "ai-tool"]
    assert "## Summary" in notes["m-1"].path.read_text()
    assert len(list(cfg.wiki.glob("*.md"))) == 2


def test_lint_run_reports_and_orphans(cfg, no_external_calls):
    ingest(cfg, [("o-1", "note", "x", "Only", ["a", "b", "c"], "2026-10-14T10:00:00+02:00")])
    (cfg.raw / "stray.txt").write_text("x")
    (cfg.failed / "f-1.json").write_text("{}")
    msg = lint.run(cfg, lambda p, s, sys_: {"merges": []})
    assert msg.startswith("lint ")
    report = no_external_calls[-1]
    assert "raw/stray.txt has no wiki entry" in report and "inbox/failed/f-1.json" in report
    assert "] lint |" in (cfg.brain / "log.md").read_text()


def test_worker_sends_one_summary(cfg, no_external_calls):
    add(cfg, "s-1", "note", "x")
    add(cfg, "s-2", "note", "y", when="2026-10-14T16:00:00+02:00")
    worker.run(cfg, fake(), llm=topic_llm([]))
    [msg] = no_external_calls
    assert msg.count("+ U-Net") == 2


def test_runner_commits_once(cfg):
    subprocess.run(["git", "init", "-q", str(cfg.brain)], check=True)
    runner.run_locked(cfg, lambda: "first")
    log = lambda: subprocess.run(["git", "-C", str(cfg.brain), "log", "--format=%s"], capture_output=True,
                                 text=True).stdout.split()
    assert log() == ["first"]
    assert "inbox/*.json" in (cfg.brain / ".gitignore").read_text()
    runner.run_locked(cfg, lambda: "nothing changed")
    assert log() == ["first"]
    add(cfg, "q-1", "note", "pending entries are not committed")
    runner.run_locked(cfg, lambda: "still nothing")
    assert log() == ["first"]


def test_search(cfg):
    ingest(cfg, [("s-1", "note", "bone conduction for running", "Shokz headphones", ["headphones", "running", "gear"],
                  "2026-10-14T10:00:00+02:00"),
                 ("s-2", "note", "U-Net paper", "U-Net", ["segmentation", "deep-learning", "paper"],
                  "2026-10-14T11:00:00+02:00")])
    hits = search(cfg.wiki, "Running SHOKZ")
    assert [h.meta["id"] for h in hits] == ["s-1"]
    assert "#headphones #running" in format_results("x", hits)
    assert search(cfg.wiki, "nothing-here") == [] and "Nothing found" in format_results("q", [])


def test_conflict_copy_is_reported_not_deleted(cfg):
    ingest(cfg, [("c-1", "web", "https://example.org/a", "Article", ["a", "b", "c"], "2026-10-14T10:00:00+02:00")])
    [orig] = cfg.wiki.glob("*.md")
    copy = orig.with_name(orig.stem + " (conflicted copy 2026-10-15 120000).md")
    copy.write_text(orig.read_text().replace("## Summary", "## Summary\nEdited on the phone."))
    assert lint.dedupe(cfg, worker._now(cfg), dry_run=False) == []
    assert orig.exists() and copy.exists() and (cfg.raw / "c-1.txt").exists()
    assert any("id c-1 is in 2 files (sync conflict copy?)" in o for o in lint.find_orphans(cfg))

import pytest

from app import readlater, topics, wiki, worker
from app.sources import FetchError

from conftest import add, fake


def test_matches_archive_mirrors(monkeypatch):
    assert readlater.matches("https://archive.ph/EAl2K")
    assert readlater.matches("https://www.archive.today/abc")
    assert not readlater.matches("https://example.org/archive.ph")
    monkeypatch.setenv("READ_LATER_DOMAINS", "nzz.ch")
    assert readlater.matches("https://www.nzz.ch/x") and not readlater.matches("https://archive.ph/x")


def test_archive_link_saved_without_fetch_or_llm(cfg, no_external_calls):
    add(cfg, "a-1", "web", "https://archive.ph/EAl2K", comment="Artikel über Wärmepumpen")

    def no_llm(*a):
        raise AssertionError("LLM must not be called")

    stats = worker.run(cfg, no_llm, llm=no_llm)
    assert stats == {"ok": 1, "failed": 0, "skipped": 0}
    [note] = cfg.wiki.glob("*.md")
    assert note.name == "2026-10-14_lesen-todo_artikel-ueber-waermepumpen.md"
    meta = wiki.read_frontmatter(note)
    assert meta["tags"] == ["lesen", "todo"] and meta["source"] == "https://archive.ph/EAl2K"
    assert "site blocks automated access" in note.read_text()
    assert (cfg.raw / "a-1.txt").exists() and not list(cfg.inbox.glob("*.json"))
    assert "+ Artikel über Wärmepumpen (lesen, todo)" in no_external_calls[-1]


def test_failed_download_falls_back_to_read_later(cfg, monkeypatch):
    def blocked(entry, brain):
        raise FetchError(f"download failed: {entry.payload}")
    monkeypatch.setattr("app.worker.fetch", blocked)
    add(cfg, "w-1", "web", "https://paywalled.example/article")
    worker.run(cfg, fake())
    [note] = cfg.wiki.glob("*.md")
    assert note.name == "2026-10-14_lesen-todo_read-later-paywalled-example-article.md"
    assert "could not download" in note.read_text()


def test_other_kinds_still_fail_normally(cfg, monkeypatch):
    def blocked(entry, brain):
        raise FetchError("private video")
    monkeypatch.setattr("app.worker.fetch", blocked)
    add(cfg, "y-1", "youtube", "https://youtu.be/x")
    assert worker.run(cfg, fake())["failed"] == 1 and not list(cfg.wiki.glob("*.md"))


def test_status_tags_are_no_topics_and_no_vocabulary(cfg):
    for i in range(3):
        add(cfg, f"r-{i}", "web", f"https://archive.ph/{i}", when=f"2026-10-1{i}T10:00:00+02:00")
    worker.run(cfg, fake(), llm=lambda *a: pytest.fail("no topic page for status tags"))
    assert not list((cfg.brain / "topics").glob("*.md"))

    seen = {}
    def spy(entry, source, existing):
        seen.update(existing)
        return fake()(entry, source, existing)
    add(cfg, "n-1", "note", "a note")
    worker.run(cfg, spy, llm=lambda *a: {})
    assert "lesen" not in seen and "todo" not in seen

import io
import json

import pytest

from app import sources, wiki, worker
from app.inbox import Entry
from app.sources import FetchError, Source
from app.urls import canonical, hn_item_id

from conftest import add

HN = "https://news.ycombinator.com/item?id=50085296"


def fake_api(monkeypatch, item: dict):
    def urlopen(url, timeout=None):
        assert url == sources.HN_API.format("50085296")
        return io.BytesIO(json.dumps(item).encode())
    monkeypatch.setattr(sources, "urlopen", urlopen)


def test_hn_ids():
    assert hn_item_id(HN) == "50085296"
    assert hn_item_id("https://news.ycombinator.com/news") is None
    assert hn_item_id("https://example.org/item?id=1") is None
    assert canonical(HN + "&p=2") == "hn:50085296"


def test_story_summarizes_linked_article(monkeypatch):
    fake_api(monkeypatch, {"type": "story", "title": "Show HN: A tiny database", "score": 312,
                           "descendants": 88, "url": "https://example.org/db"})
    monkeypatch.setattr(sources, "_web", lambda url: Source(f"Article body of {url}", "Page title"))
    src = sources._hackernews("50085296")
    assert src.title_hint == "Show HN: A tiny database" and src.article == "https://example.org/db"
    assert "Article body of https://example.org/db" in src.text and "312 points, 88 comments" in src.text


def test_linked_pdf_and_video_use_their_fetchers(monkeypatch):
    monkeypatch.setattr(sources, "_pdf_url", lambda url: Source("pdf text"))
    monkeypatch.setattr(sources, "_media", lambda url: Source("transcript"))
    assert sources.fetch_url("https://arxiv.org/pdf/2401.00001v2.pdf").text == "pdf text"
    assert sources.fetch_url("https://youtu.be/abc").text == "transcript"


def test_ask_hn_uses_post_text(monkeypatch):
    fake_api(monkeypatch, {"type": "story", "title": "Ask HN: How do you take notes?",
                           "text": "I keep losing links.<p>What works for you &amp; why?"})
    src = sources._hackernews("50085296")
    assert src.article == "" and "I keep losing links.\n\nWhat works for you & why?" in src.text


def test_blocked_article_keeps_hn_title(monkeypatch):
    fake_api(monkeypatch, {"type": "story", "title": "Long read", "url": "https://archive.ph/xyz"})
    with pytest.raises(FetchError) as e:
        sources._hackernews("50085296")
    assert e.value.title == "Long read" and e.value.article == "https://archive.ph/xyz"


def test_worker_note_keeps_hn_link_and_article(cfg, monkeypatch):
    monkeypatch.setattr("app.worker.fetch", lambda entry, brain: Source("Article text", "Show HN: Tiny DB",
                                                                        article="https://example.org/db"))
    prompts = []

    def summarize(entry, source, existing):
        from app.summarize import build_prompt
        prompts.append(build_prompt(entry, source, existing))
        return {"title": "Tiny embedded database in Rust", "tags": ["database", "rust", "embedded"],
                "summary_short": "s", "summary": "S", "key_points": ["k"]}

    add(cfg, "h-1", "web", HN)
    worker.run(cfg, summarize, llm=lambda *a: {})
    [note] = cfg.wiki.glob("*.md")
    meta = wiki.read_frontmatter(note)
    assert meta["source"] == HN and meta["article"] == "https://example.org/db"
    assert f"[Original link]({HN})\n[Linked article](https://example.org/db)" in note.read_text()
    assert "Linked article: https://example.org/db" in prompts[0] and "not the discussion" in prompts[0]

    # Raw cache keeps the article for a retry without downloading again.
    src = worker._load_source(cfg, Entry("h-1", "2026-10-14T15:32:00+02:00", "web", HN))
    assert src.article == "https://example.org/db" and src.title_hint == "Show HN: Tiny DB"


def test_worker_read_later_uses_hn_title(cfg, monkeypatch):
    def blocked(entry, brain):
        raise FetchError("linked site blocks automated access", title="Long read", article="https://archive.ph/x")
    monkeypatch.setattr("app.worker.fetch", blocked)
    add(cfg, "h-2", "web", HN)
    worker.run(cfg, lambda *a: pytest.fail("no LLM"), llm=lambda *a: {})
    [note] = cfg.wiki.glob("*.md")
    meta = wiki.read_frontmatter(note)
    assert note.name == "2026-10-14_lesen-todo_long-read.md"
    assert meta["source"] == HN and meta["article"] == "https://archive.ph/x"

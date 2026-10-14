import json

from app import inbox, naming, wiki, worker
from app.llm import LLMUnavailable

from conftest import add, fake


def test_classify():
    assert inbox.classify_text("just a thought") == [("note", "just a thought", "")]
    assert inbox.classify_text("see https://youtu.be/abc.") == [("youtube", "https://youtu.be/abc", "see")]
    assert inbox.classify_text("https://www.instagram.com/reel/x/")[0][0] == "instagram"
    assert inbox.classify_text("https://m.youtube.com/watch?v=1")[0][0] == "youtube"
    assert inbox.classify_text("https://example.org/a https://example.org/a")[0][0] == "web"
    assert len(inbox.classify_text("https://a.org https://b.org")) == 2


def test_naming():
    assert naming.slug("Größe & Übermaß") == "groesse-uebermass"
    assert naming.normalize_tags(["Deep Learning", "deep-learning", "../etc", "ÄÖ"]) == ["deep-learning", "etc", "aeoe"]
    name = naming.filename("2026-10-14", ["segmentierung", "fernerkundung"], "U-Net erklärt: " + "x" * 80)
    assert name.startswith("2026-10-14_segmentierung-fernerkundung_u-net-erklaert")
    assert "/" not in name and len(name) < 110


def test_unique_path(tmp_path):
    (tmp_path / "a.md").write_text("x")
    assert naming.unique_path(tmp_path, "a.md").name == "a-2.md"
    assert naming.unique_path(tmp_path, "a.md", keep=tmp_path / "a.md").name == "a.md"


def test_worker_note_end_to_end(cfg):
    add(cfg, "20261014-153200-1", "note", "Remember: U-Net uses skip connections.")
    stats = worker.run(cfg, fake())
    assert stats["ok"] == 1
    assert not list(cfg.inbox.glob("*.json"))
    files = list(cfg.wiki.glob("*.md"))
    assert [f.name for f in files] == ["2026-10-14_segmentierung-fernerkundung_u-net-fuer-segmentierung-erklaert.md"]
    meta = wiki.read_frontmatter(files[0])
    assert meta["id"] == "20261014-153200-1" and meta["tags"][0] == "segmentierung"
    assert "source" not in meta
    body = files[0].read_text()
    assert "## Note\nRemember: U-Net" in body
    assert (cfg.raw / "20261014-153200-1.txt").exists()
    idx = (cfg.brain / "index.md").read_text()
    assert "### deep-learning" in idx and "wiki/2026-10-14_segmentierung" in idx
    assert "## [2026-10-14] ingest | U-Net für Segmentierung erklärt" in (cfg.brain / "log.md").read_text()


def test_collision_and_reprocess(cfg):
    add(cfg, "a-1", "note", "one")
    add(cfg, "a-2", "note", "two", when="2026-10-14T16:00:00+02:00")
    worker.run(cfg, fake(title="Same"))
    assert sorted(f.name for f in cfg.wiki.glob("*.md")) == [
        "2026-10-14_segmentierung-fernerkundung_same-2.md", "2026-10-14_segmentierung-fernerkundung_same.md"]
    # Same id again (crash before inbox removal): replaces its own file, no duplicate.
    add(cfg, "a-1", "note", "one")
    worker.run(cfg, fake(title="Same", tags=["other", "tag", "x"]))
    names = sorted(f.name for f in cfg.wiki.glob("*.md"))
    assert names == ["2026-10-14_other-tag_same.md", "2026-10-14_segmentierung-fernerkundung_same-2.md"]


def test_failure_retries_then_moves(cfg):
    add(cfg, "b-1", "note", "x")

    def boom(*a):
        raise ValueError("bad")

    for i in range(1, 4):
        worker.run(cfg, boom)
    assert not list(cfg.inbox.glob("*.json"))
    failed = json.loads((cfg.failed / "b-1.json").read_text())
    assert failed["attempts"] == 3
    assert (cfg.brain / "log.md").read_text().count("] fail |") == 3


def test_llm_unavailable_does_not_count(cfg):
    add(cfg, "c-1", "note", "x")
    add(cfg, "c-2", "note", "y")
    calls = []

    def limit(*a):
        calls.append(1)
        raise LLMUnavailable("usage limit reached")

    worker.run(cfg, limit)
    assert len(calls) == 1  # stops after the first limit hit
    assert json.loads((cfg.inbox / "c-1.json").read_text())["attempts"] == 0


def test_prompt_wraps_document():
    from app.summarize import build_prompt, system_prompt
    from app.sources import Source
    e = inbox.Entry("e", "2026-10-14T15:32:00+02:00", "web", "https://x.org", comment="for work")
    p = build_prompt(e, Source("hello"), {"ml": 2})
    assert "ml (2)" in p and "<document>\nhello\n</document>" in p and "for work" in p
    assert "Controlled vocabulary" in system_prompt()

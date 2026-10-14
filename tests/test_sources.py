from pathlib import Path

import pytest

from app import summarize, worker
from app.inbox import Entry
from app.sources import FetchError, Source, _pdf, _subtitles, vtt_to_text

from conftest import add, fake


def test_vtt_to_text_dedupes_rolling_auto_captions():
    vtt = """WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:02.000 align:start position:0%
hello<00:00:00.500><c> world</c>

00:00:02.000 --> 00:00:04.000
hello world
this is &amp; that

00:00:04.000 --> 00:00:06.000
this is &amp; that
end
"""
    assert vtt_to_text(vtt) == "hello world this is & that end"


class FakeYDL:
    def __init__(self, files):
        self.files = files

    def urlopen(self, url):
        class R:
            def read(_):
                return self.files[url].encode()
        return R()


def test_subtitles_prefers_original_language():
    long = "WEBVTT\n\n00:00.000 --> 00:01.000\n" + "Guten Tag, heute geht es um Segmentierung. " * 2
    info = {
        "language": "de",
        "subtitles": {"en": [{"ext": "vtt", "url": "en"}]},
        "automatic_captions": {"de-orig": [{"ext": "vtt", "url": "de"}], "fr": [{"ext": "vtt", "url": "fr"}]},
    }
    text, how = _subtitles(FakeYDL({"de": long, "en": "x"}), info)
    assert text.startswith("Guten Tag") and how == "auto-captions, de-orig"
    assert _subtitles(FakeYDL({}), {"subtitles": {}, "automatic_captions": {}}) == ("", "")


def make_pdf(path: Path, pages: list[str]) -> None:
    import pymupdf
    doc = pymupdf.open()
    for t in pages:
        doc.new_page().insert_text((72, 72), t)
    doc.save(path)


def test_pdf_text_and_empty(tmp_path):
    make_pdf(tmp_path / "a.pdf", ["U-Net uses an encoder and a decoder with skip connections.", "Page two."])
    src = _pdf(tmp_path / "a.pdf", "unet-paper.pdf")
    assert "[page 1]" in src.text and "skip connections" in src.text and src.title_hint == "unet-paper"
    make_pdf(tmp_path / "b.pdf", [""])
    with pytest.raises(FetchError, match="no text layer"):
        _pdf(tmp_path / "b.pdf")


def test_pdf_end_to_end(cfg):
    make_pdf(cfg.raw / "p-1.pdf", ["A long enough paragraph about bone conduction headphones for running."])
    add(cfg, "p-1", "pdf", "raw/p-1.pdf")
    worker.run(cfg, fake(title="Headphones"))
    [note] = cfg.wiki.glob("*.md")
    text = note.read_text()
    assert "source: ../raw/p-1.pdf" in text and "[Original file](../raw/p-1.pdf)" in text


def test_split_respects_size_and_keeps_text():
    text = "\n\n".join(f"Paragraph {i} " + "word " * 300 for i in range(40))
    chunks = summarize.split(text, 5000)
    assert all(len(c) <= 5000 for c in chunks) and len(chunks) > 5
    assert "".join(chunks).replace(" ", "").replace("\n", "") == text.replace(" ", "").replace("\n", "")


def test_long_text_is_condensed_in_sections(monkeypatch):
    calls = []

    def fake_llm(prompt, schema, system):
        calls.append(schema)
        if schema is summarize.SECTION_SCHEMA:
            return {"notes": [f"note {len(calls)}"]}
        assert "condensed from it section by section" in prompt and "note 1" in prompt
        return {"title": "t", "tags": ["a", "b", "c"], "summary_short": "", "summary": "", "key_points": []}

    monkeypatch.setattr(summarize, "llm_json", fake_llm)
    entry = Entry("e", "2026-10-14T10:00:00+02:00", "pdf", "raw/e.pdf")
    summarize.summarize(entry, Source("x " * 100_000), {})
    assert calls.count(summarize.SECTION_SCHEMA) == 4 and calls[-1] is summarize.SCHEMA


def test_title_hint_survives_raw_cache(cfg):
    (cfg.raw / "r-1.txt").write_text("Title: Some Reel\nAuthor: x\n\nTranscript: hi")
    e = Entry("r-1", "2026-10-14T10:00:00+02:00", "instagram", "https://instagram.com/reel/x")
    assert worker._load_source(cfg, e).title_hint == "Some Reel"

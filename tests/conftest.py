from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app import inbox
from app.config import Config

TZ = ZoneInfo("Europe/Berlin")


@pytest.fixture(autouse=True)
def no_external_calls(monkeypatch):
    """Tests must never reach the real LLM or Telegram."""
    def refuse(*a, **k):
        raise AssertionError("real LLM called in a test")
    monkeypatch.setattr("app.llm._claude_cli", refuse)
    from app import sources
    real_fetch = sources.fetch

    def local_fetch(entry, brain):
        if entry.kind in ("note", "pdf"):
            return real_fetch(entry, brain)
        raise AssertionError(f"network fetch in a test: {entry.payload}")
    monkeypatch.setattr("app.worker.fetch", local_fetch)
    sent = []
    monkeypatch.setattr("app.notify.send", lambda text, silent=True: sent.append(text))
    return sent


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    c = Config(brain=tmp_path, tz=TZ, max_attempts=3, claude_model="x", claude_timeout=1)
    c.ensure_dirs()
    return c


def add(cfg, id_, kind, payload, when="2026-10-14T15:32:00+02:00", comment=""):
    inbox.write(cfg.inbox, inbox.Entry(id_, when, kind, payload, comment=comment))


def fake(title="U-Net für Segmentierung erklärt", tags=("Segmentierung", "fernerkundung", "deep-learning")):
    def f(entry, source, existing):
        return {"title": title, "tags": list(tags), "summary_short": "Short.", "summary": "Long.",
                "key_points": ["a", "b"]}
    return f

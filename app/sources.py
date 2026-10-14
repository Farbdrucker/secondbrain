"""Fetch the raw text for an inbox entry."""

import html
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from . import readlater
from .inbox import Entry, kind_for_url
from .urls import hn_item_id


class FetchError(Exception):
    def __init__(self, message: str, title: str = "", article: str = ""):
        super().__init__(message)
        self.title = title  # known even though the content could not be fetched (e.g. HN title)
        self.article = article


@dataclass
class Source:
    text: str
    title_hint: str = ""
    article: str = ""  # page actually summarized when the link points to a discussion (Hacker News)


def fetch(entry: Entry, brain: Path) -> Source:
    if entry.kind == "note":
        return Source(entry.payload)
    if entry.kind == "web":
        item = hn_item_id(entry.payload)
        return _hackernews(item) if item else _web(entry.payload)
    if entry.kind == "pdf":
        return _pdf(brain / entry.payload, entry.file_name)
    if entry.kind in ("youtube", "instagram"):
        return _media(entry.payload)
    raise FetchError(f"unknown kind: {entry.kind}")


# --- web ---------------------------------------------------------------------

def _web(url: str) -> Source:
    import trafilatura

    page = trafilatura.fetch_url(url)
    if not page:
        raise FetchError(f"download failed: {url}")
    text = trafilatura.extract(page, url=url, include_comments=False, include_tables=True)
    if not text or len(text.strip()) < 50:
        raise FetchError("no main text found on page")
    meta = trafilatura.extract_metadata(page, default_url=url)
    title = (meta.title if meta and meta.title else "") or ""
    return Source(text.strip(), title)


def fetch_url(url: str) -> Source:
    """Any linked URL: video platforms, PDF files, otherwise a web page."""
    if kind_for_url(url) in ("youtube", "instagram"):
        return _media(url)
    if urlparse(url).path.lower().endswith(".pdf"):
        return _pdf_url(url)
    return _web(url)


# --- hacker news -----------------------------------------------------------------

HN_API = "https://hacker-news.firebaseio.com/v0/item/{}.json"


def _hackernews(item_id: str) -> Source:
    """Summarize the linked article; keep the HN title. Text posts (Ask HN) use their own text."""
    try:
        with urlopen(HN_API.format(item_id), timeout=20) as r:
            data = json.loads(r.read())
    except (OSError, ValueError) as e:
        raise FetchError(f"Hacker News API: {e}") from e
    if not data or data.get("deleted") or data.get("dead"):
        raise FetchError("Hacker News item not found or deleted")

    title = html.unescape(data.get("title") or "")
    url = data.get("url") or ""
    own = _html_to_text(data.get("text") or "")
    stats = f"Hacker News: {data.get('score', 0)} points, {data.get('descendants', 0)} comments"
    if not url:
        if len(own) < 30:
            raise FetchError("Hacker News item has neither a link nor text", title=title)
        return Source(f"{stats}\n\n{own}", title)
    if readlater.matches(url):
        raise FetchError("linked site blocks automated access", title=title, article=url)
    try:
        article = fetch_url(url)
    except FetchError as e:
        raise FetchError(f"linked article: {e}", title=title, article=url) from e
    text = f"{stats}\n\n{article.text}"
    if own:
        text += f"\n\nText of the Hacker News post:\n{own}"
    return Source(text, title or article.title_hint, article=url)


def _html_to_text(fragment: str) -> str:
    text = re.sub(r"<p>|<br\s*/?>", "\n\n", fragment)
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


# --- pdf ---------------------------------------------------------------------

def _pdf(path: Path, file_name: str = "") -> Source:
    import pymupdf

    if not path.exists():
        raise FetchError(f"PDF not found: {path}")
    with pymupdf.open(path) as doc:
        pages = [p.get_text().strip() for p in doc]
        title = (doc.metadata or {}).get("title") or ""
    text = "\n\n".join(f"[page {i}]\n{t}" for i, t in enumerate(pages, 1) if t)
    if len(re.sub(r"\[page \d+\]|\s", "", text)) < 50:
        raise FetchError("PDF has no text layer (scanned?). OCR is not supported yet")
    return Source(text, title or Path(file_name).stem)


MAX_PDF_BYTES = 50 * 1024 * 1024


def _pdf_url(url: str) -> Source:
    try:
        with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=60) as r:
            data = r.read(MAX_PDF_BYTES + 1)
    except OSError as e:
        raise FetchError(f"PDF download failed: {e}") from e
    if len(data) > MAX_PDF_BYTES:
        raise FetchError("linked PDF is larger than 50 MB")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "linked.pdf"
        path.write_bytes(data)
        return _pdf(path, Path(urlparse(url).path).name)


# --- youtube / instagram -------------------------------------------------------

# Subtitles exist in many languages; prefer the video's own language, then these.
SUB_LANGS = ("en", "de")


def _ydl_opts(**extra) -> dict:
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
    cookies = os.environ.get("YTDLP_COOKIES")
    if cookies and Path(cookies).exists():
        opts["cookiefile"] = cookies
    return opts | extra


def _media(url: str) -> Source:
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError

    with tempfile.TemporaryDirectory() as tmp:
        try:
            with YoutubeDL(_ydl_opts(skip_download=True)) as ydl:
                info = ydl.extract_info(url, download=False)
                transcript, how = _subtitles(ydl, info)
            if not transcript:
                transcript, how = _transcribe_media(url, info, Path(tmp))
        except DownloadError as e:
            raise FetchError(str(e).removeprefix("ERROR: ")[:300]) from e

    header = [f"Title: {info.get('title') or ''}"]
    if info.get("uploader") or info.get("channel"):
        header.append(f"Author: {info.get('channel') or info.get('uploader')}")
    if info.get("duration"):
        m, s = divmod(int(info["duration"]), 60)
        header.append(f"Duration: {m}:{s:02d}")
    desc = (info.get("description") or "").strip()
    if desc:
        header += ["", "Description:", desc]
    if transcript:
        header += ["", f"Transcript ({how}):", transcript]
    text = "\n".join(header)
    if not transcript and len(desc) < 30:
        raise FetchError("no speech and no description found")
    return Source(text, info.get("title") or "")


def _subtitles(ydl, info: dict) -> tuple[str, str]:
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    lang = (info.get("language") or "").split("-")[0]
    candidates = []
    if lang:
        candidates += [(manual, lang), (auto, f"{lang}-orig"), (auto, lang)]
    candidates += [(manual, l) for l in SUB_LANGS] + [(manual, l) for l in manual if l != "live_chat"]
    candidates += [(auto, k) for k in auto if k.endswith("-orig")] + [(auto, l) for l in SUB_LANGS]
    for table, key in candidates:
        tracks = table.get(key) or []
        vtt = next((t for t in tracks if t.get("ext") == "vtt"), None)
        if not vtt:
            continue
        raw = ydl.urlopen(vtt["url"]).read().decode("utf-8", "replace")
        text = vtt_to_text(raw)
        if len(text) > 30:
            kind = "subtitles" if table is manual else "auto-captions"
            return text, f"{kind}, {key}"
    return "", ""


def vtt_to_text(vtt: str) -> str:
    out: list[str] = []
    for line in vtt.splitlines():
        line = line.strip()
        if (not line or line == "WEBVTT" or "-->" in line or line.isdigit()
                or line.startswith(("Kind:", "Language:", "NOTE", "STYLE"))):
            continue
        line = html.unescape(re.sub(r"<[^>]+>", "", line)).strip()
        # Auto captions repeat the previous line at the start of each cue.
        if line and line not in out[-2:]:
            out.append(line)
    return " ".join(out)


def _transcribe_media(url: str, info: dict, tmp: Path) -> tuple[str, str]:
    from yt_dlp import YoutubeDL

    limit = int(os.environ.get("WHISPER_MAX_MINUTES", "90"))
    if (info.get("duration") or 0) > limit * 60:
        raise FetchError(f"no subtitles and longer than {limit} min; not transcribing")
    with YoutubeDL(_ydl_opts(format="bestaudio/best", outtmpl=str(tmp / "audio.%(ext)s"))) as ydl:
        ydl.download([url])
    audio = next(tmp.glob("audio.*"), None)
    if not audio:
        raise FetchError("audio download produced no file")
    text, lang = transcribe(audio)
    return text, f"whisper, {lang}"


_model = None


def transcribe(audio: Path) -> tuple[str, str]:
    """Local speech-to-text. The model is loaded once per worker run and cached on disk."""
    global _model
    from faster_whisper import WhisperModel

    if _model is None:
        cache = Path(os.environ.get("CACHE_DIR", "./cache")) / "whisper"
        _model = WhisperModel(os.environ.get("WHISPER_MODEL", "small"), device="cpu",
                              compute_type="int8", download_root=str(cache))
    segments, info = _model.transcribe(str(audio), vad_filter=True)
    return " ".join(s.text.strip() for s in segments).strip(), info.language

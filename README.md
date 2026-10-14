# secondbrain

Telegram-Wissensbot: Links und Notizen per Telegram schicken. Ein Cron-Job fasst sie mit
`claude -p` zusammen, vergibt Tags und legt sie als Markdown in `brain/wiki/` ab. Dazu kommen
ein generierter `index.md` und ein append-only `log.md`.

**Stand: Phase 3.** Verarbeitet werden Notizen, Webseiten, PDFs, YouTube und Instagram.
Der Worker läuft nachts um 3 Uhr. Danach schickt er eine lautlose Telegram-Zusammenfassung und
committet `brain/`.

- **Themenseiten** (`topics/<tag>.md`): Ab 3 Einträgen bekommt ein Tag eine Synthese mit
  Überblick, Themen und offenen Fragen, Zitate verlinken auf die Einträge. Neu erzeugt wird die
  Seite nur, wenn sich ihre Einträge ändern. Das LLM-Ergebnis liegt in `topics/.cache/`.
- **Lint** (sonntags): entfernt Dubletten (gleiche URL ohne Tracking-Parameter, gleiche PDF,
  gleicher Notiztext; der älteste Eintrag bleibt). Führt synonyme Tags zusammen und benennt die
  Dateien dabei um. Meldet verwaiste Dateien und Einträge in `inbox/failed/`.
  Vorschau ohne Änderungen: `docker compose run --rm -T worker python -m app.lint --dry-run`
- **Suche:** `/search <wörter>` im Bot findet Einträge, die alle Wörter enthalten.
- **Hacker News:** Bei Links wie `news.ycombinator.com/item?id=…` holt der Worker über die
  HN-API den verlinkten Artikel. Zusammenfassung und Tags beziehen sich auf den Artikel. Im
  Frontmatter bleibt `source` der HN-Link, `article` ist der Artikel, die Notiz verlinkt beide.
  Bei „Ask HN“ und anderen Textbeiträgen wird der Beitragstext zusammengefasst. Ist der Artikel
  blockiert, entsteht ein Später-lesen-Eintrag mit dem HN-Titel.
- **Später lesen:** Links von archive.ph/.today/.is & Co. (Bot-Schutz) speichert der Worker ohne
  Abruf und ohne LLM, mit den Tags `lesen` und `todo`. Webseiten, die sich nicht laden lassen,
  landen ebenfalls dort und nicht in `failed/`. Schickst du einen Text mit dem Link, wird er zum
  Titel. Offene Einträge findest du mit `/search todo`. Gelesen? Entferne in Obsidian den Tag `todo`.
  Die Domains und Tags lassen sich in der `.env` einstellen (`READ_LATER_DOMAINS`, `READ_LATER_TAGS`).

| Typ | Vorgehen |
|---|---|
| YouTube | Untertitel (Originalsprache bevorzugt). Ohne Untertitel lädt der Worker das Audio und transkribiert es mit faster-whisper. |
| Instagram | Audio laden, mit Whisper transkribieren, dazu die Bildunterschrift |
| PDF | Text mit pymupdf. Gescannte PDFs ohne Textebene schlagen mit klarer Meldung fehl. |
| lange Texte | Über 80.000 Zeichen wird abschnittsweise verdichtet und danach zusammengefasst. |

## Einrichtung (Homelab)

1. Bot bei @BotFather anlegen und das Token notieren. Die eigene Chat-ID über @userinfobot herausfinden.
2. Auf einem Rechner mit Claude-Abo `claude setup-token` ausführen und das Token kopieren.
3. `cp .env.example .env` ausführen und die Werte eintragen.
4. `mkdir -p brain cache && git -C brain init` (Git ist optional. Lege `cache/` selbst an,
   sonst erstellt Docker den Ordner mit root als Eigentümer.)
5. `docker compose build && docker compose up -d bot`
6. Die beiden Cron-Einträge aus `crontab.example` übernehmen (`crontab -e`).

Inhalte zusätzlich in Nextcloud haben: siehe [docs/nextcloud.md](docs/nextcloud.md).

Den Worker manuell starten: `docker compose run --rm -T worker`

Beim ersten Whisper-Lauf lädt der Worker das Modell nach `cache/whisper/` (`small`: ca. 500 MB).
Instagram blockiert Downloads gelegentlich. Dann eine `cookies.txt` (Netscape-Format, z. B. per
Browser-Erweiterung exportiert) nach `cache/` legen und `YTDLP_COOKIES=/data/cache/cookies.txt`
setzen. `yt-dlp` veraltet schnell, deshalb bei Download-Fehlern zuerst das Image neu bauen
(`docker compose build --no-cache && docker compose up -d bot`).

## Aufbau

| Datei | Aufgabe |
|---|---|
| `app/bot.py` | Polling, Chat-ID-Filter, Typ-Erkennung, schreibt `inbox/<id>.json` |
| `app/worker.py` | ein Durchlauf: holen → `raw/` → LLM → `wiki/` → Log → Index |
| `app/llm.py` | `llm_json()`, die einzige Stelle mit Backend-Code (`claude -p`, ohne Werkzeuge) |
| `app/summarize.py` | Prompt und JSON-Schema |
| `app/SCHEMA.md` | Konventionen und Tag-Regeln, werden in den System-Prompt übernommen |
| `app/sources.py` | Abruf je Quelltyp: Web, PDF, YouTube/Instagram (yt-dlp, Whisper) |
| `app/notify.py` | Telegram-Zusammenfassung nach jedem Lauf (lautlos) |
| `app/topics.py` | Themenseiten pro häufigem Tag |
| `app/lint.py` | wöchentliche Pflege: Dubletten, Tag-Zusammenführung, Waisen |
| `app/search.py` | `/search` im Bot |
| `app/readlater.py` | Später-lesen-Einträge ohne Abruf und LLM |
| `app/runner.py` | Lock, `.gitignore` in `brain/`, Git-Commit nach jedem Lauf |
| `app/index.py` | erzeugt `index.md` aus dem Frontmatter, ohne LLM |

Verhalten bei Fehlern:
- Ein Fehler zählt `attempts` hoch. Nach `MAX_ATTEMPTS` wandert der Eintrag nach `inbox/failed/`.
- Ein Abo-Limit oder eine Störung zählt nicht als Versuch. Der Lauf bricht ab und versucht es beim nächsten Mal erneut.
- Der Rohtext wird vor dem LLM-Aufruf gespeichert. Ein Retry ruft die Quelle nicht noch einmal ab.
- Wird eine `id` erneut verarbeitet, ersetzt sie ihre eigene Datei. So entstehen keine Dubletten.

## Entwicklung

```bash
uv run --group dev pytest
BRAIN_DIR=./brain CLAUDE_MODEL=haiku uv run python -m app.worker
```

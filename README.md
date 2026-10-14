# secondbrain

Persönliche Wissenssammlung auf dem Homelab. Links, PDFs und Notizen gehen per Telegram an einen
Bot. Nachts fasst ein Worker sie mit `claude -p` zusammen, vergibt Tags und legt sie als
Markdown-Dateien in `brain/` ab, zusammen mit Index, Themenseiten und Änderungsprotokoll.
Das Muster folgt Karpathys „LLM Wiki“: Rohmaterial, ein vom LLM gepflegtes Wiki, `index.md`,
`log.md` und feste Konventionen.

`brain/` ist ein eigenes Git-Repo und lässt sich direkt als Obsidian-Vault öffnen.

## Was der Bot verarbeitet

| Eingabe | Vorgehen |
|---|---|
| Textnotiz | Text ohne Link wird als Notiz eingeordnet. |
| Webseite | Haupttext mit trafilatura, dann Zusammenfassung und Tags. |
| YouTube | Untertitel, bevorzugt in der Originalsprache. Ohne Untertitel wird das Audio geladen und lokal mit faster-whisper transkribiert (bis `WHISPER_MAX_MINUTES`). |
| Instagram | Audio mit yt-dlp, Transkript mit Whisper, dazu die Bildunterschrift. |
| PDF (Datei) | Text mit pymupdf. Gescannte PDFs ohne Textebene schlagen mit klarer Meldung fehl (kein OCR). Telegram erlaubt bis 20 MB. |
| Hacker News | Bei `news.ycombinator.com/item?id=…` wird der **verlinkte Artikel** zusammengefasst und getaggt (Webseite, PDF oder Video). `source` bleibt der HN-Link, `article` ist der Artikel. Bei „Ask HN“ zählt der Beitragstext. |
| Später lesen | Links von archive.ph/.today/.is & Co. (Bot-Schutz) werden ohne Abruf und ohne LLM gespeichert, mit den Tags `lesen` und `todo`. Webseiten, die sich nicht laden lassen (Paywall, Bot-Schutz), landen ebenfalls hier und gehen nicht verloren. |

Texte über 80.000 Zeichen, etwa lange PDFs oder Podcast-Transkripte, werden zuerst
abschnittsweise verdichtet und dann gemeinsam zusammengefasst.

## Bedienung in Telegram

- **Link, PDF oder Text schicken:** Der Bot antwortet sofort mit „Received (…)“. Verarbeitet
  wird im Nachtlauf.
- **Text zum Link:** Schickst du einen Text mit dem Link, z. B. `Artikel über Wärmepumpen
  https://archive.ph/…`, landet er als Kommentar in der Notiz. Bei Später-lesen-Einträgen wird er
  zum Titel.
- **Mehrere Links** in einer Nachricht ergeben je einen Eintrag.
- `/search <wörter>`: Einträge, die alle Wörter enthalten, z. B. `/search todo` für die
  Leseliste.
- `/help`: Kurzhilfe.

Der Bot reagiert nur auf die Chat-ID aus `TELEGRAM_CHAT_ID`. Nachrichten aus anderen Chats
ignoriert er und schreibt ihre Chat-ID ins Log.

## Was nachts passiert

**Täglich um 03:00, Worker:**
1. Inbox abarbeiten: Quelle holen → Rohtext nach `raw/` → LLM → Notiz nach `wiki/` → Eintrag in
   `log.md`.
2. Themenseiten für Tags mit neuen Einträgen aktualisieren.
3. `index.md` neu erzeugen.
4. `brain/` committen.
5. Eine lautlose Telegram-Zusammenfassung schicken: neue Einträge, endgültige Fehlschläge,
   aktualisierte Themen.

**Sonntags um 03:30, Lint:**
- **Dubletten entfernen:** gleiche URL (ohne Tracking-Parameter wie `utm_*` oder `igsh`), gleiche
  PDF, gleicher Notiztext. Der älteste Eintrag bleibt.
- **Tags zusammenführen:** Das LLM schlägt Synonyme vor, etwa `ml` → `machine-learning`. Der
  Code schreibt die Tags um und benennt die Dateien um.
- **Melden, was einen Blick braucht:** verwaiste Rohdaten, Einträge in `inbox/failed/` und
  Dateien mit gleicher `id`, z. B. Sync-Konfliktkopien. Diese löscht der Lint nie.

## Aufbau von `brain/`

```
brain/
├── inbox/              Warteschlange: eine .json pro Nachricht (nicht in Git)
│   └── failed/         nach MAX_ATTEMPTS Fehlversuchen
├── raw/                Rohtexte, Transkripte, PDFs
├── wiki/               eine Notiz pro Eintrag
├── topics/             Themenseite pro Tag ab TOPIC_MIN_ENTRIES Einträgen
│   └── .cache/         LLM-Ergebnis + Hash, damit unveränderte Themen nichts kosten
├── index.md            Katalog nach Tag und chronologisch, per Skript erzeugt
└── log.md              Protokoll, nur angehängt: ingest, fail, dedupe, retag, lint
```

**Dateinamen:** `YYYY-MM-DD_<tag1>-<tag2>_<titel>.md`, z. B.
`2026-10-14_image-segmentation-deep-learning_u-net-convolutional-network-architecture-for-image.md`.
Das Datum ist der Sendezeitpunkt. Die eigentliche Identität ist die `id` im Frontmatter, Dateien
dürfen also umbenannt werden.

**Notiz:**

```markdown
---
id: 20261014-160000-2
type: web
source: https://en.wikipedia.org/wiki/U-Net
received_at: '2026-10-14T16:00:00+02:00'
processed_at: '2026-10-14T16:05:12+02:00'
title: 'U-Net: convolutional network architecture for image segmentation'
tags: [image-segmentation, deep-learning, medical-imaging]
summary_short: Ein Satz für den Index.
raw: ../raw/20261014-160000-2.txt
---

## Summary
## Key points
## Comment      (falls ein Text mitgeschickt wurde)
## Source
```

Zusammenfassungen und Tags sind auf Englisch. `lesen` und `todo` sind Status-Tags. Sie bekommen
keine Themenseite, werden nie zusammengeführt und dem LLM nicht als Vokabular angeboten. Gelesen?
Entferne in Obsidian den Tag `todo`. Die Tag-Regeln für das LLM stehen in `app/SCHEMA.md`.

## Einrichtung (Homelab, Docker)

1. **Telegram-Bot anlegen:** bei @BotFather, Token notieren. Die eigene User-ID über
   @userinfobot herausfinden. Achtung: nicht die Zahl vor dem `:` im Token verwenden, das ist die
   ID des Bots.
2. **Claude-Token erzeugen:** auf einem Rechner mit Claude-Abo `claude setup-token` ausführen und
   das Token kopieren.
3. **Konfiguration:** `cp .env.example .env` und die Werte eintragen, siehe
   [Konfiguration](#konfiguration).
4. **Verzeichnisse anlegen:**

   ```bash
   mkdir -p brain cache && git -C brain init
   ```

   `cache/` selbst anlegen, sonst erstellt Docker den Ordner mit root als Eigentümer. Git in
   `brain/` ist optional. Ohne Git wird nichts committet.
5. **Bauen und starten:**

   ```bash
   docker compose build && docker compose up -d bot
   ```

   Bot und Worker teilen sich ein Image (`secondbrain:latest`). Ein Build aktualisiert beide.
6. **Cron:** die beiden Zeilen aus `crontab.example` übernehmen (`crontab -e`, Pfad anpassen).

**Optional:** `brain/` mit Nextcloud (Hetzner Storage Share) synchronisieren, siehe
[docs/nextcloud.md](docs/nextcloud.md).

## Konfiguration

Alle Werte stehen in `.env`. Pflicht sind nur die ersten drei.

| Variable | Standard | Bedeutung |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | – | Token von @BotFather |
| `TELEGRAM_CHAT_ID` | – | deine User-ID; nur dieser Chat wird angenommen |
| `CLAUDE_CODE_OAUTH_TOKEN` | – | aus `claude setup-token` |
| `CLAUDE_MODEL` | `sonnet` | Modell für `claude -p` |
| `CLAUDE_TIMEOUT` | `300` | Sekunden pro LLM-Aufruf |
| `BRAIN_TZ` | `Europe/Berlin` | Zeitzone für Dateinamen, Index und Log |
| `MAX_ATTEMPTS` | `3` | Fehlversuche, bevor ein Eintrag nach `inbox/failed/` wandert |
| `WHISPER_MODEL` | `small` | `tiny`, `base`, `small`, `medium`: größer heißt besser, aber langsamer |
| `WHISPER_MAX_MINUTES` | `90` | längere Videos ohne Untertitel werden nicht transkribiert |
| `YTDLP_COOKIES` | – | `cookies.txt` für Instagram, z. B. `/data/cache/cookies.txt` |
| `TOPIC_MIN_ENTRIES` | `3` | ab so vielen Einträgen bekommt ein Tag eine Themenseite |
| `NOTIFY` | `1` | `0` schaltet die Telegram-Zusammenfassung ab |
| `READ_LATER_DOMAINS` | archive.ph, .today, .is, .li, .md, .vn, .fo | Domains, die direkt als Später-lesen-Eintrag gespeichert werden |
| `READ_LATER_TAGS` | `lesen,todo` | Tags für Später-lesen-Einträge |

**Zum LLM:** `claude -p` verbraucht das Kontingent des Claude-Abos. Der Aufruf läuft ohne
Werkzeuge, ohne MCP-Server und in einem leeren Arbeitsverzeichnis, weil Webseiten, PDFs und
Transkripte fremder Text sind (Prompt Injection). Ist das Abo-Limit erreicht, bricht der Lauf ab,
ohne Fehlversuche zu zählen, und macht in der nächsten Nacht weiter. Ein anderes Backend, etwa die
API oder ein lokales Modell, wird an genau einer Stelle eingebaut: `llm_json()` in `app/llm.py`.

## Betrieb

```bash
docker compose run --rm -T worker                               # Worker jetzt laufen lassen
docker compose run --rm -T worker python -m app.lint --dry-run  # Lint-Vorschau, ändert nichts
docker compose run --rm -T worker python -m app.lint            # Lint jetzt laufen lassen
docker compose logs -f bot                                      # Bot-Log
tail -f /var/log/secondbrain.log                                # Cron-Ausgabe
grep "^## \[" brain/log.md | tail -20                           # letzte Ereignisse
git -C brain log --oneline -10                                  # letzte Commits
```

**Nach Code-Änderungen** neu bauen und den Bot neu starten:
`docker compose build && docker compose up -d bot`.

**Fehlersuche:**

| Symptom | Ursache / Lösung |
|---|---|
| Bot antwortet nicht | Falsche `TELEGRAM_CHAT_ID`. `docker compose logs bot` zeigt die Chat-ID ignorierter Nachrichten. |
| Einträge bleiben in `inbox/`, im Log steht nichts | Veraltetes Image: `docker compose build`. |
| YouTube- oder Instagram-Downloads schlagen fehl | `yt-dlp` veraltet schnell: `docker compose build --no-cache`. Bei Instagram hilft oft eine `cookies.txt` (Netscape-Format, per Browser-Erweiterung exportiert) in `cache/`, dazu `YTDLP_COOKIES`. |
| Erster Whisper-Lauf dauert lange | Das Modell wird einmalig nach `cache/whisper/` geladen, `small` hat ca. 500 MB. |
| „LLM unavailable“ in der Zusammenfassung | Abo-Limit oder Störung. Die Einträge bleiben liegen und werden in der nächsten Nacht verarbeitet. |

**Verhalten bei Fehlern:**
- Jeder Eintrag wird einzeln verarbeitet. Ein Fehler stoppt den Lauf nicht.
- Der Rohtext wird **vor** dem LLM-Aufruf gespeichert. Ein Retry lädt die Quelle nicht neu.
- Ein Fehler zählt `attempts` hoch. Nach `MAX_ATTEMPTS` wandert der Eintrag nach
  `inbox/failed/` und erscheint in der Zusammenfassung. Webseiten werden stattdessen sofort zu
  Später-lesen-Einträgen.
- Alle Dateien werden atomar geschrieben (temporäre Datei, dann umbenennen). Wird eine `id`
  erneut verarbeitet, ersetzt sie ihre eigene Datei.
- Worker, Lint und Nextcloud-Sync teilen sich ein Lock und laufen nie gleichzeitig.

## Code

| Datei | Aufgabe |
|---|---|
| `app/bot.py` | Polling, Chat-ID-Filter, Typ-Erkennung, `/search`, schreibt `inbox/<id>.json` |
| `app/worker.py` | Nachtlauf: Inbox → `raw/` → LLM → `wiki/`, danach Themen, Index, Meldung |
| `app/lint.py` | wöchentliche Pflege: Dubletten, Tag-Zusammenführung, Hinweise |
| `app/sources.py` | Abruf je Quelltyp: Web, PDF, YouTube/Instagram (yt-dlp, Whisper), Hacker News |
| `app/summarize.py` | Prompt und JSON-Schema, abschnittsweise Verdichtung langer Texte |
| `app/llm.py` | `llm_json()`: die einzige Stelle mit Backend-Code (`claude -p`) |
| `app/SCHEMA.md` | Konventionen und Tag-Regeln, wird in den System-Prompt übernommen |
| `app/topics.py` | Themenseiten pro häufigem Tag, mit Cache |
| `app/index.py` | erzeugt `index.md` aus dem Frontmatter, ohne LLM |
| `app/readlater.py` | Später-lesen-Einträge ohne Abruf und LLM |
| `app/search.py` | Volltextsuche für `/search` |
| `app/runner.py` | Lock, `.gitignore` in `brain/`, Git-Commit nach jedem Lauf |
| `app/notify.py` | Telegram-Zusammenfassung (lautlos) |
| `app/inbox.py`, `app/wiki.py`, `app/naming.py`, `app/urls.py`, `app/log.py` | Inbox-Format, Frontmatter, Dateinamen, URL-Normalisierung, Protokoll |

## Entwicklung

```bash
uv run --group dev pytest                                   # Tests (ohne Netz, ohne LLM)
BRAIN_DIR=./brain CLAUDE_MODEL=haiku NOTIFY=0 uv run python -m app.worker
BRAIN_DIR=./brain uv run python -m app.lint --dry-run
```

Die Tests ersetzen LLM, Telegram und Downloads durch Fakes. Ein Test, der versehentlich das
echte LLM aufruft, schlägt fehl.

## Noch nicht umgesetzt

- `/ask`: Fragen an das Wiki stellen und gute Antworten als neue Seiten ablegen
- OCR für gescannte PDFs
- PDFs über 20 MB (Grenze der Telegram-Bot-API)

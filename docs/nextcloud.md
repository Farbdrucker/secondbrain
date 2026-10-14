# brain/ mit Nextcloud synchronisieren

Ziel: Der Inhalt von `brain/` (Wiki, Themenseiten, Index, Rohdaten) ist zusätzlich in einem
Nextcloud-Ordner verfügbar. Änderungen an anderen Geräten, z. B. Notizen in Obsidian bearbeitet
oder den Tag `todo` entfernt, kommen zurück auf das Homelab und werden beim nächsten Worker-Lauf
committet.

Die Nextcloud läuft als **Hetzner Storage Share**, also gehostet und nicht auf dem Homelab.
Deshalb synchronisiert das Homelab per `nextcloudcmd` über das Internet mit der Storage Share.
Ein direktes Einbinden des Ordners als „Externer Speicher“ geht nicht, weil der
Nextcloud-Server keinen Zugriff auf das Homelab hat.

```
Homelab (brain/, git)  ⇄  nextcloudcmd (Cron)  ⇄  Hetzner Storage Share  ⇄  Laptop, Handy, Web
```

---

## Einrichtung

`nextcloudcmd` ist die Kommandozeilen-Variante des Nextcloud-Desktop-Clients. Ein Aufruf
synchronisiert einmal in beide Richtungen und beendet sich, das passt gut zu Cron.

Die Adresse der Storage Share steht in der Hetzner-Konsole und hat die Form
`https://nxXXXXX.your-storageshare.de`. In den Beispielen unten steht sie als
`https://nx12345.your-storageshare.de`.

### 1. Client installieren (Fedora-Host)

```bash
sudo dnf install nextcloud-client
nextcloudcmd --version
```

### 2. Zugang einrichten

1. In der Weboberfläche der Storage Share unter **Persönliche Einstellungen → Sicherheit →
   Geräte & Sitzungen** ein App-Passwort anlegen, z. B. „secondbrain-sync“. Nicht das normale
   Passwort verwenden: Ein App-Passwort lässt sich einzeln widerrufen.
2. In der Storage Share einen leeren Zielordner anlegen, z. B. `SecondBrain`.
3. Zugangsdaten in `~/.netrc` des Users ablegen, unter dem Cron läuft:

   ```
   machine nx12345.your-storageshare.de
   login dein-nextcloud-user
   password app-passwort-aus-schritt-1
   ```

   ```bash
   chmod 600 ~/.netrc
   ```

`-n` sorgt dafür, dass `nextcloudcmd` die Zugangsdaten aus `~/.netrc` liest. So stehen sie
weder in der Crontab noch in der Prozessliste.

### 3. Ausschlussliste

Die Datei `nextcloud-exclude.lst` im Projektverzeichnis legt fest, was **nicht** synchronisiert
wird:

| Muster | Grund |
|---|---|
| `.git/` | Ein Git-Repo, das in beide Richtungen synchronisiert wird, geht früher oder später kaputt. Git bleibt nur auf dem Homelab. |
| `.worker.lock`, `*.tmp`, `.*.tmp` | Lock und temporäre Dateien des Workers |
| `inbox/` | Warteschlange des Bots, wird nachts abgearbeitet |
| `.obsidian/workspace*.json` | Obsidian schreibt die Datei bei jedem Klick neu, das gäbe ständig Konflikte zwischen Geräten |

### 4. Einmal von Hand testen

```bash
cd /pfad/zu/secondbrain
nextcloudcmd -n --non-interactive \
  --exclude "$PWD/nextcloud-exclude.lst" \
  --path /SecondBrain \
  "$PWD/brain" https://nx12345.your-storageshare.de
```

Danach sollten in Nextcloud unter `SecondBrain/` die Ordner `wiki/`, `topics/` und `raw/` sowie
`index.md` und `log.md` liegen, aber kein `.git/` und kein `inbox/`.

Beim ersten Lauf werden alle Dateien hochgeladen. Rohdaten wie PDFs und Transkripte können
dauern.

### 5. Per Cron automatisieren

Der Sync nutzt **dasselbe Lock** wie Worker und Lint (`/tmp/secondbrain.lock`). So
synchronisiert er nie, während der Worker Dateien schreibt oder umbenennt.

Ergänzung zu `crontab.example` (`crontab -e`, Pfade und URL anpassen):

```
# Nach dem Nachtlauf (03:00) und dem Lint (So 03:30): Ergebnisse hochladen. flock wartet.
15 3 * * * flock /tmp/secondbrain.lock nextcloudcmd -n --non-interactive -s --exclude /pfad/zu/secondbrain/nextcloud-exclude.lst --path /SecondBrain /pfad/zu/secondbrain/brain https://nx12345.your-storageshare.de >> /var/log/secondbrain-sync.log 2>&1
45 3 * * 0 flock /tmp/secondbrain.lock nextcloudcmd -n --non-interactive -s --exclude /pfad/zu/secondbrain/nextcloud-exclude.lst --path /SecondBrain /pfad/zu/secondbrain/brain https://nx12345.your-storageshare.de >> /var/log/secondbrain-sync.log 2>&1

# Tagsüber alle 30 Minuten, damit Änderungen von anderen Geräten ankommen.
# flock -n: läuft gerade etwas anderes, wird dieser Durchgang übersprungen.
*/30 6-23 * * * flock -n /tmp/secondbrain.lock nextcloudcmd -n --non-interactive -s --exclude /pfad/zu/secondbrain/nextcloud-exclude.lst --path /SecondBrain /pfad/zu/secondbrain/brain https://nx12345.your-storageshare.de >> /var/log/secondbrain-sync.log 2>&1
```

Ablauf in einer Nacht:

```
03:00  worker   inbox → wiki, Themenseiten, Index, git commit
03:15  sync     neue Einträge → Nextcloud
06:00… sync     alle 30 min: Änderungen von anderen Geräten → Homelab
03:00  worker   nächste Nacht: committet auch die zurückgekommenen Änderungen
```

`/var/log/secondbrain-sync.log` muss für den Cron-User schreibbar sein. Alternativ ein Log im
Home-Verzeichnis verwenden.

### Prüfen

```bash
tail -50 /var/log/secondbrain-sync.log         # Fehler des letzten Laufs
git -C brain log --oneline -5                  # zurückgekommene Änderungen landen im nächsten Commit
```

---

## Hinweise

- **Konflikte:** Wird dieselbe Notiz zwischen zwei Sync-Läufen an zwei Stellen geändert, legt der
  Client eine Konfliktkopie an, z. B. `… (conflicted copy 2026-10-15 120000).md`. Die Kopie hat
  dieselbe `id` wie das Original. Der wöchentliche Lint löscht keine der beiden Dateien, sondern
  meldet sie („id … is in 2 files (sync conflict copy?)“). Welche Version bleibt, entscheidest
  du selbst: die gewünschte behalten, die andere löschen. Selbst nachsehen geht mit
  `find brain -name "*conflict*"`.
- **Was man auf anderen Geräten bearbeiten sollte:** Notizen in `wiki/`, vor allem Tags im
  Frontmatter. `index.md` und `topics/` werden bei jedem Lauf neu erzeugt, Änderungen daran
  gehen verloren. `log.md` nur ergänzen, nicht umschreiben.
- **Dateien umbenennen:** Das geht. Die Identität eines Eintrags ist die `id` im Frontmatter,
  nicht der Dateiname. Der Index folgt beim nächsten Lauf.
- **Obsidian auf dem Handy:** Die Nextcloud-App synchronisiert einen Vault-Ordner nicht von sich
  aus in beide Richtungen. Dafür eignen sich das Obsidian-Plugin „Remotely Save“ (WebDAV auf
  denselben Nextcloud-Ordner) oder unter Android FolderSync.
- **Datenschutz:** Mit dem Sync liegen alle Inhalte, auch `raw/` mit PDFs und Transkripten, bei
  Hetzner. Soll etwas nur auf dem Homelab bleiben, trägt man es in `nextcloud-exclude.lst` ein,
  z. B. `raw/`. Die Notizen verlinken dann auf Dateien, die in der Nextcloud fehlen.
- **Speicherplatz und Traffic:** Die Storage Share hat ein festes Kontingent. Den meisten Platz
  brauchen PDFs in `raw/`. Den Stand zeigt die Hetzner-Konsole.
- **Backup:** Nextcloud ist kein Backup. Die Git-Historie in `brain/` und die Backup-Routine des
  Homelabs bleiben die eigentliche Sicherung.

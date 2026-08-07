# Startlisten für den Karlie Krit — 08.08.2026

## Die zwei Dateien hier

| Datei | Für wen | Zweck |
|---|---|---|
| `startliste-vorlage.csv` | Erik | Pro Rennen eine Kopie ausfüllen (bib + name), wird direkt in racetag importiert |
| `tag-pool-inventar.csv` | Jan | Referenz: alle 73 jemals gesehenen Tags mit Nummern-Historie (Juni Leicht/Schwer, TT Juli) und Lesestatistik |

Die Vorlage enthält die **48 Tags, die nachweislich schon gelesen wurden** (physisch vorhanden + funktionieren). Wo bekannt, ist die `bib`-Spalte mit der Juni-Pool-Nummer vorbelegt — das ist nur ein Vorschlag und kann pro Rennen frei geändert werden. Die Spalte `referenz` zeigt, wer den Tag zuletzt trug — sie hilft beim Identifizieren des physischen Tags und **wird beim Import ignoriert**.

## Neue Pool-Vorlage direkt vom Scanner („Inventur")

Der schnellste Weg zu einer frischen Vorlage: alle Tags einmal an die Antenne halten.

1. Neues Rennen anlegen, z. B. „Inventur 05.08." — es muss **nicht gestartet** werden, Reads werden immer aufgezeichnet.
2. Reader läuft → jeden Tag einzeln 2–3× schwenken. Die Schwenk-Reihenfolge wird die Zeilenreihenfolge der CSV (z. B. gleich in Startnummern-Reihenfolge schwenken).
3. **„Export tags"** klicken (Button neben „Import CSV") → `racetag-tags-<rennen>-<datum>.csv`: jeder gelesene Tag genau einmal, `bib`/`name` leer zum Ausfüllen — direkt das Import-Format.
4. Spalte `reads` prüfen: ein Tag mit auffällig wenigen Reads bei gleichem Schwenken liest schlecht — testen oder aussortieren.

## Anleitung für Erik (pro Rennen, ~5 min)

1. Kopie der `startliste-vorlage.csv` anlegen, z. B. `rennen1-jedermann-leicht.csv`.
2. In Excel öffnen. Pro startendem Fahrer: **`bib` = Startnummer, `name` = Fahrername** eintragen.
3. Zeilen von Tags, die in diesem Rennen nicht ausgegeben werden, **einfach leer lassen oder löschen** — beides ist ok.
4. Speichern als CSV. **Egal welches CSV-Format** (Excel-Standard mit Semikolon, „CSV UTF-8", auch „Unicode Text") — der Import erkennt Trennzeichen und Encoding automatisch, Umlaute bleiben heil.
5. Datei an Jan (Stick/AirDrop/Mail).

**Nicht tun:** Spalte `tag_id` ändern · Spalten umsortieren oder Spalten *vor/zwischen* die ersten drei einfügen (zusätzliche Spalten *nach* `name` sind ok) · die Kopfzeile löschen (erste Zeile wird beim Import übersprungen).

## Import in racetag (Jan, ~1 min pro Rennen)

1. **Zuerst das Rennen anlegen/auswählen** — Fahrer sind pro Rennen gespeichert! (Runbook-Regel vom TT.)
2. „Import CSV" → Datei wählen. Toast zeigt `Imported N/M riders`; Fehlerzeilen erscheinen aufklappbar darunter (z. B. Zeilen ohne tag_id).
3. Zeilen ohne Namen werden mit leerem Namen importiert — **die Wertung läuft über die Startnummer**, Namen können jederzeit per erneutem Import derselben (ergänzten) Datei nachgezogen werden (Upsert per tag_id).

## Notfall-Plan „Liste kommt zu spät"

Das Rennen kann **pünktlich ohne Namen starten**: Vorlage unverändert importieren (Tags + Nummern sind dann gekoppelt), Liste später importieren — Namen erscheinen rückwirkend in Standings und Export. Kein Fahrer geht verloren, solange sein Tag gekoppelt ist.

## Bekannte Stolperfallen

- **Rein numerische Tag-IDs** (z. B. `000…5514`) macht Excel beim Öffnen zu Zahlen kaputt — die eine betroffene ID ist deshalb nicht in der Vorlage. Falls neue Tags dazukommen: IDs mit Buchstaben drin sind sicher.
- **Ein Tag doppelt vergeben** (zwei Zeilen, gleiche tag_id): die zweite Zeile gewinnt kommentarlos (Upsert). Vorlage pro Rennen frisch aus der Master-Kopie ziehen.
- Neue/unbekannte Tags vor Ort: einzeln per „Couple tag"-Modal koppeln (Tag schwenken → Modal geht auf), danach taucht der Fahrer nach der nächsten Lesung auf.

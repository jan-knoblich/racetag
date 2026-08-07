# Karli-Krit-Spickzettel — 08.08.2026 (offline-tauglich)

Alle Skripte: `python3 docs/karli-krit-2026-08-08/<skript>` aus `~/Documents/git/racetag`.
„App läuft" = Racetag ist offen (Skripte finden den Port selbst).

## App starten

- **`racetag`** — normal (wie Doppelklick) · **`racetag log`** — mit Live-Konsole im Terminal · `racetag debug` — zusätzlich `~/.racetag/logs/reader.log`
- Reader an **FritzBox LAN2** (LAN1 verhandelt nicht!). IP 192.168.178.22 + Power 250 sind gespeichert.
- **Reader-Verbindung tot:** App komplett beenden, `racetag log`, Connect beobachten. KEIN Auto-Reconnect. Laufendes Rennen übersteht das (Replay) — „Start race" NICHT erneut klicken.

## Die Skripte

| Befehl | Zweck | Braucht |
|---|---|---|
| `pruefe_kopplung.py` | **Der Prüflauf**: Soll/Ist aller 12 Rennen, Duplikate, Lauf↔Rad-Trennung, FG-Konsistenz, Formate. Am Ende muss „KEINE strukturellen Fehler" stehen. Während des Laufs kurz nicht koppeln (wechselt Rennen durch). | App läuft |
| `fix_falsches_rennen.py --quelle "…" --ziel "…" --von N --bis M --dry-run` | Kopplungen zwischen Rennen verschieben (falsches Rennen erwischt). **IMMER erst mit `--dry-run`**, dann ohne. | App läuft |
| `ergebnis_merge.py export.csv erik-liste.csv` | **Nach jedem Rennen**: „Export results"-CSV + Eriks Nummer↔Name-Liste → `…-mit-namen.csv` (Excel-fertig). Erik gewinnt bei Konflikten; No-Shows werden gelistet. | nichts (offline OK) |
| `lauf_auswertung.py` | **Nach dem Lauf**: 5/10-km-Wertung pro Block (10km=10. Überfahrt, 5km=5.), immun gegen Ziel-Schlenderer. Schreibt `ergebnis-lauf-*.csv`. | nichts (liest DB read-only) |
| `namen_einspielen.py liste.csv --dry-run` | Optional: digitale Nummer;Name-Liste live ins AKTIVE Rennen. | App läuft |
| `fetch_meldelisten.py` | Meldelisten frisch vom Portal. | **INTERNET** |
| `nummern_zuweisung.py` | ⚠ NICHT mehr laufen lassen — würde gedruckte Nummern verschieben! Nur für `LATE_FIXED`-Einträge (Nachzügler auf feste Reserve-Nummer, oben im Skript). | — |
| `gen_startnummern.py --banner banner-strip.png --split-before 1600` | Startnummern-PDFs neu erzeugen (Nachdruck: einzelne Seiten aus dem PDF drucken). | — |

**Backup jederzeit:** `cp -r ~/.racetag/data ~/Desktop/backup-$(date +%H%M)`

## Zirkel für die Auto-Zuweisung (exakt so ins Feld tippen)

| Rennen | Eingabe |
|---|---|
| 09:00 Lauf | `101-211,301-416,501-514` |
| 11:20 R1 | `81-90,301-304` |
| 12:10 R2 | `181-193,316-326` |
| 13:00 R3 | `281-294,331-343,401-410` |
| 15:00 leicht | `1-50` |
| 16:00 FG Quali | `411-436` (danach Export tags → in 19:30 + 20:00 importieren!) |
| 16:15 Frauen | `351-366,371-381,391-395` |
| 17:15 mittel | `101-175` ✓ FERTIG |
| 18:15 schwer | `201-240` |
| 19:30 FLINTA | `441-446` (FG-B kommt per Import aus der Quali) |

**Beim Koppeln:** Rennen im Dropdown wählen → Koppel-Modus → Zirkel → Auto-Start. Erst schwenken, dann kleben, EIN Tag zur Zeit. Fremde (schon beklebte) Plaketten WEG von der Antenne — sie sind im aktiven Rennen „unbekannt" und fressen eine Nummer!

## Pro Rennen morgen

1. Eriks Liste entgegennehmen → ablegen (Namen kommen NACHHER per `ergebnis_merge`).
2. Rennen im Dropdown wählen → Format checken: **total_laps NUR im Header ändern, NIE Zahnrad!** Zeit-Rennen: `final_laps` nach Ansage.
3. Startschuss → **„Start race"**.
4. Leader im Ziel → Abwink-Modell läuft von selbst → **„End race" SOFORT nach dem letzten Zieldurchgang** → „Export results" → CSV sichern.
5. **Direkt das nächste Rennen auswählen (nicht starten!)** — Überfahrten sind dann harmlose 0-Runden-Check-ins.

**Lauf-Spezial:** total_laps=10 — NIEMALS auf 5 drehen (Bug M3/M4, würde 10-km-Läufer einfrieren). 5-km-Wertung kommt aus `lauf_auswertung.py`.

## Reparatur während des Rennens

- Runde doppelt → **−1** · Runde fehlt (⚠-Marker) → **+1** (Prefill prüfen)
- Manueller Zeitstempel: **UTC = Ortszeit − 2 h** (09:05:23 → `2026-08-08T07:05:23.000Z`)
- Passant/Nicht-Starter zählt mit → Statusknopf **DNS/DSQ** oder „Reset rider" (geht auch nach Rennende, vor dem Export)
- Versehentlich „End race" → **„Reopen race"**
- Falscher Name/Nummer → **„Fahrer"**-Button oder Doppelklick auf die Zeile. Haken „In allen Rennen" **AUS** lassen!
- Nachmeldung: freie Reserve-Plakette ausgeben — fertig (zählt ab erster Überfahrt), Name egal.

## Wenn gar nichts mehr geht

1. `racetag log` — was sagt die Konsole?
2. DB ist der Audit-Trail: JEDE Überfahrt ist in `~/.racetag/data/racetag.db` gespeichert, auch wenn die UI spinnt. Rennen kann nach App-Neustart weiterlaufen (Replay).
3. Snapshots liegen neben der DB (alle 2 min während eines Rennens).
4. Notfall-Wertung geht immer im Nachhinein aus der DB (wie `lauf_auswertung.py` — Überfahrten × Zeitstempel).

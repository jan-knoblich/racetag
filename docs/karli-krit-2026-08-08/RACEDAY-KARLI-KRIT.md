# RACEDAY-Runbook — Karli Krit Leipzig, 08.08.2026

Rennbüro + Start/Ziel: Karl-Liebknecht-Straße 31. **Nummern-Modell: eine Nummer + ein Tag pro PERSON für den ganzen Tag** (Mehrfachstarter behalten beides).

## Heute Abend (Vorbereitung, ~1 h)

1. **App starten** (Doppelklick Racetag.app — neuer Build!). Reader kann aus bleiben fürs Rennen-Anlegen.
2. **Rennen anlegen:** `python3 docs/karli-krit-2026-08-08/create_races.py` — legt alle 11 Slots an (idempotent, findet den App-Port selbst). Danach im Race-Dropdown prüfen.
3. **Koppel-Session** (Reader an, FritzBox **LAN2!**):
   - Rennen egal (z. B. Default race) — Riders sind zwar per-race, aber der Export läuft über die Reads + Kopplungen desselben Rennens: **im selben Rennen bleiben** für Koppeln + Export!
   - **„Koppel-Modus"** klicken → Tag ans Lesefeld → grüne Karte „NEUER TAG" + Doppel-Piep → **Nummer vom Label tippen → Enter** → nächster Tag. Name leer lassen (kommt morgen).
   - Karte blau „Bereits gekoppelt: Nr. X" = dieser Tag ist schon vergeben (Label-Check!). Falsches Label → „Ändern (neu koppeln)".
   - Doppelte Nummer wird gewarnt (zweites Enter überschreibt bewusst).
4. **Master-CSV exportieren:** „Export tags" → `racetag-tags-….csv` speichern als **`tagesmaster.csv`**. Enthält jetzt tag_id;bib;(name) aller gekoppelten Tags. Spalte `reads` checken: Tag mit auffällig wenigen Reads liest schlecht → aussortieren.
5. **Master in ALLE 11 Rennen importieren:** pro Rennen: Race wählen → Import CSV → tagesmaster.csv (≈10 s/Rennen). Unbenutzte Tags bleiben unsichtbar — Teilnehmer erscheinen erst nach dem ersten Read.
6. Backup: `cp -r ~/.racetag/data ~/Desktop/karli-vorabend.bak`

## Morgen früh (5 min)

- Frische Meldelisten ziehen (Nachmeldungen über Nacht): `python3 docs/karli-krit-2026-08-08/fetch_meldelisten.py` — überschreibt `rennen/*.csv`.

## Morgen pro Slot (Friction-Minimum)

| Schritt | Aktion |
|---|---|
| Sign-on läuft | Erik notiert Namen ↔ ausgegebene Nummer auf der Meldeliste (`rennen/<HHMM>-….csv` ausgedruckt/als Referenz) |
| ~5 min vorher | Race im Dropdown wählen → Format checken: Runden-Rennen `total_laps` im **Header** (NIE Zahnrad — M3/M4-Bug!), Zeit-Rennen `final_laps` (Default 3) nach Ansage des Sprechers |
| Startschuss | **„Start race"** klicken |
| Während | Glocke/laps-to-go-Banner beachten; ⚠-Marker = evtl. verpasste Lesung → Reparatur-Kasten |
| Leader im Ziel | Abwink-Modell läuft automatisch (jeder wird bei seiner nächsten Durchfahrt gewertet) |
| Danach | **„End race"** → **„Export results"** → CSV sichern. Versehentliches End: „Reopen race" |

**Namen nachziehen (optional, zwischen den Slots):** `tagesmaster.csv` in Excel offen halten; Namen neben die vergebene Nummer eintragen (aus Eriks Liste); vor dem Ergebnis-Export einmal ins aktuelle Rennen re-importieren → Namen erscheinen rückwirkend in Standings + Export. Eine Datei für den ganzen Tag (Nummern sind personenfest!).

**Nachmeldung vor Ort:** neuer Fahrer bekommt freien Tag+Nummer → Koppel-Modus kurz an (oder „Couple tag → rider") im AKTUELLEN Rennen koppeln, in tagesmaster.csv nachtragen (sonst fehlt er in späteren Rennen!).

## Reparatur-Kasten (Kurzfassung — Details im TT-Runbook `docs/issues/RECHECK-2026-07-25.md` §5)

- Runde zu viel (Doppelzählung): **−1** beim Fahrer.
- Runde fehlt (⚠-Marker): **+1** — Prefill-Zeitstempel prüfen.
- Manueller Zeitstempel: **UTC = Ortszeit − 2 h** (08:05:23 → `2026-08-08T06:05:23.000Z`).
- Getaggter Nicht-Starter quert die Linie mid-race: Zeile via Statusknopf **DSQ/DNF** setzen oder ignorieren; „Reset rider" entfernt die Wertung.
- Fixed-Gear-Finals (19:30/20:00): gleiche Tags wie Quali, eigenes Rennen — nichts umkoppeln.

## Troubleshooting

- **Keine Reads:** Reader an **LAN2** der FritzBox? (LAN1 verhandelt nicht.) IP 192.168.178.22, Power 250 — beides persistiert, Doppelklick reicht.
- **Reader-Verbindung tot** (Kabel/Strom): App komplett beenden und neu starten — es gibt KEINEN Auto-Reconnect. Laufendes Rennen übersteht das: Events werden gespoolt/replayed, „Start race" NICHT erneut klicken.
- **SSE „Connecting…" dauerhaft:** Seite neu laden (in der App: App-Neustart).
- Koppel-Modus piept nicht: Ton-Häkchen im Panel; Karte ist die Wahrheit, Piep nur Komfort.

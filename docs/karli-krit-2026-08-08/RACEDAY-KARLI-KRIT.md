# RACEDAY-Runbook — Karli Krit + Karli Lauf, Leipzig 08.08.2026

Rennbüro + Start/Ziel: Karl-Liebknecht-Straße 31. In der App sind **12 Rennen** angelegt (09:00 Lauf bis 20:00 Fixed Gear A).

## Das Modell in einem Satz

**Tag klebt auf Plakette N ⇒ Tag↔Nummer ist dauerhaft.** Die Zirkel bestimmen nur, welche Plaketten in welchem Rennen ausgegeben werden; die Person (der Name) wechselt pro Rennen. Nummern dürfen sich zwischen Lauf und Rad überlappen — jedes Rennen hat seine eigene Fahrerliste.

### Nummernzirkel (Excel „Nummernzirkel Karli Krit" + Erik-Ansage für den Lauf)

| Rennen | Zirkel |
|---|---|
| 09:00 Lauf (EIN Rennen, 5+10 km gleichzeitig) | Erwachsene 10 km = 100er-Block, dann 300er, 500er usw. — Blöcke trennen die Wertung |
| 11:20 R1 | U15m `81-92` · U17w `301-306` |
| 12:10 R2 | U17m `181-190` · Masters 4 `316-326` |
| 13:00 R3 | Masters 2 `281-294` · Masters 3 `331-343` — ⚠ Junioren/U19m-Zirkel fehlt im Excel, Erik fragen |
| 15:00 Jedermann leicht | `1-75` |
| 16:15 Frauen | Lizenz `351-369` · Jedefrau `371-381` · Juniorinnen `391-399` |
| 17:15 Jedermann mittel | `101-175` |
| 18:15 Jedermann schwer | `201-275` |
| Kids / Fixed Gear | kein Zirkel im Excel — vor Ort klären |

## Heute Abend: Kleben mit Auto-Zuweisung (Fließband)

1. `racetag` (oder `racetag log` fürs Terminal-Log). Reader an **FritzBox LAN2!**
2. In einem Arbeits-Rennen bleiben (z. B. Default race) → **„Koppel-Modus"** → unten den Zirkel eintragen (z. B. `1-75` oder `1-75,81-92,101-175`) → **„Auto-Zuweisung starten"**.
3. Fließband: App zeigt groß **„Nr. 1"** → frischen Tag über die Antenne → Piep = gekoppelt, App zeigt „Nr. 2" → Tag auf Plakette 1 kleben → nächster Tag. **Reihenfolge ist alles:** immer erst schwenken, dann kleben, nie zwei Tags gleichzeitig ins Feld. Plakette fehlt/kaputt → „Überspringen".
4. Schon-gekoppelte Tags stören nicht: blauer Hinweis „schon Nr. X", die Schleife rückt NICHT weiter.
5. Alle Zirkel durcharbeiten (auch Läufer-Plaketten!). Abbrechen/fortsetzen jederzeit — beim Neustart bietet die Schleife nur noch freie Nummern an.
6. **„Export tags"** → als `tagesmaster.csv` sichern (enthält jetzt tag_id;Nummer). `reads`-Spalte checken: schwache Tags aussortieren.
7. **Master in ALLE 12 Rennen importieren** (Race wählen → Import CSV, ~10 s je Rennen). Damit ist jede Plakette in jedem Rennen startklar — Ausgabe einer Plakette ist die komplette „Anmeldung", ganz ohne Reader.
8. Backup: `cp -r ~/.racetag/data ~/Desktop/karli-vorabend.bak`

## Morgen früh (5 min)

- Frische Meldelisten: `python3 docs/karli-krit-2026-08-08/fetch_meldelisten.py` (überschreibt `rennen/*.csv`, inkl. Lauf — gestern 199 Lauf- + 213 Rad-Meldungen).

## Pro Rennen (Friction-Minimum)

| Schritt | Aktion |
|---|---|
| Sign-on | Plakette aus dem Zirkel des Rennens ausgeben; Erik notiert Name ↔ Nummer auf der Meldeliste (`rennen/<HHMM>-….csv`) |
| ~5 min vorher | Race im Dropdown wählen → Format checken: `total_laps` im **Header** (NIE Zahnrad — M3/M4-Bug!), Zeit-Rennen: `final_laps` nach Ansage |
| Startschuss | **„Start race"** |
| Während | Namen eintragen (unten); ⚠-Marker = evtl. verpasste Lesung → Reparatur-Kasten |
| Leader im Ziel | Abwink-Modell läuft automatisch (Lauf: siehe eigener Kasten unten) |
| Danach | **„End race"** → **„Export results"** → CSV sichern. Versehentlich beendet: „Reopen race" |
| Sofort danach | **Nächstes Slot-Rennen auswählen** (nicht starten!) — siehe unten |

### Lauf-Sonderfall: 5 km + 10 km in einem Rennen

Das Rennen läuft mit **total_laps = 10** — **NIEMALS während oder nach dem Lauf auf 5 zurückdrehen!** (Offener M3/M4-Bug: `finished` wird nicht neu berechnet, und 10-km-Läufer würden bei ihrer nächsten Überfahrt fälschlich bei 5 Runden eingefroren.) Die 5-km-Läufer brauchen auch kein Einfrieren: Jede Überfahrt liegt mit Zeitstempel im Audit-Trail.

**Auswertung nach dem Lauf (End race, dann):** `python3 docs/karli-krit-2026-08-08/lauf_auswertung.py` — liest die DB read-only und nimmt pro Läufer exakt die N-te Überfahrt seines Blocks (100er → 10., 300er → 5.; Blöcke oben im Skript anpassen, sobald Erik sie bestätigt). Immun gegen Ziel-Schlenderer (Extra-Überfahrten werden ignoriert und ausgewiesen); Läufer mit zu wenigen Lesungen landen als „unvollständig" unter der Wertung statt mit falscher Zeit drin. Ausgabe: `ergebnis-lauf-<block>.csv` sortiert nach Zeit. Die Live-Standings während des Laufs zeigen für 5-km-Läufer kein „finished" — egal, die Wertung kommt aus dem Skript.

**Namen eintragen — ohne Reader, jederzeit:** Button **„Fahrer"** (oder Doppelklick auf Standings-Zeile) → Nummer suchen → Name tippen → Enter. **⚠ Haken „In allen Rennen übernehmen" AUS lassen** (ist Standard): dieselbe Plakette trägt in verschiedenen Rennen verschiedene PERSONEN — Übernehmen würde Namen in frühere/spätere Rennen schreiben. Nur setzen, wenn jemand nachweislich mit derselben Plakette mehrfach startet.

**Plaketten-Recycling zwischen den Rennen:** eingesammelte Plakette einfach neu ausgeben — Tag+Nummer bleiben ja gleich, nur der Name im neuen Rennen ist anders (Fahrer-Editor). Nichts umkoppeln.

**Nachmeldung:** freie Plakette aus dem Zirkel ausgeben, Name per Fahrer-Editor — fertig, kein Reader nötig.

**Zwischen den Rennen (wichtig!):** Ein *laufendes* Rennen bei offener Strecke ist das einzige Fenster, in dem Passanten/Warmup-Fahrer mit Plakette echte Runden sammeln. Deshalb: (1) **„End race" sofort nach dem letzten Zieldurchgang.** (2) **Direkt das nächste Rennen auswählen, NICHT starten** — Überfahrten sind dann 0-Runden-Check-ins, die nebenbei die Tags verifizieren. Passant in laufendem Rennen: DNS/DSQ setzen oder „Reset rider" (geht auch nach Rennende, vor dem Export).

## Reparatur-Kasten (Details: TT-Runbook `docs/issues/RECHECK-2026-07-25.md` §5)

- Runde doppelt: **−1** · Runde fehlt (⚠): **+1** (Prefill-Zeitstempel prüfen)
- Manueller Zeitstempel: **UTC = Ortszeit − 2 h** (09:05:23 → `2026-08-08T07:05:23.000Z`)
- Falsch gekoppelt: Fahrer-Editor (Doppelklick) → Nummer korrigieren

## Troubleshooting

- **Keine Reads:** Reader an **LAN2**? IP 192.168.178.22 + Power 250 sind persistiert.
- **Reader-Verbindung tot:** App beenden + neu starten (`racetag log` zeigt den Connect live). KEIN Auto-Reconnect. Laufendes Rennen übersteht das (Spool/Replay), „Start race" NICHT erneut klicken.
- Logs: `racetag log` = Live-Konsole; `~/.racetag/data/racetag.db` = lückenloser Audit-Trail; `~/.racetag/logs/` nur Spool/Debug.
- Kein Piep: Ton-Häkchen im Panel; die Karte ist die Wahrheit.

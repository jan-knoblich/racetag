# RACEDAY-Runbook — Karli Krit + Karli Lauf, Leipzig 08.08.2026

Rennbüro + Start/Ziel: Karl-Liebknecht-Straße 31. In der App sind **12 Rennen** angelegt (09:00 Lauf bis 20:00 Fixed Gear A).

## Das Modell in einem Satz

**Tag klebt auf Plakette N ⇒ Tag↔Nummer ist dauerhaft.** Die Zirkel bestimmen nur, welche Plaketten in welchem Rennen ausgegeben werden; die Person (der Name) wechselt pro Rennen. Nummern dürfen sich zwischen Lauf und Rad überlappen — jedes Rennen hat seine eigene Fahrerliste.

### Nummernzirkel (Excel „Nummernzirkel Karli Krit" + Erik-Ansage für den Lauf)

| Rennen | Zirkel |
|---|---|
| 09:00 Lauf (EIN Rennen, 5+10 km gleichzeitig) | 10 km `101-220` · 5 km `301-420` · U18 `501-530` — inkl. **+20 % Nachmelde-Puffer** (10 km: 193–211, 5 km: 397–416, U18: 512–514 sind Puffer-Blätter mit leerem Namen); Blöcke trennen die Wertung |
| 11:20 R1 | U15m `81-90` · U17w `301-304` |
| 12:10 R2 | U17m `181-193` · Masters 4 `316-326` — beide VOLL, keine Reserve |
| 13:00 R3 | Masters 2 `281-294` · Masters 3 `331-343` · Junioren `401-410` |
| 15:00 Jedermann leicht | `1-50` (gekürzt) |
| 16:00/19:30/20:00 Fixed Gear | `411-436` · FLINTA `441-446` (FG behält die Plakette Quali→Finals) |
| 16:15 Frauen | Lizenz `351-366` · Jedefrau `371-381` · Juniorinnen `391-395` |
| 17:15 Jedermann mittel | `101-175` (volle Breite) |
| 18:15 Jedermann schwer | `201-240` (gekürzt) |

| Kids | kein Zirkel — vor Ort klären |

(Zirkel am 07.08. spät aufs Tag-Budget gekürzt: Zirkel ≈ Meldungen + realistische Reserve. Achtung: der leicht-Zirkel liegt damit UNTER dem 75er-Rennlimit — wenn der Andrang das sprengt, Reserve-Bereiche 451-500/600+ nachziehen.)

Tagesweit noch komplett frei (für Kids/Spontanes): `76-80, 93-100, 200, 276-280, 295-300, 400, 451-500, 600+`.
**Alle 412 Meldungen sind nummeriert** (`zuweisung/`-Listen, Stand 07.08. abends) — beim Kleben daran denken: auch die Plaketten **181–193, 401–410, 411–450** brauchen Tags!

## Drucken: `startnummern-karli-krit.pdf` (in Downloads)

**545 A5-Querseiten** im A5.docx-Format (Banner + Riesennummer), sortiert nach Slot → Nummer (Stapel kommen rennfertig aus dem Drucker):
- Lauf: 199 Gemeldete + 42 Puffer (+20 % je Block) = 241 Blätter
- Rad: komplette (teils gekürzte) Zirkel = 304 Blätter; Reserve-Nummern stehen mit leerem Namen am Ende der Anmeldelisten
- **175 Nummern sind absichtlich doppelt** (Lauf-Papier UND Rad-Plakette) — zwei physische Nummern, zwei Tags!
- ⚠ **~545 Tags** werden heute Abend gebraucht. U17m + Masters 4 sind voll gemeldet — dort KEINE Reserve.

Neu erzeugen: `gen_startnummern.py --banner banner-strip.png`.

## Heute Abend: Kleben mit Auto-Zuweisung — PRO RENNEN (entschieden 07.08. ~21:30)

**Jede Plakette wird direkt in ihrem Rennen gekoppelt.** Kein Arbeits-Rennen, keine Master-Importe — die Fahrerlisten entstehen beim Koppeln selbst. Doppelte Nummern (Lauf 101 vs. mittel 101) sind dadurch automatisch sauber getrennt.

1. `racetag` (oder `racetag log`). Reader an **FritzBox LAN2!**
2. **Pro Rennen:** Rennen im Dropdown wählen → „Koppel-Modus" → Zirkel des Rennens eintragen (Tabelle oben) → „Auto-Zuweisung starten" → Fließband: schwenken → Piep → kleben → nächster. Immer erst schwenken, dann kleben, ein Tag zur Zeit; kaputte Plakette → „Überspringen". Beim Neustart bietet die Schleife nur freie Nummern an.
3. **Fixed Gear (Plakette bleibt beim Fahrer über 3 Rennen):** 411-436 nur im **Quali-Rennen (16:00)** koppeln → dort „Export tags" → die Datei in **19:30** und **20:00** importieren (gleiche Plakette = gleicher Tag). FLINTA 441-446 zusätzlich direkt im 19:30-Rennen per Auto-Zuweisung.
4. **Lauf-Papiere** (101-211, 301-416, 501-514) im Lauf-Rennen — fertig zu Ende koppeln.
5. **Prüflauf:** `python3 docs/events/2026-08-08-karli-krit/pruefe_kopplung.py` — prüft alle 12 Rennen: Soll/Ist pro Zirkel, doppelte Nummern, Lauf↔Rad-Tag-Trennung, FG-Tag-Gleichheit, Formate, Start-Flags. Muss am Ende „KEINE strukturellen Fehler" sagen und überall voll gekoppelt zeigen.
6. Backup: `cp -r ~/.racetag/data ~/Desktop/karli-vorabend.bak`

**Sign-on morgen:** Anmeldeliste (`zuweisung/…-anmeldeliste.csv`, drucken) → Name abhaken → Plakette ausgeben. Namen kommen NICHT live in die App — Ergebnisse werden im Nachhinein gemerged (siehe unten). Falsch gekoppelte Plaketten-Gruppen repariert `fix_falsches_rennen.py --quelle … --ziel … --von … --bis …` (Dry-Run zuerst).

## Morgen früh (5 min)

- Frische Meldelisten: `python3 docs/events/2026-08-08-karli-krit/fetch_meldelisten.py` (überschreibt `rennen/*.csv`, inkl. Lauf — gestern 199 Lauf- + 213 Rad-Meldungen).

## Pro Rennen (Friction-Minimum)

| Schritt | Aktion |
|---|---|
| Sign-on | Plakette aus dem Zirkel des Rennens ausgeben; Erik führt Name ↔ Nummer |
| **T−15: Eriks finale Liste kommt** | Nur die DELTAS zur Vorab-Zuweisung einpflegen (siehe unten) — die Zeitmessung wartet NIE auf Namen, das Rennen startet notfalls mit halbfertiger Liste |
| ~5 min vorher | Race im Dropdown wählen → Format checken: `total_laps` im **Header** (NIE Zahnrad — M3/M4-Bug!), Zeit-Rennen: `final_laps` nach Ansage |
| Startschuss | **„Start race"** |
| Während | Namen eintragen (unten); ⚠-Marker = evtl. verpasste Lesung → Reparatur-Kasten |
| Leader im Ziel | Abwink-Modell läuft automatisch (Lauf: siehe eigener Kasten unten) |
| Danach | **„End race"** → **„Export results"** → CSV sichern. Versehentlich beendet: „Reopen race" |
| Sofort danach | **Nächstes Slot-Rennen auswählen** (nicht starten!) — siehe unten |

### Lauf-Sonderfall: 5 km + 10 km in einem Rennen

Das Rennen läuft mit **total_laps = 10** — **NIEMALS während oder nach dem Lauf auf 5 zurückdrehen!** (Offener M3/M4-Bug: `finished` wird nicht neu berechnet, und 10-km-Läufer würden bei ihrer nächsten Überfahrt fälschlich bei 5 Runden eingefroren.) Die 5-km-Läufer brauchen auch kein Einfrieren: Jede Überfahrt liegt mit Zeitstempel im Audit-Trail.

**Auswertung nach dem Lauf (End race, dann):** `python3 docs/events/2026-08-08-karli-krit/lauf_auswertung.py` — liest die DB read-only und nimmt pro Läufer exakt die N-te Überfahrt seines Blocks (100er → 10., 300er → 5.; Blöcke oben im Skript anpassen, sobald Erik sie bestätigt). Immun gegen Ziel-Schlenderer (Extra-Überfahrten werden ignoriert und ausgewiesen); Läufer mit zu wenigen Lesungen landen als „unvollständig" unter der Wertung statt mit falscher Zeit drin. Ausgabe: `ergebnis-lauf-<block>.csv` sortiert nach Zeit. Die Live-Standings während des Laufs zeigen für 5-km-Läufer kein „finished" — egal, die Wertung kommt aus dem Skript.

### Eriks 15-Minuten-Liste: NICHTS tun müssen — Namen kommen im Nachhinein

**Entschieden 07.08.: Tagsüber läuft alles nur über Nummern.** Eriks Liste pro Rennen einfach einsammeln/ablegen — eingeheiratet werden die Namen NACH dem Rennen:

```
„Export results" → python3 docs/events/2026-08-08-karli-krit/ergebnis_merge.py export.csv erik-liste.csv
```

→ schreibt `…-mit-namen.csv` (Excel-tauglich): Namen aus Eriks Liste (Erik gewinnt bei Konflikt mit Vorab-Namen), Warnung für Nummern ganz ohne Namen, No-Shows werden gelistet. Funktioniert genauso für die Lauf-Ergebnisse aus `lauf_auswertung.py`. Eriks Listenformat ist egal (Nummer;Name oder Name,Nummer, Kopfzeile ja/nein) — muss nur digital vorliegen (abtippen/abfotografieren+abtippen reicht abends).

Optional, wenn zwischendurch Luft ist (schöner für Live-Standings/Sprecher): Namen per **„Fahrer"**-Button oder `namen_einspielen.py liste.csv` (gegen die laufende App, aktives Rennen) schon tagsüber einpflegen — nötig ist es nicht.

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

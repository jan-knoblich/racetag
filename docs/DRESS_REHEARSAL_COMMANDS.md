# Racetag — Generalprobe Cheat-Sheet (2026-06-14)

> ⚠️ **STAND JUNI — TEILWEISE VERALTET.** Für das TT-Training am 26.07. gilt das
> Runbook in `docs/issues/RECHECK-2026-07-25.md`. Seit Juli macht die App
> selbst, was hier noch manuell beschrieben ist: **kein `export READER_IP`**
> (§2 — IP kommt aus den Settings), **kein SSH für Antennen-Power/mux** (§1 —
> Auto-Konfiguration beim Connect). Weiterhin gültig: §0 Netz-Checks, §6
> Troubleshooting, §7 Backup.

**Druck mich aus** und nimm mich mit auf die Bahn. Alle Commands sind copy-paste-fertig, mit den aktuell gültigen IPs.

## Aktuelle Konfiguration

| Was | Wert |
| --- | --- |
| FritzBox | **7330**, FritzOS 6.56, IP `192.168.178.1`, SSID am Aufkleber |
| Reader | Sirit INfinity 510, MAC `00:17:9E:00:37:D2`, **IP `192.168.178.22`** (fixe DHCP-Lease) — **an Port LAN2 anschließen, NICHT LAN1** (LAN1 = Gigabit, verhandelt mit dem alten Sirit-PHY nicht; 26.07. bestätigt) |
| Reader-Hardware | F1-Sicherung mit Drahtbrücke überbrückt (echte Sicherung folgt) |
| Racetag-App | `/Users/jan/Documents/git/racetag/apps/desktop/dist/Racetag.app` |
| Backup-App | `/Users/jan/Documents/git/racetag/apps/desktop/dist/Racetag.app.prev` (Rollback) |

---

## 0. Pre-Flight am Mac (vor dem ersten Tag-Wave)

```bash
# Bin ich im FritzBox-Netz? (sollte 192.168.178.x sein)
ifconfig | awk '/^[a-z]/{iface=$1} /inet [0-9]/ && !/inet 127/{print iface, $2}'

# FritzBox erreichbar?
ping -c 3 192.168.178.1

# Reader erreichbar?
ping -c 3 192.168.178.22

# Reader-Ports offen (CONTROL=50007, EVENT=50008)?
nc -vz -w 3 192.168.178.22 50007
nc -vz -w 3 192.168.178.22 50008
```

Erwartet: alle 4 Checks grün. Wenn `ping 192.168.178.22` scheitert → siehe Abschnitt **Troubleshooting**.

---

## 1. SSH in den Reader

```bash
ssh -oKexAlgorithms=+diffie-hellman-group14-sha1 \
    -oHostKeyAlgorithms=+ssh-rsa \
    -oPubkeyAcceptedAlgorithms=+ssh-rsa \
    cliuser@192.168.178.22
```

User: `cliuser`, **kein Passwort** (Enter drücken).

Falls `~/.ssh/config` schon gesetzt ist (`Host 192.168.178.22` Block):

```bash
ssh cliuser@192.168.178.22
```

### Reader-CLI (innerhalb der SSH-Sitzung)

```text
# Status
setup.operating_mode                 # erwartet: active
setup.operating_mode=active          # falls standby → aktivieren

# Antennen-Power (0,1 dBm Schritte; 300 = 30 dBm Max)
antennas.1.conducted_power           # aktuellen Wert lesen
antennas.1.conducted_power=300       # auf Max für die Generalprobe
setup.operating_mode=active          # neu aktivieren

# Tags die aktuell gesehen werden (Reader-DB, unabhängig von Racetag)
tag.db.get()
tag.db.clear()                       # vor Test-Beginn zurücksetzen

# Komplette Config dump
reader.profile.show_running_config()

# Reader-Neustart (wenn was hängt)
reader.reboot()                      # 60 s warten, dann re-pingen
```

---

## 2. Racetag starten

```bash
# Alte Instanz killen (falls eine läuft)
pkill -f "dist/Racetag.app/Contents/MacOS/Racetag"
sleep 1

# Saubere DB (nur wenn frisch starten, sonst weglassen!)
# mkdir -p ~/.racetag/backups
# cp ~/.racetag/data/racetag.db ~/.racetag/backups/pre-rehearsal-$(date +%Y%m%d-%H%M).db 2>/dev/null
# rm -f ~/.racetag/data/racetag.db*

# Reader-IP für diese Session setzen
export READER_IP=192.168.178.22

# Starten (Foreground, Logs sichtbar)
/Users/jan/Documents/git/racetag/apps/desktop/dist/Racetag.app/Contents/MacOS/Racetag
```

### Oder im Hintergrund + Log-Datei

```bash
export READER_IP=192.168.178.22
/Users/jan/Documents/git/racetag/apps/desktop/dist/Racetag.app/Contents/MacOS/Racetag > /tmp/racetag_run.log 2>&1 &

# Port aus Log holen
grep -oE 'http://127.0.0.1:[0-9]+' /tmp/racetag_run.log | head -1

# Reader-Init-Status prüfen (sollte CONTROL connected + EVENT connected zeigen)
grep -E "CONTROL connected|EVENT connected|configuration applied|serial_number" /tmp/racetag_run.log
```

**Im Log erwartet (Reihenfolge wichtig):**

```
Spawning reader-service: ... --ip 192.168.178.22 ...
Connecting to CONTROL at 192.168.178.22:50007...
CONTROL connected.
Connecting to EVENT at 192.168.178.22:50008...
EVENT connected.
[SESSION] obtained id from EVENT: <N>
[SESSION] bound event channel id <N>
[CONTROL] >> info.time_zone=UTC     ← Zone ZUERST
[CONTROL] >> info.time=...           ← dann Zeit
[READER] serial_number=0973B40149483C3F
[SESSION] configuration applied (11 commands ...)
```

---

## 3. Im UI testen (pywebview-Fenster oder Browser auf `http://127.0.0.1:<PORT>`)

### Rennen für die Generalprobe anlegen

1. **„+" neben Race-Selector** → Modal öffnet sich
2. Name: `Generalprobe Bahn 2026-06-14`
3. Termin: heute, Startzeit eintragen
4. Total laps: realistisch (z. B. 5 für einen 250m-Kurs)
5. Häkchen „Activate this race after creating" lassen
6. **Create**

### Rider koppeln

- Beim Tag-Wave erscheint Modal automatisch → Bib + Name eintragen
- **Oder**: „Couple tag → rider" klicken, Tag schwenken, dann Bib/Name

### Rennen starten

- **Start race** klicken → Banner: „Running since HH:MM:SS"
- **Wichtig**: nach Start mindestens **10 s warten**, bevor der erste Tag gewaved wird (First-Lap-Cooldown)

### Rennen beenden

- **End race** klicken → Banner: „Ended at HH:MM:SS"
- **SOFORT** danach: **Export results** klicken → CSV wird heruntergeladen
- **⚠️ Erst NACH CSV-Export** App neu starten oder anderes Rennen aktivieren (BUG-004: ended-Rennen verlieren nach Restart die Standings-Anzeige)

---

## 4. Backend per curl (für Debug / Skripting)

```bash
PORT=53895   # aus Log-Output anpassen

# Status
curl -s http://127.0.0.1:$PORT/race | python3 -m json.tool
curl -s http://127.0.0.1:$PORT/classification | python3 -m json.tool
curl -s http://127.0.0.1:$PORT/riders | python3 -m json.tool
curl -s http://127.0.0.1:$PORT/races | python3 -m json.tool

# Race control (alternative zum UI)
curl -s -X POST http://127.0.0.1:$PORT/race/start
curl -s -X POST http://127.0.0.1:$PORT/race/end
curl -s -X POST http://127.0.0.1:$PORT/race/reset

# CSV-Export
curl -s -OJ http://127.0.0.1:$PORT/classification.csv
ls -la racetag-*.csv
```

---

## 5. Generalprobe — was wir konkret testen

### Pre-Flight (vor Renn-Beginn, ~15 min)

- [ ] Mac im FritzBox-Netz (`ifconfig` zeigt `192.168.178.x`)
- [ ] `ping 192.168.178.22` antwortet
- [ ] SSH in Reader funktioniert, `tag.db.get()` zeigt Reads (Tag in Reichweite halten)
- [ ] Antennen-Power per SSH auf `300` setzen für maximale Reichweite
- [ ] Racetag-App startet, Log zeigt sauberen Init bis `configuration applied`
- [ ] UI lädt Race-Selector

### Reichweiten-Test (T1–T4 aus DRESS_REHEARSAL_PLAN.md)

- Tag am **Helm** befestigen → Reichweite messen, notieren
- Tag an der **Gabel** → Reichweite messen, notieren
- Tag am **Rahmen** → Reichweite messen, notieren
- Tag am **Schuh/Knöchel** → Reichweite messen, notieren

Notiere für jede Position: **bei welcher Distanz wird er noch zuverlässig gelesen?**

### Funktionaler Test (T5–T9)

- [ ] Tag waven → unknown_tag SSE → Modal öffnet sich → koppeln
- [ ] Start race → Tag waven → Lap 1 wird gezählt
- [ ] Sofortiger zweiter Wave: kein Lap 2 (Cooldown wirkt)
- [ ] Nach 12 s wieder waven: Lap 2 zählt
- [ ] Diagnostics-Panel zeigt Antenna-1-Counts
- [ ] End race → Spät-Wave wird ignoriert
- [ ] **Export results CSV** → Datei in Excel öffnen → schaut sie sinnvoll aus?

### Stress-Tests (T10–T11) — bewusst „expected to fail"

- [ ] **Reader-Power-Cycle** mitten im Rennen (Netzstecker ziehen, 10 s, wieder rein) — Auto-Reconnect fehlt noch → notieren wie lange Reader+Racetag brauchen, manuell zu erholen
- [ ] **FritzBox kurz aus** — Mac+Reader-Reconnect-Verhalten beobachten
- [ ] **Racetag-App killen** und neu starten mitten im Rennen — Persistenz: Standings zurück da? (✓ wenn Rennen noch nicht ended)

### Multi-Race-Test

- [ ] Zweites Rennen anlegen (Bib-Nummern dürfen sich überschneiden mit Rennen 1)
- [ ] Aktivieren → Rennen 1 verschwindet aus Standings (richtig!), Rider 1 nicht sichtbar (richtig!)
- [ ] Zurück zu Rennen 1 schalten → Standings wieder da

---

## 6. Troubleshooting

### `ping 192.168.178.22` scheitert

```bash
# 1. Mac im richtigen Netz?
ifconfig | grep "inet 192.168.178"
# Wenn leer: Mac ist nicht im FritzBox-Netz. WLAN/LAN prüfen.

# 2. Reader gebootet?
# In FritzBox UI → Heimnetz → Netzwerk → Geräte und Benutzer
# sirit510 sollte als "Online" angezeigt sein

# 3. ARP-Cache zeigt Reader-MAC?
arp -a | grep -i "0:17:9e"

# 4. Hat sich die IP geändert? (FritzBox-Lease nicht greifend?)
# In FritzBox UI prüfen, welche IP der Reader aktuell hat
# Falls anders als .22: READER_IP entsprechend setzen
```

### `CONTROL connect timeout`

- Reader hängt → SSH rein, `reader.reboot()`, 60 s warten
- Andere Racetag-Instanz hält noch CONTROL-Socket: `pkill -f "dist/Racetag.app/Contents/MacOS/Racetag"` und neu

### Reader liest gar nicht

```bash
# In SSH:
setup.operating_mode               # muss "active" sein
antennas.1.conducted_power         # muss > 0 sein (300 = max)
tag.db.get()                       # zeigt sie überhaupt Tags?
```

Wenn `tag.db.get()` leer obwohl Tag in 50 cm Entfernung gehalten wird → RF-Problem (Antenne nicht angeschlossen, Power zu niedrig, Tag-Orientierung).

### App-Fenster bleibt schwarz / öffnet nicht

```bash
# Im Background gestartet? Log anschauen:
tail -50 /tmp/racetag_run.log

# Falls Python-Traceback: backup-App probieren
/Users/jan/Documents/git/racetag/apps/desktop/dist/Racetag.app.prev/Contents/MacOS/Racetag
```

### Standings-Zeit negativ / 1999er-Zeitstempel

Sollte mit dem Fix vom 25.5. erledigt sein. Wenn doch wieder: per SSH manuell setzen:

```text
info.time_zone=UTC
info.time=2026-06-14T08:30:00
info.time
```

(UTC-Zeit eintragen, nicht lokale!)

---

## 7. Nach der Generalprobe

```bash
# DB sichern (autoritative Quelle der Ergebnisse)
cp -r ~/.racetag/data ~/Desktop/racetag-generalprobe-$(date +%Y%m%d-%H%M).bak

# CSV-Exports einsammeln (von ~/Downloads oder wo der Browser sie speichert)
ls -la ~/Downloads/racetag-*.csv

# App sauber beenden
pkill -f "dist/Racetag.app/Contents/MacOS/Racetag"
```

Notizen aus der Generalprobe (Reichweite je Tag-Position, Auto-Reconnect-Wartezeit, beobachtete Quirks) bitte direkt in `docs/issues/DRESS_REHEARSAL_PLAN.md` Abschnitt „Findings" eintragen — daraus wird die Priorität bis zum 24.6.

---

## 8. Was *nicht* zu tun ist während der Generalprobe

- ❌ Code anfassen / committen — wenn ein Bug auftritt, **dokumentieren statt fixen**
- ❌ App neu bauen — der aktuelle Build (`Racetag.app` von 13.6. 20:42) ist getestet
- ❌ FritzBox neu konfigurieren — IPv4-Settings + DHCP-Lease bleiben wie sie sind
- ❌ Reader-Sicherung anfassen — die Drahtbrücke läuft, echte Sicherung kommt nach der Probe
- ❌ Mehrere Rennen ohne CSV-Export dazwischen (BUG-004)

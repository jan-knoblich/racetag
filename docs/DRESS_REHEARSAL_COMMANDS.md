# Racetag — Generalprobe Cheat-Sheet (2026-06-14)

> ⚠️ **STAND JUNI — TEILWEISE VERALTET.** Für das TT-Training am 26.07. gilt das
> Runbook in `docs/issues/RECHECK-2026-07-25.md`. Seit Juli macht die App
> selbst, was hier noch manuell beschrieben ist: **kein `export READER_IP`**
> (§2 — IP kommt aus den Settings), **kein SSH für Antennen-Power/mux** (§1 —
> Auto-Konfiguration beim Connect). Weiterhin gültig: §0 Netz-Checks, §6
> Troubleshooting, §7 Backup.
>
> ⚠️ **ÜBERHOLT SEIT 2026-09 (Windows-Build, PLAN-WINDOWS-NONTECHIE).** Die App
> verbindet sich jetzt **automatisch neu**, sucht den Reader selbst im Netz
> (keine IP-Suche, kein `READER_IP`), zeigt den Reader-Zustand in der
> Statusleiste und schreibt Logs immer nach `~/.racetag/logs/`. Die UI ist
> deutsch (Knopfnamen in §3 sind die alten englischen). Die Abschnitte §0–§8
> bleiben als historische Notizen der Mac-Generalprobe stehen; Stellen, die
> nicht mehr stimmen, sind mit **[überholt]** markiert. Für die Probe auf dem
> Windows-Ziel-PC gilt **§9 Windows resilience rehearsal** am Ende.

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
# [überholt 2026-09] nicht mehr nötig: die App nimmt die gespeicherte IP oder sucht den Reader selbst
export READER_IP=192.168.178.22

# Starten (Foreground, Logs sichtbar)
/Users/jan/Documents/git/racetag/apps/desktop/dist/Racetag.app/Contents/MacOS/Racetag
```

### Oder im Hintergrund + Log-Datei

> **[überholt 2026-09]** `READER_IP` ist nicht mehr nötig, und die Logs stehen immer in `~/.racetag/logs/` (`shell.log`, `backend.log`, `reader.log`). Die Log-Zeilen unten stammen aus dem Juni-Build; der Ablauf (connect → session bind → Zeitzone vor Zeit → configuration) gilt weiter, der genaue Wortlaut nicht.

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

> **[überholt 2026-09]** UI jetzt deutsch: „Rennen anlegen“, „Anlegen“, „Tag → Fahrer koppeln“, „Rennen starten“, „Rennen beenden“, „Ergebnisse exportieren“. Das Koppel-Fenster öffnet sich nur nach Klick auf „Tag → Fahrer koppeln“.

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

- [ ] **Reader-Power-Cycle** mitten im Rennen (Netzstecker ziehen, 10 s, wieder rein) — Auto-Reconnect fehlt noch → notieren wie lange Reader+Racetag brauchen, manuell zu erholen **[überholt 2026-09: Auto-Reconnect ist eingebaut, siehe §9 Zeile R4]**
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
# [überholt 2026-09] nicht mehr nötig: die App findet den Reader unter der neuen IP und speichert sie
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
# [überholt 2026-09] Logs immer hier: ~/.racetag/logs/shell.log, backend.log, reader.log, crash.log

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

---

## 9. Windows resilience rehearsal

_Angelegt 2026-09-13 für PLAN-WINDOWS-NONTECHIE (Phase F). Noch **nicht durchgeführt**._

Ziel: Jede Zeile der Failure-Mode-Matrix aus [PLAN-WINDOWS-NONTECHIE.md §9](PLAN-WINDOWS-NONTECHIE.md#9-failure-mode-matrix-what-recovers-automatically) einmal auf dem **echten Ziel-PC mit dem echten Reader** auslösen und das beobachtete Verhalten eintragen. Bisher ist all das nur automatisiert gegen einen simulierten Reader auf macOS getestet. Abweichungen in der Spalte „Ergebnis“ notieren, **nicht vor Ort fixen** (§8 gilt weiter).

### 9.0 Rahmendaten (ausfüllen)

| Was | Wert |
| --- | --- |
| Datum / Ort | |
| PC (Modell, Windows-Version + Build, Adminrechte ja/nein) | |
| Racetag-Version (Settings → „Hilfe & Support“) und Installer-Dateiname | |
| WebView2-Version (`app_info.json` im Support-Paket) | |
| Reader-IP / Seriennummer | |
| FritzBox-Anschluss Reader / Laptop | LAN 2 / |

### 9.1 Vorbereitung und Hilfsbefehle (PowerShell, ohne Adminrechte)

```powershell
# Sicherung der bestehenden Daten (Racetag vorher schließen)
Copy-Item -Recurse "$env:USERPROFILE\.racetag" "$env:USERPROFILE\Desktop\racetag-backup-$(Get-Date -Format yyyyMMdd-HHmm)"

# Logs live mitlesen (je ein eigenes Fenster)
Get-Content "$env:USERPROFILE\.racetag\logs\reader.log" -Wait -Tail 20
Get-Content "$env:USERPROFILE\.racetag\logs\shell.log"  -Wait -Tail 20

# Backend-Port aus shell.log holen und Reader-Status abfragen
$port = (Select-String -Path "$env:USERPROFILE\.racetag\logs\shell.log" -Pattern 'backend listening on http://127.0.0.1:(\d+)' | Select-Object -Last 1).Matches.Groups[1].Value
Invoke-RestMethod "http://127.0.0.1:$port/reader/status" | Format-List state, ip, target_source, serial, antennas, error, consecutive_failures, supervisor

# Beide Racetag-Prozesse mit Kommandozeile anzeigen (Shell und --reader-service)
Get-CimInstance Win32_Process -Filter "Name='Racetag.exe'" | Select-Object ProcessId, ParentProcessId, CommandLine

# Reader erreichbar?
Test-NetConnection 192.168.178.22 -Port 50007
```

Stoppuhr bereithalten: Bei jeder Unterbrechung die Zeit bis „gelb“ und bis wieder „grün“ messen.

### 9.2 Installation und Start

| # | Prüfung | So durchführen | Erwartet | Ergebnis (OK/✗, Notiz) |
| --- | --- | --- | --- | --- |
| I1 | Installer vom USB-Stick | `Racetag-Setup-<version>.exe` vom Stick kopieren, doppelklicken | Keine SmartScreen-Warnung, **kein UAC-Dialog**, Willkommensseite deutsch mit SmartScreen-Hinweis | |
| I2 | Installer als Download | Datei aus dem Browser laden, starten | „Der Computer wurde durch Windows geschützt“ → „Weitere Informationen“ → „Trotzdem ausführen“ funktioniert; Screenshots für die Bedienungsanleitung machen | |
| I3 | Aufgaben-Seite | – | „Desktop-Symbol erstellen“ angehakt, „Racetag beim Anmelden an Windows automatisch starten“ nicht angehakt | |
| I4 | Installationsort | Nach Setup prüfen | `%LOCALAPPDATA%\Programs\Racetag\Racetag.exe`, Startmenü-Eintrag, Desktop-Symbol; „Racetag starten“ öffnet die App | |
| I5 | Defender | Rechtsklick auf Installer und Installationsordner → „Mit Microsoft Defender überprüfen“ | Keine Funde (sonst False-Positive-Meldung an Microsoft, Plan E3) | |
| I6 | Self-Test des installierten Builds | `$env:RACETAG_LOG_DIR="$env:TEMP\rt-selftest"; $env:RACETAG_NO_DIALOGS="1"; (Start-Process "$env:LOCALAPPDATA\Programs\Racetag\Racetag.exe" -ArgumentList '--selftest' -Wait -PassThru).ExitCode` (danach PowerShell-Fenster schließen, damit die Variablen weg sind) | Exit-Code `0` in < 30 s; `selftest.log` in `%TEMP%\rt-selftest` zeigt 4× PASS; kein Racetag-Prozess bleibt übrig | |
| I7 | Kaltstart, keine Konsolenfenster | PC und Reader aus; PC starten, Racetag starten, **dann** Reader einschalten | Fenster < 5 s; Reader gelb „verbinde…“/„wird gesucht…“ → grün „verbunden (IP)“ ohne Eingabe; zu keinem Zeitpunkt blitzt ein schwarzes Konsolenfenster auf | |
| I8 | Erster Start mit leerer Datenbank | Racetag schließen, `%USERPROFILE%\.racetag` umbenennen (Sicherung aus 9.1!), starten | Einrichtungs-Assistent „Schritt 1 von 3“; Reader wird ohne gespeicherte IP gefunden; Antennentest-Kacheln werden grün; „Rennen anlegen und fertig“ legt Rennen an. Zweiter Durchgang mit wieder leerem Ordner: „Überspringen“, Racetag schließen und neu starten → Assistent erscheint **nicht** wieder. Danach Ordner zurückbenennen | |
| I9 | Logs | Nach I7 `%USERPROFILE%\.racetag\logs` ansehen | `shell.log`, `backend.log`, `reader.log` vorhanden; `reader.log` ohne Farbcodes; Startzeile mit Version | |
| I10 | Anzeige | Windows-Skalierung 125 % und 150 % | Kopfzeile und Einstellungen bedienbar, deutsche Texte nicht abgeschnitten, Tooltips erscheinen beim Überfahren (auch auf deaktivierten Knöpfen) | |

### 9.3 Failure-Mode-Matrix

Eine Zeile je Zeile der Plan-Matrix (R1–R14). Wo sich ein Ausfall auf dem Ziel-PC nicht gefahrlos auslösen lässt, steht das in der Spalte „So auslösen“; dann nur die automatisierte Abdeckung vermerken.

| # | Ausfall | So auslösen | Erwartet in der UI | Ergebnis (OK/✗, gemessene Zeiten, Notiz) |
| --- | --- | --- | --- | --- |
| R1 | Reader beim App-Start noch nicht gebootet | Reader aus, Racetag starten, nach 30 s Reader einschalten | Reader gelb „verbinde…“ bzw. „verbinde neu in N s“; Tooltip zählt „Fehlgeschlagene Versuche“; nach dem Booten grün ohne Eingabe. Zeit Reader-Strom → grün: ___ s | |
| R2 | Reader-IP geändert (DHCP, neuer Router) | Ohne FritzBox-Umbau: Settings → „Reader-Adresse (IP)“ auf eine falsche Adresse im selben Netz (z. B. `192.168.178.99`) → „Speichern“ | Gelb „verbinde…“ (Quelle „Einstellungen“); nach ca. 6 Fehlversuchen (≈ 1 min) Suche; Toast „Reader unter neuer Adresse gefunden: 192.168.178.22“; grün; Settings zeigt wieder `.22`; `backend.log` enthält `reader_ip changed via discovery`. Zeit bis Toast: ___ s | |
| R3 | Reader nach Stromausfall auf Werks-IP `169.254.1.2` | Nur wenn der Reader nach hartem Stromausfall tatsächlich auf `169.254.1.2` hochkommt (in FritzBox nicht mehr sichtbar). Vorher `route print` nach `169.254.0.0` durchsuchen und `Test-NetConnection 169.254.1.2 -Port 50007` | Suche findet ihn (Quelle „Werks-Adresse“), Toast „Reader unter neuer Adresse gefunden: 169.254.1.2“, grün. **Offene Frage:** erreicht Windows 169.254/16 aus dem FritzBox-Netz überhaupt? Ergebnis von `Test-NetConnection` notieren | |
| R4 | Kabel gezogen / Reader-Neustart mitten im Rennen | Testrennen starten, Tags laufen lassen. (a) Reader-Netzwerkkabel 20 s ziehen. (b) Reader-Netzstecker 10 s ziehen. (c) per SSH `reader.reboot()` | Gelb „Verbindung verloren, verbinde neu…“ + Toast „Verbindung zum Reader verloren – verbinde neu…“; danach grün + Toast „Reader wieder verbunden (Unterbrechung …)“; Rangliste vor und nach der Unterbrechung vollständig; `reader.log`: neue `event.connection id`, Bind, Zeitzone vor Zeit, Antennen-Konfiguration. Zeiten (a) bis gelb ___ s / bis grün ___ s; (b) ___ / ___; (c) ___ / ___ | |
| R5 | Reader-Service-Prozess stürzt ab | Den `Racetag.exe`-Prozess mit `--reader-service` beenden: `Get-CimInstance Win32_Process -Filter "Name='Racetag.exe'" \| Where-Object CommandLine -like '*--reader-service*' \| ForEach-Object { Stop-Process -Id $_.ProcessId -Force }` | Reader kurz rot „keine Rückmeldung“ (Toast „Reader-Dienst antwortet nicht“) oder gelb, nach wenigen Sekunden grün; Tooltip „Reader-Dienst neu gestartet: 1×“; `shell.log` zeigt Neustart mit neuer PID | |
| R6 | Backend vom Reader-Service aus nicht erreichbar | Im Desktop-Build nicht auslösbar (Backend läuft im selben Prozess). Abgedeckt durch `test_http_client_retry.py` / `test_spool_hardening.py`. Nach der Probe prüfen: `logs\spool.jsonl` leer oder nicht vorhanden | Nichts sichtbar | |
| R7 | SSE-Stream bricht ab | Nicht direkt auslösbar. Ersatz: auf „Verbindung“ klicken (erzwingt Neuaufbau) | „Verbindung“ kurz gelb, dann „live“; Reader/Antennen kurz grau, dann wieder farbig | |
| R8 | App hart beendet | Task-Manager → Details → **Shell**-`Racetag.exe` (ohne `--reader-service`, siehe `ParentProcessId`) → „Task beenden“ | Beide `Racetag.exe` verschwinden sofort (Job Object); neuer Start sauber, ohne „Racetag läuft bereits“; Reader verbindet neu; Rennen und Runden vollständig | |
| R9 | Zweiter Start während Racetag läuft | Racetag minimieren, Desktop-Symbol erneut doppelklicken | Vorhandenes Fenster kommt nach vorn, Dialog „Racetag läuft bereits“; nach OK läuft weiter genau eine Instanz | |
| R10 | Backend-Thread stirbt | Ohne Testhook nicht auslösbar. Abgedeckt durch die Watchdog-Tests in `apps/desktop/tests`. Nur vermerken | (Dialog „Racetag – Interner Fehler“, „Ja“ startet neu) | |
| R11 | WebView2 fehlt | **Nicht auf dem Einsatz-PC.** Nur auf einer Test-VM/einem PC ohne Runtime: Installer ohne Internet, dann mit Internet | Ohne Internet: Setup-Warnung mit Link, Setup endet; App-Start zeigt „Racetag – WebView2 fehlt“, „Ja“ öffnet Microsoft-Seite. Mit Internet: Runtime wird still installiert, App startet | |
| R12 | Unbehandelte Ausnahme in der Shell | Nicht gefahrlos auslösbar. Abgedeckt durch `test_native.py`. Nach der Probe prüfen, dass kein `crash.log` entstanden ist | (Dialog „Racetag – Unerwarteter Fehler“ mit Pfad zu `crash.log`) | |
| R13 | Uhrdrift des Readers | Racetag ≥ 35 min mit Reader verbunden lassen; dann per SSH `info.time` mit der PC-Uhr (UTC) vergleichen; `reader.log` nach der Resync-Zeile durchsuchen | Abweichung ≤ 2 s; Resync ungefähr alle 30 min ohne Fehler, keine „no reply to clock resync“-Meldung | |
| R14 | Datenordner nicht beschreibbar / Platte fast voll | In einem **neuen** PowerShell-Fenster: `$env:RACETAG_DATA_DIR="C:\Windows\System32\racetag-test"; & "$env:LOCALAPPDATA\Programs\Racetag\Racetag.exe"` (Racetag vorher schließen; Fenster danach schließen). Platte-voll nicht auslösen | Dialog „Racetag – Datenordner“, Racetag beendet sich; normaler Start danach unverändert | |

### 9.4 Reader- und Bedienfunktionen auf echter Hardware

| # | Prüfung | So durchführen | Erwartet | Ergebnis (OK/✗, Notiz) |
| --- | --- | --- | --- | --- |
| H1 | Reader suchen während verbunden | Settings → „Reader suchen“ | Nach ≤ 15 s Liste mit aktuellem Reader („aktuell verbunden“); Tags werden währenddessen weiter gezählt; die Probe-Verbindung stört die laufende Sitzung nicht (`reader.log` ohne Verbindungsverlust) | |
| H2 | Suche ohne gespeicherte IP | Settings → IP-Feld leeren → „Speichern“ | Reader gelb „wird gesucht…“ → findet Reader (Quelle „automatischer Suche“), grün, IP wieder gespeichert | |
| H3 | Antennenleistung live ändern | Settings → „Antennenleistung“ 300 → 250 → „Speichern“ | Toast „Einstellungen gespeichert – Reader verbindet neu“; kurze Unterbrechung, dann grün; per SSH `antennas.1.conducted_power` = 250; **kein** App-Neustart. Dauer der Unterbrechung: ___ s. Danach auf 300 zurück | |
| H4 | „Reader neu verbinden“ | Settings → „Reader neu verbinden“ | Toast „Reader wird neu verbunden…“; neue Reader-Service-PID; grün; Reader hat keine hängende alte Sitzung (Tags zählen weiter) | |
| H5 | Antennentest + Pill | Tag vor Antenne 1, dann 2 halten | „Antennen“ grün „1, 2 OK“; Tooltip zeigt Lesungen je Antenne; Antennen-Diagnose zählt Durchgänge im laufenden Rennen | |
| H6 | Keepalive-Optionen | `reader.log` nach einem Verbindungsaufbau durchsuchen | Keine Warnung zu `SIO_KEEPALIVE_VALS`/Keepalive | |
| H7 | Ergebnisse exportieren | Rennen beenden → „Ergebnisse exportieren“ | Speichern-Dialog; CSV öffnet in Excel mit korrekten Umlauten | |
| H8 | Support-Paket | Settings → „Support-Paket erstellen“ | Speichern-Dialog auf dem Desktop mit `Racetag-Support-JJJJMMTT-HHMM.zip`; Zip enthält Logs, `racetag.db`-Kopie, `config.json`, `reader_status.json`, `app_info.json`; Abbrechen zeigt „Abgebrochen.“ | |
| H9 | Datenordner öffnen | Settings → „Datenordner öffnen“ | Explorer öffnet `%USERPROFILE%\.racetag` | |
| H10 | Sauberes Beenden | Fenster mit X schließen | Beide Prozesse weg; `reader.log` zeigt `stdin closed`, Spool-Flush und Status `stopped`, Exit-Code 0 (kein hartes Beenden) | |
| H11 | Autostart-Häkchen | Installer erneut mit Autostart an, abmelden/anmelden; dann erneut mit Autostart aus | Racetag startet bei Anmeldung; nach zweitem Setup kein Autostart mehr | |
| H12 | Upgrade und Deinstallation | Neuen Installer über laufendes Racetag; danach Deinstallation | Setup bietet Schließen an; Rennen bleiben; Deinstallation meldet, dass `%USERPROFILE%\.racetag` erhalten bleibt | |

### 9.5 Nach der Probe

- Ergebnisse dieser Tabelle committen und Abweichungen als Issues in `docs/issues/` anlegen.
- Gemessene Zeiten (R1, R2, R4, H3) in [PLAN-WINDOWS-NONTECHIE.md §13](PLAN-WINDOWS-NONTECHIE.md#13-implementation-status-2026-09-13) nachtragen.
- Screenshots (SmartScreen, Installer, Statusleiste grün/gelb/rot, Assistent, Support-Paket) für [BEDIENUNGSANLEITUNG-WINDOWS.md](BEDIENUNGSANLEITUNG-WINDOWS.md) ablegen und die Platzhalter ersetzen.
- Sicherung aus 9.1 behalten, bis die Probe ausgewertet ist.

# Racetag – Bedienungsanleitung für Windows

Diese Anleitung ist für die Person, die am Renntag die Zeitnahme bedient. Technisches Wissen ist nicht nötig. Alles, was du anklicken musst, steht hier genau so, wie es auf dem Bildschirm heißt.

Für den Renntag selbst gibt es eine Kurzfassung auf einer Seite: [SPICKZETTEL-WINDOWS.md](SPICKZETTEL-WINDOWS.md).

Stellen, an denen später ein Bildschirmfoto eingefügt wird, sind so markiert:

> [Screenshot: Beschreibung]

---

## Inhalt

1. [Was du brauchst](#1-was-du-brauchst)
2. [Racetag installieren](#2-racetag-installieren)
3. [Aufbau an der Strecke](#3-aufbau-an-der-strecke)
4. [Erster Start mit dem Assistenten](#4-erster-start-mit-dem-assistenten)
5. [Das Racetag-Fenster und die Statusleiste](#5-das-racetag-fenster-und-die-statusleiste)
6. [Rennen anlegen, starten und beenden](#6-rennen-anlegen-starten-und-beenden)
7. [Fahrer koppeln](#7-fahrer-koppeln)
8. [Runden von Hand korrigieren](#8-runden-von-hand-korrigieren)
9. [Ergebnisse exportieren](#9-ergebnisse-exportieren)
10. [Wenn etwas nicht geht](#10-wenn-etwas-nicht-geht)
11. [Support-Paket erstellen und an Jan schicken](#11-support-paket-erstellen-und-an-jan-schicken)
12. [Der Datenordner](#12-der-datenordner)

---

## 1. Was du brauchst

- Den **Reader** (grauer Kasten „Sirit INfinity 510“) mit seinem Netzteil
- Die **Antennen** (meist zwei) mit ihren Antennenkabeln
- Die **FritzBox** mit Netzteil
- Ein **Netzwerkkabel** vom Reader zur FritzBox (und am besten ein Ersatzkabel)
- Den **Laptop** mit Windows 10 oder Windows 11 und seinem Ladekabel
- Die **Tags** (Transponder) für die Fahrer, dazu ein paar Ersatz-Tags zum Testen

Internet brauchst du am Renntag **nicht**.

---

## 2. Racetag installieren

Die Installation machst du nur einmal. Bei einer neuen Version gehst du genauso vor; deine Rennen und Fahrer bleiben dabei erhalten.

Administratorrechte brauchst du nicht. Racetag wird nur für dein Windows-Benutzerkonto installiert.

### 2.1 Installer holen

Du bekommst von Jan eine Datei mit dem Namen `Racetag-Setup-<Version>.exe`, zum Beispiel `Racetag-Setup-0.3.0.exe`.

- **Vom USB-Stick (empfohlen):** Kopiere die Datei vom Stick auf den Desktop und doppelklicke sie. Windows zeigt dann in der Regel keine Warnung.
- **Als Download:** Lade die Datei über den Link herunter, den Jan dir schickt, und doppelklicke sie im Ordner „Downloads“.

### 2.2 Windows-Warnung „Der Computer wurde durch Windows geschützt“

Racetag ist nicht digital signiert. Deshalb zeigt Windows bei heruntergeladenen Dateien oft ein blaues Fenster mit dieser Warnung. Das ist bei Racetag normal.

1. Klicke im blauen Fenster auf **„Weitere Informationen“**.
2. Klicke dann auf den Knopf **„Trotzdem ausführen“**.

> [Screenshot: Blaues SmartScreen-Fenster „Der Computer wurde durch Windows geschützt“ mit markiertem Link „Weitere Informationen“]

> [Screenshot: Dasselbe Fenster nach dem Klick, Knopf „Trotzdem ausführen“ markiert]

Diese Warnung kommt nur beim Installer. Das installierte Racetag startet danach ohne Warnung.

### 2.3 Installer durchklicken

1. **Willkommen:** Die erste Seite erklärt noch einmal die Windows-Warnung. Klicke auf **„Weiter“**.

   > [Screenshot: Willkommensseite des Racetag-Setup-Assistenten]

2. **Zusätzliche Aufgaben:**
   - **„Desktop-Symbol erstellen“** ist angehakt. So lassen.
   - **„Racetag beim Anmelden an Windows automatisch starten“** ist nicht angehakt. So lassen, außer Jan sagt etwas anderes.

   Klicke auf **„Installieren“** (oder „Weiter“).

   > [Screenshot: Seite „Zusätzliche Aufgaben“ mit den beiden Häkchen]

3. **Microsoft WebView2:** Auf manchen älteren Computern fehlt ein Baustein von Microsoft, den Racetag für sein Fenster braucht. Der Installer lädt ihn dann selbst herunter und zeigt „Microsoft WebView2-Laufzeit wird installiert …“. Dafür braucht der Laptop **einmalig Internet**. Das kann ein paar Minuten dauern.

   Kommt stattdessen die Meldung, dass die WebView2-Laufzeit nicht installiert werden konnte: Laptop mit dem Internet verbinden und den Installer noch einmal starten. Hilft das nicht, Jan Bescheid geben.

4. **Fertig:** Auf der letzten Seite ist **„Racetag starten“** angehakt. Klicke auf **„Fertigstellen“**. Racetag öffnet sich.

   > [Screenshot: Letzte Seite des Setups mit Häkchen „Racetag starten“]

Ab jetzt startest du Racetag mit einem Doppelklick auf das Symbol **Racetag** auf dem Desktop oder über das Startmenü.

> [Screenshot: Desktop mit dem Racetag-Symbol]

### 2.4 Neue Version installieren

Einfach den neuen Installer genauso ausführen. Läuft Racetag noch, bietet der Installer an, es zu schließen. Deine Rennen, Fahrer und Einstellungen bleiben erhalten.

### 2.5 Racetag deinstallieren

Windows-Einstellungen → **Apps** → **Racetag** → **Deinstallieren**. Deine Daten (Rennen, Fahrer, Protokolle) bleiben dabei auf dem Laptop; die Meldung am Ende sagt dir, wo.

---

## 3. Aufbau an der Strecke

Die Reihenfolge ist egal. Racetag darf auch schon laufen, bevor der Reader eingeschaltet ist: Es sucht den Reader so lange, bis er da ist.

1. **Antennen aufstellen** an der Ziellinie, links und rechts der Fahrbahn, mit der Vorderseite zu den Fahrern.
2. **Antennenkabel** an die Antennen-Anschlüsse **1** und **2** am Reader schrauben. Nur handfest anziehen, aber so, dass nichts wackelt.
3. **Netzwerkkabel** vom Reader in die FritzBox stecken, und zwar in den Anschluss **LAN 2**.

   > **Wichtig: nicht LAN 1!** Mit LAN 1 bekommt der Reader keine Verbindung. LAN 3 und LAN 4 sind für den Reader nicht getestet.

   > [Screenshot: Rückseite der FritzBox, Anschluss LAN 2 markiert, LAN 1 durchgestrichen]

4. **FritzBox einschalten** und etwa 2 Minuten warten.
5. **Reader einschalten** (Netzteil einstecken). Der Reader braucht nach dem Einschalten etwa 30 bis 60 Sekunden, bis er bereit ist.
6. **Laptop mit der FritzBox verbinden:** entweder über das WLAN der FritzBox (Name und Kennwort stehen auf dem Aufkleber unten an der FritzBox) oder mit einem zweiten Netzwerkkabel in einen freien LAN-Anschluss.

   > Der Laptop muss im Netz **dieser** FritzBox sein. Ist er in einem anderen WLAN (Handy-Hotspot, Vereinsheim-WLAN), findet Racetag den Reader nicht.

7. **Laptop ans Ladekabel** hängen. Ein Rennen dauert oft länger als der Akku.
8. **Laptop-Deckel offen lassen.** Solange Racetag läuft, geht der Laptop nicht von selbst in den Energiesparmodus und der Bildschirm bleibt an. Wird aber der **Deckel zugeklappt**, schläft der Laptop trotzdem ein, und dann werden keine Durchfahrten erfasst.

   > **Einmalig vor dem ersten Renntag einstellen:** Start → „Systemsteuerung“ eintippen → **Energieoptionen** → links **„Auswählen, was beim Zuklappen passieren soll“** → bei „Beim Zuklappen“ für **„Netzbetrieb“** und **„Akkubetrieb“** jeweils **„Nichts unternehmen“** wählen → **„Änderungen speichern“**.

---

## 4. Erster Start mit dem Assistenten

1. Doppelklicke auf **Racetag** auf dem Desktop. Nach ein paar Sekunden öffnet sich das Racetag-Fenster.
2. Beim ersten Start erscheint der **Einrichtungs-Assistent** („Einrichtung“, „Schritt 1 von 3“). Er führt dich in drei Schritten durch alles Nötige.

Du kannst den Assistenten jederzeit mit **„Überspringen“** schließen. Wieder öffnen kannst du ihn über das Zahnrad ⚙ (Einstellungen) → Bereich „Hilfe & Support“ → **„Einrichtungs-Assistent“**.

Hast du den Assistenten einmal abgeschlossen oder übersprungen, erscheint er beim nächsten Start von Racetag nicht mehr von selbst.

### Schritt 1 von 3: Reader finden

Racetag sucht den Reader von selbst. Warte, bis der Kasten unter dem Text **grün** wird und darin steht:

**„Reader: verbunden (192.168.178.22) · Seriennummer: …“**

(Die Zahlen können bei dir anders sein. Wichtig sind der grüne Kasten und das Wort „verbunden“.)

> [Screenshot: Assistent Schritt 1 mit grünem Kasten „Reader: verbunden (…) · Seriennummer: …“]

Steht dort nach einer Minute noch nichts Grünes:

- Ist der Reader eingeschaltet? Steckt das Netzwerkkabel in **LAN 2**?
- Ist der Laptop im Netz der FritzBox (siehe Abschnitt 3, Punkt 6)?
- Klicke auf **„Reader suchen“**. Die Suche dauert bis zu 15 Sekunden. Wird ein Reader gefunden, erscheint er in einer Liste. Klicke daneben auf **„Übernehmen“**.

Klicke dann auf **„Weiter“**.

### Schritt 2 von 3: Antennentest

Für jede Antenne gibt es ein Kästchen („Antenne 1“, „Antenne 2“), in dem zuerst „wartet auf Tag…“ steht.

1. Nimm einen Tag in die Hand.
2. Halte ihn nacheinander vor jede Antenne (Abstand etwa ein halber Meter).
3. Das Kästchen der Antenne wird **grün** und zeigt „… Lesungen“, sobald sie den Tag gelesen hat.

Sind alle Kästchen grün, steht darunter: **„Alle Antennen lesen Tags – weiter zum nächsten Schritt.“**

> [Screenshot: Assistent Schritt 2 mit zwei grünen Antennen-Kästchen]

Bleibt ein Kästchen grau:

- Antennenkabel an dieser Antenne **und** am Reader prüfen.
- Tag näher an die Antenne halten und etwas drehen.
- **„Test neu starten“** klicken und noch einmal probieren.

Steht dort „Noch keine Antenne erkannt. Antennenkabel prüfen.“, erkennt der Reader gar keine Antenne. Kabel prüfen und in den Einstellungen **„Reader neu verbinden“** klicken.

Klicke dann auf **„Weiter“**.

### Schritt 3 von 3: Erstes Rennen anlegen

1. **Name des Rennens** eintragen, z. B. „Volksradrennen Sonntag“.
2. **Rundenzahl** eintragen, z. B. 5.
3. Klicke auf **„Rennen anlegen und fertig“**.

Das Rennen ist jetzt angelegt und ausgewählt. Gestartet ist es noch nicht; das machst du erst, wenn es losgeht (Abschnitt 6). Unten rechts erscheint dazu die Meldung „… – zum Start „Rennen starten“ drücken, erst dann zählen Runden“, und der Knopf **„Rennen starten“** blinkt kurz. **Vor dem Druck auf „Rennen starten“ werden keine Runden gezählt.**

**Wenn im aktiven Rennen schon Fahrer eingetragen sind** (z. B. weil du vorher schon eine Startliste geladen hast), fragt Schritt 3 zuerst:

- **„Dieses Rennen weiterverwenden (die Fahrer bleiben erhalten)“** – das ist vorausgewählt und fast immer richtig. Name und Rundenzahl werden übernommen, der Knopf heißt dann **„Rennen übernehmen und fertig“**.
- **„Neues, leeres Rennen anlegen“** – nur wählen, wenn du wirklich ein zusätzliches Rennen ohne Fahrer willst.

Läuft gerade ein Rennen, fragt Racetag vor dem Anlegen eines neuen Rennens noch einmal nach.

> [Screenshot: Assistent Schritt 3 mit ausgefülltem Namen und Rundenzahl]

---

## 5. Das Racetag-Fenster und die Statusleiste

> [Screenshot: Ganzes Racetag-Fenster mit Beschriftungen: Knopfleiste oben, Statusleiste, Rennstatus, Rangliste, Antennen-Diagnose unten]

Von oben nach unten siehst du:

1. **Die Knopfleiste** mit „CSV importieren“, „Tags exportieren“, „Tag → Fahrer koppeln“, „Koppel-Modus“, „Fahrer“, der Rennauswahl „Rennen:“ mit dem „+“, „Rennen starten“, „Rennen beenden“, „Ergebnisse exportieren“, „Rennen zurücksetzen“, dem Zahnrad ⚙ (Einstellungen) und „Rundenzahl:“.
2. **Die Statusleiste** mit drei farbigen Feldern: **Reader**, **Antennen**, **Verbindung**.
3. **Den Rennstatus**, z. B. „Nicht gestartet – zum Beginnen „Rennen starten“ drücken“ oder „Läuft seit 10:02:13“.
4. **Die Rangliste** mit allen Fahrern.
5. Ganz unten die aufklappbare **„Antennen-Diagnose“**.

**Erklärungen gibt es überall:** Halte die Maus kurz über einen Knopf, eine Spaltenüberschrift oder ein Feld, dann erscheint eine Erklärung. Neben vielen Feldern in den Einstellungen gibt es ein kleines **i**. Ein Klick darauf erklärt das Feld ausführlich und nennt den Standardwert.

### 5.1 Die Statusleiste – eine Ampel für jeden Teil

Jedes der drei Felder hat einen farbigen Punkt:

| Farbe | Bedeutung |
| --- | --- |
| **Grün** | Alles in Ordnung. |
| **Gelb** | Racetag arbeitet gerade daran (sucht, verbindet, verbindet neu). Meist erledigt sich das von selbst. Kurz warten. |
| **Rot** | Hier stimmt etwas nicht, und es dauert schon länger. Siehe [Abschnitt 10](#10-wenn-etwas-nicht-geht). |
| **Grau** | Keine aktuelle Angabe möglich, z. B. solange die „Verbindung“ nicht grün ist. |

**Maus über ein Feld halten** zeigt Einzelheiten (Adresse des Readers, Seriennummer, letzte Lesung, Lesungen je Antenne) und was du tun kannst.

**Auf ein Feld klicken:**

- **Reader** → öffnet die Einstellungen.
- **Antennen** → öffnet die Antennen-Diagnose unten im Fenster.
- **Verbindung** → verbindet das Fenster sofort neu.

> [Screenshot: Statusleiste, alles grün: Feld „Reader“ mit „verbunden (192.168.178.22)“, Feld „Antennen“ mit „1, 2 OK“, Feld „Verbindung“ mit „live“]

### 5.2 Was die Felder anzeigen können

**Reader** – die Verbindung zum Reader:

| Anzeige | Farbe | Bedeutung |
| --- | --- | --- |
| verbunden (192.168.178.22) | Grün | Reader ist verbunden, Tags werden gelesen. |
| wird gesucht… | Gelb | Racetag sucht den Reader im Netzwerk. |
| verbinde… / verbinde neu in 5 s | Gelb | Racetag kennt die Adresse und versucht, den Reader zu erreichen. |
| wird eingerichtet… | Gelb | Reader ist erreichbar und wird gerade eingestellt (dauert ein paar Sekunden). |
| Verbindung verloren, verbinde neu… | Gelb | Verbindung ist abgerissen, Racetag verbindet von selbst neu. |
| mehrere gefunden | Gelb | Im Netzwerk sind mehrere Reader. Du musst den richtigen auswählen. |
| startet… | Gelb | Direkt nach dem Öffnen von Racetag: Der Reader-Teil startet gerade (dauert ein paar Sekunden). |
| wird neu gestartet… | Gelb | Nach „Reader neu verbinden“: Der Reader-Teil startet neu (dauert ein paar Sekunden). |
| keine Rückmeldung | Rot | Der Reader-Teil von Racetag meldet sich nicht. |
| gestoppt | Rot | Der Reader-Teil wurde beendet (normal beim Schließen von Racetag). |

**Antennen** – welche Antennen-Anschlüsse eingeschaltet sind und ob sie lesen:

| Anzeige | Farbe | Bedeutung |
| --- | --- | --- |
| 1, 2 OK | Grün | Diese Anschlüsse sind eingeschaltet. Das heißt noch nicht sicher, dass dort ein Kabel steckt – deshalb vor dem Rennen den Antennentest machen (Tag vor jede Antenne halten). |
| 1 OK, 2 liest nichts | Gelb | Das Rennen läuft, die anderen Antennen lesen Tags, aber Antenne 2 hat in der letzten Minute nichts gelesen. Antennenkabel an Anschluss 2 prüfen. |
| keine erkannt | Gelb | Selten: Reader verbunden, aber keine Antenne eingeschaltet. |
| – | Grau | Reader nicht verbunden, deshalb keine Angabe. |

Ob eine Antenne wirklich liest, siehst du, wenn du die Maus über das Feld hältst: Dort stehen die Lesungen der letzten 60 Sekunden je Antenne.

**Verbindung** – die Verbindung zwischen dem Fenster und Racetag selbst:

| Anzeige | Farbe | Bedeutung |
| --- | --- | --- |
| live | Grün | Rangliste wird sofort aktualisiert. |
| verbinde… / verbinde neu in 3 s | Gelb | Kurze Unterbrechung, Racetag verbindet von selbst neu. |
| getrennt – neuer Versuch in 10 s | Rot | Mehrere Versuche sind fehlgeschlagen. |

### 5.3 Kurze Meldungen unten rechts

Racetag blendet unten rechts kurze Meldungen ein, zum Beispiel:

- „Verbindung zum Reader verloren – verbinde neu…“
- „Reader wieder verbunden (Unterbrechung 12 s)“
- „Reader unter neuer Adresse gefunden: 192.168.178.23“ – Racetag hat den Reader nach einem Adresswechsel selbst wiedergefunden und merkt sich die neue Adresse. Du musst nichts tun.
- „Reader-Dienst antwortet nicht“ – siehe [Abschnitt 10](#10-wenn-etwas-nicht-geht).

---

## 6. Rennen anlegen, starten und beenden

### 6.1 Rennen anlegen

Lege für jeden Lauf des Tages ein eigenes Rennen an (z. B. „Jedermann“, „Lizenz“, „Kinder“).

1. Klicke auf das **„+“** neben „Rennen:“.
2. Im Fenster **„Rennen anlegen“**:
   - **Name des Rennens** (Pflicht).
   - **Geplanter Start (Datum / Uhrzeit)** (freiwillig, nur zur Anzeige).
   - **Rennformat:** „Feste Rundenzahl“ oder „Zeit + Schlussrunden“. Im Zweifel „Feste Rundenzahl“.
   - **Rundenzahl.**
   - **Einzelwertung:** normalerweise aus lassen. Nur einschalten, wenn jeder Fahrer seine Runden selbst zu Ende fährt (z. B. Zeitfahren).
   - **Auto-Snapshot-Intervall:** so lassen (120).
   - **„Dieses Rennen nach dem Anlegen aktivieren“** angehakt lassen.
3. Klicke auf **„Anlegen“**.

> [Screenshot: Fenster „Rennen anlegen“]

### 6.2 Das richtige Rennen auswählen

In der Auswahl **„Rennen:“** steht das **aktive** Rennen. Alle Durchgänge an der Ziellinie zählen für dieses Rennen. Vor jedem Lauf also prüfen, dass hier das richtige Rennen steht.

Die Rundenzahl lässt sich jederzeit oben bei **„Rundenzahl:“** ändern. Danach **„Übernehmen“** klicken.

### 6.3 Rennen starten

Wenn der Startschuss fällt: Klicke auf **„Rennen starten“**.

- Der Rennstatus zeigt **„Läuft seit …“** mit der Startuhrzeit.
- Der Knopf zeigt jetzt **„Rennen läuft“**.
- Erst ab jetzt zählen Durchgänge als Runden.

Während des Rennens füllt sich die Rangliste von selbst. Im Rennstatus steht, wie viele Runden der Führende noch fahren muss („noch 1 Runde“, „LETZTE RUNDE / Zieleinlauf“).

### 6.4 Rennen beenden

Wenn alle im Ziel sind:

1. Klicke auf **„Rennen beenden“**.
2. Bestätige die Frage „Rennen beenden? Die Rangliste wird eingefroren.“ mit **OK**.
3. Der Rennstatus zeigt **„Beendet um …“**.
4. **Gleich danach die Ergebnisse exportieren** (Abschnitt 9).

Aus Versehen beendet? Klicke auf **„Rennen wieder öffnen“**.

### 6.5 „Rennen zurücksetzen“ – nur für Proben

**„Rennen zurücksetzen“** löscht **alle Runden** des aktiven Rennens. Die gekoppelten Fahrer bleiben. Das lässt sich nicht rückgängig machen. Benutze das nur nach einem Probelauf, nie nach einem echten Rennen.

### 6.6 Racetag beenden

Das Fenster mit dem **X** oben rechts schließen. Racetag fragt dann: **„Racetag wirklich beenden? Solange Racetag geschlossen ist, werden keine Durchfahrten erfasst.“**

- **OK** beendet Racetag. Alles ist bereits gespeichert; beim nächsten Start ist alles wieder da.
- **Abbrechen** lässt Racetag weiterlaufen.

Während eines Rennens also erst **„Rennen beenden“** und die Ergebnisse exportieren, dann Racetag schließen.

Direkt nach dem Schließen kann das Beenden noch einige Sekunden dauern. Öffnest du Racetag in dieser Zeit wieder, wartet es kurz und startet dann von selbst. Kommt stattdessen die Meldung „Racetag wird gerade noch beendet oder gestartet“: ein paar Sekunden warten und Racetag erneut öffnen.

---

## 7. Fahrer koppeln

„Koppeln“ heißt: Racetag lernt, welcher Tag zu welcher Startnummer gehört. Das gilt immer für das **aktive Rennen**. Wähle also zuerst oben bei „Rennen:“ das richtige Rennen.

### 7.1 Startliste aus einer Datei laden

Wenn Jan dir eine Startliste als CSV-Datei gibt:

1. Klicke auf **„CSV importieren“**.
2. Wähle die Datei aus.
3. Racetag meldet, wie viele Fahrer importiert wurden. Gab es Probleme mit einzelnen Zeilen, erscheint **„Importfehler anzeigen“**.

### 7.2 Einen einzelnen Tag koppeln

1. Klicke auf **„Tag → Fahrer koppeln“**.
2. Halte den Tag an eine Antenne.
3. Es öffnet sich das Fenster **„Fahrer koppeln“**. Die Tag-ID ist schon eingetragen. (Das Fenster kommt nur bei Tags, die in diesem Rennen noch keinem Fahrer gehören. Einen schon gekoppelten Tag änderst du über **„Fahrer“**, siehe 7.4.)
4. **Startnummer** und **Name des Fahrers** eintragen.
5. Klicke auf **„Speichern“** (oder drücke Enter).

### 7.3 Viele Tags nacheinander: der Koppel-Modus

1. Klicke auf **„Koppel-Modus“**. Oben erscheint eine große Karte „Tag an die Antenne halten…“.
2. Tag an die Antenne halten. Die Karte zeigt „NEUER TAG“.
3. **Startnummer** tippen, **Enter** drücken. Fertig, der nächste Tag ist dran.
4. Zum Schluss auf **„Beenden“** klicken.

Mit **„Auto-Zuweisung starten“** vergibt Racetag die Nummern sogar selbst: Trage vorher in das Feld daneben den Nummernbereich ein (z. B. `1-75`) und halte dann einfach einen Tag nach dem anderen an die Antenne.

> [Screenshot: Koppel-Modus mit Karte „NEUER TAG“ und Eingabefeld Startnummer]

### 7.4 Fahrer nachträglich ändern

Klicke auf **„Fahrer“**, suche nach Startnummer oder Name, klicke den Treffer an, ändere die Angaben und klicke auf **„Speichern“**. Das geht auch ohne Reader, z. B. bei Nachmeldungen. Ein Doppelklick auf eine Zeile der Rangliste öffnet den Fahrer direkt.

---

## 8. Runden von Hand korrigieren

In der Rangliste hat jede Zeile rechts in der Spalte **„Runden ±“** drei kleine Knöpfe:

- **+1** – eine Runde gutschreiben (z. B. wenn der Reader einen Fahrer verpasst hat).
- **−1** – die letzte Runde wieder abziehen.
- **✎** – öffnet **„Runde bearbeiten“**: Runde mit eigener Uhrzeit nachtragen, Runde entfernen oder einen Status setzen (**DNF** = aufgegeben, **DNS** = nicht gestartet, **DSQ** = disqualifiziert).

Ein **⚠** neben der Rundenzahl bedeutet: Der Reader hat bei diesem Fahrer vermutlich eine Runde verpasst. Prüfe das und trage die Runde mit **+1** nach.

---

## 9. Ergebnisse exportieren

1. Achte darauf, dass oben bei „Rennen:“ das Rennen ausgewählt ist, dessen Ergebnis du willst.
2. Klicke auf **„Ergebnisse exportieren“**.
3. Es öffnet sich ein Speichern-Fenster. Wähle einen Ort (z. B. den Desktop) und klicke auf **„Speichern“**.
4. Unten rechts erscheint „Exportiert: …“ mit dem Dateinamen.

Die Datei ist eine CSV-Datei. Sie lässt sich mit Excel öffnen.

> [Screenshot: Speichern-Fenster für die Ergebnis-Datei]

Zusätzlich speichert Racetag für das aktive Rennen alle 2 Minuten automatisch eine Sicherung der Rangliste und der Datenbank im Datenordner (Abschnitt 12), solange beim Rennen das „Auto-Snapshot-Intervall“ auf dem Standardwert 120 steht. Bei einem Absturz geht also nichts verloren.

---

## 10. Wenn etwas nicht geht

**Zuerst:** Keine Panik und nicht vorschnell Racetag schließen. Die meisten Unterbrechungen behebt Racetag selbst innerhalb von Sekunden. Alles, was bis dahin gezählt wurde, ist gespeichert.

**Wichtig zu wissen:** Fahrer, die **während** einer Unterbrechung über die Linie fahren, kann Racetag nicht erfassen. Diese Runden trägst du danach mit **+1** oder **✎** nach (Abschnitt 8).

### 10.1 Feld „Reader“

| Anzeige | Was du tun kannst |
| --- | --- |
| **Gelb: „wird gesucht…“** | 1. Ist der Reader eingeschaltet? 2. Steckt das Netzwerkkabel am Reader und in der FritzBox in **LAN 2** (nicht LAN 1)? 3. Ist der Laptop im Netz der FritzBox? 4. Nach dem Einschalten braucht der Reader bis zu einer Minute. Warten. 5. Danach: Zahnrad ⚙ → **„Reader suchen“** → beim gefundenen Reader **„Übernehmen“**. |
| **Gelb: „verbinde…“ oder „verbinde neu in … s“** | Wie oben: Strom und Kabel prüfen, warten. Racetag versucht es von selbst weiter und sucht nach etwa einer Minute ohne Erfolg auch im ganzen Netzwerk nach dem Reader. |
| **Gelb: „wird eingerichtet…“** | Einige Sekunden warten. Bleibt es länger als eine Minute so: Zahnrad ⚙ → **„Reader neu verbinden“**. |
| **Gelb: „Verbindung verloren, verbinde neu…“** | Die Verbindung ist abgerissen (Kabel gewackelt, Reader ohne Strom). Kabel und Strom prüfen. Racetag verbindet von selbst neu; danach kommt „Reader wieder verbunden“. Verpasste Runden nachtragen. |
| **Gelb: „mehrere gefunden“** | Zahnrad ⚙ → **„Reader suchen“**. In der Liste steht bei jedem Reader die Seriennummer („S/N …“). Vergleiche sie mit dem Aufkleber an deinem Reader und klicke dort auf **„Übernehmen“**. |
| **Gelb: „startet…“ oder „wird neu gestartet…“** | Normal direkt nach dem Öffnen von Racetag oder nach „Reader neu verbinden“. Ein paar Sekunden warten. |
| **Rot: „keine Rückmeldung“** | Zahnrad ⚙ → **„Reader neu verbinden“**. Nach 10 Sekunden sollte es wieder gelb oder grün werden. Bleibt es rot: Racetag schließen und neu starten. Bleibt es auch dann rot: Support-Paket an Jan (Abschnitt 11). |
| **Rot: „gestoppt“** | Kommt normalerweise nur kurz beim Schließen. Bleibt es länger als etwa 30 Sekunden rot: Racetag schließen und neu starten. |
| **Grau** | Solange die „Verbindung“ nicht grün ist, zeigt der Reader die letzte bekannte Angabe in Grau. Erst die „Verbindung“ prüfen. |

> [Screenshot: Statusleiste mit gelbem Feld „Reader: Verbindung verloren, verbinde neu…“]

**Nach einem Stromausfall am Reader** kann es sein, dass der Reader auf seine Werksadresse zurückfällt. Racetag probiert diese Adresse bei der Suche mit. Findet Racetag den Reader trotzdem nach 2 Minuten nicht, Jan anrufen.

### 10.2 Feld „Antennen“

| Anzeige | Was du tun kannst |
| --- | --- |
| **Gelb: „1 OK, 2 liest nichts“** | Die anderen Antennen lesen, diese nicht. Antennenkabel an dieser Antenne und am Reader prüfen und festdrehen. Verpasste Runden danach nachtragen. |
| **Gelb: „keine erkannt“** | Antennenkabel an den Antennen und am Reader prüfen und festdrehen. Dann Zahnrad ⚙ → **„Reader neu verbinden“**. |
| **Grün, aber eine Antenne liest nichts** | Vor dem Start oder wenn noch kaum Fahrer vorbeigefahren sind, bleibt das Feld grün. Maus über das Feld halten: Steht bei einer Antenne dauerhaft „0 Lesungen“, obwohl Fahrer vorbeifahren, ist das Kabel dieser Antenne locker oder defekt. Kabel prüfen. |
| **Grau: „–“** | Der Reader ist nicht verbunden. Erst das Feld „Reader“ in Ordnung bringen. |

### 10.3 Feld „Verbindung“

| Anzeige | Was du tun kannst |
| --- | --- |
| **Gelb: „verbinde…“ / „verbinde neu in … s“** | Kurz warten, das behebt sich meist von selbst. |
| **Rot: „getrennt – neuer Versuch in … s“** | Auf das Feld klicken. Wird es nicht wieder grün: Racetag schließen und neu starten. Deine Daten bleiben erhalten. |

### 10.4 Fenster mit Fehlermeldung

| Fenster-Titel | Was du tun kannst |
| --- | --- |
| **„Racetag läuft bereits“** | Racetag ist schon offen. Das vorhandene Fenster benutzen; es ist eventuell unten in der Taskleiste. Nicht mehrfach auf das Symbol klicken. |
| **„Racetag – WebView2 fehlt“** | Ein Baustein von Microsoft fehlt. Laptop mit dem Internet verbinden, **„Ja“** klicken, auf der Microsoft-Seite die Laufzeit herunterladen und installieren, dann Racetag neu starten. Oder den Racetag-Installer noch einmal ausführen. |
| **„Racetag – Datenordner“** | Die Festplatte ist fast voll oder der Datenordner ist gesperrt. Speicherplatz freigeben (z. B. alte Downloads löschen) und Racetag neu starten. Sonst Jan anrufen. |
| **„Racetag – Startfehler“** | Racetag neu starten. Kommt die Meldung wieder: Support-Paket an Jan (Abschnitt 11). |
| **„Racetag – Interner Fehler“** | **„Ja“** klicken. Racetag startet neu, gespeicherte Rennen und Zeiten bleiben erhalten. |
| **„Racetag – Unerwarteter Fehler“** | Racetag neu starten. Danach Support-Paket an Jan schicken. |

### 10.5 Weitere Probleme

| Problem | Was du tun kannst |
| --- | --- |
| Fahrer bekommen zwei Runden auf einmal | Zahnrad ⚙ → **„Mindest-Rundenabstand (s)“** erhöhen (Standard 8), **„Speichern“**. Zu viel gezählte Runden mit **−1** abziehen. |
| Tags werden weit neben der Linie gelesen (z. B. wartende Fahrer im Startbereich) | Zahnrad ⚙ → **„Antennenleistung“** in Zehnerschritten verringern (z. B. von 300 auf 250), **„Speichern“**. Wirkt nach wenigen Sekunden. Achtung: Der Reader verbindet dabei kurz neu (siehe Hinweis unten). |
| Fahrer werden an der Linie verpasst | Antennenleistung wieder erhöhen (höchstens 300). Tags richtig am Rad/Helm befestigt? Verpasste Runden nachtragen. |
| Das Fenster bleibt weiß oder leer | Racetag schließen und neu starten. Hilft das nicht: Racetag-Installer noch einmal ausführen (mit Internet). |
| Nach dem Doppelklick passiert nichts | 10 Sekunden warten, nicht mehrfach klicken. Kommt kein Fenster: Laptop neu starten und noch einmal versuchen. Dann Jan anrufen. |
| Unten erscheint „Neue Version … verfügbar“ | Jan Bescheid geben. Am Renntag nichts neu installieren. |
| Der Laptop war im Energiesparmodus (z. B. Deckel zugeklappt) | Deckel öffnen, Racetag verbindet sich von selbst wieder. In dieser Zeit wurden keine Durchfahrten erfasst: Runden nachtragen. Für das nächste Mal die Einstellung aus Abschnitt 3, Punkt 8 machen. |

> **Hinweis zu Änderungen während des Rennens:** Wenn du in den Einstellungen die **Reader-Adresse** oder die **Antennenleistung** speicherst, bei einem gefundenen Reader auf **„Übernehmen“** klickst oder auf **„Reader neu verbinden“** klickst, ist der Reader für einige Sekunden getrennt. Fahrer, die genau dann über die Linie fahren, werden nicht erfasst. Läuft das Rennen und ist der Reader verbunden, fragt Racetag deshalb vorher nach („Das Rennen läuft. … Trotzdem …?“). Mit **Abbrechen** bleibt alles, wie es ist. Mach solche Änderungen nur vor dem Start, in einer Pause, oder wenn der Reader ohnehin nicht funktioniert.

---

## 11. Support-Paket erstellen und an Jan schicken

Wenn etwas nicht funktioniert, braucht Jan die Protokolle von Racetag. Das Support-Paket packt alles Nötige in **eine** ZIP-Datei: die Protokolle, eine Kopie der Datenbank (mit Rennen und Fahrernamen) und die aktuellen Einstellungen.

1. Klicke auf das Zahnrad **⚙** (Einstellungen).
2. Scrolle zum Bereich **„Hilfe & Support“**.
3. Klicke auf **„Support-Paket erstellen“**.
4. Es öffnet sich ein Speichern-Fenster, meist direkt auf dem **Desktop**, mit einem Namen wie `Racetag-Support-20260913-1402.zip`. Klicke auf **„Speichern“**.
5. Unter dem Knopf steht dann „Gespeichert: …“ mit dem Speicherort.

> [Screenshot: Einstellungen, Bereich „Hilfe & Support“ mit den Knöpfen „Einrichtungs-Assistent“, „Datenordner öffnen“, „Support-Paket erstellen“]

6. Schicke diese ZIP-Datei an Jan (per E-Mail oder Messenger). Kontakt: ______________________ (hier Jans Kontakt eintragen)
7. Schreibe kurz dazu:
   - **Was** ist passiert?
   - **Wann** ungefähr (Uhrzeit)?
   - **Was** stand in der Statusleiste (Farbe und Text)?

**Wenn Racetag gar nicht mehr startet**, geht der Knopf natürlich nicht. Dann so:

1. Drücke die **Windows-Taste + R**.
2. Tippe `%USERPROFILE%\.racetag` ein und drücke Enter. Der Racetag-Ordner öffnet sich.
3. Klicke mit der rechten Maustaste auf den Ordner **`logs`** und wähle **„Komprimieren in ZIP-Datei“** (Windows 11) bzw. **„Senden an“ → „ZIP-komprimierter Ordner“** (Windows 10).
4. Schicke die entstandene ZIP-Datei an Jan.

---

## 12. Der Datenordner

Racetag speichert alles in einem Ordner in deinem Benutzerordner:

`C:\Users\<dein Name>\.racetag\`

Du öffnest ihn am einfachsten über Zahnrad ⚙ → „Hilfe & Support“ → **„Datenordner öffnen“**.

| Ordner / Datei | Inhalt |
| --- | --- |
| `data\racetag.db` | Die Datenbank: alle Rennen, Fahrer, Runden und Einstellungen. |
| `data\snapshots\` | Automatische Sicherungen während der Rennen (alle 2 Minuten, die letzten 30 je Rennen bleiben). |
| `logs\` | Protokolle für Jan (werden automatisch klein gehalten). |

Wichtig:

- **Nichts in diesem Ordner löschen oder umbenennen.**
- Bei Installation, neuer Version und Deinstallation bleibt der Ordner erhalten.
- **Sicherung nach dem Renntag:** Racetag schließen und den ganzen Ordner `.racetag` auf einen USB-Stick kopieren.

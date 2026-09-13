// Tooltip catalogue and the single tooltip bubble.
//
// Loaded LAST (after script.js), when the DOM above the <script> tags exists.
//
// How tips are found, highest priority first:
//   1. `data-tip` on the element (static markup, RT_TIPS below, or set live by
//      script.js — e.g. the status pills rewrite their `data-tip` every tick).
//   2. A native `title`. It is adopted into `data-tip` the first time the
//      element is hovered/focused and then removed, so the browser's own
//      tooltip never doubles the bubble. A later `title` write (script.js does
//      that for some dynamic elements) replaces an adopted tip again.
// Static `title` attributes in index.html stay as the no-JS fallback.
//
// The bubble is one `position: fixed` element on <body>, so it is never
// clipped by `overflow: hidden` containers, and it works for <input>/<select>
// where CSS ::after cannot. Mouse: hover with a short delay. Keyboard: shown
// on keyboard focus, Escape hides. Touch: taps never pop tips on ordinary
// buttons (the tap is the action); `.rt-info` icons toggle their tip on tap.
(function () {
  'use strict';

  // Tips keyed by element id (German), written for a race marshal without
  // technical background: what the control does, when to use it. Info icons
  // (`…Info` ids, class `rt-info`) carry the long explanation of a field,
  // always starting with its default value.
  //
  // Elements rendered dynamically carry their tip in the template instead,
  // because they do not exist yet here: standings row buttons, tag ids,
  // missed-read markers and status badges (script.js renderStandings, texts
  // RT.S.rowTip*), rider list items (RT.S.ridersListItemTip), reader
  // candidates (RT.S.candidateApplyTip). Elements whose tip changes at runtime
  // are listed in RT.DYNAMIC_TIP_IDS below.
  window.RT_TIPS = {
    // ---- Header ----------------------------------------------------------------
    backendUrlControl: 'Adresse des Racetag-Servers, z. B. http://localhost:8600. Nur ändern, wenn Racetag auf einem anderen Rechner läuft.',
    connectBtn: 'Mit der eingetragenen Server-Adresse verbinden und Rangliste und Rennen neu laden.',
    importCsvBtn: 'Startliste aus einer CSV-Datei laden (Spalten: tag_id, bib, name; optional verein, uci_id). Fahrer mit schon bekannter Tag-ID werden aktualisiert. CSV-Dateien aus Excel funktionieren.',
    exportTagsBtn: 'Alle in diesem Rennen gelesenen Tags als Startlisten-Vorlage (CSV) speichern.\nSo geht’s: Tags an der Antenne vorbeiführen, exportieren, in Excel Startnummer und Name eintragen und über „CSV importieren“ wieder laden.',
    coupleTagBtn: 'Einen einzelnen Tag einem Fahrer zuordnen: klicken, Tag an die Antenne halten, dann Startnummer und Name eintragen.',
    coupleModeBtn: 'Viele Tags nacheinander koppeln: Tag an die Antenne halten, Startnummer tippen, Enter – nächster Tag. Mit der Auto-Zuweisung sogar ohne Tippen. Nochmal klicken beendet den Modus.',
    ridersBtn: 'Fahrer suchen und bearbeiten: Startnummer, Name, Verein oder UCI-ID ändern – auch ohne Reader, z. B. bei Nachmeldungen.\nTipp: Doppelklick auf eine Zeile der Rangliste öffnet den Fahrer direkt.',
    raceSelect: 'Wählt das aktive Rennen. Alle neuen Durchgänge zählen für das hier gewählte Rennen. Beim Wechsel werden Rangliste und Fahrer dieses Rennens geladen.',
    newRaceBtn: 'Neues Rennen anlegen (Name, Rennformat, Rundenzahl).',
    startRaceBtn: 'Startet die Zeitnahme für das aktive Rennen. Erst ab jetzt zählen Durchgänge als Runden.',
    endRaceBtn: 'Beendet das Rennen. Die Rangliste wird eingefroren, weitere Durchgänge ändern nichts mehr.\nVersehentlich beendet? „Rennen wieder öffnen“ macht es rückgängig.',
    reopenRaceBtn: 'Macht ein versehentliches „Rennen beenden“ rückgängig. Durchgänge, die inzwischen erfasst wurden, zählen wieder mit.',
    exportCsvBtn: 'Die aktuelle Rangliste des aktiven Rennens als CSV-Datei speichern (lässt sich mit Excel öffnen).',
    resetRaceBtn: 'Löscht alle Runden und Durchgänge des aktiven Rennens und setzt es auf „nicht gestartet“ zurück. Die gekoppelten Fahrer bleiben erhalten.\nAchtung: lässt sich nicht rückgängig machen.',
    settingsBtn: 'Einstellungen: Reader suchen und verbinden, Antennenleistung, Rundenregeln, Hilfe und Support.',
    totalLapsInput: 'Anzahl Runden bis ins Ziel für das aktive Rennen (1–999). Mit „Übernehmen“ speichern.',
    applyLapsBtn: 'Speichert die eingetragene Rundenzahl für das aktive Rennen. Wirkt sofort, auch während das Rennen läuft.',
    tagColumnControl: 'Blendet die Spalte mit der Tag-ID in der Rangliste ein oder aus. Wird nur zur Fehlersuche gebraucht.',
    raceStatus: 'Zustand des aktiven Rennens: nicht gestartet, läuft (mit den verbleibenden Runden des Führenden) oder beendet.',
    importErrorsToggle: 'Zeigt die Zeilen der CSV-Datei, die nicht importiert werden konnten, jeweils mit Grund.',

    // ---- Koppel-Modus ------------------------------------------------------------
    coupleCounter: 'So viele Tags wurden gekoppelt, seit der Koppel-Modus geöffnet wurde.',
    coupleBeepControl: 'Ton an oder aus.\nDoppelton: neuer Tag – bitte Nummer eingeben.\nEinzelton: Tag schon bekannt oder gespeichert.',
    coupleModeCloseBtn: 'Koppel-Modus beenden. Bei ungespeicherter Eingabe wird vorher nachgefragt.',
    coupleRecoupleBtn: 'Diesem schon gekoppelten Tag eine andere Startnummer oder einen anderen Namen geben.',
    coupleAutoRanges: 'Welche Startnummern verteilt werden sollen, z. B. 1-75 oder 101-175,181-190. Schon vergebene Nummern werden übersprungen.',
    coupleAutoStartBtn: 'Automatische Zuweisung: Racetag zeigt die nächste freie Nummer an, und jeder neue Tag an der Antenne bekommt genau diese Nummer – ohne Tippen. Schon gekoppelte Tags bleiben unverändert.',
    coupleAutoStopBtn: 'Automatische Zuweisung anhalten. Schon gekoppelte Tags bleiben erhalten.',
    coupleBib: 'Startnummer für den angezeigten Tag. Enter speichert.',
    coupleName: 'Name des Fahrers (optional). Enter speichert.',
    coupleSaveBtn: 'Tag mit dieser Startnummer koppeln und zum nächsten Tag gehen (Enter).',
    coupleSkipBtn: 'Diesen Tag – im Auto-Modus diese Nummer – auslassen und weitermachen.\nEscape im Eingabefeld: erst leeren, beim zweiten Mal überspringen.',

    // ---- Standings table headers -----------------------------------------------
    standingsColPos: 'Platz. Fahrer mit DNF, DNS oder DSQ bekommen keinen Platz und stehen am Ende.',
    standingsColTag: 'Tag-ID des Transponders. Klick auf eine ID kopiert sie.',
    standingsColBib: 'Startnummer. „–“ bedeutet: Dieser Tag ist noch keinem Fahrer zugeordnet.',
    standingsColName: 'Name des Fahrers, dahinter ggf. der Status (DNF, DNS, DSQ).\nDoppelklick auf eine Zeile öffnet den Fahrer zum Bearbeiten.',
    standingsColLaps: 'Gezählte Runden. Ein ⚠ bedeutet: Der Reader hat vermutlich eine Runde verpasst – mit +1 nachtragen.',
    standingsColFinished: '„Ja“, sobald der Fahrer nach seiner letzten Runde die Ziellinie überquert hat.',
    standingsColLastPass: 'Uhrzeit, zu der der Fahrer zuletzt über die Ziellinie gefahren ist.',
    standingsColGap: 'Rückstand auf den Führenden: Zeit bei gleicher Rundenzahl, „+N Rd.“ bei überrundeten Fahrern.',
    standingsColTotal: 'Zeit vom Rennstart bis zum letzten Durchgang, in Sekunden.',
    netTimeHeader: 'Netto-Zeit: vom ersten eigenen Durchgang bis ins Ziel – wichtig bei Einzelstart (Zeitfahren).\nKlick sortiert danach, nochmal Klick zeigt wieder die offizielle Reihenfolge.',
    standingsColLapActions: 'Runden von Hand korrigieren: +1 gutschreiben, −1 abziehen, ✎ mit eigener Uhrzeit nachtragen oder Status setzen.',

    // ---- Antennen-Diagnose -------------------------------------------------------
    diagnosticsSummary: 'Zeigt pro Antenne, wie viele Durchgänge in der letzten Minute erfasst wurden.\nBleibt eine Antenne bei 0, obwohl Fahrer vorbeifahren: Antennenkabel prüfen.',
    diagnosticsColAntenna: 'Nummer des Antennen-Anschlusses am Reader.',
    diagnosticsColPasses: 'Gespeicherte Durchgänge dieser Antenne in den letzten 60 Sekunden (aktives Rennen).',

    // ---- Fahrer bearbeiten ----------------------------------------------------------
    ridersSearch: 'Nach Startnummer, Name oder Tag-ID suchen. Einen Treffer anklicken, um ihn zu bearbeiten.',
    riderEditTag: 'Tag-ID des gewählten Fahrers (nicht änderbar).',
    riderEditBib: 'Startnummer des Fahrers.',
    riderEditName: 'Name, wie er in der Rangliste und im Export erscheint.',
    riderEditVerein: 'Verein oder Team (optional, erscheint im Export). Leer lassen behält den bisherigen Eintrag.',
    riderEditUci: 'UCI-ID des Fahrers (optional). Leer lassen behält den bisherigen Eintrag.',
    riderEditAllRaces: 'Startnummer und Name auch in allen anderen Rennen ändern, in denen dieser Tag gekoppelt ist.',
    riderEditAllRacesInfo: 'Standard: aus.\nAn: Startnummer und Name werden auch in allen anderen Rennen geändert, in denen dieser Tag gekoppelt ist.\nNur einschalten, wenn der Fahrer den ganzen Tag denselben Tag und dieselbe Nummer behält. Werden Tags zwischen den Rennen neu vergeben, würde das die Ergebnisse früherer Rennen verfälschen.',
    riderEditSaveBtn: 'Änderungen an diesem Fahrer speichern (Enter).',
    ridersCloseBtn: 'Fenster schließen. Nicht gespeicherte Änderungen gehen verloren.',

    // ---- Fahrer koppeln (einzeln) ------------------------------------------------------
    modalTagId: 'Der zuletzt gelesene, noch keinem Fahrer zugeordnete Tag.',
    modalBib: 'Startnummer, die dieser Tag bekommt (Pflichtfeld). Enter speichert.',
    modalName: 'Name des Fahrers (optional). Enter speichert.',
    modalSaveBtn: 'Tag mit Startnummer und Name speichern (Enter).',
    modalCancelBtn: 'Schließen, ohne zu koppeln.',
    modalRetryBtn: 'Speichern noch einmal versuchen.',

    // ---- Rennen anlegen ------------------------------------------------------------------
    newRaceName: 'Name, der in der Rennauswahl und im Export steht (Pflichtfeld).',
    newRaceScheduled: 'Optional: geplanter Start. Erscheint neben dem Namen in der Rennauswahl. Die Zeitnahme beginnt trotzdem erst mit „Rennen starten“.',
    newRaceFormat: 'Feste Rundenzahl oder feste Zeit mit anschließenden Schlussrunden.',
    newRaceFormatInfo: 'Standard: Feste Rundenzahl.\nFeste Rundenzahl: Das Rennen endet, wenn die eingestellte Rundenzahl gefahren ist.\nZeit + Schlussrunden: Es wird eine feste Zeit gefahren (z. B. 30 Minuten). Danach läutet die Glocke die Schlussrunden ein – üblich bei Kriterien nach Zeit.',
    newRaceTotalLaps: 'Anzahl Runden bis ins Ziel (1–999).',
    newRaceDurationMin: 'Wie lange gefahren wird, bevor die Schlussrunden beginnen (in Minuten).',
    newRaceFinalLaps: 'Anzahl Runden, die nach Ablauf der Zeit noch gefahren werden.',
    newRaceFinalLapsInfo: 'Standard: 3.\nIst die Zeit abgelaufen, legt die nächste Zieldurchfahrt des Führenden das Ziel fest: seine Runden plus diese Schlussrunden. 0 = diese Zieldurchfahrt ist schon der Zieleinlauf.',
    newRacePerRider: 'Jeder Fahrer ist erst im Ziel, wenn er selbst die volle Rundenzahl gefahren ist.',
    newRacePerRiderInfo: 'Standard: aus (Kriterium).\nAus: Erreicht der Führende die Rundenzahl, beginnt der Zieleinlauf – alle anderen sind bei ihrer nächsten Zieldurchfahrt im Ziel, egal in welcher Runde.\nAn: Jeder Fahrer ist erst im Ziel, wenn er selbst alle Runden gefahren ist, z. B. bei Zeitfahren oder Training.',
    newRaceSnapshotInterval: 'Wie oft Racetag automatisch eine Sicherung dieses Rennens anlegt (in Sekunden, 0 = aus).',
    newRaceSnapshotIntervalInfo: 'Standard: 120 Sekunden.\nIn diesem Abstand speichert Racetag automatisch eine Kopie der Rangliste (CSV) und der Datenbank im Datenordner; die letzten 30 bleiben erhalten. So geht bei einem Absturz nichts verloren.\nNur auf 0 (aus) stellen, wenn der Speicherplatz knapp ist.',
    newRaceActivate: 'Das neue Rennen sofort zum aktiven Rennen machen. Aus: Es wird nur angelegt und kann später in der Rennauswahl gewählt werden.',
    newRaceSaveBtn: 'Rennen mit diesen Angaben anlegen.',
    newRaceCancelBtn: 'Schließen, ohne ein Rennen anzulegen.',

    // ---- Einstellungen ---------------------------------------------------------------------
    settingsReaderStatus: 'Aktueller Zustand der Verbindung zum Reader. Wird laufend aktualisiert.',
    readerDiscoverBtn: 'Sucht den Reader automatisch im Netzwerk (dauert bis zu 15 Sekunden). Gefundene Reader lassen sich mit einem Klick übernehmen.',
    readerRestartBtn: 'Trennt die Verbindung zum Reader und baut sie neu auf. Hilft, wenn der Reader-Status länger rot oder orange bleibt.\nDer Reader liest dabei einige Sekunden lang nicht – im laufenden Rennen nur benutzen, wenn er nicht ohnehin getrennt ist.',
    settingsReaderIp: 'Netzwerk-Adresse (IP) des Readers, z. B. 192.168.178.22.',
    settingsReaderIpInfo: 'Standard: leer – Racetag sucht den Reader dann selbst und merkt sich die gefundene Adresse.\nNormalerweise nichts eintragen, sondern „Reader suchen“ benutzen. Nur von Hand ändern, wenn die Suche keinen Reader findet und du die Adresse kennst (z. B. aus der Geräteliste des Routers). Nach dem Speichern verbindet sich Racetag innerhalb weniger Sekunden neu.',
    settingsAntennaPower: 'Sendeleistung der Antennen, 100 bis 300.',
    settingsAntennaPowerInfo: 'Standard: 300 (volle Leistung).\nBestimmt, wie weit entfernt die Antennen einen Tag noch erkennen. Weniger Leistung = kleineres Lesefeld.\nNur verringern, wenn Tags auch weit neben der Ziellinie gelesen werden, z. B. Fahrer, die im Startbereich warten. Werden dagegen Fahrer verpasst, wieder erhöhen. In Zehnerschritten ändern. Wirkt nach wenigen Sekunden: Der Reader verbindet dabei kurz neu – im laufenden Rennen nur ändern, wenn es nötig ist.',
    settingsMinLap: 'Wartezeit nach einem Durchgang, bevor derselbe Tag wieder als Runde zählt (in Sekunden).',
    settingsMinLapInfo: 'Standard: 8 Sekunden.\nBeim Vorbeifahren wird ein Tag oft mehrmals hintereinander gelesen. Innerhalb dieser Zeit nach einer gezählten Runde zählt derselbe Tag nicht noch einmal.\nErhöhen, wenn Fahrer doppelt gezählt werden (z. B. weil sie an der Linie stehen bleiben). Der Wert muss deutlich kürzer sein als die schnellste mögliche Runde.',
    settingsTotalLaps: 'Anzahl Runden bis ins Ziel für das aktive Rennen (1–999).',
    settingsTotalLapsInfo: 'Standard: 5.\nDerselbe Wert wie „Rundenzahl“ in der Kopfzeile. Lässt sich auch während des Rennens ändern, z. B. wenn die Rennleitung Runden streicht.',
    settingsSnapshotInterval: 'Wie oft Racetag automatisch eine Sicherung des aktiven Rennens anlegt (in Sekunden, 0 = aus).',
    settingsSnapshotIntervalInfo: 'Standard: 120 Sekunden.\nIn diesem Abstand speichert Racetag automatisch eine Kopie der Rangliste (CSV) und der Datenbank im Datenordner; die letzten 30 bleiben erhalten. So geht bei einem Absturz nichts verloren. Gilt nur für das aktive Rennen.\nNur auf 0 (aus) stellen, wenn der Speicherplatz knapp ist.',
    settingsAdvancedSummary: 'Selten benötigte Anzeige-Optionen.',
    assistantOpenBtn: 'Startet die Einrichtung erneut: Reader finden, Antennentest, erstes Rennen anlegen.',
    openDataFolderBtn: 'Öffnet den Ordner mit Datenbank, Sicherungen und Protokollen im Explorer.',
    supportBundleBtn: 'Packt Protokolle, eine Kopie der Datenbank und die aktuellen Einstellungen in eine ZIP-Datei, die du an den Support schicken kannst.',
    settingsSaveBtn: 'Geänderte Felder speichern. Reader-Adresse und Antennenleistung wirken nach wenigen Sekunden; der Reader verbindet dabei kurz neu.',
    settingsCancelBtn: 'Schließen, ohne die Felder zu speichern. Die Knöpfe „Reader suchen“ und „Reader neu verbinden“ haben bereits sofort gewirkt.',

    // ---- Runde bearbeiten ------------------------------------------------------------------
    lapEditRider: 'Der Fahrer, dessen Runden du gerade korrigierst.',
    lapEditCurrentLaps: 'So viele Runden sind für diesen Fahrer im Moment gezählt.',
    lapEditTimestamp: 'Uhrzeit für die nachgetragene Runde. Leer = jetzt.',
    lapEditTimestampInfo: 'Standard: leer – die Runde zählt mit der aktuellen Uhrzeit.\nLiegt der verpasste Durchgang schon länger zurück, hier die tatsächliche Uhrzeit eintragen, sonst stimmt die Zeit des Fahrers nicht.\nFormat: 2026-06-25T18:30:00.000Z in Weltzeit (UTC) – das ist die deutsche Uhrzeit minus 2 Stunden im Sommer bzw. minus 1 Stunde im Winter. Bei einer vermuteten verpassten Runde ist das Feld schon ausgefüllt.',
    lapEditStatusDnf: 'DNF (nicht beendet): Der Fahrer hat aufgegeben. Er bekommt keinen Platz und steht am Ende der Rangliste.',
    lapEditStatusDns: 'DNS (nicht gestartet): Der Fahrer ist nicht angetreten. Kein Platz, Ende der Rangliste.',
    lapEditStatusDsq: 'DSQ (disqualifiziert): Der Fahrer wurde von der Rennleitung ausgeschlossen. Kein Platz, Ende der Rangliste.',
    lapEditStatusClear: 'Status entfernen – der Fahrer wird wieder normal gewertet.',
    lapEditAddBtn: 'Eine Runde gutschreiben – mit der Uhrzeit aus dem Feld oben, sonst mit der aktuellen Uhrzeit (Enter).',
    lapEditRemoveBtn: 'Die zuletzt erfasste Runde dieses Fahrers löschen.',
    lapEditResetBtn: 'ALLE Durchgänge dieses Fahrers löschen, z. B. wenn er beim Einzelstart schon im Startbereich gelesen wurde. Der Fahrer startet danach neu.',
    lapEditCancelBtn: 'Fenster schließen, ohne etwas zu ändern.',

    // ---- Einrichtungs-Assistent ------------------------------------------------------------
    assistantReaderStatus: 'Aktueller Zustand der Verbindung zum Reader. Wird laufend aktualisiert.',
    assistantDiscoverBtn: 'Sucht den Reader automatisch im Netzwerk (dauert bis zu 15 Sekunden).',
    assistantAntennaResetBtn: 'Setzt alle Antennen wieder auf „wartet“ und beginnt den Test von vorn.',
    assistantRaceName: 'Name des ersten Rennens, z. B. „Volksradrennen Sonntag“.',
    assistantRaceLaps: 'Anzahl Runden bis ins Ziel (1–999). Lässt sich später jederzeit ändern.',
    assistantReuseKeep: 'Das aktive Rennen behalten: Es bekommt den eingegebenen Namen und die Rundenzahl, alle eingetragenen Fahrer bleiben erhalten. Empfohlen, wenn die Startliste schon geladen ist.',
    assistantReuseNew: 'Ein zusätzliches, leeres Rennen anlegen und aktivieren. Die schon eingetragenen Fahrer bleiben im bisherigen Rennen und müssen für das neue Rennen neu geladen werden.',
    assistantSkipBtn: 'Assistent schließen. Er lässt sich jederzeit über die Einstellungen wieder öffnen.',
    assistantBackBtn: 'Zurück zum vorherigen Schritt.',
    assistantNextBtn: 'Weiter zum nächsten Schritt. Im letzten Schritt wird das Rennen angelegt (oder das aktive Rennen übernommen). Gestartet wird es erst später mit „Rennen starten“.',
  };

  const RT = window.RT || (window.RT = {});

  // Ids whose `data-tip` script.js writes at runtime (not in RT_TIPS): the
  // status pills (live detail) and the update notice (installed version).
  RT.DYNAMIC_TIP_IDS = Object.freeze(['pillReader', 'pillAntennas', 'pillConnection', 'updateNotice']);
  const BUBBLE_ID = 'rtTipBubble';
  const SHOW_DELAY_MS = 350;
  const TOUCH_MOUSE_GUARD_MS = 800; // synthetic mouse events follow a tap
  const TOUCH_AUTO_HIDE_MS = 6000;
  const WATCH_INTERVAL_MS = 400;
  const VIEWPORT_MARGIN_PX = 8;
  const GAP_PX = 8;
  const NON_TYPING_KEYS = new Set(['Shift', 'Control', 'Alt', 'Meta', 'CapsLock', 'Escape']);

  let bubble = null;
  let current = null; // element whose tip is shown
  let pending = null; // element waiting for the show delay
  let showTimer = null;
  let watchTimer = null;
  let touchHideTimer = null;
  let lastTouchAt = 0;
  // Focus tips only follow keyboard navigation. Text inputs match
  // :focus-visible even on mouse clicks and modals focus their first field
  // programmatically, both of which would pop a bubble over the form.
  let keyboardMode = false;
  let observer = null;

  function applyTips(root) {
    const scope = root && typeof root.contains === 'function' ? root : document;
    Object.keys(window.RT_TIPS).forEach((id) => {
      const el = document.getElementById(id);
      if (!el || (scope !== document && !scope.contains(el))) return;
      el.setAttribute('data-tip', window.RT_TIPS[id]);
      el.removeAttribute('data-tip-from-title');
      el.removeAttribute('title');
    });
  }

  function ensureBubble() {
    if (bubble && bubble.isConnected) return bubble;
    bubble = document.createElement('div');
    bubble.id = BUBBLE_ID;
    bubble.className = 'rt-tip';
    bubble.setAttribute('role', 'tooltip');
    bubble.hidden = true;
    document.body.appendChild(bubble);
    return bubble;
  }

  // Resolve the tip text of one element, adopting a native title on the way.
  function tipText(el) {
    const title = el.getAttribute('title');
    if (title !== null) {
      const adopt = title.trim() !== ''
        && (!el.hasAttribute('data-tip') || el.hasAttribute('data-tip-from-title'));
      if (adopt) {
        el.setAttribute('data-tip', title);
        el.setAttribute('data-tip-from-title', '1');
      }
      el.removeAttribute('title');
    }
    return (el.getAttribute('data-tip') || '').trim();
  }

  function findTipElement(node) {
    let el = node && node.nodeType === 1 ? node : (node && node.parentElement) || null;
    while (el && el !== document.body && el !== bubble) {
      if ((el.hasAttribute('data-tip') || el.hasAttribute('title')) && tipText(el)) return el;
      el = el.parentElement;
    }
    return null;
  }

  function position(el) {
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) {
      hide();
      return;
    }
    const vw = document.documentElement.clientWidth || window.innerWidth;
    const vh = document.documentElement.clientHeight || window.innerHeight;
    bubble.style.left = '0px';
    bubble.style.top = '0px';
    const box = bubble.getBoundingClientRect();
    let top = rect.bottom + GAP_PX;
    let placement = 'below';
    if (top + box.height > vh - VIEWPORT_MARGIN_PX && rect.top - GAP_PX - box.height >= VIEWPORT_MARGIN_PX) {
      top = rect.top - GAP_PX - box.height;
      placement = 'above';
    }
    const maxLeft = Math.max(VIEWPORT_MARGIN_PX, vw - box.width - VIEWPORT_MARGIN_PX);
    const left = Math.min(Math.max(rect.left + rect.width / 2 - box.width / 2, VIEWPORT_MARGIN_PX), maxLeft);
    bubble.style.left = `${Math.round(left)}px`;
    bubble.style.top = `${Math.round(Math.max(VIEWPORT_MARGIN_PX, top))}px`;
    bubble.setAttribute('data-placement', placement);
  }

  function addDescribedBy(el) {
    const ids = (el.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean);
    if (!ids.includes(BUBBLE_ID)) el.setAttribute('aria-describedby', ids.concat(BUBBLE_ID).join(' '));
  }

  function removeDescribedBy(el) {
    const ids = (el.getAttribute('aria-describedby') || '').split(/\s+/).filter((id) => id && id !== BUBBLE_ID);
    if (ids.length) el.setAttribute('aria-describedby', ids.join(' '));
    else el.removeAttribute('aria-describedby');
  }

  function cancelPending() {
    clearTimeout(showTimer);
    showTimer = null;
    pending = null;
  }

  function show(el) {
    cancelPending();
    const text = tipText(el);
    if (!text) {
      hide();
      return;
    }
    if (current) hide(); // also drops the previous observer/watch timer
    ensureBubble();
    current = el;
    bubble.textContent = text;
    bubble.hidden = false;
    position(el);
    if (!current) return; // position() hid it (element not rendered)
    addDescribedBy(el);

    // Live tips (status pills) change while visible; follow them.
    if (typeof MutationObserver === 'function') {
      observer = new MutationObserver(() => {
        if (!current) return;
        const updated = tipText(current);
        if (!updated) {
          hide();
          return;
        }
        if (bubble.textContent !== updated) bubble.textContent = updated;
        position(current);
      });
      observer.observe(el, { attributes: true, attributeFilter: ['data-tip', 'title'] });
    }
    // Rows are rebuilt on every SSE frame: a removed or hidden target never
    // fires mouseout, so poll for it while the bubble is up.
    watchTimer = setInterval(() => {
      if (!current || !current.isConnected || current.closest('[hidden]')) hide();
    }, WATCH_INTERVAL_MS);
  }

  function hide() {
    cancelPending();
    clearTimeout(touchHideTimer);
    touchHideTimer = null;
    clearInterval(watchTimer);
    watchTimer = null;
    if (observer) {
      observer.disconnect();
      observer = null;
    }
    if (current) removeDescribedBy(current);
    current = null;
    if (bubble) bubble.hidden = true;
  }

  function scheduleShow(el) {
    if (el === current || el === pending) return;
    cancelPending();
    pending = el;
    showTimer = setTimeout(() => {
      const target = pending;
      pending = null;
      showTimer = null;
      if (target && target.isConnected) show(target);
    }, current ? 0 : SHOW_DELAY_MS); // moving between tipped elements: no delay
  }

  document.addEventListener('mouseover', (e) => {
    if (Date.now() - lastTouchAt < TOUCH_MOUSE_GUARD_MS) return;
    const el = findTipElement(e.target);
    if (el) scheduleShow(el);
    else if (current || pending) hide();
  });

  document.addEventListener('mouseout', (e) => {
    if (Date.now() - lastTouchAt < TOUCH_MOUSE_GUARD_MS) return;
    const owner = current || pending;
    if (!owner) return;
    const to = e.relatedTarget;
    if (to && owner.contains(to)) return;
    if (!to) hide(); // pointer left the window
  });

  document.addEventListener('focusin', (e) => {
    const el = findTipElement(e.target);
    if (el && keyboardMode) show(el);
    else if (current) hide();
  });

  document.addEventListener('focusout', (e) => {
    if (current && (e.target === current || current.contains(e.target))) hide();
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (current) hide(); // no preventDefault: modals close on the same key
      return;
    }
    if (e.key === 'Tab' || e.key.startsWith('Arrow')) keyboardMode = true;
    else if (current && !NON_TYPING_KEYS.has(e.key)) hide(); // typing: get the bubble out of the way
  }, true);

  document.addEventListener('pointerdown', (e) => {
    keyboardMode = false;
    if (e.pointerType === 'mouse') {
      if (current) hide(); // clicking acts on the control; drop the tip
      return;
    }
    lastTouchAt = Date.now();
    const info = e.target && e.target.closest ? e.target.closest('.rt-info') : null;
    if (info && info === current) {
      hide();
      return;
    }
    hide();
    if (info && tipText(info)) {
      show(info);
      touchHideTimer = setTimeout(hide, TOUCH_AUTO_HIDE_MS);
    }
  }, { passive: true });

  // Info icons are buttons: a mouse click or Enter/Space pins the tip open
  // (hover may already show it; pointerdown above hid it for mouse clicks).
  document.addEventListener('click', (e) => {
    if (Date.now() - lastTouchAt < TOUCH_MOUSE_GUARD_MS) return;
    const info = e.target && e.target.closest ? e.target.closest('.rt-info') : null;
    if (info && tipText(info)) show(info);
  });

  window.addEventListener('scroll', () => { if (current) hide(); }, { capture: true, passive: true });
  window.addEventListener('resize', () => { if (current) hide(); });

  RT.applyTips = applyTips;
  applyTips(document);
})();

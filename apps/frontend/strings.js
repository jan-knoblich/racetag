// Operator-facing string table (German) and error-message helpers.
//
// Loaded FIRST (before api.js, script.js, tooltips.js) so every later script
// can use `RT.S`, `RT.fmt()` and `RT.apiError()`. Everything lives under the
// single `window.RT` namespace on purpose: the other classic scripts share one
// global scope, and a duplicate top-level `const` would kill the whole page.
//
// Conventions:
//   - Keys are English camelCase, values are German.
//   - Placeholders use `{name}` and are filled by `RT.fmt(key, {name: …})`.
//   - Multi-line tooltip texts use "\n" (the tooltip bubble keeps line breaks).
//   - Code, logs and API field names stay English; only values here are shown.
(function () {
  'use strict';

  const S = {
    // ---- Status bar: pill labels -------------------------------------------
    pillReaderLabel: 'Reader',
    pillAntennasLabel: 'Antennen',
    pillConnectionLabel: 'Verbindung',
    pillNoData: '–',

    // ---- Status bar: Reader pill ---------------------------------------------
    readerStateActive: 'verbunden',
    readerStateActiveWithIp: 'verbunden ({ip})',
    readerStateConnecting: 'verbinde…',
    readerStateConnectingRetry: 'verbinde neu in {s} s',
    readerStateConfiguring: 'wird eingerichtet…',
    readerStateSearching: 'wird gesucht…',
    readerStateMultiple: 'mehrere gefunden',
    readerStateLost: 'Verbindung verloren, verbinde neu…',
    readerStateUnknown: 'keine Rückmeldung',
    readerStateStopped: 'gestoppt',
    readerStateRestarting: 'wird neu gestartet…',
    readerStateStarting: 'startet…',
    readerStateUnavailable: 'nicht verfügbar',
    readerHintPending: 'Reader-Status wird abgefragt…',
    readerHintConfiguring: 'Der Reader ist erreichbar und wird gerade eingerichtet.',
    readerHintConnecting: 'Kabel und Strom am Reader prüfen. Nach dem Einschalten braucht der Reader etwa 30 Sekunden.',
    readerHintSearching: 'Reader wird im Netzwerk gesucht… (Kabel und Strom prüfen)',
    readerHintMultiple: 'Mehrere Reader gefunden – in den Einstellungen den richtigen auswählen.',
    readerHintLost: 'Die Verbindung zum Reader ist abgerissen. Racetag verbindet automatisch neu – Netzwerkkabel prüfen.',
    readerHintUnknown: 'Der Reader-Dienst meldet sich nicht. In den Einstellungen „Reader neu verbinden“ versuchen.',
    readerHintStopped: 'Der Reader-Dienst wurde beendet.',
    readerHintRestarting: 'Der Reader-Dienst wird gerade neu gestartet. Das dauert ein paar Sekunden.',
    readerHintStarting: 'Der Reader-Dienst startet gerade. Das dauert ein paar Sekunden.',
    readerHintUnavailable: 'Diese Version der Anwendung liefert keinen Reader-Status.',
    readerHintStale: 'Anzeige veraltet – keine Live-Verbindung zur Anwendung.',
    readerTipIp: 'Adresse: {ip}',
    readerTipSource: 'Adresse aus: {source}',
    readerSourceCli: 'Startparameter',
    readerSourceConfig: 'Einstellungen',
    readerSourceDiscovery: 'automatischer Suche',
    readerTipSerial: 'Seriennummer: {serial}',
    readerTipAntennas: 'Antennen: {ports}',
    readerTipPower: 'Antennenleistung: {power}',
    readerTipLastRead: 'Letzte Lesung: vor {age}',
    readerTipNoReadYet: 'Letzte Lesung: noch keine',
    readerTipConnectedSince: 'Verbunden seit {time}',
    readerTipFailures: 'Fehlgeschlagene Versuche: {n}',
    readerTipError: 'Letzte Meldung: {error}',
    readerTipRestarts: 'Reader-Dienst neu gestartet: {n}×',
    readerTipClick: 'Klick öffnet die Einstellungen.',

    // Known reader-service error texts (English on the wire) → German.
    readerErrorNoReader: 'kein Reader gefunden',
    readerErrorMultiple: 'mehrere Reader gefunden',
    readerErrorTimeout: 'Zeitüberschreitung beim Verbinden',
    readerErrorRefused: 'Verbindung abgelehnt',
    readerErrorUnreachable: 'Reader nicht erreichbar',
    readerErrorSession: 'Reader hat die Sitzung nicht bestätigt',
    readerErrorNoIp: 'keine Reader-Adresse eingestellt',
    readerErrorClosed: 'Reader hat die Verbindung getrennt',
    readerErrorNoReply: 'Reader antwortet nicht',
    readerErrorConfig: 'Einrichtung des Readers fehlgeschlagen',
    readerErrorConnection: 'Verbindungsfehler',
    readerErrorOther: 'technischer Fehler (Details im Protokoll)',

    // ---- Status bar: Antennen pill -------------------------------------------
    antennasOk: '{ports} OK',
    antennasNoneDetected: 'keine erkannt',
    // Powered ports the reader did not report as connected (antennas_detected)
    // and that have not read a tag on this connection yet.
    antennasNotDetected: '{ports} nicht erkannt',
    antennasPartialNotDetected: '{ok} OK, {missing} nicht erkannt',
    // Race running, other antennas read tags, this one read nothing in 60 s.
    antennasSilent: '{ports} liest nichts',
    antennasPartialSilent: '{ok} OK, {silent} liest nichts',
    antennasTipPort: 'Antenne {port}: {reads} Lesungen, {passes} Durchgänge (letzte 60 s)',
    antennasTipPortPasses: 'Antenne {port}: {passes} Durchgänge (letzte 60 s)',
    antennasTipNoReader: 'Reader nicht verbunden – noch keine Antennendaten.',
    antennasTipNoneDetected: 'Reader verbunden, aber keine Antenne erkannt. Antennenkabel prüfen.',
    antennasTipNotDetected: 'Antenne {port} wurde beim Verbinden nicht erkannt. Antennenkabel an Anschluss {port} prüfen. Liest die Antenne beim Test trotzdem Tags, ist alles in Ordnung.',
    antennasTipSilent: 'Antenne {port} hat in der letzten Minute nichts gelesen, obwohl die anderen Antennen Tags lesen. Antennenkabel an Anschluss {port} prüfen.',
    antennasTipNoData: 'Noch keine Antennendaten.',
    antennasTipZeroHint: 'Zeigt ein Anschluss trotz vorbeifahrender Fahrer 0 Lesungen, das Antennenkabel prüfen.',
    antennasTipClick: 'Klick öffnet die Antennen-Diagnose.',

    // ---- Status bar: Verbindung pill -----------------------------------------
    connConnecting: 'verbinde…',
    connLive: 'live',
    connReconnectingIn: 'verbinde neu in {s} s',
    connReconnectingNow: 'verbinde neu…',
    connDown: 'getrennt – neuer Versuch in {s} s',
    connDisconnected: 'getrennt',
    connTipLive: 'Live-Verbindung zur Anwendung steht. Ergebnisse werden sofort aktualisiert.',
    connTipConnecting: 'Verbindung zur Anwendung wird aufgebaut…',
    connTipReconnecting: 'Live-Verbindung unterbrochen – Racetag verbindet automatisch neu.',
    connTipDown: 'Die Anwendung ist nicht erreichbar. Racetag versucht es automatisch weiter.',
    connTipDisconnected: 'Keine Verbindung zur Anwendung.',
    connTipBackend: 'Server: {url}',
    connTipVersion: 'Version: {version}',
    connTipDataDir: 'Datenordner: {path}',
    connTipClick: 'Klick verbindet sofort neu.',

    // ---- Durations -------------------------------------------------------------
    durationSeconds: '{s} s',
    durationMinutes: '{m} min {s} s',
    durationHours: '{h} h {m} min',

    // ---- Toasts ----------------------------------------------------------------
    toastReaderLost: 'Verbindung zum Reader verloren – verbinde neu…',
    toastReaderBack: 'Reader wieder verbunden (Unterbrechung {duration})',
    toastReaderNewIp: 'Reader unter neuer Adresse gefunden: {ip}',
    toastReaderServiceSilent: 'Reader-Dienst antwortet nicht',
    toastReaderIpApplied: 'Reader-Adresse {ip} übernommen – verbinde neu…',
    toastReaderRestart: 'Reader wird neu verbunden…',
    toastSettingsSaved: 'Einstellungen gespeichert',
    toastSettingsSavedReconnect: 'Einstellungen gespeichert – Reader verbindet neu',
    toastRaceCreated: 'Rennen „{name}“ angelegt',
    toastRaceCreatedStartHint: 'Rennen „{name}“ angelegt – zum Start „Rennen starten“ drücken, erst dann zählen Runden',
    toastRaceKeptStartHint: 'Rennen „{name}“ eingerichtet – zum Start „Rennen starten“ drücken, erst dann zählen Runden',
    toastDataFolderFailed: 'Datenordner konnte nicht geöffnet werden',
    toastSupportBundleSaved: 'Support-Paket gespeichert',

    // ---- Settings modal --------------------------------------------------------
    settingsNoActiveRace: 'Kein aktives Rennen',
    settingsRaceLoadFailed: 'Rennen konnte nicht geladen werden',
    settingsNetworkError: 'Netzwerkfehler',
    settingsLoadFailed: 'Einstellungen konnten nicht geladen werden',
    settingsSaveFailed: 'Speichern fehlgeschlagen',
    settingsSnapshotSaveFailed: 'Snapshot-Intervall konnte nicht gespeichert werden',
    settingsVersion: 'Version {version}',
    settingsVersionUnknown: 'Version unbekannt',
    settingsDataDir: 'Datenordner: {path}',

    // Reader discovery / restart
    discoverRunning: 'Suche läuft… (bis zu 15 Sekunden)',
    discoverFoundOne: '1 Reader gefunden – zum Verwenden „Übernehmen“ klicken.',
    discoverFoundMany: '{n} Reader gefunden – den richtigen auswählen.',
    discoverFoundCurrent: 'Reader gefunden – diese Adresse ist bereits eingestellt.',
    discoverNone: 'Kein Reader gefunden. Netzwerkkabel und Strom am Reader prüfen und erneut suchen.',
    discoverTimeout: 'Keine Antwort innerhalb von 15 Sekunden. Bitte erneut suchen.',
    discoverServiceUnavailable: 'Der Reader-Dienst läuft nicht. „Reader neu verbinden“ versuchen.',
    discoverNotSupported: 'Die Reader-Suche ist in dieser Version nicht verfügbar.',
    discoverFailed: 'Reader-Suche fehlgeschlagen',
    discoverLastResult: 'Zuletzt gefunden:',
    candidateSerial: 'S/N {serial}',
    candidateSerialUnknown: 'S/N unbekannt',
    candidateSourceArp: 'aus ARP-Tabelle',
    candidateSourceSweep: 'Netzwerk-Suche',
    candidateSourceLinklocal: 'Werks-Adresse',
    candidateSourceConnected: 'aktuell verbunden',
    candidateApply: 'Übernehmen',
    candidateApplyTip: 'Diese Adresse speichern und den Reader damit verbinden.',
    candidateCurrent: 'eingestellt',
    readerIpApplyFailed: 'Reader-Adresse konnte nicht gespeichert werden',
    readerRestartFailed: 'Neu verbinden fehlgeschlagen',
    readerRestartNotSupported: 'Neu verbinden ist in dieser Version nicht verfügbar.',

    // Desktop support tooling
    supportBusy: 'Support-Paket wird erstellt…',
    supportSaved: 'Gespeichert: {path}',
    supportCancelled: 'Abgebrochen.',
    supportFailed: 'Support-Paket konnte nicht erstellt werden.',
    desktopApiUnavailable: 'Diese Funktion gibt es nur in der Desktop-App.',

    // ---- First-run assistant ---------------------------------------------------
    assistantStepOf: 'Schritt {step} von {total}',
    assistantNext: 'Weiter',
    assistantFinish: 'Rennen anlegen und fertig',
    assistantFinishKeep: 'Rennen übernehmen und fertig',
    assistantCreating: 'Wird angelegt…',
    assistantAntennaLabel: 'Antenne {port}',
    assistantAntennaWaiting: 'wartet auf Tag…',
    assistantAntennaReads: '{n} Lesungen',
    assistantAntennaNoReader: 'Der Reader ist noch nicht verbunden. Zurück zu Schritt 1 oder kurz warten.',
    assistantAntennaNoPorts: 'Noch keine Antenne erkannt. Antennenkabel prüfen.',
    assistantAntennaNotDetected: 'nicht erkannt – Kabel prüfen',
    assistantAntennaProgress: '{ok} von {total} Antennen haben den Tag gelesen.',
    assistantAntennaAllOk: 'Alle Antennen lesen Tags – weiter zum nächsten Schritt.',
    assistantRaceNameRequired: 'Bitte einen Namen für das Rennen eingeben.',
    assistantRaceLapsInvalid: 'Die Rundenzahl muss zwischen 1 und 999 liegen.',
    assistantRaceCreateFailed: 'Rennen konnte nicht angelegt werden',
    assistantRaceKeepFailed: 'Rennen konnte nicht übernommen werden',
    // Step 3 when the active, not yet started race already has riders.
    assistantReuseText: 'Im aktiven Rennen „{name}“ sind schon Fahrer eingetragen (Anzahl: {n}). Ein neues Rennen beginnt ohne Fahrer.',
    confirmAssistantReplaceRunning: 'Das Rennen „{name}“ läuft gerade. Wird jetzt ein neues Rennen aktiviert, zählen die Durchgänge nicht mehr für das laufende Rennen.\n\nNeues Rennen trotzdem anlegen und aktivieren?',

    // ---- Update notice -----------------------------------------------------------
    updateAvailable: 'Neue Version {version} verfügbar',
    updateTip: 'Installiert ist Version {current}. Klick öffnet die Download-Seite.',

    // ---- Race control (header buttons, race banner) -----------------------------
    // The backend creates a race named "Default race" on a fresh database.
    raceDefaultName: 'Standard-Rennen',
    btnStartRace: 'Rennen starten',
    btnRaceRunning: 'Rennen läuft',
    btnRaceEnded: 'Rennen beendet',
    btnEndRace: 'Rennen beenden',
    btnEnded: 'Beendet',
    raceBannerNotStarted: 'Nicht gestartet – zum Beginnen „Rennen starten“ drücken',
    raceBannerRunningSince: 'Läuft seit {time}',
    raceBannerEndedAt: 'Beendet um {time}',
    raceBannerFinishing: ' — 🏁 LETZTE RUNDE / Zieleinlauf',
    raceBannerOneLapToGo: ' — 🔔 noch 1 Runde',
    raceBannerLapsToGo: ' — noch {n} Runden',
    confirmEndRace: 'Rennen beenden? Die Rangliste wird eingefroren.',
    // Actions that make the reader reconnect, asked only while a race runs
    // and the reader is connected.
    confirmReaderRestartRace: 'Das Rennen läuft. Beim Neuverbinden liest der Reader einige Sekunden lang nicht – Fahrer, die dann über die Linie fahren, werden nicht erfasst.\n\nReader trotzdem neu verbinden?',
    confirmReaderSettingsRace: 'Das Rennen läuft. Nach dem Speichern verbindet der Reader kurz neu – Durchgänge in diesen Sekunden können verloren gehen.\n\nTrotzdem speichern?',
    confirmReaderIpRace: 'Das Rennen läuft und der Reader ist verbunden. Mit der neuen Adresse verbindet Racetag neu – Durchgänge in diesen Sekunden können verloren gehen.\n\nAdresse trotzdem übernehmen?',
    confirmReopenRace: 'Rennen wieder öffnen? Durchgänge, die nach dem Beenden erfasst wurden, werden mitgezählt.',
    confirmResetRace: 'Rennen zurücksetzen? Alle Runden und Durchgänge dieses Rennens werden gelöscht. Die gekoppelten Fahrer bleiben erhalten.',
    toastRaceStarted: 'Rennen gestartet',
    toastRaceEnded: 'Rennen beendet',
    toastRaceReopened: 'Rennen wieder geöffnet',
    toastRaceReset: 'Rennen zurückgesetzt',
    toastRaceResetRemote: 'Das Rennen wurde zurückgesetzt',
    toastRaceSwitched: 'Rennen gewechselt',
    toastTotalLapsSet: 'Rundenzahl auf {n} gesetzt',
    raceStartFailed: 'Rennen konnte nicht gestartet werden',
    raceEndFailed: 'Rennen konnte nicht beendet werden',
    raceReopenFailed: 'Rennen konnte nicht wieder geöffnet werden',
    raceResetFailed: 'Rennen konnte nicht zurückgesetzt werden',
    raceActivateFailed: 'Rennen konnte nicht gewechselt werden',
    totalLapsSaveFailed: 'Rundenzahl konnte nicht gespeichert werden',
    raceDurationInvalid: 'Die Dauer muss mindestens 1 Minute betragen.',

    // ---- Export / clipboard ------------------------------------------------------
    exportFailed: 'Export fehlgeschlagen',
    toastExported: 'Exportiert: {filename}',
    toastExportCancelled: 'Export abgebrochen',
    toastTagCopied: 'Tag-ID kopiert: {tag}…',
    toastCopyFailed: 'Kopieren fehlgeschlagen',

    // ---- CSV import ----------------------------------------------------------------
    // Column names tag_id / bib / name are the file format and stay as they are.
    csvEmpty: 'Die CSV-Datei ist leer oder enthält keine Datenzeilen.',
    csvNoDataRows: 'Keine Datenzeilen in der CSV-Datei gefunden.',
    csvDelimiterSemicolon: 'Semikolon',
    csvDelimiterTab: 'Tabulator',
    csvDelimiterComma: 'Komma',
    csvRowLabel: '(Zeile {n})',
    csvTooFewColumns: 'nur {n} Spalte(n) – erwartet: tag_id, bib, name (erkanntes Trennzeichen: {delimiter})',
    csvEmptyTagId: 'Spalte tag_id ist leer',
    csvImporting: 'Importiere {i}/{total} Fahrer ({errors} Fehler)…',
    csvImportedToast: '{imported}/{total} Fahrer importiert{skipped}{errors}',
    csvImportComplete: 'Import abgeschlossen: {imported}/{total} Fahrer{skipped}',
    csvSkippedNote: ', {n} leere übersprungen',
    csvErrorsNote: ' ({n} Fehler)',
    csvImportFailed: 'Fehler beim CSV-Import',
    csvReadFailed: 'Die CSV-Datei konnte nicht gelesen werden',

    // ---- Riders: register modal, rider editor, labels ------------------------------
    bibLabel: 'Nr. {bib}',
    riderLabel: 'Nr. {bib} – {name}',
    riderNoName: '—',
    holdTagNearAntenna: 'Tag an die Antenne halten…',
    toastRiderRegistered: 'Nr. {bib} – {name} gekoppelt',
    saveFailed: 'Speichern fehlgeschlagen',
    ridersNoMatch: 'Kein Treffer',
    ridersNoneInRace: 'Keine Fahrer im aktiven Rennen (Startliste schon importiert?)',
    ridersListItemTip: 'Klicken, um diesen Fahrer zu bearbeiten.',
    toastRiderSaved: 'Nr. {bib} – {name} gespeichert{races}',
    toastRiderSavedRaces: ' ({n} Rennen)',

    // ---- Standings table -------------------------------------------------------------
    standingsNoBib: '–',
    standingsUnknownName: 'Unbekannt',
    standingsFinishedYes: 'Ja',
    standingsFinishedNo: 'Nein',
    standingsGapLaps: '+{n} Rd.',
    standingsNetTime: 'Netto-Zeit',
    standingsNetTimeSorted: 'Netto-Zeit ▲',
    rowTipLapAdd: 'Eine Runde gutschreiben (Zeit: jetzt). Für einen Fahrer, den der Reader gerade verpasst hat.',
    rowTipLapRemove: 'Die letzte Runde dieses Fahrers wieder abziehen.',
    rowTipLapEdit: 'Runde mit eigener Uhrzeit nachtragen, Runde abziehen oder Status setzen (DNF/DNS/DSQ).',
    rowTipNoRider: 'Für diesen Tag ist noch kein Fahrer gekoppelt – zuerst über „Fahrer“ oder „Koppel-Modus“ eine Startnummer zuordnen.',
    rowTipCopyTag: 'Klicken, um die Tag-ID in die Zwischenablage zu kopieren.',
    rowTipMissedReads: '{n} Runde(n) evtl. vom Reader verpasst — klick +1 zum Nachtragen',
    rowTipStatusDnf: 'DNF: Rennen nicht beendet (aufgegeben).',
    rowTipStatusDns: 'DNS: nicht gestartet.',
    rowTipStatusDsq: 'DSQ: disqualifiziert.',
    toastSortNet: 'Sortiert nach Netto-Zeit (Zeitfahr-Ergebnis)',
    toastSortOfficial: 'Offizielle Reihenfolge (Runden + Zeit)',
    diagnosticsEmpty: 'Keine Durchgänge in den letzten 60 s',

    // ---- Manual lap corrections ----------------------------------------------------
    lapsCountOne: '1 Runde',
    lapsCountMany: '{n} Runden',
    toastLapAdded: 'Runde hinzugefügt – jetzt {laps}',
    toastLapRemoved: 'Runde entfernt – jetzt {laps}',
    toastLapAddedFor: 'Runde hinzugefügt – {label} hat jetzt {laps}',
    toastLapRemovedFor: 'Runde entfernt – {label} hat jetzt {laps}',
    confirmRemoveLap: 'Die letzte Runde dieses Fahrers entfernen?',
    confirmRemoveLapFor: 'Die letzte Runde von {label} entfernen?',
    confirmResetRider: 'ALLE Durchgänge von {label} löschen? Der Fahrer startet danach einen frischen Versuch.',
    toastRiderReset: '{label} zurückgesetzt ({n} Durchgänge gelöscht)',
    toastSuspectedMissedLap: 'Vermutete verpasste Runde — Zeitstempel vorbelegt, bitte bestätigen',
    toastLastPassLongAgo: 'Der letzte Durchgang ist länger her – bitte die Uhrzeit der Runde eintragen',
    toastStatusSet: 'Status gesetzt: {status}',
    toastStatusCleared: 'Status entfernt',
    lapAddFailed: 'Runde konnte nicht hinzugefügt werden',
    lapRemoveFailed: 'Runde konnte nicht entfernt werden',
    statusSetFailed: 'Status konnte nicht gesetzt werden',
    riderResetFailed: 'Fahrer konnte nicht zurückgesetzt werden',

    // ---- Koppel-Modus (serial coupling + auto-assign) -------------------------------
    coupleQueueOne: '1 weiterer neuer Tag wartet',
    coupleQueueMany: '{n} weitere neue Tags warten',
    coupleStateRecouple: 'NEU KOPPELN',
    coupleStateNew: 'NEUER TAG',
    coupleEnterNumber: 'Nummer eingeben',
    coupleStateKnown: 'Bereits gekoppelt',
    toastAlreadyCoupled: 'Bereits gekoppelt: {rider}',
    coupleDuplicateBib: 'Nr. {bib} ist bereits an {holder} vergeben – nochmal Enter/Speichern zum trotzdem Koppeln',
    coupleCounter: '{n} gekoppelt',
    coupleSaveFailed: 'Koppeln fehlgeschlagen',
    confirmDiscardCoupling: 'Ungespeicherte Kopplung verwerfen?',
    coupleAutoStoppedRaceChanged: 'Auto-Zuweisung gestoppt — Rennen gewechselt',
    coupleLogRaceChanged: '— Rennen gewechselt —',
    toastCoupleRaceChanged: 'Koppel-Modus: Rennen gewechselt',
    coupleStateAuto: 'AUTO — TAG SCHWENKEN',
    coupleAutoProgress: '{done} vergeben · noch {left} Nummern',
    coupleRangesInvalid: 'Zirkel unlesbar — Format: 1-75 oder 101-175,181-190',
    coupleRangesAllTaken: 'Alle Nummern dieses Zirkels sind schon vergeben',
    toastCoupleAutoStarted: 'Auto-Zuweisung: {n} freie Nummern',
    toastCoupleAutoComplete: 'Zirkel komplett: {n} Nummern vergeben',
    toastCoupleAutoStopped: 'Auto-Zuweisung gestoppt',
    toastCoupleAutoTagKnown: 'Tag ist schon {rider} — anderen Tag nehmen',
    coupleAutoSaveFailed: 'Nr. {bib} konnte nicht gekoppelt werden',
    toastCoupleModeActive: 'Koppel-Modus ist aktiv — Panel benutzen',

    // ---- Generic API errors (RT.apiError) ----------------------------------------
    errNetwork: 'Keine Verbindung zur Anwendung.',
    errHttp400: 'Ungültige Anfrage (400).',
    errHttp401: 'Zugriff verweigert (401). API-Schlüssel prüfen.',
    errHttp403: 'Keine Berechtigung (403).',
    errHttp404: 'Nicht gefunden (404).',
    errHttp409: 'In diesem Zustand nicht möglich (409).',
    errHttp422: 'Ungültige Eingabe (422).',
    errHttp422Fields: 'Ungültige Eingabe bei: {fields}.',
    errHttp5xx: 'Interner Fehler der Anwendung ({status}). Bitte ein Support-Paket erstellen.',
    errHttpOther: 'Unerwartete Antwort der Anwendung ({status}).',

    // Known backend `detail` texts (see DETAIL_PATTERNS below).
    errDetailApiKey: 'Zugriff verweigert. API-Schlüssel prüfen.',
    errDetailRaceNotFound: 'Rennen nicht gefunden.',
    errDetailRaceNotActiveExport: 'Das Rennen ist nicht aktiv – zuerst aktivieren, dann exportieren.',
    errDetailScheduledAt: 'Datum/Uhrzeit ist ungültig.',
    errDetailRaceNotEnded: 'Das Rennen ist nicht beendet.',
    errDetailDurationFinalLaps: 'Dauer und Schlussrunden müssen zusammen angegeben werden.',
    errDetailDeleteActiveRace: 'Das aktive Rennen kann nicht gelöscht werden – zuerst ein anderes Rennen aktivieren.',
    errDetailSnapshotActiveOnly: 'Ein Snapshot ist nur für das aktive Rennen möglich.',
    errDetailReaderIp: 'Die Reader-Adresse ist keine gültige IPv4-Adresse (z. B. 192.168.178.22).',
    errDetailMinLap: 'Der Mindest-Rundenabstand muss zwischen 0 und 60 Sekunden liegen.',
    errDetailTotalLaps: 'Die Rundenzahl muss zwischen 1 und 999 liegen.',
    errDetailAntennaPower: 'Die Antennenleistung muss zwischen 100 und 300 liegen.',
    errDetailNoRider: 'Für diesen Tag ist im aktiven Rennen kein Fahrer registriert.',
    errDetailRiderResetEnded: 'Das Rennen ist beendet – Zurücksetzen ist nicht mehr möglich.',
    errDetailRaceNotStarted: 'Das Rennen läuft noch nicht – zuerst starten, dann Runden gutschreiben.',
    errDetailLapChangesEnded: 'Das Rennen ist beendet – Runden können nicht mehr geändert werden.',
    errDetailNoLapToRemove: 'Es gibt keine Runde zum Entfernen.',
    errDetailInvalidStatus: 'Ungültiger Status (erlaubt: DNF, DNS, DSQ oder leer).',
    errDetailLapNotCredited: 'Runde nicht gutgeschrieben: Der letzte Durchgang ist zu kurz her oder die Zeit liegt vor dem Rennstart.',

    // Request fields named in FastAPI validation errors (422 `loc`), see FIELD_KEYS.
    fieldTagId: 'Tag-ID',
    fieldBib: 'Startnummer',
    fieldName: 'Name',
    fieldVerein: 'Verein',
    fieldUciId: 'UCI-ID',
    fieldTotalLaps: 'Rundenzahl',
    fieldSnapshotInterval: 'Auto-Snapshot-Intervall',
    fieldDuration: 'Dauer',
    fieldFinalLaps: 'Schlussrunden',
    fieldFinishMode: 'Wertungsart',
    fieldScheduledAt: 'Datum/Uhrzeit',
    fieldTimestamp: 'Zeitstempel',
    fieldStatus: 'Status',
    fieldReaderIp: 'Reader-Adresse',
    fieldMinLap: 'Mindest-Rundenabstand',
    fieldAntennaPower: 'Antennenleistung',
  };

  // API field names (last element of a validation error's `loc`) → string keys.
  const FIELD_KEYS = {
    tag_id: 'fieldTagId',
    bib: 'fieldBib',
    name: 'fieldName',
    verein: 'fieldVerein',
    uci_id: 'fieldUciId',
    total_laps: 'fieldTotalLaps',
    snapshot_interval_s: 'fieldSnapshotInterval',
    duration_s: 'fieldDuration',
    final_laps: 'fieldFinalLaps',
    finish_mode: 'fieldFinishMode',
    scheduled_at: 'fieldScheduledAt',
    timestamp: 'fieldTimestamp',
    status: 'fieldStatus',
    reader_ip: 'fieldReaderIp',
    min_lap_interval_s: 'fieldMinLap',
    antenna_power: 'fieldAntennaPower',
  };

  // Backend `detail` texts → string keys. Tested in order, first match wins;
  // regexes because some texts embed ids (e.g. "race 3f2a… not found").
  const DETAIL_PATTERNS = [
    [/invalid or missing api key/i, 'errDetailApiKey'],
    [/race is not active/i, 'errDetailRaceNotActiveExport'],
    [/race (\S+ )?not found/i, 'errDetailRaceNotFound'],
    [/scheduled_at must be/i, 'errDetailScheduledAt'],
    [/race is not ended/i, 'errDetailRaceNotEnded'],
    [/duration_s and final_laps/i, 'errDetailDurationFinalLaps'],
    [/cannot delete the active race/i, 'errDetailDeleteActiveRace'],
    [/manual snapshot only/i, 'errDetailSnapshotActiveOnly'],
    [/reader_ip must be/i, 'errDetailReaderIp'],
    [/min_lap_interval_s must be/i, 'errDetailMinLap'],
    [/total_laps must be/i, 'errDetailTotalLaps'],
    [/antenna_power must be/i, 'errDetailAntennaPower'],
    [/no rider registered/i, 'errDetailNoRider'],
    [/rider reset is not allowed/i, 'errDetailRiderResetEnded'],
    [/race has not started/i, 'errDetailRaceNotStarted'],
    [/manual lap changes are not allowed/i, 'errDetailLapChangesEnded'],
    [/no lap events to remove/i, 'errDetailNoLapToRemove'],
    [/status must be one of/i, 'errDetailInvalidStatus'],
    [/lap not credited/i, 'errDetailLapNotCredited'],
  ];

  const hasOwn = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);

  // Fill `{name}` placeholders. An unknown key returns the key itself so a
  // missing translation is visible during development instead of blank.
  function fmt(key, vars) {
    const template = hasOwn(S, key) ? S[key] : String(key);
    if (!vars) return template;
    return template.replace(/\{(\w+)\}/g, (match, name) => (
      hasOwn(vars, name) && vars[name] != null ? String(vars[name]) : match
    ));
  }

  // Pull the human-readable parts out of a FastAPI error body:
  // {"detail": "text"}, {"detail": ["a", "b"]} or the validation shape
  // {"detail": [{"msg": "…", "loc": ["body", "bib"]}]}. Non-JSON bodies are
  // used as-is. Returns {texts, fields}: `fields` are the German names of the
  // request fields a validation error points at (unknown fields are skipped).
  function extractDetails(bodyText) {
    const out = { texts: [], fields: [] };
    if (bodyText == null || bodyText === '') return out;
    let detail = bodyText;
    try {
      const parsed = JSON.parse(bodyText);
      detail = parsed && typeof parsed === 'object' && 'detail' in parsed ? parsed.detail : parsed;
    } catch (_e) {
      // plain-text body (proxy error page, etc.)
    }
    const items = Array.isArray(detail) ? detail : [detail];
    items.forEach((item) => {
      if (typeof item === 'string') {
        if (item !== '') out.texts.push(item);
        return;
      }
      if (!item || typeof item !== 'object') return;
      if (typeof item.msg === 'string' && item.msg !== '') out.texts.push(item.msg);
      const loc = Array.isArray(item.loc) ? item.loc : [];
      const fieldKey = FIELD_KEYS[loc[loc.length - 1]];
      if (fieldKey && !out.fields.includes(S[fieldKey])) out.fields.push(S[fieldKey]);
    });
    return out;
  }

  function statusMessage(status) {
    const code = Number(status) || 0;
    if (code === 0) return S.errNetwork;
    if (hasOwn(S, `errHttp${code}`)) return S[`errHttp${code}`];
    if (code >= 500) return fmt('errHttp5xx', { status: code });
    return fmt('errHttpOther', { status: code });
  }

  // German message for a failed API call. `status` 0 means "no response"
  // (network error). Known English `detail` texts are translated, validation
  // errors name the affected fields in German; anything else falls back to a
  // status-code message so no raw English reaches the operator. The raw body
  // is logged for support.
  function apiError(status, bodyText, fallbackKey) {
    const { texts, fields } = extractDetails(bodyText);
    const translated = [];
    texts.forEach((text) => {
      const hit = DETAIL_PATTERNS.find(([pattern]) => pattern.test(text));
      if (hit && !translated.includes(S[hit[1]])) translated.push(S[hit[1]]);
    });
    if (texts.length && translated.length < texts.length && typeof console !== 'undefined') {
      console.warn(`API error ${status}:`, bodyText);
    }
    const prefix = fallbackKey && hasOwn(S, fallbackKey) ? S[fallbackKey] : '';
    let message;
    if (translated.length) message = translated.join(' ');
    else if (fields.length) message = fmt('errHttp422Fields', { fields: fields.join(', ') });
    else message = statusMessage(status);
    return prefix ? `${prefix}: ${message}` : message;
  }

  const RT = window.RT || {};
  RT.S = S;
  RT.fmt = fmt;
  RT.apiError = apiError;
  window.RT = RT;
})();

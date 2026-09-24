# Plan: Excel exports (results for the operator, all readings for cleaning)

_Date: 2026-09-16 — field request after the first Windows test_

Two gaps found in the field:

1. The operator gets results as CSV. He cannot read that directly; he needs a file that opens in Excel with correct columns, headings and times.
2. There is no way to get **all** tag readings out of the app. Jan needs them on his own machine to clean the data (missed and duplicate reads, wrong couplings).

Both are exports only. No timing logic changes.

## 1. Decisions

- **Format:** real `.xlsx` via **openpyxl** (added to `apps/backend/requirements.txt`, pure Python, already used in the event scripts under `docs/events/`). The PyInstaller specs pick it up automatically through the source-import collector.
- **CSV endpoints stay** (`/classification.csv`, `/races/{id}/classification.csv`, `/tags.csv`) for scripts and for the Docker deployment.
- **Times** are written as real Excel datetimes (format `TT.MM.JJJJ HH:MM:SS,000`), durations as `[h]:mm:ss,0`. Every sheet gets a bold header row, freeze panes, autofilter and sensible column widths.
- **Language:** all sheet names, headers and cell texts are German.
- **The readings export is the authoritative raw data.** It must never hide rows: every stored `tag_events` row appears, including those that did not count as a lap.

## 2. Endpoints (backend)

| Method | Path | Content |
| --- | --- | --- |
| `GET` | `/races/{race_id}/results.xlsx` | Workbook for one race |
| `GET` | `/results.xlsx` | Same for the active race |
| `GET` | `/races/{race_id}/readings.xlsx` | All readings of one race |
| `GET` | `/readings.xlsx` | All readings of **all** races (Jan's cleaning file) |

All four honour the existing API-key dependency, return `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` and a `Content-Disposition` filename built from the race name and date, e.g. `Racetag-Ergebnis-Volksradrennen-2026-09-16.xlsx`, `Racetag-Lesungen-alle-2026-09-16.xlsx`. Filenames are ASCII-sanitised (umlauts transliterated, spaces to `-`).

### 2.1 `results.xlsx`

- Sheet **"Ergebnis"**: Platz, Startnummer, Name, Verein, UCI-ID, Runden, Gesamtzeit, Netto-Zeit, Letzte Durchfahrt, Rückstand, Status (DNF/DNS/DSQ), Tag-ID. Same order the UI shows (official order), finished riders first, status riders at the end.
- Sheet **"Rennen"**: name, scheduled/started/ended, total laps, finish mode, duration, cooldown, lap count, rider count, Racetag version, export timestamp.
- Sheet **"Fahrer"**: every registered rider of the race (Startnummer, Name, Verein, UCI-ID, Tag-ID, Status), also those without a single reading.

### 2.2 `readings.xlsx`

- Sheet **"Lesungen"**, one row per stored `tag_events` row, ordered by race then timestamp: Nr, Rennen, Zeit (lokal), Zeit (UTC), Sekunden seit Rennstart, Tag-ID, Startnummer, Name, Verein, Antenne, RSSI, Ereignis, Reader-Seriennummer, Gewertet, Runde, Abstand zur vorherigen Lesung (s), Abstand zur vorherigen gewerteten Runde (s).
  - **Gewertet / Runde** are reconstructed by replaying the events per rider with the race's start time and cooldown, reusing the domain rule in `domain/race.py` rather than a second implementation. The column header carries a comment saying it is a reconstruction.
- Sheet **"Zusammenfassung"**: per rider Lesungen gesamt, davon gewertet, verworfen, erste und letzte Lesung, Antennen-Verteilung.
- Sheet **"Rennen"**: metadata of every exported race.
- Sheet **"Fahrer"**: rider list across races (race, Startnummer, Name, Verein, UCI-ID, Tag-ID, Status).
- Unknown tags (no rider) appear with empty Startnummer/Name and are counted in "Zusammenfassung" under `Unbekannt`.

## 3. Desktop bridge

`_RacetagApi.save_binary(data_base64: str, default_filename: str, file_types: tuple = ...) -> dict` writes a native save dialog result to disk and returns `{"ok": bool, "path": str | None, "error": str | null}`. `ok: false` with `error: null` means the dialog was cancelled; a failed write (file open in Excel) returns a German error string, mirroring the `save_csv` fix.

## 4. Frontend

- Header button **"Ergebnisse (Excel)"** replaces the current CSV button as the primary action; CSV stays reachable under Einstellungen → Erweitert as "Ergebnisse als CSV".
- Einstellungen → Erweitert: **"Alle Lesungen (Excel)"** with a one-line explanation that this is the file for the data check, plus "Nur dieses Rennen" / "Alle Rennen".
- Downloads go through `window.pywebview.api.save_binary` in the desktop build (fetch → arrayBuffer → base64) and through a normal blob download in the browser. Show a German toast with the saved path, or the cancel/error text.
- Tooltips for both buttons, in the operator's language ("Öffnet sich direkt in Excel").

## 5. Tests

- Backend: generate each workbook in tests and read it back with openpyxl — sheet names, header row, row counts against the fixture data, a known lap time as a real datetime, reconstruction column matching the laps the API reports, unknown-tag handling, empty race, race with DNF/DNS/DSQ, filename sanitising.
- Desktop: `save_binary` writes bytes, reports cancel and a write error.
- Frontend: `node --check`, tip coverage, and the existing headless-Chrome smoke extended by the two new buttons (browser mode).

## 6. Out of scope

- No changes to lap counting, standings or the CSV format.
- No PDF export (the event scripts under `docs/events/` already do that off-line).

---

## 7. Implementation status (2026-09-24)

Implemented and verified on macOS; **not** yet exercised on the Windows PC.

| Item | State |
| --- | --- |
| `openpyxl` in `apps/backend/requirements.txt` | done; the PyInstaller source-import collector picks it up automatically (verified: `openpyxl`, `.styles`, `.utils`, `.comments`, `.worksheet.worksheet`) |
| `racetag-backend/exports_xlsx.py` | done (workbook layout only, no DB access, no `import app`) |
| `storage.iter_event_rows()` | done (raw rows incl. id, chronological) |
| Four endpoints (§2) | done; `GET /races/{id}/results.xlsx` returns 409 for a non-active race, matching `classification.csv`, because standings live in memory for the active race only. The **readings** endpoints work for any race. |
| Desktop `save_binary` | done (native save dialog, cancel vs. write error, 200 MB guard) |
| Frontend buttons (§4) | done: header "Ergebnisse (Excel)"; Settings → Erweitert "Alle Lesungen (Excel)" with scope select and "Ergebnisse als CSV" |
| Tests | `apps/backend/tests/test_exports_xlsx.py`, 10 tests: standings match, every rider incl. those without readings, every stored reading kept, cooldown duplicate not counted, unknown tag present, counted column == API lap count, summary counts, per-race vs. all-races, empty race, 404/409, 20 000-row scale, filename sanitising |
| Suites after the change | backend 252, desktop 143 (+7 skipped), reader-service 148, all passing; `node --check` clean; desktop `--selftest` exit 0 |
| Real HTTP end-to-end | 10/10 checks against uvicorn from source: both workbooks download, open in openpyxl, correct sheets, real datetimes, `Content-Disposition` filename |

Open:

- Not run on Windows, and not opened in real Excel — only read back with openpyxl.
- Results export for a **non-active** race still needs the race to be activated first (inherited from the CSV behaviour).
- The reconstruction ignores manual lap corrections (documented in the sheet comment and in `OPERATOR_GUIDE.md`).

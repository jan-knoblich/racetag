const $ = (sel) => document.querySelector(sel);

// API key injected at runtime by Docker (placeholder replaced on container start)
const __RACETAG_API_KEY__ = "__RACETAG_FRONTEND_API_KEY__";
// Backend URL injected at runtime by Docker (placeholder replaced on container start)
const __RACETAG_BACKEND_URL__ = "__RACETAG_FRONTEND_BACKEND_URL__";

const isPlaceholder = (v) => typeof v === 'string' && v.startsWith('__RACETAG_');

// localStorage can throw (blocked site data, some embedded WebViews). Several
// reads run while this script loads, where an exception would kill the whole
// page, so every access goes through these helpers.
function rtStorageGet(key) {
  try {
    return window.localStorage.getItem(key);
  } catch (_e) {
    return null;
  }
}

function rtStorageSet(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch (_e) {
    // storage unavailable — the setting just isn't remembered
  }
}

// Migrate a stale localStorage value that was the old hardcoded default
// ('http://localhost:8600' / '127.0.0.1:8600') written by earlier builds
// before same-origin defaulting. If we're being served from a different
// origin (e.g. the desktop app on a random port), the stored value is wrong
// for this run — drop it so the new window.location.origin default wins.
(() => {
  try {
    const stored = localStorage.getItem('racetag.backend');
    const stale = ['http://localhost:8600', 'http://127.0.0.1:8600'];
    if (
      stored && stale.includes(stored.replace(/\/$/, '')) &&
      window.location.origin && !stale.includes(window.location.origin.replace(/\/$/, ''))
    ) {
      localStorage.removeItem('racetag.backend');
    }
  } catch (_e) { /* localStorage may be unavailable — ignore */ }
})();

// Default backend URL resolution, highest priority first:
//   1. Explicit user override saved in localStorage (set via the UI input).
//   2. Docker build-time substitution of the __RACETAG_FRONTEND_BACKEND_URL__
//      placeholder — used in the nginx container where backend + frontend are
//      on different origins.
//   3. Same origin as the page itself (window.location.origin) — this is the
//      right answer for the packaged desktop app (FastAPI serves the frontend
//      via StaticFiles, so every fetch stays on the same host:port) and for
//      anyone who opens http://localhost:8600/ directly with a running backend.
//   4. http://localhost:8600 as a last-resort fallback (e.g. if window.location
//      is somehow unavailable, which shouldn't happen in a browser context).
const state = {
  backend:
    rtStorageGet('racetag.backend')
    || (!isPlaceholder(__RACETAG_BACKEND_URL__) && __RACETAG_BACKEND_URL__)
    || (typeof window !== 'undefined' && window.location && window.location.origin)
    || 'http://localhost:8600',
  showTagColumn: true,
  lastStandings: [],
  // TT view: 'official' (backend order) or 'net' (sorted by net time).
  // Persisted so a mid-session app restart keeps the TT result view.
  sortMode: rtStorageGet('racetag.sortMode') === 'net' ? 'net' : 'official',
  es: null,
  // W-012: registration flow state
  awaitingRead: false,
  lastUnknownTag: null, // { tag_id, timestamp } | null
  // W-036: current total laps setting
  totalLaps: 5,
  // Explicit-start model: race must be started before laps count
  raceStarted: false,
  raceStartedAt: null,
  // Multi-race: id/name of active race + ended state
  activeRaceId: null,
  activeRaceName: null,
  activeRaceScheduledAt: null,
  raceEnded: false,
  raceEndedAt: null,
};

// German operator message for a failed API response (body read best-effort).
// `fallbackKey` names the RT.S prefix ("… fehlgeschlagen"). Network failures
// use RT.apiError(0, null, key) directly; the English exception text is only
// logged, never shown.
async function rtResponseError(res, fallbackKey) {
  const body = await res.text().catch(() => '');
  return RT.apiError(res.status, body, fallbackKey);
}

// Race name for display: the backend's bootstrap race is named in English.
function rtRaceDisplayName(name) {
  return name === BOOTSTRAP_RACE_NAME ? RT.S.raceDefaultName : name;
}

// "1 Runde" / "N Runden"
function rtLapsText(n) {
  return Number(n) === 1 ? RT.S.lapsCountOne : RT.fmt('lapsCountMany', { n });
}

// "Nr. 42 – Name" (name falls back to a dash)
function rtRiderLabel(bib, name) {
  return RT.fmt('riderLabel', { bib, name: name || RT.S.riderNoName });
}

// ---------------------------------------------------------------------------
// W-035 — Robust CSV tokenizer
// Handles: UTF-8 BOM, quoted fields with embedded commas, CRLF, "" → ",
// trailing blank lines. Zero external dependencies.
// ---------------------------------------------------------------------------
// Detect the field delimiter from the header line (AUDIT-2026-07 M8).
// German Excel saves "CSV" with semicolons; tab-separated exports also occur.
// We count occurrences OUTSIDE quotes on the first line and pick the winner,
// defaulting to comma. Without this, a semicolon file parsed as one field per
// line and every row was silently skipped ("Imported 0/57").
function detectDelimiter(text) {
  const firstLine = text.split(/\r?\n/, 1)[0] || '';
  const counts = { ',': 0, ';': 0, '\t': 0 };
  let inQuotes = false;
  for (const ch of firstLine) {
    if (ch === '"') inQuotes = !inQuotes;
    else if (!inQuotes && ch in counts) counts[ch]++;
  }
  let best = ',';
  for (const d of [';', '\t']) {
    if (counts[d] > counts[best]) best = d;
  }
  return best;
}

function parseCSVRobust(text, delimiter) {
  // Strip UTF-8 BOM if present
  if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1);

  const delim = delimiter || detectDelimiter(text);

  const rows = [];
  let i = 0;
  const len = text.length;

  while (i < len) {
    const row = [];
    // Parse one row
    while (i < len) {
      if (text[i] === '"') {
        // Quoted field
        i++; // skip opening quote
        let field = '';
        while (i < len) {
          if (text[i] === '"') {
            if (i + 1 < len && text[i + 1] === '"') {
              // Escaped quote inside quoted field
              field += '"';
              i += 2;
            } else {
              // Closing quote
              i++;
              break;
            }
          } else {
            field += text[i++];
          }
        }
        row.push(field);
      } else {
        // Unquoted field — read until delimiter or end of line
        let field = '';
        while (i < len && text[i] !== delim && text[i] !== '\n' && text[i] !== '\r') {
          field += text[i++];
        }
        row.push(field.trim());
      }

      // After a field: consume the delimiter (continue row) or newline/end (end row)
      if (i < len && text[i] === delim) {
        i++; // next field in same row
        continue;
      }
      break; // newline or EOF
    }

    // Consume CRLF or LF
    if (i < len && text[i] === '\r') i++;
    if (i < len && text[i] === '\n') i++;

    rows.push(row);
  }

  return rows;
}

// ---------------------------------------------------------------------------
// W-030 — UTC-at-source, browser-local display
// ---------------------------------------------------------------------------
function formatTimestampForDisplay(isoUtc) {
  if (!isoUtc) return '';
  const d = new Date(isoUtc);
  if (isNaN(d.getTime())) return isoUtc; // fall back to raw string if unparseable
  return d.toLocaleTimeString(undefined, {
    hour12: false,
    timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  });
}

// Decode a CSV file's raw bytes. German Excel saves "CSV" as windows-1252,
// not UTF-8 — FileReader.readAsText() would turn every umlaut into U+FFFD
// before the parser ever sees it. Strict UTF-8 first (BOM'd or plain), then
// cp1252 fallback. UTF-16 BOMs are honoured too so Excel's "Unicode Text"
// export (UTF-16LE, tab-separated) imports as well.
function decodeCsvBytes(buffer) {
  const bytes = new Uint8Array(buffer);
  if (bytes.length >= 2) {
    if (bytes[0] === 0xff && bytes[1] === 0xfe) return new TextDecoder('utf-16le').decode(buffer);
    if (bytes[0] === 0xfe && bytes[1] === 0xff) return new TextDecoder('utf-16be').decode(buffer);
  }
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(buffer);
  } catch {
    return new TextDecoder('windows-1252').decode(buffer);
  }
}

// ---------------------------------------------------------------------------
// W-013 — Bulk CSV import: POST each row to /riders
// Replaces the old browser-only tagData map approach.
// ---------------------------------------------------------------------------
async function importCSVToBackend(csvText) {
  const delimiter = detectDelimiter(csvText);
  const rows = parseCSVRobust(csvText, delimiter);
  if (rows.length < 2) {
    setStatus(RT.S.csvEmpty);
    return;
  }
  const delimName = delimiter === ';' ? RT.S.csvDelimiterSemicolon
    : delimiter === '\t' ? RT.S.csvDelimiterTab : RT.S.csvDelimiterComma;

  // First row is header — skip it. Optional Stammdaten columns are matched
  // BY HEADER NAME so legacy templates (col 4 = "kategorie (Import
  // ignoriert)" etc.) keep their ignore-semantics untouched.
  const headerCells = rows[0].map((h) => h.trim().toLowerCase());
  const vereinIdx = headerCells.findIndex((h) => h === 'verein');
  const uciIdx = headerCells.findIndex((h) => h === 'uci_id' || h === 'uci-id');
  const dataRows = rows.slice(1).filter(r => r.some(cell => cell !== ''));
  const total = dataRows.length;
  if (total === 0) {
    setStatus(RT.S.csvNoDataRows);
    return;
  }

  const errors = []; // { tag_id, reason }
  let imported = 0;
  let skippedEmpty = 0;

  // Show errors container (hidden until there are errors)
  const errContainer = $('#importErrors');
  const errList = $('#importErrorList');
  if (errContainer) errContainer.hidden = true;
  if (errList) errList.innerHTML = '';

  for (let i = 0; i < total; i++) {
    const row = dataRows[i];
    // M8: never skip a data row silently — record why it was rejected so the
    // operator sees it (a semicolon file parsed with the wrong delimiter used
    // to produce "Imported 0/57" with no explanation).
    if (row.length < 3) {
      errors.push({
        tag_id: (row[0] || RT.fmt('csvRowLabel', { n: i + 2 })).slice(0, 40),
        reason: RT.fmt('csvTooFewColumns', { n: row.length, delimiter: delimName }),
      });
      continue;
    }
    const [tag_id, bib, name] = row;
    if (!tag_id) {
      errors.push({ tag_id: RT.fmt('csvRowLabel', { n: i + 2 }), reason: RT.S.csvEmptyTagId });
      continue;
    }
    // A tag-pool template row nobody filled in (no bib, no name) carries zero
    // information — skip it so an unfilled template imports cleanly instead
    // of coupling dozens of blank riders.
    if (!bib && !name) {
      skippedEmpty++;
      continue;
    }

    setStatus(RT.fmt('csvImporting', { i: i + 1, total, errors: errors.length }));

    try {
      const body = { tag_id, bib, name };
      if (vereinIdx >= 0 && row[vereinIdx]) body.verein = row[vereinIdx];
      if (uciIdx >= 0 && row[uciIdx]) body.uci_id = row[uciIdx];
      const res = await fetch(`${state.backend}/riders`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getApiHeaders() },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        imported++;
      } else {
        errors.push({ tag_id, reason: await rtResponseError(res) });
      }
    } catch (err) {
      console.warn('CSV import request failed:', err);
      errors.push({ tag_id, reason: RT.apiError(0, null) });
    }
  }

  // Summary toast
  const skippedNote = skippedEmpty ? RT.fmt('csvSkippedNote', { n: skippedEmpty }) : '';
  const errorsNote = errors.length ? RT.fmt('csvErrorsNote', { n: errors.length }) : '';
  showToast(RT.fmt('csvImportedToast', { imported, total, skipped: skippedNote, errors: errorsNote }));
  setStatus(RT.fmt('csvImportComplete', { imported, total, skipped: skippedNote }));

  // Show per-row errors in collapsible list
  if (errors.length > 0 && errContainer && errList) {
    errors.forEach(({ tag_id, reason }) => {
      const li = document.createElement('li');
      li.textContent = `${tag_id}: ${reason}`;
      errList.appendChild(li);
    });
    errContainer.hidden = false;
  }
}

// ---------------------------------------------------------------------------
// W-012 — Register-rider modal helpers
// ---------------------------------------------------------------------------
function openRegisterModal(tag_id) {
  state.awaitingRead = false; // Clear flag immediately so no stacking
  const modal = $('#registerModal');
  const tagInput = $('#modalTagId');
  const bibInput = $('#modalBib');
  const nameInput = $('#modalName');
  const errBanner = $('#modalError');

  if (!modal) return;

  tagInput.value = tag_id;
  bibInput.value = '';
  nameInput.value = '';
  if (errBanner) errBanner.hidden = true;

  modal.hidden = false;
  bibInput.focus();
}

function closeRegisterModal() {
  const modal = $('#registerModal');
  if (modal) modal.hidden = true;
  state.awaitingRead = false;
}

async function submitRegisterModal() {
  const tag_id = $('#modalTagId').value.trim();
  const bib = $('#modalBib').value.trim();
  const name = $('#modalName').value.trim();
  const errBanner = $('#modalError');

  if (!tag_id || !bib) return;

  try {
    const res = await fetch(`${state.backend}/riders`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getApiHeaders() },
      body: JSON.stringify({ tag_id, bib, name }),
    });

    if (res.ok) {
      // Clear the cached unknown tag: without this, the NEXT "Couple tag"
      // click re-opens the modal with the tag we JUST registered (stale
      // cache), and typing the next rider's data would silently overwrite
      // the previous rider via the upsert. The next click now waits for a
      // fresh wave instead.
      state.lastUnknownTag = null;
      closeRegisterModal();
      showToast(RT.fmt('toastRiderRegistered', { bib, name: name || RT.S.riderNoName }));
    } else {
      if (errBanner) {
        errBanner.textContent = await rtResponseError(res, 'saveFailed');
        errBanner.hidden = false;
      }
      // 401: keep the modal open and offer the Retry button
      if (res.status === 401) {
        const retryBtn = $('#modalRetryBtn');
        if (retryBtn) retryBtn.hidden = false;
      }
    }
  } catch (err) {
    console.warn('Register rider failed:', err);
    if (errBanner) {
      errBanner.textContent = RT.apiError(0, null, 'saveFailed');
      errBanner.hidden = false;
    }
  }
}

// ---------------------------------------------------------------------------
// W-075 — Serial coupling mode (Koppel-Modus)
//
// Rapidly couple many tags in a row: wave tag → big feedback card → type the
// bib → Enter → next tag. Driven by the backend's tag_seen SSE frames (fired
// for registered AND unknown tags, throttled ~2 s per tag server-side).
//
// Core invariant: once a NEW tag is ARMED (waiting for its bib), nothing but
// Save / Skip / mode off / race switch may change the tag under the
// operator's fingers — further new tags queue instead of replacing it, so
// typing "12" for tag A can never couple tag B.
// ---------------------------------------------------------------------------

const couple = {
  active: false,
  phase: 'idle', // 'idle' | 'info' | 'armed'
  armedTag: null, // { tag_id, recouple } while awaiting bib entry
  infoTag: null, // { tag_id, bib, name } shown on the verification card
  infoTimer: null,
  queue: [], // tag_ids of NEW tags seen while armed (deduped)
  ridersByTag: new Map(),
  ridersByBib: new Map(),
  sessionCount: 0,
  pendingConfirmBib: null, // duplicate-bib two-step confirm latch
  saving: false,
  muted: rtStorageGet('racetag.coupleBeep') === 'off',
  audioCtx: null,
  // W-077 auto-assign loop state
  auto: { running: false, queue: [], current: null, done: 0, total: 0 },
};

const COUPLE_QUEUE_CAP = 3;
const COUPLE_INFO_CLEAR_MS = 4000;

function coupleShortTag(tagId) {
  return tagId.length > 10 ? `…${tagId.slice(-8)}` : tagId;
}

function coupleBeep(kind) {
  if (couple.muted || !couple.audioCtx) return;
  const ctx = couple.audioCtx;
  if (ctx.state === 'suspended') ctx.resume();
  const tone = (freq, start, dur) => {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.value = freq;
    const t0 = ctx.currentTime + start;
    gain.gain.setValueAtTime(0.0001, t0);
    gain.gain.exponentialRampToValueAtTime(0.3, t0 + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    osc.connect(gain).connect(ctx.destination);
    osc.start(t0);
    osc.stop(t0 + dur + 0.02);
  };
  if (kind === 'new') {
    tone(880, 0, 0.12); // rising double tone = action needed (type the bib)
    tone(1320, 0.16, 0.18);
  } else {
    tone(660, 0, 0.25); // single mid tone = known tag / save confirmed
  }
}

async function refreshCoupleRiders() {
  try {
    const res = await fetch(`${state.backend}/riders`, { headers: getApiHeaders() });
    if (!res.ok) return;
    const data = await res.json();
    couple.ridersByTag = new Map();
    couple.ridersByBib = new Map();
    for (const r of data.items || []) {
      couple.ridersByTag.set(r.tag_id, r);
      couple.ridersByBib.set(String(r.bib).trim(), r);
    }
  } catch {
    // cache refresh is best-effort; the backend stays authoritative
  }
}

function coupleSetCard(mod, stateText, mainText, tagId) {
  const card = $('#coupleCard');
  card.className = `couple-card couple-card--${mod}`;
  $('#coupleCardState').textContent = stateText;
  $('#coupleCardMain').textContent = mainText;
  const tagEl = $('#coupleCardTag');
  tagEl.textContent = tagId ? coupleShortTag(tagId) : '';
  tagEl.title = tagId || '';
}

function coupleRenderQueueNote() {
  const note = $('#coupleQueueNote');
  if (couple.queue.length === 0) {
    note.hidden = true;
    return;
  }
  note.textContent = couple.queue.length === 1
    ? RT.S.coupleQueueOne
    : RT.fmt('coupleQueueMany', { n: couple.queue.length });
  note.hidden = false;
}

function coupleSetInputsEnabled(enabled) {
  $('#coupleBib').disabled = !enabled;
  $('#coupleName').disabled = !enabled;
  $('#coupleSaveBtn').disabled = !enabled || !$('#coupleBib').value.trim();
}

function coupleClearWarn() {
  const warn = $('#coupleWarn');
  warn.hidden = true;
  warn.textContent = '';
  couple.pendingConfirmBib = null;
}

function coupleToIdle() {
  couple.phase = 'idle';
  couple.armedTag = null;
  couple.infoTag = null;
  if (couple.infoTimer) {
    clearTimeout(couple.infoTimer);
    couple.infoTimer = null;
  }
  $('#coupleRecoupleBtn').hidden = true;
  $('#coupleBib').value = '';
  $('#coupleName').value = '';
  coupleClearWarn();
  coupleSetInputsEnabled(false);
  coupleSetCard('waiting', RT.S.holdTagNearAntenna, '', null);
  coupleRenderQueueNote();
}

function coupleArm(tagId, opts = {}) {
  couple.phase = 'armed';
  couple.armedTag = { tag_id: tagId, recouple: !!opts.recouple };
  couple.infoTag = null;
  if (couple.infoTimer) {
    clearTimeout(couple.infoTimer);
    couple.infoTimer = null;
  }
  $('#coupleRecoupleBtn').hidden = true;
  coupleClearWarn();
  if (opts.recouple) {
    coupleSetCard('recouple', RT.S.coupleStateRecouple,
      opts.prefillBib ? RT.fmt('bibLabel', { bib: opts.prefillBib }) : '', tagId);
    $('#coupleBib').value = opts.prefillBib || '';
    $('#coupleName').value = opts.prefillName || '';
  } else {
    coupleSetCard('new', RT.S.coupleStateNew, RT.S.coupleEnterNumber, tagId);
    $('#coupleBib').value = '';
    $('#coupleName').value = '';
    coupleBeep('new');
  }
  coupleSetInputsEnabled(true);
  const bibInput = $('#coupleBib');
  bibInput.focus();
  if (opts.recouple) bibInput.select();
}

function coupleShowInfo(data) {
  couple.phase = 'info';
  couple.infoTag = { tag_id: data.tag_id, bib: data.bib, name: data.name };
  coupleSetCard('known', RT.S.coupleStateKnown, coupleRiderText(data), data.tag_id);
  $('#coupleRecoupleBtn').hidden = false;
  if (couple.infoTimer) clearTimeout(couple.infoTimer);
  couple.infoTimer = setTimeout(() => {
    if (couple.phase === 'info') coupleToIdle();
  }, COUPLE_INFO_CLEAR_MS);
}

// "Nr. 42 – Name", or just "Nr. 42" when the rider has no name.
function coupleRiderText(rider) {
  return rider.name ? rtRiderLabel(rider.bib, rider.name) : RT.fmt('bibLabel', { bib: rider.bib });
}

function onCoupleTagSeen(data) {
  if (!couple.active) return;
  if (couple.auto.running) {
    onCoupleAutoTagSeen(data);
    return;
  }
  const tagId = data.tag_id;

  if (couple.phase === 'armed') {
    if (tagId === couple.armedTag.tag_id) return; // re-read of the armed tag
    if (data.registered) {
      // Verification info in passing — never disturbs the armed tag.
      showToast(RT.fmt('toastAlreadyCoupled', { rider: coupleRiderText(data) }));
      coupleBeep('known');
      return;
    }
    if (!couple.queue.includes(tagId) && couple.queue.length < COUPLE_QUEUE_CAP) {
      couple.queue.push(tagId);
      coupleRenderQueueNote();
    }
    return; // no beep — a beep strictly means "the tag on the card"
  }

  if (data.registered) {
    const sameTag = couple.phase === 'info'
      && couple.infoTag && couple.infoTag.tag_id === tagId;
    coupleShowInfo(data);
    if (!sameTag) coupleBeep('known'); // held-in-field re-read: timer restart only
  } else {
    coupleArm(tagId);
  }
}

function couplePopQueue() {
  while (couple.queue.length) {
    const next = couple.queue.shift();
    if (couple.ridersByTag.has(next)) continue; // coupled meanwhile — stale
    coupleRenderQueueNote();
    coupleArm(next);
    return;
  }
  coupleToIdle();
}

function coupleLogEntry(bib, name, tagId) {
  const li = document.createElement('li');
  const t = new Date();
  const hh = String(t.getHours()).padStart(2, '0');
  const mm = String(t.getMinutes()).padStart(2, '0');
  const tagSpan = `<span class="tag-id-copyable" data-tag-id="${htmlEscape(tagId)}"`
    + ` title="${htmlEscape(tagId)}">${htmlEscape(coupleShortTag(tagId))}</span>`;
  li.innerHTML = `${hh}:${mm} · ${htmlEscape(rtRiderLabel(bib, name))} · ${tagSpan}`;
  const log = $('#coupleLog');
  log.insertBefore(li, log.firstChild);
}

async function coupleSave() {
  if (!couple.active || couple.phase !== 'armed' || couple.saving) return;
  const bib = $('#coupleBib').value.trim();
  const name = $('#coupleName').value.trim();
  if (!bib) return;
  const tagId = couple.armedTag.tag_id;

  // Duplicate-bib soft guard (client cache only; backend stays permissive).
  const holder = couple.ridersByBib.get(bib);
  if (holder && holder.tag_id !== tagId && couple.pendingConfirmBib !== bib) {
    const warn = $('#coupleWarn');
    warn.textContent = RT.fmt('coupleDuplicateBib', {
      bib, holder: holder.name || coupleShortTag(holder.tag_id),
    });
    warn.hidden = false;
    couple.pendingConfirmBib = bib;
    return;
  }

  couple.saving = true;
  $('#coupleSaveBtn').disabled = true;
  try {
    const res = await fetch(`${state.backend}/riders`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getApiHeaders() },
      body: JSON.stringify({ tag_id: tagId, bib, name }),
    });
    if (res.ok) {
      coupleLogEntry(bib, name, tagId);
      couple.sessionCount += 1;
      $('#coupleCounter').textContent = RT.fmt('coupleCounter', { n: couple.sessionCount });
      // Optimistic cache insert so the dup-bib guard and the queue staleness
      // check see the new rider immediately; full refresh in the background.
      const rider = { tag_id: tagId, bib, name };
      couple.ridersByTag.set(tagId, rider);
      couple.ridersByBib.set(bib, rider);
      refreshCoupleRiders();
      coupleBeep('known');
      couplePopQueue();
    } else {
      const warn = $('#coupleWarn');
      warn.textContent = await rtResponseError(res, 'coupleSaveFailed');
      warn.hidden = false;
    }
  } catch (err) {
    console.warn('Coupling save failed:', err);
    const warn = $('#coupleWarn');
    warn.textContent = RT.apiError(0, null, 'coupleSaveFailed');
    warn.hidden = false;
  } finally {
    couple.saving = false;
    coupleSetInputsEnabled(couple.phase === 'armed');
  }
}

function coupleSkip() {
  if (couple.auto.running) {
    // Auto mode: skip the displayed number (damaged plate, no taker, …)
    coupleAutoAdvance();
    return;
  }
  if (couple.phase !== 'armed') return;
  couplePopQueue();
}

function coupleModeOn() {
  couple.active = true;
  couple.queue = [];
  if (!couple.audioCtx) {
    try {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (AC) couple.audioCtx = new AC(); // inside the click = user gesture
    } catch {
      couple.audioCtx = null; // audio is additive; the card is the feedback
    }
  }
  if (couple.audioCtx && couple.audioCtx.state === 'suspended') couple.audioCtx.resume();
  $('#coupleBeepToggle').checked = !couple.muted;
  const rangesInput = $('#coupleAutoRanges');
  if (rangesInput && !rangesInput.value) {
    rangesInput.value = rtStorageGet('racetag.autoRanges') || '';
  }
  $('#couplePanel').hidden = false;
  $('#coupleModeBtn').classList.add('couple-mode-active');
  // Kill a pending one-shot arm so the register modal can't pop over the panel.
  state.awaitingRead = false;
  coupleToIdle();
  refreshCoupleRiders();
}

function coupleModeOff() {
  if (couple.phase === 'armed' && $('#coupleBib').value.trim()) {
    if (!window.confirm(RT.S.confirmDiscardCoupling)) return;
  }
  if (couple.auto.running) coupleAutoStop();
  couple.active = false;
  couple.queue = [];
  coupleToIdle();
  $('#couplePanel').hidden = true;
  $('#coupleModeBtn').classList.remove('couple-mode-active');
  // sessionCount, the log DOM, and the AudioContext survive for re-opening.
}

function onCoupleRaceChanged() {
  if (!couple.active) return;
  if (couple.auto.running) coupleAutoStop(RT.S.coupleAutoStoppedRaceChanged);
  couple.queue = [];
  const divider = document.createElement('li');
  divider.className = 'couple-log-divider';
  divider.textContent = RT.S.coupleLogRaceChanged;
  const log = $('#coupleLog');
  log.insertBefore(divider, log.firstChild);
  coupleToIdle();
  refreshCoupleRiders();
  showToast(RT.S.toastCoupleRaceChanged);
}

// ---- W-077: auto-assign loop ----------------------------------------------
// The app shows the next free number from the race's Nummernzirkel; waving an
// UNKNOWN tag couples it to that number automatically and advances. Registered
// tags never advance the loop — and since riders are per-race, a tag recycled
// from an earlier race counts as unknown in THIS race, so recycling needs no
// special handling at all.

function parseNumberRanges(text) {
  const out = [];
  const seen = new Set();
  for (const part of String(text).split(',')) {
    const p = part.trim();
    if (!p) continue;
    const m = p.match(/^(\d+)\s*-\s*(\d+)$/);
    if (m) {
      const a = parseInt(m[1], 10);
      const b = parseInt(m[2], 10);
      if (b < a || b - a > 2000) return null;
      for (let n = a; n <= b; n += 1) {
        if (!seen.has(n)) { seen.add(n); out.push(n); }
      }
    } else if (/^\d+$/.test(p)) {
      const n = parseInt(p, 10);
      if (!seen.has(n)) { seen.add(n); out.push(n); }
    } else {
      return null;
    }
  }
  return out;
}

function coupleAutoRender() {
  const a = couple.auto;
  coupleSetCard('new', RT.S.coupleStateAuto, RT.fmt('bibLabel', { bib: a.current }), null);
  $('#coupleCardTag').textContent = RT.fmt('coupleAutoProgress', { done: a.done, left: a.queue.length + 1 });
}

function coupleAutoStart() {
  const raw = $('#coupleAutoRanges').value;
  const nums = parseNumberRanges(raw);
  if (!nums || !nums.length) {
    showToast(RT.S.coupleRangesInvalid);
    return;
  }
  rtStorageSet('racetag.autoRanges', raw);
  const free = nums.filter((n) => !couple.ridersByBib.has(String(n)));
  if (!free.length) {
    showToast(RT.S.coupleRangesAllTaken);
    return;
  }
  couple.auto.running = true;
  couple.auto.queue = free;
  couple.auto.total = free.length;
  couple.auto.done = 0;
  couple.auto.current = couple.auto.queue.shift();
  couple.phase = 'auto';
  couple.armedTag = null;
  couple.infoTag = null;
  if (couple.infoTimer) {
    clearTimeout(couple.infoTimer);
    couple.infoTimer = null;
  }
  $('#coupleRecoupleBtn').hidden = true;
  coupleClearWarn();
  coupleSetInputsEnabled(false);
  $('#coupleAutoStartBtn').hidden = true;
  $('#coupleAutoStopBtn').hidden = false;
  coupleAutoRender();
  showToast(RT.fmt('toastCoupleAutoStarted', { n: free.length }));
}

function coupleAutoStop(message) {
  couple.auto.running = false;
  couple.auto.queue = [];
  couple.auto.current = null;
  $('#coupleAutoStartBtn').hidden = false;
  $('#coupleAutoStopBtn').hidden = true;
  if (message) showToast(message);
  if (couple.active) coupleToIdle();
}

function coupleAutoAdvance() {
  if (couple.auto.queue.length) {
    couple.auto.current = couple.auto.queue.shift();
    coupleAutoRender();
  } else {
    coupleAutoStop(RT.fmt('toastCoupleAutoComplete', { n: couple.auto.done }));
  }
}

let _coupleAutoSaving = false;
async function onCoupleAutoTagSeen(data) {
  if (data.registered) {
    showToast(RT.fmt('toastCoupleAutoTagKnown', { rider: coupleRiderText(data) }));
    coupleBeep('known');
    return;
  }
  if (_coupleAutoSaving) return; // one tag at a time; drop overlapping reads
  _coupleAutoSaving = true;
  const bib = String(couple.auto.current);
  const tagId = data.tag_id;
  try {
    const res = await fetch(`${state.backend}/riders`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getApiHeaders() },
      body: JSON.stringify({ tag_id: tagId, bib, name: '' }),
    });
    if (res.ok) {
      coupleLogEntry(bib, '', tagId);
      couple.sessionCount += 1;
      couple.auto.done += 1;
      $('#coupleCounter').textContent = RT.fmt('coupleCounter', { n: couple.sessionCount });
      const rider = { tag_id: tagId, bib, name: '' };
      couple.ridersByTag.set(tagId, rider);
      couple.ridersByBib.set(bib, rider);
      refreshCoupleRiders();
      coupleBeep('known'); // audible "saved — next number is up"
      coupleAutoAdvance();
    } else {
      const reason = await rtResponseError(res);
      showToast(`${RT.fmt('coupleAutoSaveFailed', { bib })}: ${reason}`, 'error');
    }
  } catch (err) {
    console.warn('Auto-assign save failed:', err);
    showToast(`${RT.fmt('coupleAutoSaveFailed', { bib })}: ${RT.S.errNetwork}`, 'error');
  } finally {
    _coupleAutoSaving = false;
  }
}

// ---------------------------------------------------------------------------
// W-076 — Rider editor modal (Fahrer)
//
// Edit bib/name WITHOUT a reader: late entries at sign-on get their name
// typed here (search by bib), with optional propagation to every race the
// tag is registered in (day model: one tag + one number per person).
// Also opened via double-click on a standings row.
// ---------------------------------------------------------------------------

const ridersUi = {
  items: [], // riders of the active race (GET /riders)
  selectedTag: null,
};

async function refreshRidersUiList() {
  try {
    const res = await fetch(`${state.backend}/riders`, { headers: getApiHeaders() });
    if (!res.ok) return;
    ridersUi.items = (await res.json()).items || [];
  } catch {
    // list stays stale; save path reports real errors
  }
  renderRidersUiList();
}

function renderRidersUiList() {
  const list = $('#ridersList');
  if (!list) return;
  const q = ($('#ridersSearch').value || '').trim().toLowerCase();
  const filtered = ridersUi.items.filter((r) => {
    if (!q) return true;
    return String(r.bib).toLowerCase().includes(q)
      || (r.name || '').toLowerCase().includes(q)
      || r.tag_id.toLowerCase().includes(q);
  });
  filtered.sort((a, b) => {
    const na = parseInt(a.bib, 10);
    const nb = parseInt(b.bib, 10);
    if (Number.isNaN(na) || Number.isNaN(nb)) return String(a.bib).localeCompare(String(b.bib));
    return na - nb;
  });
  list.innerHTML = '';
  for (const r of filtered.slice(0, 200)) {
    const li = document.createElement('li');
    li.dataset.tagId = r.tag_id;
    if (r.tag_id === ridersUi.selectedTag) li.classList.add('riders-list--selected');
    li.title = RT.S.ridersListItemTip;
    li.innerHTML = `<strong>${htmlEscape(RT.fmt('bibLabel', { bib: r.bib }))}</strong> ${htmlEscape(r.name || RT.S.riderNoName)}`
      + ` <span class="riders-list-tag">${htmlEscape(coupleShortTag(r.tag_id))}</span>`;
    list.appendChild(li);
  }
  if (!filtered.length) {
    const li = document.createElement('li');
    li.className = 'riders-list-empty';
    li.textContent = ridersUi.items.length ? RT.S.ridersNoMatch : RT.S.ridersNoneInRace;
    list.appendChild(li);
  }
}

function selectRiderForEdit(tagId) {
  const r = ridersUi.items.find((x) => x.tag_id === tagId);
  if (!r) return;
  ridersUi.selectedTag = tagId;
  $('#riderEditTag').value = r.tag_id;
  $('#riderEditBib').value = r.bib;
  $('#riderEditName').value = r.name || '';
  $('#riderEditVerein').value = r.verein || '';
  $('#riderEditUci').value = r.uci_id || '';
  $('#riderEditSaveBtn').disabled = false;
  const err = $('#riderEditError');
  err.hidden = true;
  renderRidersUiList();
  const nameInput = $('#riderEditName');
  nameInput.focus();
  nameInput.select();
}

function openRidersModal(preselectTag) {
  const modal = $('#ridersModal');
  if (!modal) return;
  modal.hidden = false;
  ridersUi.selectedTag = preselectTag || null;
  $('#riderEditTag').value = '';
  $('#riderEditBib').value = '';
  $('#riderEditName').value = '';
  $('#riderEditVerein').value = '';
  $('#riderEditUci').value = '';
  $('#riderEditSaveBtn').disabled = true;
  $('#riderEditError').hidden = true;
  refreshRidersUiList().then(() => {
    if (preselectTag) selectRiderForEdit(preselectTag);
  });
  if (!preselectTag) $('#ridersSearch').focus();
}

function closeRidersModal() {
  const modal = $('#ridersModal');
  if (modal) modal.hidden = true;
}

async function saveRiderEdit() {
  const tag_id = $('#riderEditTag').value.trim();
  const bib = $('#riderEditBib').value.trim();
  const name = $('#riderEditName').value.trim();
  const err = $('#riderEditError');
  if (!tag_id || !bib) return;
  try {
    const res = await fetch(`${state.backend}/riders`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getApiHeaders() },
      body: JSON.stringify({
        tag_id, bib, name,
        verein: $('#riderEditVerein').value.trim(),
        uci_id: $('#riderEditUci').value.trim(),
        all_races: $('#riderEditAllRaces').checked,
      }),
    });
    if (res.ok) {
      const dto = await res.json().catch(() => ({}));
      const n = dto.races_updated;
      showToast(RT.fmt('toastRiderSaved', {
        bib,
        name: name || RT.S.riderNoName,
        races: typeof n === 'number' ? RT.fmt('toastRiderSavedRaces', { n }) : '',
      }));
      err.hidden = true;
      refreshRidersUiList();
      if (couple.active) refreshCoupleRiders();
    } else {
      err.textContent = await rtResponseError(res, 'saveFailed');
      err.hidden = false;
    }
  } catch (e) {
    console.warn('Rider save failed:', e);
    err.textContent = RT.apiError(0, null, 'saveFailed');
    err.hidden = false;
  }
}

// ---------------------------------------------------------------------------
// W-074 — Settings modal
// ---------------------------------------------------------------------------

// Snapshot of values fetched from GET /config when modal opens.
let _settingsOriginal = {};

async function openSettingsModal() {
  const modal = $('#settingsModal');
  if (!modal) return;

  // Reset error banner
  const errBanner = $('#settingsError');
  if (errBanner) errBanner.hidden = true;

  // Reader section: live status + the most recent discovery result (e.g. the
  // reader-service found several readers and needs the operator to choose).
  renderReaderStatusBox('#settingsReaderStatus');
  hideInlineStatus('#readerDiscoverStatus');
  const lastCandidates = readerUi.status && Array.isArray(readerUi.status.candidates)
    ? readerUi.status.candidates : [];
  if (lastCandidates.length) {
    setInlineStatus('#readerDiscoverStatus', 'info', RT.S.discoverLastResult);
    renderReaderCandidates('#readerCandidates', lastCandidates);
  } else {
    renderReaderCandidates('#readerCandidates', []);
  }
  hideInlineStatus('#supportStatus');
  renderSettingsMeta();

  try {
    const res = await fetch(`${state.backend}/config`, { headers: getApiHeaders() });
    if (!res.ok) {
      const body = await res.text().catch(() => '');
      const failure = new Error(`GET /config failed: ${res.status}`);
      failure.operatorMessage = RT.apiError(res.status, body);
      throw failure;
    }
    const cfg = await res.json();
    _settingsOriginal = cfg;
    rememberAppConfig(cfg);
    renderSettingsMeta();
    // The candidate list marks the configured IP; refresh it with fresh config.
    if (lastCandidates.length) renderReaderCandidates('#readerCandidates', lastCandidates);

    const ipInput = $('#settingsReaderIp');
    const minLapInput = $('#settingsMinLap');
    const totalLapsInput = $('#settingsTotalLaps');
    const snapInput = $('#settingsSnapshotInterval');
    const antPowerInput = $('#settingsAntennaPower');

    if (ipInput) ipInput.value = cfg.reader_ip ?? '';
    if (minLapInput) minLapInput.value = cfg.min_lap_interval_s ?? '';
    if (totalLapsInput) totalLapsInput.value = cfg.total_laps ?? '';
    if (antPowerInput) antPowerInput.value = cfg.antenna_power ?? '';

    // Auto-snapshot interval is per-race — fetch it from the active race.
    // Disable the input + warn the operator if there's no active race or the
    // detail fetch fails, instead of silently dropping the field on submit
    // (review #19).
    if (snapInput) {
      snapInput.value = '';
      snapInput.disabled = false;
      _settingsOriginal.active_race_id = null;
      _settingsOriginal.snapshot_interval_s = null;
      try {
        const r = await fetch(`${state.backend}/race`, { headers: getApiHeaders() });
        const raceData = r.ok ? await r.json() : null;
        const activeId = raceData ? raceData.id : null;
        if (!activeId) {
          snapInput.disabled = true;
          snapInput.placeholder = RT.S.settingsNoActiveRace;
        } else {
          const r2 = await fetch(`${state.backend}/races/${activeId}`, { headers: getApiHeaders() });
          if (r2.ok) {
            const raceRow = await r2.json();
            _settingsOriginal.snapshot_interval_s = raceRow.snapshot_interval_s ?? null;
            _settingsOriginal.active_race_id = activeId;
            snapInput.value = raceRow.snapshot_interval_s ?? '';
          } else {
            snapInput.disabled = true;
            snapInput.placeholder = RT.S.settingsRaceLoadFailed;
          }
        }
      } catch (_e) {
        snapInput.disabled = true;
        snapInput.placeholder = RT.S.settingsNetworkError;
      }
    }
  } catch (err) {
    _settingsOriginal = {};
    if (errBanner) {
      errBanner.textContent = `${RT.S.settingsLoadFailed}: ${err.operatorMessage || RT.S.errNetwork}`;
      errBanner.hidden = false;
    }
  }

  modal.hidden = false;
  const ipInput = $('#settingsReaderIp');
  if (ipInput) ipInput.focus();
}

function closeSettingsModal() {
  const modal = $('#settingsModal');
  if (modal) modal.hidden = true;
}

async function submitSettingsModal() {
  const errBanner = $('#settingsError');
  if (errBanner) errBanner.hidden = true;

  const ipVal = $('#settingsReaderIp')?.value.trim() || null;
  const minLapVal = $('#settingsMinLap')?.value;
  const totalLapsVal = $('#settingsTotalLaps')?.value;

  // Build patch body with only changed fields
  const patch = {};

  const originalIp = _settingsOriginal.reader_ip ?? null;
  const newIp = ipVal || null;
  if (newIp !== originalIp) patch.reader_ip = newIp;

  const originalMinLap = _settingsOriginal.min_lap_interval_s ?? null;
  const newMinLap = minLapVal !== '' && minLapVal != null ? parseFloat(minLapVal) : null;
  if (newMinLap !== originalMinLap) patch.min_lap_interval_s = newMinLap;

  const originalTotal = _settingsOriginal.total_laps ?? null;
  const newTotal = totalLapsVal !== '' && totalLapsVal != null ? parseInt(totalLapsVal, 10) : null;
  if (newTotal !== originalTotal) patch.total_laps = newTotal;

  const antPowerVal = $('#settingsAntennaPower')?.value;
  const originalAntPower = _settingsOriginal.antenna_power ?? null;
  const newAntPower = (antPowerVal !== '' && antPowerVal != null) ? parseInt(antPowerVal, 10) : null;
  if (newAntPower !== null && newAntPower !== originalAntPower) patch.antenna_power = newAntPower;

  // Snapshot interval is per-race; PATCH the active race separately. Tracked
  // here so we can short-circuit if nothing else changed. If the modal opened
  // without an active race (input is disabled), skip snapshot handling.
  const snapInput = $('#settingsSnapshotInterval');
  const snapVal = snapInput?.value;
  const snapDisabled = !!snapInput?.disabled;
  const originalSnap = _settingsOriginal.snapshot_interval_s ?? null;
  const newSnap = (snapVal !== '' && snapVal != null) ? Math.max(0, parseInt(snapVal, 10) || 0) : null;
  const activeRaceId = _settingsOriginal.active_race_id || null;
  const snapChanged = !snapDisabled && activeRaceId != null && newSnap !== originalSnap;

  if (Object.keys(patch).length === 0 && !snapChanged) {
    closeSettingsModal();
    return;
  }

  // Cancel keeps the dialog open with every edit intact.
  if (('reader_ip' in patch || 'antenna_power' in patch)
      && !confirmReaderInterruption('confirmReaderSettingsRace')) {
    return;
  }

  try {
    if (Object.keys(patch).length > 0) {
      const res = await fetch(`${state.backend}/config`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', ...getApiHeaders() },
        body: JSON.stringify(patch),
      });
      if (!res.ok) {
        const body = await res.text().catch(() => '');
        if (errBanner) {
          errBanner.textContent = RT.apiError(res.status, body, 'settingsSaveFailed');
          errBanner.hidden = false;
        }
        return;
      }
      rememberAppConfig(await res.json().catch(() => null));
    }

    if (snapChanged && activeRaceId) {
      const r2 = await fetch(`${state.backend}/races/${activeRaceId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', ...getApiHeaders() },
        body: JSON.stringify({ snapshot_interval_s: newSnap }),
      });
      if (!r2.ok) {
        const body = await r2.text().catch(() => '');
        if (errBanner) {
          errBanner.textContent = RT.apiError(r2.status, body, 'settingsSnapshotSaveFailed');
          errBanner.hidden = false;
        }
        return;
      }
    }

    closeSettingsModal();
    // The reader-service picks up a changed reader IP / antenna power from
    // its next heartbeat reply (≤ 2 s) and reconnects by itself — no app
    // restart, but the reader session is rebuilt (hence the confirm above).
    showToast(('reader_ip' in patch || 'antenna_power' in patch)
      ? RT.S.toastSettingsSavedReconnect
      : RT.S.toastSettingsSaved);
  } catch (_err) {
    if (errBanner) {
      errBanner.textContent = `${RT.S.settingsSaveFailed}: ${RT.S.errNetwork}`;
      errBanner.hidden = false;
    }
  }
}

// ---------------------------------------------------------------------------
// Toast notification (bottom-right, auto-dismiss)
//
// showToast(message) keeps its original behaviour (green, 3 s). Recovery and
// failure messages pass a severity: 'warn' (amber, 5 s) or 'error' (red, 7 s).
// ---------------------------------------------------------------------------
const TOAST_DURATION_MS = { info: 3000, warn: 5000, error: 7000 };

function showToast(message, severity, durationMs) {
  const toast = $('#toastContainer');
  if (!toast) return;
  const level = severity === 'warn' || severity === 'error' ? severity : 'info';
  toast.textContent = message;
  toast.classList.toggle('toast--warn', level === 'warn');
  toast.classList.toggle('toast--error', level === 'error');
  toast.setAttribute('role', level === 'error' ? 'alert' : 'status');
  toast.classList.add('toast--visible');
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => toast.classList.remove('toast--visible'), durationMs || TOAST_DURATION_MS[level]);
}

// ---------------------------------------------------------------------------
// Standings helpers
// ---------------------------------------------------------------------------

// Transient operator messages (CSV import progress, "hold a tag…") shown in
// the status bar next to the pills. Connection state has its own pill and no
// longer goes through here. Messages fade after STATUS_MESSAGE_MS.
const STATUS_MESSAGE_MS = 30000;
let _statusMessageTimer = null;

function setStatus(text) {
  const el = $('#statusMessage');
  if (!el) return;
  clearTimeout(_statusMessageTimer);
  el.textContent = text || '';
  el.hidden = !text;
  if (text) {
    _statusMessageTimer = setTimeout(() => { el.hidden = true; }, STATUS_MESSAGE_MS);
  }
}

function saveBackend(url) {
  state.backend = url.replace(/\/$/, '');
  rtStorageSet('racetag.backend', state.backend);
}

function applyTagColumnVisibility() {
  document.querySelectorAll('.tag-col').forEach((el) => {
    el.style.display = state.showTagColumn ? '' : 'none';
  });
}

// Create-race modal: show laps OR duration+final-laps depending on format.
function applyRaceFormatVisibility() {
  const isTime = $('#newRaceFormat')?.value === 'time';
  const lapsField = $('#newRaceLapsField');
  const durationField = $('#newRaceDurationField');
  const finalLapsField = $('#newRaceFinalLapsField');
  if (lapsField) lapsField.hidden = isTime;
  if (durationField) durationField.hidden = !isTime;
  if (finalLapsField) finalLapsField.hidden = !isTime;
}

// HTML-escape helper for values that flow through innerHTML. Used for any
// server-supplied string that ends up between tags OR inside a "-quoted
// attribute. (Review #20: tag_id was interpolated raw into data-tag-id and
// span text; an EPC starting with `" onerror=...` would have escaped the
// attribute.)
function htmlEscape(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// TT view: client-side sort by net time (first pass → finish). Purely a
// DISPLAY order — the backend's official ordering and all data stay
// untouched. Riders without a net time (still on course / only one pass)
// keep their official relative order below the ranked block; DNF/DNS/DSQ
// stay at the bottom.
function sortStandingsForDisplay(items) {
  if (state.sortMode !== 'net') return items;
  const ranked = [];
  const unranked = [];
  const statusRows = [];
  for (const p of items) {
    if (p.status) statusRows.push(p);
    else if (typeof p.net_time_ms === 'number') ranked.push(p);
    else unranked.push(p);
  }
  ranked.sort((a, b) => a.net_time_ms - b.net_time_ms);
  return [...ranked, ...unranked, ...statusRows];
}

function renderStandings(items) {
  state.lastStandings = items;
  const tbody = $('#standingsTable tbody');
  tbody.innerHTML = '';
  const netMode = state.sortMode === 'net';
  sortStandingsForDisplay(items).forEach((p, idx) => {
    // F7: show the standard cycling representation of a deficit — "+N Rd."
    // (Runden) for lapped riders — instead of a bare smaller lap count or an
    // empty gap cell. Same-lap riders keep the time gap to the leader.
    let gap;
    if (typeof p.laps_behind === 'number' && p.laps_behind > 0) {
      gap = RT.fmt('standingsGapLaps', { n: p.laps_behind });
    } else if (typeof p.gap_ms === 'number') {
      gap = formatMs(p.gap_ms);
    } else {
      gap = '';
    }
    // W-012: prefer bib/name from server standings; fall back to placeholders
    const bibRaw = p.bib;
    const bib = (bibRaw != null && bibRaw !== '') ? htmlEscape(bibRaw) : RT.S.standingsNoBib;
    const name = p.name ? htmlEscape(p.name) : RT.S.standingsUnknownName;
    const tagId = htmlEscape(p.tag_id);
    const tr = document.createElement('tr');
    const total = typeof p.total_time_ms === 'number' ? secondsWithMs(p.total_time_ms) : '';
    // Net time (TT): first pass → finish pass. The individual time for
    // staggered-start formats; m:ss.mmm.
    const net = typeof p.net_time_ms === 'number' ? formatMs(p.net_time_ms) : '';

    // F3: DNF/DNS/DSQ. Non-classified riders get a status badge, a muted row,
    // and a blank position cell (they have no finishing rank).
    const status = (p.status || '').toLowerCase();
    const isClassified = !status;
    // In net-sort mode only riders WITH a net time carry a rank (they sort
    // first, so idx+1 IS the net rank); riders still on course show blank.
    const posCell = !isClassified
      ? ''
      : (netMode && typeof p.net_time_ms !== 'number') ? '' : (idx + 1);
    if (!isClassified) tr.classList.add('status-row');
    const statusTipKey = { dnf: 'rowTipStatusDnf', dns: 'rowTipStatusDns', dsq: 'rowTipStatusDsq' }[status];
    const statusTitle = statusTipKey ? ` title="${htmlEscape(RT.S[statusTipKey])}"` : '';
    const statusBadge = status
      ? ` <span class="status-badge status-${htmlEscape(status)}"${statusTitle}>${htmlEscape(status.toUpperCase())}</span>`
      : '';

    // F5: missed-read annotation. A warning marker on the row invites the
    // operator to fix it with the (pre-filled) manual +1 — never automatic.
    const missed = p.suspected_missed_reads || 0;
    const missedMarker = missed > 0
      ? ` <span class="missed-read" title="${htmlEscape(RT.fmt('rowTipMissedReads', { n: missed }))}">⚠︎${missed > 1 ? '×' + missed : ''}</span>`
      : '';

    // Manual-lap-correction buttons. Disabled when the row has NO registered
    // rider (bib null/undefined). An empty string OR the literal '0' is still
    // a valid bib — the explicit null-check guards bib zero. (Review #22.)
    const noRider = (bibRaw == null);
    const disabledAttr = noRider ? 'disabled' : '';
    // Disabled buttons explain why instead of what they would do.
    const tipAttr = (key) => `title="${htmlEscape(noRider ? RT.S.rowTipNoRider : RT.S[key])}"`;
    const lapActions = `
      <div class="lap-actions">
        <button class="lap-plus" data-tag-id="${tagId}" data-action="add"
                ${tipAttr('rowTipLapAdd')} ${disabledAttr}>+1</button>
        <button class="lap-minus" data-tag-id="${tagId}" data-action="remove"
                ${tipAttr('rowTipLapRemove')} ${disabledAttr}>&minus;1</button>
        <button class="lap-edit" data-tag-id="${tagId}" data-action="edit"
                ${tipAttr('rowTipLapEdit')} ${disabledAttr}>&#9998;</button>
      </div>`;
    // W-030: route last_pass_time through formatTimestampForDisplay
    tr.innerHTML = `
      <td>${posCell}</td>
      <td class="tag-col"><span class="tag-id-copyable" data-tag-id="${tagId}" title="${htmlEscape(RT.S.rowTipCopyTag)}">${tagId}</span></td>
      <td>${bib}</td>
      <td>${name}${statusBadge}</td>
      <td>${p.laps}${missedMarker}</td>
      <td class="${p.finished ? 'finished' : ''}">${p.finished ? RT.S.standingsFinishedYes : RT.S.standingsFinishedNo}</td>
      <td>${formatTimestampForDisplay(p.last_pass_time)}</td>
      <td>${gap}</td>
      <td>${total}</td>
      <td>${net}</td>
      <td>${lapActions}</td>
    `;
    tbody.appendChild(tr);
  });
  applyTagColumnVisibility();
}

// ---------------------------------------------------------------------------
// Manual lap correction (per-row +1 / -1 / edit). Delegated handler in
// wireUp() so we don't bind a listener per row.
// ---------------------------------------------------------------------------

async function manualLapAdd(tag_id, timestamp = null) {
  const body = timestamp ? { timestamp } : {};
  try {
    const res = await fetch(`${state.backend}/riders/${encodeURIComponent(tag_id)}/laps`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getApiHeaders() },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      showToast(await rtResponseError(res, 'lapAddFailed'), 'error');
      return null;
    }
    return await res.json();
  } catch (err) {
    console.warn('Manual lap add failed:', err);
    showToast(RT.apiError(0, null, 'lapAddFailed'), 'error');
    return null;
  }
}

async function manualLapRemove(tag_id) {
  try {
    const res = await fetch(`${state.backend}/riders/${encodeURIComponent(tag_id)}/laps`, {
      method: 'DELETE',
      headers: getApiHeaders(),
    });
    if (!res.ok) {
      showToast(await rtResponseError(res, 'lapRemoveFailed'), 'error');
      return null;
    }
    return await res.json();
  } catch (err) {
    console.warn('Manual lap remove failed:', err);
    showToast(RT.apiError(0, null, 'lapRemoveFailed'), 'error');
    return null;
  }
}

// Lap-edit modal (with custom-timestamp option). When `prefillTs` is given
// (e.g. the suspected missed-read midpoint) the timestamp field starts filled
// so the operator only has to confirm.
function openLapEditModal(tag_id, prefillTs) {
  const modal = $('#lapEditModal');
  if (!modal) return;
  const p = (state.lastStandings || []).find((r) => r.tag_id === tag_id);
  const bib = p && p.bib ? p.bib : '—';
  const name = p && p.name ? p.name : '—';
  const st = p && p.status ? ` [${String(p.status).toUpperCase()}]` : '';
  $('#lapEditRider').value = `${bib} – ${name}${st}`;
  $('#lapEditCurrentLaps').value = p ? String(p.laps) : '0';
  $('#lapEditTimestamp').value = prefillTs || '';
  $('#lapEditError').hidden = true;
  // Highlight the currently-set status button.
  const curStatus = (p && p.status) ? String(p.status).toLowerCase() : '';
  document.querySelectorAll('#lapEditModal .status-btn').forEach((b) => {
    b.classList.toggle('status-btn--active', (b.dataset.status || '') === curStatus);
  });
  modal.dataset.tagId = tag_id;
  modal.hidden = false;
  $('#lapEditTimestamp').focus();
}

async function setRiderStatus(tag_id, status) {
  try {
    const res = await fetch(`${state.backend}/riders/${encodeURIComponent(tag_id)}/status`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', ...getApiHeaders() },
      body: JSON.stringify({ status: status || null }),
    });
    if (!res.ok) {
      showToast(await rtResponseError(res, 'statusSetFailed'), 'error');
      return null;
    }
    return await res.json();
  } catch (err) {
    console.warn('Set rider status failed:', err);
    showToast(RT.apiError(0, null, 'statusSetFailed'), 'error');
    return null;
  }
}

function closeLapEditModal() {
  const modal = $('#lapEditModal');
  if (modal) modal.hidden = true;
}

async function submitLapEditAdd() {
  const modal = $('#lapEditModal');
  const errBanner = $('#lapEditError');
  if (!modal || !modal.dataset.tagId) return;
  const ts = $('#lapEditTimestamp').value.trim();
  const result = await manualLapAdd(modal.dataset.tagId, ts || null);
  if (result) {
    showToast(RT.fmt('toastLapAdded', { laps: rtLapsText(result.laps) }));
    if (errBanner) errBanner.hidden = true;
    closeLapEditModal();
  }
}

async function submitLapEditRemove() {
  const modal = $('#lapEditModal');
  const errBanner = $('#lapEditError');
  if (!modal || !modal.dataset.tagId) return;
  if (!confirm(RT.S.confirmRemoveLap)) return;
  const result = await manualLapRemove(modal.dataset.tagId);
  if (result) {
    showToast(RT.fmt('toastLapRemoved', { laps: rtLapsText(result.laps) }));
    if (errBanner) errBanner.hidden = true;
    closeLapEditModal();
  }
}

// Rider reset: wipe ALL passes so a botched measurement (e.g. a TT start
// read captured while staging) can be redone from scratch.
async function submitLapEditReset() {
  const modal = $('#lapEditModal');
  if (!modal || !modal.dataset.tagId) return;
  const tag_id = modal.dataset.tagId;
  const label = _bibLabelFor(tag_id);
  if (!confirm(RT.fmt('confirmResetRider', { label }))) return;
  try {
    const res = await fetch(`${state.backend}/riders/${encodeURIComponent(tag_id)}/passes`, {
      method: 'DELETE',
      headers: getApiHeaders(),
    });
    if (!res.ok) {
      showToast(await rtResponseError(res, 'riderResetFailed'), 'error');
      return;
    }
    const data = await res.json();
    showToast(RT.fmt('toastRiderReset', { label, n: data.deleted_events }));
    closeLapEditModal();
  } catch (err) {
    console.warn('Rider reset failed:', err);
    showToast(RT.apiError(0, null, 'riderResetFailed'), 'error');
  }
}

function _bibLabelFor(tag_id) {
  const p = (state.lastStandings || []).find((r) => r.tag_id === tag_id);
  if (p && p.bib != null && p.bib !== '') return RT.fmt('bibLabel', { bib: p.bib });
  return tag_id.length > 12 ? `${tag_id.slice(0, 12)}…` : tag_id;
}

async function onStandingsTableClick(e) {
  const btn = e.target.closest('button[data-action]');
  if (!btn) return;
  const tag_id = btn.dataset.tagId;
  if (!tag_id) return;
  const action = btn.dataset.action;
  const label = _bibLabelFor(tag_id);
  if (action === 'add') {
    const p = (state.lastStandings || []).find((r) => r.tag_id === tag_id);
    // F5: if the detector suspects a missed read, credit the lap at the
    // midpoint of the suspected gap — open the modal pre-filled so the
    // operator confirms the (correct) time rather than stamping "now".
    if (p && p.suspected_missed_reads > 0 && p.suspected_gap_midpoint) {
      showToast(RT.S.toastSuspectedMissedLap);
      openLapEditModal(tag_id, p.suspected_gap_midpoint);
      return;
    }
    // F6: the +1 button credits a lap at server-now, which is correct for the
    // common case (the rider is crossing right now and the reader missed
    // them). But if the rider's last pass was a long time ago, crediting
    // server-now would inflate their race time — so route those through the
    // timestamp modal instead of silently stamping "now".
    const STALE_MS = 120_000; // 2 min — longer than any realistic circuit lap
    if (p && p.last_pass_time) {
      const age = Date.now() - new Date(p.last_pass_time).getTime();
      if (isFinite(age) && age > STALE_MS) {
        showToast(RT.S.toastLastPassLongAgo);
        openLapEditModal(tag_id);
        return;
      }
    }
    const result = await manualLapAdd(tag_id);
    if (result) showToast(RT.fmt('toastLapAddedFor', { label, laps: rtLapsText(result.laps) }));
  } else if (action === 'remove') {
    if (!confirm(RT.fmt('confirmRemoveLapFor', { label }))) return;
    const result = await manualLapRemove(tag_id);
    if (result) showToast(RT.fmt('toastLapRemovedFor', { label, laps: rtLapsText(result.laps) }));
  } else if (action === 'edit') {
    openLapEditModal(tag_id);
  }
}

function formatMs(ms) {
  if (ms <= 0) return '0.000';
  const s = Math.floor(ms / 1000);
  const remMs = ms % 1000;
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m}:${String(sec).padStart(2, '0')}.${String(remMs).padStart(3, '0')}`;
}

function secondsWithMs(ms) {
  if (ms == null) return '';
  return (ms / 1000).toFixed(3);
}

async function loadSnapshot() {
  const url = `${state.backend}/classification`;
  const headers = getApiHeaders();
  const res = await fetch(url, { headers });
  if (!res.ok) throw new Error(`GET /classification failed: ${res.status}`);
  const data = await res.json();
  renderStandings(data.standings || []);
}

// W-036: fetch current race config (and multi-race fields)
async function loadRaceConfig() {
  try {
    const res = await fetch(`${state.backend}/race`, { headers: getApiHeaders() });
    if (!res.ok) return;
    const data = await res.json();
    if (typeof data.total_laps === 'number') {
      state.totalLaps = data.total_laps;
      const input = $('#totalLapsInput');
      if (input) input.value = state.totalLaps;
    }
    state.raceStarted = !!data.started;
    state.raceStartedAt = data.started_at || null;
    state.raceEnded = !!data.ended;
    state.raceEndedAt = data.ended_at || null;
    state.activeRaceId = data.id || null;
    state.activeRaceName = data.name || null;
    state.activeRaceScheduledAt = data.scheduled_at || null;
    // F1/F2: finishing phase + laps-to-go (bell).
    state.finishMode = data.finish_mode || 'leader';
    state.finishing = !!data.finishing;
    state.lapsToGo = (typeof data.laps_to_go === 'number') ? data.laps_to_go : null;
    renderRaceStatus();
  } catch {
    // silently ignore — config sync is best-effort
  }
}

// Update the in-header race-status banner + Start-button enabled state.
function renderRaceStatus() {
  const banner = $('#raceStatus');
  const startBtn = $('#startRaceBtn');
  const endBtn = $('#endRaceBtn');
  const racePrefix = state.activeRaceName
    ? `${rtRaceDisplayName(state.activeRaceName)} — `
    : '';

  // F8: bell / laps-to-go suffix for the leader while the race is live.
  let ltgSuffix = '';
  if (state.finishing) {
    ltgSuffix = RT.S.raceBannerFinishing;
  } else if (typeof state.lapsToGo === 'number') {
    if (state.lapsToGo === 1) ltgSuffix = RT.S.raceBannerOneLapToGo;
    else if (state.lapsToGo > 0) ltgSuffix = RT.fmt('raceBannerLapsToGo', { n: state.lapsToGo });
  }

  const reopenBtn = $('#reopenRaceBtn');
  const pending = !(state.raceEnded && state.raceEndedAt) && !(state.raceStarted && state.raceStartedAt);
  if (banner) banner.classList.toggle('race-status--pending', pending);
  if (startBtn && !pending) startBtn.classList.remove('btn-attention');
  if (state.raceEnded && state.raceEndedAt) {
    const t = formatTimestampForDisplay(state.raceEndedAt);
    if (banner) banner.textContent = `${racePrefix}${RT.fmt('raceBannerEndedAt', { time: t })}`;
    if (startBtn) { startBtn.disabled = true; startBtn.textContent = RT.S.btnRaceEnded; }
    if (endBtn) { endBtn.disabled = true; endBtn.textContent = RT.S.btnEnded; }
    if (reopenBtn) reopenBtn.hidden = false;
  } else if (state.raceStarted && state.raceStartedAt) {
    const t = formatTimestampForDisplay(state.raceStartedAt);
    if (banner) banner.textContent = `${racePrefix}${RT.fmt('raceBannerRunningSince', { time: t })}${ltgSuffix}`;
    if (startBtn) { startBtn.disabled = true; startBtn.textContent = RT.S.btnRaceRunning; }
    if (endBtn) { endBtn.disabled = false; endBtn.textContent = RT.S.btnEndRace; }
    if (reopenBtn) reopenBtn.hidden = true;
  } else {
    if (banner) banner.textContent = `${racePrefix}${RT.S.raceBannerNotStarted}`;
    if (startBtn) { startBtn.disabled = false; startBtn.textContent = RT.S.btnStartRace; }
    if (endBtn) { endBtn.disabled = true; endBtn.textContent = RT.S.btnEndRace; }
    if (reopenBtn) reopenBtn.hidden = true;
  }
}

// Multi-race: load the race list into the selector and sync active state.
async function loadRaces() {
  try {
    const res = await fetch(`${state.backend}/races`, { headers: getApiHeaders() });
    if (!res.ok) return;
    const data = await res.json();
    const sel = $('#raceSelect');
    if (!sel) return;
    sel.innerHTML = '';
    (data.items || []).forEach((r) => {
      const opt = document.createElement('option');
      opt.value = r.id;
      const schedSuffix = r.scheduled_at
        ? ` (${formatTimestampForDisplay(r.scheduled_at)})`
        : '';
      opt.textContent = `${rtRaceDisplayName(r.name)}${schedSuffix}`;
      if (r.is_active) opt.selected = true;
      sel.appendChild(opt);
    });
    state.activeRaceId = data.active_race_id;
    const activeRace = (data.items || []).find((r) => r.is_active);
    state.activeRaceName = activeRace ? activeRace.name : null;
    state.activeRaceScheduledAt = activeRace ? activeRace.scheduled_at : null;
    renderRaceStatus();
  } catch {
    // best-effort, ignore
  }
}

// Multi-race: switch the active race on the backend, then reload everything.
async function activateRace(raceId) {
  try {
    const res = await fetch(`${state.backend}/races/${raceId}/activate`, {
      method: 'POST',
      headers: getApiHeaders(),
    });
    if (!res.ok) {
      showToast(await rtResponseError(res, 'raceActivateFailed'), 'error');
      return;
    }
    showToast(RT.S.toastRaceSwitched);
    // After activate, re-pull everything that's race-scoped
    await Promise.all([
      loadRaces(),
      loadRaceConfig(),
      loadSnapshot(),
    ]);
  } catch (e) {
    console.warn('Activate race failed:', e);
    showToast(RT.apiError(0, null, 'raceActivateFailed'), 'error');
  }
}

// W-051: fetch antenna diagnostics and render into diagnostics table.
// Polled every ANTENNA_POLL_MS for the status bar, and faster while the
// diagnostics panel is open.
async function refreshDiagnostics() {
  try {
    const res = await fetch(`${state.backend}/diagnostics/antennas?window_s=60`, {
      headers: getApiHeaders(),
    });
    if (!res.ok) return;
    const data = await res.json();
    const counts = data.counts || {};
    antennaUi.passes = counts; // D1: the Antennen pill shows these too
    renderStatusBar();
    const tbody = document.querySelector('#diagnosticsTable tbody');
    if (!tbody) return;
    tbody.innerHTML = '';
    const antennas = Object.keys(counts).sort((a, b) => Number(a) - Number(b));
    if (antennas.length === 0) {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td colspan="2" style="color:var(--muted)">${htmlEscape(RT.S.diagnosticsEmpty)}</td>`;
      tbody.appendChild(tr);
    } else {
      antennas.forEach((ant) => {
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${ant}</td><td>${counts[ant]}</td>`;
        tbody.appendChild(tr);
      });
    }
  } catch {
    // silently ignore
  }
}

// W-012: fetch recent-reads ring so we can open modal immediately if a tag
// is already waiting. Called once on first connection.
async function fetchRecentUnknownTag() {
  try {
    const res = await fetch(`${state.backend}/riders/recent-reads?limit=1`, {
      headers: getApiHeaders(),
    });
    if (!res.ok) return; // endpoint may not exist yet — fail silently
    const data = await res.json();
    const items = data.items || data; // tolerate both shapes
    if (Array.isArray(items) && items.length > 0) {
      state.lastUnknownTag = items[0]; // { tag_id, timestamp, … }
    }
  } catch {
    // Backend endpoint not yet deployed — silently ignore (W-012 stub)
  }
}

function connectSSE() {
  const url = `${state.backend}/stream`;
  if (state.es) state.es.close();
  state.es = connectSSEWithHeaders(url, getApiHeaders(), {
    // M7: on every (re)connect, re-pull the full state. The backend /stream
    // sends no initial snapshot, so any lap / race_started / race_ended
    // broadcast during an outage window would otherwise be missed and the UI
    // would show stale standings under a green "Live" badge until the next
    // lap. Re-fetching heals both the standings and the race lifecycle state.
    onOpen: () => {
      loadRaces();
      loadRaceConfig();
      loadSnapshot().catch(() => {});
      // W-075: rider cache may have drifted during the outage window.
      if (couple.active) refreshCoupleRiders();
      // D1: reader state may have changed while the stream was down.
      loadReaderStatus();
    },
    onError: () => {}, // state handled by onStateChange
    onStateChange: (sseState, detail) => setConnectionState(sseState, detail),
    onMessage: (ev) => {
      try {
        const data = JSON.parse(ev.data);

        if (data?.type === 'standings') {
          renderStandings(data.items || []);
          // F8: keep the bell / laps-to-go banner live on every lap.
          if ('laps_to_go' in data || 'finishing' in data) {
            state.finishing = !!data.finishing;
            state.lapsToGo = (typeof data.laps_to_go === 'number') ? data.laps_to_go : null;
            renderRaceStatus();
          }
        }

        // W-012: handle unknown_tag SSE event
        if (data?.type === 'unknown_tag') {
          state.lastUnknownTag = { tag_id: data.tag_id, timestamp: data.timestamp };
          // W-075: while coupling mode is on, the panel owns tag handling —
          // never pop the one-shot register modal over it.
          if (state.awaitingRead && !couple.active) {
            openRegisterModal(data.tag_id);
          }
        }

        // D1: reader-service heartbeat state (connection, antennas, discovery)
        if (data?.type === 'reader_status') {
          handleReaderStatus(data);
        }

        // W-075: serial coupling mode — live tag feed (ignored unless open)
        if (data?.type === 'tag_seen') {
          onCoupleTagSeen(data);
        }

        // W-036: race reset — clear standings table locally
        if (data?.type === 'race_reset') {
          renderStandings([]);
          showToast(RT.S.toastRaceResetRemote);
        }

        // W-036: race updated — sync totalLaps
        if (data?.type === 'race_updated') {
          if (typeof data.total_laps === 'number') {
            state.totalLaps = data.total_laps;
            const input = $('#totalLapsInput');
            if (input) input.value = state.totalLaps;
            // Re-render standings so finish threshold visually updates
            renderStandings(state.lastStandings);
          }
        }

        // Race started — explicit-start model
        if (data?.type === 'race_started') {
          state.raceStarted = true;
          state.raceStartedAt = data.started_at || null;
          renderRaceStatus();
        }

        // Race ended — multi-race
        if (data?.type === 'race_ended') {
          state.raceEnded = true;
          state.raceEndedAt = data.ended_at || null;
          renderRaceStatus();
        }

        // Race reopened (accidental End undone)
        if (data?.type === 'race_reopened') {
          state.raceEnded = false;
          state.raceEndedAt = null;
          renderRaceStatus();
          loadSnapshot().catch(() => {});
        }

        // Multi-race: active race changed (someone activated a different race)
        if (data?.type === 'active_race_changed') {
          // Re-pull everything race-scoped
          loadRaces();
          loadRaceConfig();
          loadSnapshot();
          onCoupleRaceChanged(); // W-075: riders are per-race — drop armed tag
        }
      } catch {
        // ignore non-JSON payloads
      }
    },
  });
}

// ---------------------------------------------------------------------------
// D4 — App mode (desktop build vs. browser/Docker) and shared helpers
// ---------------------------------------------------------------------------

const ANTENNA_POLL_MS = 10000;
const STATUS_BAR_TICK_MS = 1000; // countdowns and "last read x s ago"
const ANTENNA_WINDOW_MS = 60000;
const CONFIG_FETCH_TIMEOUT_MS = 5000;
// The backend itself waits up to 15 s for the reader-service's answer.
const DISCOVER_CLIENT_TIMEOUT_MS = 20000;
const PYWEBVIEW_WAIT_MS = 5000;
const UPDATE_CHECK_TIMEOUT_MS = 3000;
const UPDATE_RELEASES_API = 'https://api.github.com/repos/jan-knoblich/racetag/releases/latest';
const UPDATE_RELEASES_PAGE = 'https://github.com/jan-knoblich/racetag/releases/latest';

const appInfo = {
  desktop: false, // GET /config → desktop (true only in the packaged app)
  version: null, // GET /config → version (or the desktop bridge)
  readerIp: null, // effective reader_ip from the last /config response
  dataDir: null, // desktop only, from the pywebview bridge
  assistantDone: null, // GET /config → assistant_done; null when the backend lacks the field
};

async function fetchWithTimeout(url, options, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

function isShown(selector) {
  const el = $(selector);
  return !!el && !el.hidden;
}

function rememberAppConfig(cfg) {
  if (!cfg || typeof cfg !== 'object') return;
  if ('reader_ip' in cfg) appInfo.readerIp = cfg.reader_ip || null;
  if (typeof cfg.version === 'string' && cfg.version) appInfo.version = cfg.version;
  if (typeof cfg.assistant_done === 'boolean') appInfo.assistantDone = cfg.assistant_done;
}

async function fetchConfigFrom(base) {
  try {
    const res = await fetchWithTimeout(`${base}/config`, { headers: getApiHeaders() }, CONFIG_FETCH_TIMEOUT_MS);
    if (!res.ok) return null;
    // nginx answers unknown paths with index.html, so this throws there.
    const cfg = await res.json();
    return cfg && typeof cfg === 'object' ? cfg : null;
  } catch (_e) {
    return null;
  }
}

// Decide desktop vs. browser mode. The desktop shell serves this page from the
// backend itself, so a same-origin /config answering desktop:true is
// authoritative and overrides any Backend URL remembered in localStorage.
async function loadAppConfig() {
  const loc = window.location;
  const origin = loc && /^https?:$/.test(loc.protocol) ? loc.origin : null;
  let cfg = origin ? await fetchConfigFrom(origin) : null;
  if (cfg && cfg.desktop === true) {
    appInfo.desktop = true;
    state.backend = origin;
  } else if (state.backend !== origin) {
    cfg = await fetchConfigFrom(state.backend);
  }
  rememberAppConfig(cfg);
  return cfg;
}

function applyAppMode() {
  const desktop = appInfo.desktop;
  document.body.classList.toggle('desktop-mode', desktop);
  const urlControl = $('#backendUrlControl');
  if (urlControl) urlControl.hidden = desktop;
  const connectBtn = $('#connectBtn');
  if (connectBtn) connectBtn.hidden = desktop;
  const input = $('#backendUrl');
  if (input) input.value = state.backend;

  // The tag column is an expert option in the desktop build: move the
  // existing control (its change listener travels with it) into
  // Settings → Erweitert. Browser mode keeps it in the header.
  const advanced = $('#settingsAdvanced');
  const advancedBody = $('#settingsAdvancedBody');
  const tagToggle = $('#tagColumnToggle');
  const tagControl = tagToggle ? tagToggle.closest('label') : null;
  if (desktop && advanced && advancedBody && tagControl) {
    advancedBody.appendChild(tagControl);
    advanced.hidden = false;
  }
  ['#openDataFolderBtn', '#supportBundleBtn'].forEach((sel) => {
    const el = $(sel);
    if (el) el.hidden = !desktop;
  });
}

// Initial load and every manual (re)connect: pull the current state, then open
// the live stream. The stream retries on its own and reloads everything once
// it opens, so a backend that is not up yet heals without operator action.
async function connectToBackend() {
  setConnectionState('connecting');
  try {
    await loadSnapshot();
  } catch (e) {
    console.warn('Initial snapshot failed; the live stream keeps retrying:', e);
  }
  connectSSE();
  fetchRecentUnknownTag();
  loadRaceConfig();
  loadRaces();
  loadReaderStatus();
  refreshDiagnostics();
}

function formatDuration(ms) {
  const total = Math.max(0, Math.round(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const sec = total % 60;
  if (h > 0) return RT.fmt('durationHours', { h, m });
  if (m > 0) return RT.fmt('durationMinutes', { m, s: sec });
  return RT.fmt('durationSeconds', { s: sec });
}

function setInlineStatus(selector, level, text, busy) {
  const el = $(selector);
  if (!el) return;
  const className = `inline-status inline-status--${level}${busy ? ' inline-status--busy' : ''}`;
  if (el.className !== className) el.className = className;
  if (el.textContent !== text) el.textContent = text;
  el.hidden = false;
}

function hideInlineStatus(selector) {
  const el = $(selector);
  if (!el) return;
  el.hidden = true;
  el.textContent = '';
}

// ---------------------------------------------------------------------------
// D1 — Status bar: Reader / Antennen / Verbindung pills
// ---------------------------------------------------------------------------

// Before its first heartbeat a running reader-service child shows "startet…"
// for at most this long after page load, then the red "keine Rückmeldung".
const READER_BOOT_GRACE_MS = 30000;
const READER_UI_STARTED_AT = Date.now();

const readerUi = {
  available: null, // false once GET /reader/status is missing (older backend)
  status: null, // last reader status (GET /reader/status or SSE reader_status)
  receivedAt: 0, // Date.now() when `status` arrived; ages next_retry_s
  lostAt: null, // Date.now() when an active reader connection went away
};

const antennaUi = {
  samples: [], // [{t, reads: {"1": n}}] from reader status frames
  passes: null, // {"1": n} stored passes in the last 60 s (/diagnostics/antennas)
};

const connUi = {
  state: 'connecting', // 'connecting' | 'live' | 'reconnecting'
  attempt: 0, // consecutive failed stream attempts (api.js)
  retryAt: null, // Date.now() of the next scheduled attempt
};

// Stream attempts failed in a row before the connection counts as down (red).
const CONNECTION_DOWN_AFTER = 3;

function setConnectionState(sseState, detail = {}) {
  connUi.state = sseState;
  connUi.attempt = detail.attempt || 0;
  connUi.retryAt = sseState === 'reconnecting' ? Date.now() + (detail.delayS || 0) * 1000 : null;
  renderStatusBar();
}

function translateReaderError(error) {
  const text = String(error || '');
  if (/no reader found/i.test(text)) return RT.S.readerErrorNoReader;
  if (/multiple readers/i.test(text)) return RT.S.readerErrorMultiple;
  if (/time(d)? ?out/i.test(text)) return RT.S.readerErrorTimeout;
  if (/refused/i.test(text)) return RT.S.readerErrorRefused;
  if (/unreachable|no route/i.test(text)) return RT.S.readerErrorUnreachable;
  if (/event\.connection|session|bind/i.test(text)) return RT.S.readerErrorSession;
  if (/no reader ip/i.test(text)) return RT.S.readerErrorNoIp;
  if (/closed/i.test(text)) return RT.S.readerErrorClosed;
  if (/no reply/i.test(text)) return RT.S.readerErrorNoReply;
  if (/configuration failed/i.test(text)) return RT.S.readerErrorConfig;
  if (/socket error|send failed|reset by peer|broken pipe/i.test(text)) return RT.S.readerErrorConnection;
  // Unrecognised English text stays out of the UI; it is in reader.log.
  return RT.S.readerErrorOther;
}

// Seconds until the reader-service's next connect attempt, aged by the time
// since the status arrived; null when no retry is pending.
function readerRetryRemainingS(rs) {
  if (typeof rs.next_retry_s !== 'number' || rs.next_retry_s <= 0) return null;
  const remaining = rs.next_retry_s - (Date.now() - readerUi.receivedAt) / 1000;
  return remaining >= 1 ? Math.ceil(remaining) : null;
}

// {level: 'ok'|'warn'|'error'|'neutral', text, hint} for the reader state.
function describeReader() {
  const S = RT.S;
  if (readerUi.available === false) {
    return { level: 'neutral', text: S.readerStateUnavailable, hint: S.readerHintUnavailable };
  }
  const rs = readerUi.status;
  if (!rs) return { level: 'neutral', text: S.pillNoData, hint: S.readerHintPending };
  if (rs.state !== 'active' && /multiple readers/i.test(rs.error || '')) {
    return { level: 'warn', text: S.readerStateMultiple, hint: S.readerHintMultiple };
  }
  // Desktop build: the reader-service child is supervised by the shell.
  const sup = rs.supervisor && typeof rs.supervisor === 'object' ? rs.supervisor : null;
  // "Reader neu verbinden" respawns the child: the old one's last heartbeat is
  // 'stopped' while the replacement starts. Not an error; the backend turns it
  // into 'unknown' (red) after ~6 s if the new child never reports.
  if (rs.state === 'stopped' && sup && (sup.restart_pending === true || sup.running === true)) {
    return { level: 'warn', text: S.readerStateRestarting, hint: S.readerHintRestarting };
  }
  // Right after launch: the child runs but has not sent its first heartbeat.
  if (rs.state === 'unknown' && !rs.updated_at && sup && sup.running === true
      && Date.now() - READER_UI_STARTED_AT < READER_BOOT_GRACE_MS) {
    return { level: 'warn', text: S.readerStateStarting, hint: S.readerHintStarting };
  }
  const retryS = readerRetryRemainingS(rs);
  switch (rs.state) {
    case 'active':
      return {
        level: 'ok',
        text: rs.ip ? RT.fmt('readerStateActiveWithIp', { ip: rs.ip }) : S.readerStateActive,
        hint: '',
      };
    case 'configuring':
      return { level: 'warn', text: S.readerStateConfiguring, hint: S.readerHintConfiguring };
    case 'connecting':
      return {
        level: 'warn',
        text: retryS ? RT.fmt('readerStateConnectingRetry', { s: retryS }) : S.readerStateConnecting,
        hint: (rs.consecutive_failures || 0) > 0 ? S.readerHintConnecting : '',
      };
    case 'searching':
      return { level: 'warn', text: S.readerStateSearching, hint: S.readerHintSearching };
    case 'lost':
      return { level: 'warn', text: S.readerStateLost, hint: S.readerHintLost };
    case 'stopped':
      return { level: 'error', text: S.readerStateStopped, hint: S.readerHintStopped };
    default: // 'unknown': no heartbeat from the reader-service
      return { level: 'error', text: S.readerStateUnknown, hint: S.readerHintUnknown };
  }
}

function readerDetailLines(rs) {
  const S = RT.S;
  const lines = [];
  if (!rs) return lines;
  if (rs.ip) {
    lines.push(RT.fmt('readerTipIp', { ip: rs.ip }));
    const sourceKey = { cli: 'readerSourceCli', config: 'readerSourceConfig', discovery: 'readerSourceDiscovery' }[rs.target_source];
    if (sourceKey) lines.push(RT.fmt('readerTipSource', { source: S[sourceKey] }));
  }
  if (rs.serial) lines.push(RT.fmt('readerTipSerial', { serial: rs.serial }));
  if (Array.isArray(rs.antennas) && rs.antennas.length) {
    lines.push(RT.fmt('readerTipAntennas', { ports: rs.antennas.join(', ') }));
  }
  if (rs.state === 'active') {
    const lastAt = rs.last_event_at ? Date.parse(rs.last_event_at) : NaN;
    lines.push(Number.isFinite(lastAt)
      ? RT.fmt('readerTipLastRead', { age: formatDuration(Date.now() - lastAt) })
      : S.readerTipNoReadYet);
    if (rs.connected_since) {
      lines.push(RT.fmt('readerTipConnectedSince', { time: formatTimestampForDisplay(rs.connected_since) }));
    }
  } else {
    if (rs.consecutive_failures > 0) lines.push(RT.fmt('readerTipFailures', { n: rs.consecutive_failures }));
    if (rs.error) lines.push(RT.fmt('readerTipError', { error: translateReaderError(rs.error) }));
  }
  if (rs.supervisor && rs.supervisor.restart_count > 0) {
    lines.push(RT.fmt('readerTipRestarts', { n: rs.supervisor.restart_count }));
  }
  return lines;
}

function recordAntennaSample(rs) {
  if (!rs.antenna_reads || typeof rs.antenna_reads !== 'object') return;
  const now = Date.now();
  antennaUi.samples.push({ t: now, reads: { ...rs.antenna_reads } });
  // Keep exactly one sample at or before the window start as the baseline.
  while (antennaUi.samples.length > 2 && antennaUi.samples[1].t <= now - ANTENNA_WINDOW_MS) {
    antennaUi.samples.shift();
  }
}

// Raw reads per port in (roughly) the last 60 s, from the since-connection
// counters in reader status frames. Frames arrive every ≤ 5 s, so this is an
// estimate; the counters reset on every new reader connection.
function antennaReadsInWindow() {
  const samples = antennaUi.samples;
  if (!samples.length) return {};
  const latest = samples[samples.length - 1].reads;
  const cutoff = Date.now() - ANTENNA_WINDOW_MS;
  let base = samples[0].reads;
  for (const sample of samples) {
    if (sample.t > cutoff) break;
    base = sample.reads;
  }
  const rs = readerUi.status;
  const connectedAt = rs && rs.connected_since ? Date.parse(rs.connected_since) : NaN;
  if (samples[0].t > cutoff && Number.isFinite(connectedAt) && connectedAt >= cutoff) {
    base = {}; // whole connection lies inside the window: counters are the answer
  }
  const out = {};
  Object.keys(latest).forEach((port) => {
    const now = Number(latest[port]) || 0;
    const before = Number(base[port]) || 0;
    out[port] = now >= before ? now - before : now; // counter reset → count from 0
  });
  return out;
}

const byPortNumber = (a, b) => Number(a) - Number(b);

// A running race where the other antennas read at least this many tags in the
// last 60 s while one powered antenna read none: that antenna is suspicious.
const ANTENNA_SILENT_MIN_OTHER_READS = 5;

// Powered ports (`antennas`, which always include the fallback ports) that
// the reader did not report as connected (`antennas_detected`) and that have
// not read a single tag on this connection. Empty when the reader-service
// does not publish detection (null / missing field): nothing to judge then.
// A port that reads tags is connected, whatever detection said.
function antennasNotDetected(rs, ports) {
  if (!rs || !Array.isArray(rs.antennas_detected)) return [];
  const detected = rs.antennas_detected.map(String);
  const readsSinceConnect = rs.antenna_reads || {};
  return ports.filter((port) => !detected.includes(port) && !(Number(readsSinceConnect[port]) > 0));
}

// Ports without reads in the window while the race runs and other ports read.
function antennasSilent(ports, reads, exclude) {
  if (!(state.raceStarted && !state.raceEnded) || ports.length < 2) return [];
  return ports.filter((port) => {
    if (exclude.includes(port) || (Number(reads[port]) || 0) > 0) return false;
    const others = ports.reduce((sum, p) => (p === port ? sum : sum + (Number(reads[p]) || 0)), 0);
    return others >= ANTENNA_SILENT_MIN_OTHER_READS;
  });
}

function describeAntennas() {
  const S = RT.S;
  const passes = antennaUi.passes || {};
  const rs = readerUi.status;
  if (readerUi.available !== false && rs) {
    if (rs.state !== 'active') return { level: 'neutral', text: S.pillNoData, tip: S.antennasTipNoReader };
    const ports = Array.isArray(rs.antennas) ? rs.antennas.map(String) : [];
    if (!ports.length) {
      return { level: 'warn', text: S.antennasNoneDetected, tip: S.antennasTipNoneDetected };
    }
    const reads = antennaReadsInWindow();
    const missing = antennasNotDetected(rs, ports);
    const silent = antennasSilent(ports, reads, missing);
    const ok = ports.filter((port) => !missing.includes(port) && !silent.includes(port));
    const lines = [
      ...missing.map((port) => RT.fmt('antennasTipNotDetected', { port })),
      ...silent.map((port) => RT.fmt('antennasTipSilent', { port })),
      ...ports.map((port) => RT.fmt('antennasTipPort', {
        port, reads: reads[port] || 0, passes: passes[port] || 0,
      })),
      S.antennasTipZeroHint,
    ];
    const tip = lines.join('\n');
    if (!missing.length && !silent.length) {
      return { level: 'ok', text: RT.fmt('antennasOk', { ports: ports.join(', ') }), tip };
    }
    let text;
    if (missing.length) {
      text = ok.length || silent.length
        ? RT.fmt('antennasPartialNotDetected', { ok: [...ok, ...silent].sort(byPortNumber).join(', '), missing: missing.join(', ') })
        : RT.fmt('antennasNotDetected', { ports: missing.join(', ') });
    } else {
      text = ok.length
        ? RT.fmt('antennasPartialSilent', { ok: ok.join(', '), silent: silent.join(', ') })
        : RT.fmt('antennasSilent', { ports: silent.join(', ') });
    }
    return { level: 'warn', text, tip };
  }
  // No reader status (older backend, or not loaded yet): fall back to the
  // ports that produced passes recently.
  const seen = Object.keys(passes).sort(byPortNumber);
  if (!seen.length) return { level: 'neutral', text: S.pillNoData, tip: S.antennasTipNoData };
  return {
    level: 'neutral',
    text: seen.join(', '),
    tip: seen.map((port) => RT.fmt('antennasTipPortPasses', { port, passes: passes[port] })).join('\n'),
  };
}

function describeConnection() {
  const S = RT.S;
  const down = connUi.attempt >= CONNECTION_DOWN_AFTER;
  if (connUi.state === 'live') return { level: 'ok', text: S.connLive, hint: S.connTipLive };
  if (connUi.state === 'reconnecting') {
    const retryS = connUi.retryAt ? Math.ceil((connUi.retryAt - Date.now()) / 1000) : 0;
    let text = S.connReconnectingNow;
    if (retryS > 0) text = RT.fmt(down ? 'connDown' : 'connReconnectingIn', { s: retryS });
    return { level: down ? 'error' : 'warn', text, hint: down ? S.connTipDown : S.connTipReconnecting };
  }
  if (connUi.attempt > 0) {
    return { level: down ? 'error' : 'warn', text: S.connReconnectingNow, hint: down ? S.connTipDown : S.connTipReconnecting };
  }
  return { level: 'warn', text: S.connConnecting, hint: S.connTipConnecting };
}

function setPill(id, level, text, tip, stale) {
  const el = document.getElementById(id);
  if (!el) return;
  const className = `status-pill status-pill--${level}${stale ? ' status-pill--stale' : ''}`;
  if (el.className !== className) el.className = className;
  const textEl = el.querySelector('.status-pill-text');
  if (textEl && textEl.textContent !== text) textEl.textContent = text;
  // data-tip (not title): tooltips.js follows live data-tip changes.
  if (el.getAttribute('data-tip') !== tip) el.setAttribute('data-tip', tip);
}

function renderStatusBar() {
  const S = RT.S;
  // Without a live stream the reader/antenna pills keep the last known text
  // but lose their colour: a green "verbunden" must not outlive the stream.
  const stale = connUi.state !== 'live' && !!readerUi.status;

  const reader = describeReader();
  const readerLines = [reader.hint, ...readerDetailLines(readerUi.status)];
  if (stale) readerLines.push(S.readerHintStale);
  readerLines.push(S.readerTipClick);
  setPill('pillReader', stale ? 'neutral' : reader.level, reader.text,
    readerLines.filter(Boolean).join('\n'), stale);

  const antennas = describeAntennas();
  setPill('pillAntennas', stale ? 'neutral' : antennas.level, antennas.text,
    `${antennas.tip}\n${S.antennasTipClick}`, stale);

  const conn = describeConnection();
  const connLines = [conn.hint];
  if (!appInfo.desktop) connLines.push(RT.fmt('connTipBackend', { url: state.backend }));
  if (appInfo.version) connLines.push(RT.fmt('connTipVersion', { version: appInfo.version }));
  if (appInfo.dataDir) connLines.push(RT.fmt('connTipDataDir', { path: appInfo.dataDir }));
  connLines.push(S.connTipClick);
  setPill('pillConnection', conn.level, conn.text, connLines.join('\n'), false);

  if (isShown('#settingsModal')) renderReaderStatusBox('#settingsReaderStatus');
  if (isShown('#assistantModal')) renderAssistant();
}

// Toasts for the recovery events of the failure matrix. Nothing is announced
// for the very first status after page load.
function announceReaderTransition(prev, next) {
  if (!prev) return;
  const S = RT.S;
  if (prev.state === 'active' && next.state !== 'active') {
    readerUi.lostAt = Date.now();
    if (next.state === 'unknown') showToast(S.toastReaderServiceSilent, 'error');
    else if (next.state !== 'stopped') showToast(S.toastReaderLost, 'warn');
  } else if (next.state === 'unknown' && prev.state !== 'unknown') {
    showToast(S.toastReaderServiceSilent, 'error');
  }
  if (next.state === 'active' && prev.state !== 'active' && readerUi.lostAt) {
    showToast(RT.fmt('toastReaderBack', { duration: formatDuration(Date.now() - readerUi.lostAt) }));
    readerUi.lostAt = null;
  }
  if (next.ip && prev.ip && next.ip !== prev.ip && next.target_source === 'discovery') {
    appInfo.readerIp = next.ip; // the backend persisted it before replying
    showToast(RT.fmt('toastReaderNewIp', { ip: next.ip }));
  }
}

// A different backend means a different reader-service: forget the old state.
function resetReaderUi() {
  readerUi.available = null;
  readerUi.status = null;
  readerUi.lostAt = null;
  antennaUi.samples = [];
  antennaUi.passes = null;
}

function handleReaderStatus(rs) {
  if (!rs || typeof rs !== 'object' || typeof rs.state !== 'string') return;
  readerUi.available = true;
  announceReaderTransition(readerUi.status, rs);
  readerUi.status = rs;
  readerUi.receivedAt = Date.now();
  recordAntennaSample(rs);
  renderStatusBar();
}

async function loadReaderStatus() {
  try {
    const res = await fetch(`${state.backend}/reader/status`, { headers: getApiHeaders() });
    if (res.status === 404 || res.status === 405) {
      readerUi.available = false;
      renderStatusBar();
      return;
    }
    if (!res.ok) return;
    handleReaderStatus(await res.json());
  } catch (_e) {
    // backend unreachable: the Verbindung pill already says so
  }
}

// Reader state as a two-line box (Settings and assistant step 1).
function renderReaderStatusBox(selector) {
  const el = $(selector);
  if (!el) return;
  const reader = describeReader();
  const rs = readerUi.status;
  let headline = `${RT.S.pillReaderLabel}: ${reader.text}`;
  if (rs && rs.state === 'active' && rs.serial) {
    headline += ` · ${RT.fmt('readerTipSerial', { serial: rs.serial })}`;
  }
  const text = reader.hint ? `${headline}\n${reader.hint}` : headline;
  if (el.textContent !== text) el.textContent = text;
  const className = `settings-reader-status settings-reader-status--${reader.level}`;
  if (el.className !== className) el.className = className;
}

// ---------------------------------------------------------------------------
// C4 — Reader discovery, one-click IP apply, reconnect
// ---------------------------------------------------------------------------

const CANDIDATE_SOURCE_KEYS = {
  arp: 'candidateSourceArp',
  sweep: 'candidateSourceSweep',
  linklocal: 'candidateSourceLinklocal',
  connected: 'candidateSourceConnected',
};

// Candidates currently rendered per list selector, for re-rendering after
// an IP was applied (the applied row turns into "eingestellt").
const candidateLists = {};

function renderReaderCandidates(selector, candidates) {
  const list = $(selector);
  if (!list) return;
  const items = Array.isArray(candidates)
    ? candidates.filter((c) => c && typeof c.ip === 'string' && c.ip)
    : [];
  candidateLists[selector] = items;
  list.textContent = '';
  list.hidden = items.length === 0;
  items.forEach((c) => {
    const li = document.createElement('li');
    li.className = 'reader-candidate';
    const info = document.createElement('div');
    info.className = 'reader-candidate-info';
    const ip = document.createElement('span');
    ip.className = 'reader-candidate-ip';
    ip.textContent = c.ip;
    const meta = document.createElement('span');
    meta.className = 'reader-candidate-meta';
    const serial = c.serial ? RT.fmt('candidateSerial', { serial: c.serial }) : RT.S.candidateSerialUnknown;
    const sourceKey = CANDIDATE_SOURCE_KEYS[c.source];
    meta.textContent = sourceKey ? `${serial} · ${RT.S[sourceKey]}` : serial;
    info.append(ip, meta);
    li.appendChild(info);
    if (appInfo.readerIp && c.ip === appInfo.readerIp) {
      const badge = document.createElement('span');
      badge.className = 'reader-candidate-current';
      badge.textContent = RT.S.candidateCurrent;
      li.appendChild(badge);
    } else {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn-primary reader-candidate-apply';
      btn.dataset.ip = c.ip;
      btn.dataset.tip = RT.S.candidateApplyTip;
      btn.textContent = RT.S.candidateApply;
      li.appendChild(btn);
    }
    list.appendChild(li);
  });
}

// Restarting the reader, applying another IP or a new antenna power makes the
// reader-service drop and rebuild the reader session; passes in that gap are
// not recorded. While a race runs with a connected reader, ask first.
// `messageKey` names the RT.S confirm text. True = go ahead.
function confirmReaderInterruption(messageKey) {
  const rs = readerUi.status;
  const raceLive = state.raceStarted && !state.raceEnded;
  if (!raceLive || !rs || rs.state !== 'active') return true;
  return window.confirm(RT.S[messageKey]);
}

// Persist a reader IP. The reader-service sees it in its next heartbeat reply
// and reconnects by itself (contract §0.1), so nothing else is needed here.
async function applyReaderIp(ip) {
  try {
    const res = await fetch(`${state.backend}/config`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', ...getApiHeaders() },
      body: JSON.stringify({ reader_ip: ip }),
    });
    if (!res.ok) {
      const body = await res.text().catch(() => '');
      showToast(RT.apiError(res.status, body, 'readerIpApplyFailed'), 'error');
      return false;
    }
    rememberAppConfig(await res.json().catch(() => null));
  } catch (_e) {
    showToast(`${RT.S.readerIpApplyFailed}: ${RT.S.errNetwork}`, 'error');
    return false;
  }
  appInfo.readerIp = ip;
  // Keep an open Settings form consistent so "Speichern" does not revert it.
  _settingsOriginal.reader_ip = ip;
  const ipInput = $('#settingsReaderIp');
  if (ipInput) ipInput.value = ip;
  showToast(RT.fmt('toastReaderIpApplied', { ip }));
  return true;
}

async function onCandidateListClick(e) {
  const btn = e.target.closest('button.reader-candidate-apply');
  if (!btn || btn.disabled) return;
  if (btn.dataset.ip !== appInfo.readerIp && !confirmReaderInterruption('confirmReaderIpRace')) return;
  btn.disabled = true;
  const applied = await applyReaderIp(btn.dataset.ip);
  if (!applied) {
    btn.disabled = false;
    return;
  }
  Object.keys(candidateLists).forEach((selector) => {
    if (candidateLists[selector].length) renderReaderCandidates(selector, candidateLists[selector]);
  });
}

async function runReaderDiscovery(buttonSelector, statusSelector, listSelector) {
  const btn = $(buttonSelector);
  if (btn) btn.disabled = true;
  setInlineStatus(statusSelector, 'info', RT.S.discoverRunning, true);
  renderReaderCandidates(listSelector, []);
  try {
    const res = await fetchWithTimeout(`${state.backend}/reader/discover`, {
      method: 'POST',
      headers: getApiHeaders(),
    }, DISCOVER_CLIENT_TIMEOUT_MS);
    if (res.status === 404 || res.status === 405 || res.status === 501) {
      setInlineStatus(statusSelector, 'warn', RT.S.discoverNotSupported);
      return;
    }
    if (!res.ok) {
      const body = await res.text().catch(() => '');
      setInlineStatus(statusSelector, 'error', RT.apiError(res.status, body, 'discoverFailed'));
      return;
    }
    const data = await res.json();
    const candidates = Array.isArray(data.candidates) ? data.candidates : [];
    if (data.error === 'reader_service_unavailable') {
      setInlineStatus(statusSelector, 'error', RT.S.discoverServiceUnavailable);
    } else if (!candidates.length) {
      setInlineStatus(statusSelector, 'warn', data.error === 'timeout' ? RT.S.discoverTimeout : RT.S.discoverNone);
    } else if (candidates.length === 1 && candidates[0].ip === appInfo.readerIp) {
      setInlineStatus(statusSelector, 'ok', RT.S.discoverFoundCurrent);
    } else {
      setInlineStatus(statusSelector, 'ok', candidates.length === 1
        ? RT.S.discoverFoundOne
        : RT.fmt('discoverFoundMany', { n: candidates.length }));
    }
    renderReaderCandidates(listSelector, candidates);
  } catch (e) {
    setInlineStatus(statusSelector, 'error', e && e.name === 'AbortError'
      ? RT.S.discoverTimeout
      : `${RT.S.discoverFailed}: ${RT.S.errNetwork}`);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function restartReader() {
  const btn = $('#readerRestartBtn');
  if (btn && btn.disabled) return;
  // In the desktop build this puts the reader in standby and respawns the
  // reader-service: several seconds without reads.
  if (!confirmReaderInterruption('confirmReaderRestartRace')) return;
  if (btn) btn.disabled = true;
  try {
    const res = await fetch(`${state.backend}/reader/restart`, { method: 'POST', headers: getApiHeaders() });
    if (res.ok) {
      showToast(RT.S.toastReaderRestart);
    } else if (res.status === 404 || res.status === 405 || res.status === 501) {
      showToast(RT.S.readerRestartNotSupported, 'warn');
    } else {
      const body = await res.text().catch(() => '');
      showToast(RT.apiError(res.status, body, 'readerRestartFailed'), 'error');
    }
  } catch (_e) {
    showToast(`${RT.S.readerRestartFailed}: ${RT.S.errNetwork}`, 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// D6 — Desktop support tooling (pywebview js_api bridge)
// ---------------------------------------------------------------------------

// Resolve window.pywebview.api once `method` is callable, or null after
// PYWEBVIEW_WAIT_MS. The bridge is injected asynchronously after page load and
// announces itself with 'pywebviewready'; the event may already have fired,
// so poll as well.
function getDesktopApi(method) {
  const lookup = () => {
    const api = window.pywebview && window.pywebview.api;
    return api && typeof api[method] === 'function' ? api : null;
  };
  const ready = lookup();
  if (ready) return Promise.resolve(ready);
  return new Promise((resolve) => {
    const deadline = Date.now() + PYWEBVIEW_WAIT_MS;
    let poll = null;
    const check = () => {
      const api = lookup();
      if (!api && Date.now() < deadline) return;
      window.removeEventListener('pywebviewready', check);
      clearInterval(poll);
      resolve(api);
    };
    window.addEventListener('pywebviewready', check);
    poll = setInterval(check, 200);
  });
}

function renderSettingsMeta() {
  const versionEl = $('#settingsVersion');
  if (versionEl) {
    versionEl.textContent = appInfo.version
      ? RT.fmt('settingsVersion', { version: appInfo.version })
      : RT.S.settingsVersionUnknown;
  }
  const dirEl = $('#settingsDataDir');
  if (dirEl) {
    dirEl.textContent = appInfo.dataDir ? RT.fmt('settingsDataDir', { path: appInfo.dataDir }) : '';
    dirEl.hidden = !appInfo.dataDir;
  }
}

async function loadDesktopAppInfo() {
  const api = await getDesktopApi('app_info');
  if (!api) return;
  try {
    const info = await api.app_info();
    if (!info || typeof info !== 'object') return;
    if (typeof info.data_dir === 'string' && info.data_dir) appInfo.dataDir = info.data_dir;
    if (!appInfo.version && typeof info.version === 'string' && info.version) appInfo.version = info.version;
    renderSettingsMeta();
    renderStatusBar();
  } catch (e) {
    console.warn('app_info failed:', e);
  }
}

async function openDataFolder() {
  const api = await getDesktopApi('open_data_folder');
  if (!api) {
    showToast(RT.S.desktopApiUnavailable, 'warn');
    return;
  }
  try {
    if (!(await api.open_data_folder())) showToast(RT.S.toastDataFolderFailed, 'error');
  } catch (e) {
    console.warn('open_data_folder failed:', e);
    showToast(RT.S.toastDataFolderFailed, 'error');
  }
}

async function createSupportBundle() {
  const btn = $('#supportBundleBtn');
  if (btn) btn.disabled = true;
  setInlineStatus('#supportStatus', 'info', RT.S.supportBusy, true);
  try {
    const api = await getDesktopApi('create_support_bundle');
    if (!api) {
      setInlineStatus('#supportStatus', 'warn', RT.S.desktopApiUnavailable);
      return;
    }
    const result = await api.create_support_bundle();
    if (result && result.ok) {
      setInlineStatus('#supportStatus', 'ok', RT.fmt('supportSaved', { path: result.path || '' }));
      showToast(RT.S.toastSupportBundleSaved);
    } else if (result && result.error) {
      console.warn('create_support_bundle failed:', result.error);
      setInlineStatus('#supportStatus', 'error', RT.S.supportFailed);
    } else {
      setInlineStatus('#supportStatus', 'neutral', RT.S.supportCancelled); // save dialog cancelled
    }
  } catch (e) {
    console.warn('create_support_bundle failed:', e);
    setInlineStatus('#supportStatus', 'error', RT.S.supportFailed);
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// D5 — First-run assistant: find reader → antenna test → first race
// ---------------------------------------------------------------------------

const ASSISTANT_DONE_KEY = 'racetag.assistantDone';
const ASSISTANT_STEPS = 3;
const ASSISTANT_POLL_MS = 2000; // GET /reader/status while the antenna test runs
const START_HINT_TOAST_MS = 9000;
const START_BUTTON_ATTENTION_MS = 10000;
// storage._ensure_default_race() creates this race on every fresh database.
const BOOTSTRAP_RACE_NAME = 'Default race';

const assistantUi = {
  step: 1,
  baseline: {}, // antenna_reads when the antenna test (re)started
  baselineConnection: null, // connected_since of that baseline
  gridPorts: null, // port list the antenna grid was built for
  pollTimer: null,
  creating: false,
  context: null, // active race as seen when step 3 opened (loadAssistantRaceContext)
  contextPromise: null,
  contextSeq: 0,
  doneSaved: false, // assistant_done: true reached the backend in this session
};

// "No races yet" in the sense of the contract. The backend always bootstraps
// one untouched "Default race", so that race alone still counts as none.
function hasNoRealRaces(items) {
  if (!items.length) return true;
  const only = items[0];
  return items.length === 1 && only.name === BOOTSTRAP_RACE_NAME && !only.started && !only.ended;
}

// The done flag lives in the backend config (assistant_done), because the
// desktop window forgets localStorage on every launch (private browser
// profile, random port). localStorage is only the fallback for a backend
// that does not know the field yet.
function isAssistantDone() {
  if (typeof appInfo.assistantDone === 'boolean') return appInfo.assistantDone;
  return rtStorageGet(ASSISTANT_DONE_KEY) === '1';
}

async function markAssistantDone() {
  appInfo.assistantDone = true;
  rtStorageSet(ASSISTANT_DONE_KEY, '1');
  if (assistantUi.doneSaved) return;
  try {
    const res = await fetch(`${state.backend}/config`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', ...getApiHeaders() },
      body: JSON.stringify({ assistant_done: true }),
    });
    if (!res.ok) {
      console.warn('assistant_done not saved:', res.status);
      return;
    }
    assistantUi.doneSaved = true;
    rememberAppConfig(await res.json().catch(() => null));
  } catch (e) {
    console.warn('assistant_done not saved:', e);
  }
}

// Riders registered in the active race; null when unknown.
async function fetchActiveRiderCount() {
  try {
    const res = await fetch(`${state.backend}/riders`, { headers: getApiHeaders() });
    if (!res.ok) return null;
    const data = await res.json();
    if (typeof data.count === 'number') return data.count;
    return Array.isArray(data.items) ? data.items.length : null;
  } catch (_e) {
    return null;
  }
}

async function maybeShowAssistant() {
  if (!appInfo.desktop || isAssistantDone()) return;
  try {
    const res = await fetch(`${state.backend}/races`, { headers: getApiHeaders() });
    if (!res.ok) return;
    const data = await res.json();
    if (!hasNoRealRaces(data.items || [])) return;
    // An untouched bootstrap race can already hold the prepared start list
    // (CSV import or pairing after skipping the assistant). Setup is done
    // then — step 3 would move the active race away from those riders.
    const riders = await fetchActiveRiderCount();
    if (riders === null) return;
    if (riders > 0) {
      markAssistantDone();
      return;
    }
  } catch (_e) {
    return;
  }
  if (isAssistantDone()) return;
  if (document.querySelector('.modal-backdrop:not([hidden])')) return; // never cover a dialog
  openAssistant();
}

function openAssistant() {
  const modal = $('#assistantModal');
  if (!modal) return;
  closeSettingsModal();
  hideInlineStatus('#assistantDiscoverStatus');
  renderReaderCandidates('#assistantCandidates', []);
  const errBox = $('#assistantRaceError');
  if (errBox) errBox.hidden = true;
  assistantUi.context = null;
  assistantUi.contextPromise = null;
  const keepRadio = $('#assistantReuseKeep');
  if (keepRadio) keepRadio.checked = true;
  renderAssistantReuse();
  modal.hidden = false;
  assistantGoTo(1);
}

// Closing by any route (skip, Escape, finish) counts as done; the assistant
// stays available from Settings.
function closeAssistant() {
  const modal = $('#assistantModal');
  if (!modal || modal.hidden) return;
  modal.hidden = true;
  stopAssistantPoll();
  markAssistantDone();
}

function stopAssistantPoll() {
  clearInterval(assistantUi.pollTimer);
  assistantUi.pollTimer = null;
}

function assistantGoTo(step) {
  assistantUi.step = Math.min(Math.max(step, 1), ASSISTANT_STEPS);
  const current = assistantUi.step;
  for (let i = 1; i <= ASSISTANT_STEPS; i += 1) {
    const section = $(`#assistantStep${i}`);
    if (section) section.hidden = i !== current;
  }
  document.querySelectorAll('#assistantProgress li').forEach((li) => {
    const n = Number(li.dataset.step);
    li.classList.toggle('is-current', n === current);
    li.classList.toggle('is-done', n < current);
  });
  const label = $('#assistantStepLabel');
  if (label) label.textContent = RT.fmt('assistantStepOf', { step: current, total: ASSISTANT_STEPS });
  const backBtn = $('#assistantBackBtn');
  if (backBtn) backBtn.hidden = current === 1;
  const nextBtn = $('#assistantNextBtn');
  updateAssistantFinishLabel();

  stopAssistantPoll();
  if (current === 2) {
    startAntennaTest();
    // reader_status SSE frames can be up to 5 s apart; the heartbeat behind
    // GET /reader/status is ≤ 2 s old, so tiles turn green promptly.
    assistantUi.pollTimer = setInterval(loadReaderStatus, ASSISTANT_POLL_MS);
  }
  renderAssistant();
  if (current === ASSISTANT_STEPS) {
    loadAssistantRaceContext();
    const nameInput = $('#assistantRaceName');
    if (nameInput) nameInput.focus();
  } else if (nextBtn) {
    nextBtn.focus();
  }
}

function resetAntennaTest() {
  const rs = readerUi.status;
  assistantUi.baseline = rs && rs.antenna_reads ? { ...rs.antenna_reads } : {};
  assistantUi.baselineConnection = rs ? rs.connected_since || null : null;
  assistantUi.gridPorts = null;
}

// The cached status can be several seconds old; reads that happened before
// the test started must not count, so take the baseline from a fresh one.
async function startAntennaTest() {
  resetAntennaTest();
  renderAssistant();
  await loadReaderStatus();
  resetAntennaTest();
  renderAssistant();
}

function renderAssistant() {
  renderReaderStatusBox('#assistantReaderStatus');
  if (assistantUi.step === 2) renderAntennaTest();
}

function renderAntennaTest() {
  const grid = $('#assistantAntennaGrid');
  if (!grid) return;
  const rs = readerUi.status;
  const active = !!rs && rs.state === 'active';
  const reads = active && rs.antenna_reads ? rs.antenna_reads : {};
  // Counters restart with every reader connection; a baseline from an older
  // connection would hide real reads.
  const baseline = rs && (rs.connected_since || null) === assistantUi.baselineConnection
    ? assistantUi.baseline : {};
  const ports = active && Array.isArray(rs.antennas) ? rs.antennas.map(String) : [];
  Object.keys(reads).forEach((port) => { if (!ports.includes(port)) ports.push(port); });
  ports.sort(byPortNumber);

  const key = ports.join(',');
  if (assistantUi.gridPorts !== key) {
    grid.textContent = '';
    ports.forEach((port) => {
      const tile = document.createElement('div');
      tile.className = 'antenna-tile';
      tile.dataset.port = port;
      const label = document.createElement('span');
      label.className = 'antenna-tile-label';
      label.textContent = RT.fmt('assistantAntennaLabel', { port });
      const value = document.createElement('span');
      value.className = 'antenna-tile-value';
      tile.append(label, value);
      grid.appendChild(tile);
    });
    assistantUi.gridPorts = key;
  }

  let okCount = 0;
  const notDetected = active ? antennasNotDetected(rs, ports) : [];
  Array.from(grid.children).forEach((tile) => {
    const port = tile.dataset.port;
    const count = Math.max(0, (Number(reads[port]) || 0) - (Number(baseline[port]) || 0));
    if (count > 0) okCount += 1;
    const className = `antenna-tile${count > 0 ? ' antenna-tile--ok' : ''}`;
    if (tile.className !== className) tile.className = className;
    const valueEl = tile.querySelector('.antenna-tile-value');
    let text = RT.S.assistantAntennaWaiting;
    if (count > 0) text = RT.fmt('assistantAntennaReads', { n: count });
    else if (notDetected.includes(port)) text = RT.S.assistantAntennaNotDetected;
    if (valueEl && valueEl.textContent !== text) valueEl.textContent = text;
  });

  if (!active) {
    setInlineStatus('#assistantAntennaHint', 'warn', RT.S.assistantAntennaNoReader);
  } else if (!ports.length) {
    setInlineStatus('#assistantAntennaHint', 'warn', RT.S.assistantAntennaNoPorts);
  } else if (okCount === ports.length) {
    setInlineStatus('#assistantAntennaHint', 'ok', RT.S.assistantAntennaAllOk);
  } else {
    setInlineStatus('#assistantAntennaHint', 'info', RT.fmt('assistantAntennaProgress', { ok: okCount, total: ports.length }));
  }
}

// Step 3 looks at the active race first: if it is not started yet and already
// has riders (a start list was imported before the assistant ran), creating
// and activating a new race would leave those riders behind in a race that is
// no longer active. The operator then chooses; keeping the race is the default.
function loadAssistantRaceContext() {
  const seq = ++assistantUi.contextSeq;
  const promise = (async () => {
    try {
      const res = await fetch(`${state.backend}/races`, { headers: getApiHeaders() });
      if (!res.ok) return null;
      const data = await res.json();
      const active = (data.items || []).find((r) => r && r.is_active);
      if (!active || !active.id) return null;
      const untouched = !active.started && !active.ended;
      const riders = untouched ? await fetchActiveRiderCount() : null;
      return {
        id: active.id,
        name: active.name,
        started: !!active.started,
        ended: !!active.ended,
        totalLaps: active.total_laps,
        snapshotIntervalS: active.snapshot_interval_s ?? null,
        riders: riders || 0,
      };
    } catch (_e) {
      return null;
    }
  })();
  assistantUi.contextPromise = promise;
  promise.then((ctx) => {
    if (seq !== assistantUi.contextSeq) return;
    const wasKeepable = assistantCanKeepRace(assistantUi.context);
    assistantUi.context = ctx;
    if (!wasKeepable && assistantCanKeepRace(ctx)) prefillAssistantKeep(ctx);
    renderAssistantReuse();
  });
  return promise;
}

function assistantCanKeepRace(ctx) {
  return !!ctx && !ctx.started && !ctx.ended && ctx.riders > 0;
}

function assistantKeepsRace() {
  const keepRadio = $('#assistantReuseKeep');
  return assistantCanKeepRace(assistantUi.context) && !!keepRadio && keepRadio.checked;
}

function prefillAssistantKeep(ctx) {
  const keepRadio = $('#assistantReuseKeep');
  if (keepRadio) keepRadio.checked = true;
  const nameInput = $('#assistantRaceName');
  if (nameInput && !nameInput.value.trim() && ctx.name && ctx.name !== BOOTSTRAP_RACE_NAME) {
    nameInput.value = ctx.name;
  }
  const lapsInput = $('#assistantRaceLaps');
  if (lapsInput && lapsInput.value === lapsInput.defaultValue
      && Number.isInteger(ctx.totalLaps) && ctx.totalLaps >= 1 && ctx.totalLaps <= 999) {
    lapsInput.value = String(ctx.totalLaps);
  }
}

function renderAssistantReuse() {
  const ctx = assistantUi.context;
  const show = assistantCanKeepRace(ctx);
  const box = $('#assistantReuseBox');
  if (box) box.hidden = !show;
  const text = $('#assistantReuseText');
  if (text && show) {
    text.textContent = RT.fmt('assistantReuseText', { name: rtRaceDisplayName(ctx.name), n: ctx.riders });
  }
  updateAssistantFinishLabel();
}

function updateAssistantFinishLabel() {
  const nextBtn = $('#assistantNextBtn');
  if (!nextBtn || assistantUi.creating) return;
  let label = RT.S.assistantNext;
  if (assistantUi.step === ASSISTANT_STEPS) {
    label = assistantKeepsRace() ? RT.S.assistantFinishKeep : RT.S.assistantFinish;
  }
  nextBtn.textContent = label;
}

// Races only count laps after "Rennen starten": say so and point at the button.
function showStartRaceHint(toastKey, name) {
  showToast(RT.fmt(toastKey, { name: rtRaceDisplayName(name) }), 'info', START_HINT_TOAST_MS);
  const startBtn = $('#startRaceBtn');
  if (!startBtn || startBtn.disabled) return;
  startBtn.classList.remove('btn-attention');
  void startBtn.offsetWidth; // restart the animation
  startBtn.classList.add('btn-attention');
  clearTimeout(startBtn._attentionTimer);
  startBtn._attentionTimer = setTimeout(() => startBtn.classList.remove('btn-attention'), START_BUTTON_ATTENTION_MS);
}

async function assistantCreateRace() {
  if (assistantUi.creating) return;
  const errBox = $('#assistantRaceError');
  const showError = (message) => {
    if (!errBox) return;
    errBox.textContent = message;
    errBox.hidden = false;
  };
  if (errBox) errBox.hidden = true;
  const name = ($('#assistantRaceName')?.value || '').trim();
  const laps = Number.parseInt($('#assistantRaceLaps')?.value || '', 10);
  if (!name) {
    showError(RT.S.assistantRaceNameRequired);
    return;
  }
  if (!Number.isInteger(laps) || laps < 1 || laps > 999) {
    showError(RT.S.assistantRaceLapsInvalid);
    return;
  }

  const nextBtn = $('#assistantNextBtn');
  assistantUi.creating = true;
  if (nextBtn) {
    nextBtn.disabled = true;
    nextBtn.textContent = RT.S.assistantCreating;
  }
  let failKey = 'assistantRaceCreateFailed';
  try {
    // Decide on the loaded race context, never on a half-loaded one.
    if (!assistantUi.contextPromise) loadAssistantRaceContext();
    await assistantUi.contextPromise;
    const ctx = assistantUi.context;

    if (assistantKeepsRace()) {
      failKey = 'assistantRaceKeepFailed';
      const patch = { name, total_laps: laps };
      if (ctx.snapshotIntervalS == null) patch.snapshot_interval_s = 120; // assistant default
      const res = await fetch(`${state.backend}/races/${encodeURIComponent(ctx.id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', ...getApiHeaders() },
        body: JSON.stringify(patch),
      });
      if (!res.ok) {
        const body = await res.text().catch(() => '');
        showError(RT.apiError(res.status, body, failKey));
        return;
      }
      const updated = await res.json().catch(() => null);
      closeAssistant();
      await Promise.all([loadRaces(), loadRaceConfig()]);
      showStartRaceHint('toastRaceKeptStartHint', (updated && updated.name) || name);
      return;
    }

    // Activating a new race while another one runs would stop counting passes
    // for the running race.
    if (ctx && ctx.started && !ctx.ended
        && !confirm(RT.fmt('confirmAssistantReplaceRunning', { name: rtRaceDisplayName(ctx.name) }))) {
      return;
    }

    // Same defaults as the create-race modal: fixed laps, criterium finish,
    // 120 s auto-snapshots.
    const res = await fetch(`${state.backend}/races`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getApiHeaders() },
      body: JSON.stringify({
        name, total_laps: laps, snapshot_interval_s: 120, finish_mode: 'leader', scheduled_at: null,
      }),
    });
    if (!res.ok) {
      const body = await res.text().catch(() => '');
      showError(RT.apiError(res.status, body, failKey));
      return;
    }
    const created = await res.json();
    closeAssistant();
    await activateRace(created.id);
    // activateRace reports its own failure; keep that toast visible.
    if (state.activeRaceId === created.id) showStartRaceHint('toastRaceCreatedStartHint', created.name);
  } catch (_e) {
    showError(`${RT.S[failKey]}: ${RT.S.errNetwork}`);
  } finally {
    assistantUi.creating = false;
    if (nextBtn) nextBtn.disabled = false;
    updateAssistantFinishLabel();
  }
}

function assistantNext() {
  if (assistantUi.step < ASSISTANT_STEPS) assistantGoTo(assistantUi.step + 1);
  else assistantCreateRace();
}

// ---------------------------------------------------------------------------
// D7 — Update notice (silent on every error)
// ---------------------------------------------------------------------------

// Numeric dotted-version comparison; build/pre-release suffixes ("+sha",
// "-rc1") are ignored. Unparseable input compares equal (no notice).
function compareVersions(a, b) {
  const parse = (v) => String(v).trim().replace(/^v/i, '').split(/[+-]/)[0].split('.')
    .map((part) => (/^\d+$/.test(part) ? Number(part) : NaN));
  const pa = parse(a);
  const pb = parse(b);
  if (pa.some(Number.isNaN) || pb.some(Number.isNaN)) return 0;
  for (let i = 0; i < Math.max(pa.length, pb.length); i += 1) {
    const diff = (pa[i] || 0) - (pb[i] || 0);
    if (diff !== 0) return diff > 0 ? 1 : -1;
  }
  return 0;
}

async function checkForUpdate() {
  const current = appInfo.version;
  if (!current) return;
  try {
    const res = await fetchWithTimeout(UPDATE_RELEASES_API, {}, UPDATE_CHECK_TIMEOUT_MS);
    if (!res.ok) return;
    const data = await res.json();
    const latest = String((data && data.tag_name) || '').replace(/^v/i, '');
    if (!latest || compareVersions(latest, current) <= 0) return;
    const link = $('#updateNotice');
    if (!link) return;
    link.href = typeof data.html_url === 'string' && data.html_url.startsWith('https://github.com/')
      ? data.html_url : UPDATE_RELEASES_PAGE;
    link.textContent = RT.fmt('updateAvailable', { version: latest });
    link.setAttribute('data-tip', RT.fmt('updateTip', { current }));
    link.hidden = false;
  } catch (_e) {
    // offline, rate-limited or blocked: no notice
  }
}

// ---------------------------------------------------------------------------
// Escape closes the top-most open modal
// ---------------------------------------------------------------------------

const MODAL_CLOSERS = [
  ['#assistantModal', closeAssistant],
  ['#settingsModal', closeSettingsModal],
  ['#lapEditModal', closeLapEditModal],
  ['#newRaceModal', () => { $('#newRaceModal').hidden = true; }],
  ['#ridersModal', closeRidersModal],
  ['#registerModal', closeRegisterModal],
];

function closeModalOnEscape(e) {
  if (e.key !== 'Escape' || e.defaultPrevented) return;
  const open = MODAL_CLOSERS.find(([selector]) => isShown(selector));
  if (!open) return;
  e.preventDefault();
  open[1]();
}

// ---------------------------------------------------------------------------
// init
// ---------------------------------------------------------------------------
function init() {
  const input = $('#backendUrl');
  const tagToggle = $('#tagColumnToggle');
  input.value = state.backend;
  tagToggle.checked = state.showTagColumn;
  applyTagColumnVisibility();

  tagToggle.addEventListener('change', (e) => {
    state.showTagColumn = e.target.checked;
    applyTagColumnVisibility();
  });

  // Click-to-copy for any tag-id rendered with .tag-id-copyable. Event
  // delegation on document so it works for rows added later via SSE updates.
  document.addEventListener('click', (e) => {
    const el = e.target && e.target.closest && e.target.closest('.tag-id-copyable');
    if (!el) return;
    const tagId = el.dataset.tagId || el.textContent.trim();
    if (!tagId) return;
    const ok = (val) => showToast(RT.fmt('toastTagCopied', { tag: val.slice(0, 12) }));
    const fail = () => showToast(RT.S.toastCopyFailed, 'warn');
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(tagId).then(() => ok(tagId), fail);
    } else {
      // execCommand fallback for old WebViews
      try {
        const ta = document.createElement('textarea');
        ta.value = tagId;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        ta.remove();
        ok(tagId);
      } catch { fail(); }
    }
  });

  // Browser/Docker mode only (hidden in the desktop build).
  $('#connectBtn').addEventListener('click', () => {
    saveBackend(input.value);
    resetReaderUi();
    fetchConfigFrom(state.backend).then((cfg) => {
      rememberAppConfig(cfg);
      renderSettingsMeta();
    });
    connectToBackend();
  });

  // D1: status pills double as shortcuts.
  const pillReader = $('#pillReader');
  if (pillReader) pillReader.addEventListener('click', openSettingsModal);
  const pillAntennas = $('#pillAntennas');
  if (pillAntennas) {
    pillAntennas.addEventListener('click', () => {
      const panel = $('#diagnosticsPanel');
      if (!panel) return;
      panel.open = true; // fires 'toggle' → starts the panel's own polling
      panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    });
  }
  const pillConnection = $('#pillConnection');
  if (pillConnection) pillConnection.addEventListener('click', connectToBackend);

  // Escape closes the top-most modal (all of them, not only the new ones).
  document.addEventListener('keydown', closeModalOnEscape);

  // Multi-race: switch active race via selector
  const raceSelect = $('#raceSelect');
  if (raceSelect) {
    raceSelect.addEventListener('change', (e) => {
      const newId = e.target.value;
      if (newId && newId !== state.activeRaceId) {
        activateRace(newId);
      }
    });
  }

  // Multi-race: open create-race modal
  const newRaceBtn = $('#newRaceBtn');
  if (newRaceBtn) {
    newRaceBtn.addEventListener('click', () => {
      const modal = $('#newRaceModal');
      const err = $('#newRaceError');
      const nameInput = $('#newRaceName');
      if (modal) modal.hidden = false;
      if (err) err.hidden = true;
      if (nameInput) { nameInput.value = ''; nameInput.focus(); }
      const sched = $('#newRaceScheduled');
      if (sched) sched.value = '';
      const laps = $('#newRaceTotalLaps');
      if (laps) laps.value = state.totalLaps || 5;
      const snap = $('#newRaceSnapshotInterval');
      if (snap) snap.value = 120;
      const act = $('#newRaceActivate');
      if (act) act.checked = true;
      // Reset race-format controls to fixed-laps default.
      const fmt = $('#newRaceFormat');
      if (fmt) { fmt.value = 'laps'; applyRaceFormatVisibility(); }
      const perRider = $('#newRacePerRider');
      if (perRider) perRider.checked = false;
    });
  }

  // Toggle laps vs duration+final-laps fields based on the format selector.
  const fmtSel = $('#newRaceFormat');
  if (fmtSel) fmtSel.addEventListener('change', applyRaceFormatVisibility);

  const newRaceCancel = $('#newRaceCancelBtn');
  if (newRaceCancel) {
    newRaceCancel.addEventListener('click', () => {
      const modal = $('#newRaceModal');
      if (modal) modal.hidden = true;
    });
  }

  const newRaceSave = $('#newRaceSaveBtn');
  if (newRaceSave) {
    newRaceSave.addEventListener('click', async () => {
      const name = ($('#newRaceName')?.value || '').trim();
      const schedRaw = ($('#newRaceScheduled')?.value || '').trim();
      const totalLaps = parseInt($('#newRaceTotalLaps')?.value || '5', 10) || 5;
      const snapRaw = ($('#newRaceSnapshotInterval')?.value || '').trim();
      const snapshot_interval_s = snapRaw === '' ? null : Math.max(0, parseInt(snapRaw, 10) || 0);
      const activate = !!$('#newRaceActivate')?.checked;
      const errBox = $('#newRaceError');
      if (!name) {
        if (errBox) { errBox.textContent = RT.S.assistantRaceNameRequired; errBox.hidden = false; }
        return;
      }

      // Race format (F1/F2).
      const finish_mode = $('#newRacePerRider')?.checked ? 'per_rider' : 'leader';
      const isTimeBased = $('#newRaceFormat')?.value === 'time';
      const body = { name, total_laps: totalLaps, snapshot_interval_s, finish_mode };
      if (isTimeBased) {
        const mins = parseInt($('#newRaceDurationMin')?.value || '0', 10) || 0;
        const finalLaps = Math.max(0, parseInt($('#newRaceFinalLaps')?.value || '0', 10) || 0);
        if (mins <= 0) {
          if (errBox) { errBox.textContent = RT.S.raceDurationInvalid; errBox.hidden = false; }
          return;
        }
        body.duration_s = mins * 60;
        body.final_laps = finalLaps;
        // total_laps is unused in a time race, but the backend field is
        // required (ge=1); a large value keeps it out of the way.
        body.total_laps = 999;
      }

      // datetime-local gives "YYYY-MM-DDTHH:MM" \u2014 treat as UTC for simplicity
      // (operators on race day enter the local time of the event; we keep it
      // as-is and tag with Z so the backend parses it; race-day timezone
      // policy can be refined later).
      body.scheduled_at = schedRaw ? `${schedRaw}:00.000Z` : null;
      try {
        const res = await fetch(`${state.backend}/races`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...getApiHeaders() },
          body: JSON.stringify(body),
        });
        if (!res.ok) {
          const message = await rtResponseError(res, 'assistantRaceCreateFailed');
          if (errBox) { errBox.textContent = message; errBox.hidden = false; }
          return;
        }
        const created = await res.json();
        const modal = $('#newRaceModal');
        if (modal) modal.hidden = true;
        showToast(RT.fmt('toastRaceCreated', { name: created.name }));
        if (activate) {
          await activateRace(created.id);
        } else {
          await loadRaces();
        }
      } catch (e) {
        console.warn('Create race failed:', e);
        if (errBox) { errBox.textContent = RT.apiError(0, null, 'assistantRaceCreateFailed'); errBox.hidden = false; }
      }
    });
  }

  // End race button
  const endRaceBtn = $('#endRaceBtn');
  if (endRaceBtn) {
    endRaceBtn.addEventListener('click', async () => {
      if (!confirm(RT.S.confirmEndRace)) return;
      try {
        const res = await fetch(`${state.backend}/race/end`, {
          method: 'POST',
          headers: getApiHeaders(),
        });
        if (res.ok) {
          const data = await res.json();
          state.raceEnded = true;
          state.raceEndedAt = data.ended_at || null;
          renderRaceStatus();
          showToast(RT.S.toastRaceEnded);
        } else {
          showToast(await rtResponseError(res, 'raceEndFailed'), 'error');
        }
      } catch (e) {
        console.warn('End race failed:', e);
        showToast(RT.apiError(0, null, 'raceEndFailed'), 'error');
      }
    });
  }

  // Reopen race — undo an accidental End click. Passes recorded while the
  // race was wrongly ended are recovered (they were persisted all along).
  const reopenRaceBtn = $('#reopenRaceBtn');
  if (reopenRaceBtn) {
    reopenRaceBtn.addEventListener('click', async () => {
      if (!confirm(RT.S.confirmReopenRace)) return;
      try {
        const res = await fetch(`${state.backend}/race/reopen`, {
          method: 'POST',
          headers: getApiHeaders(),
        });
        if (res.ok) {
          state.raceEnded = false;
          state.raceEndedAt = null;
          renderRaceStatus();
          loadRaceConfig();
          loadSnapshot().catch(() => {});
          showToast(RT.S.toastRaceReopened);
        } else {
          showToast(await rtResponseError(res, 'raceReopenFailed'), 'error');
        }
      } catch (e) {
        console.warn('Reopen race failed:', e);
        showToast(RT.apiError(0, null, 'raceReopenFailed'), 'error');
      }
    });
  }

  // Export CSV buttons (results + tag inventory).
  //
  // In the packaged desktop app (pywebview/WKWebView) the usual Blob +
  // <a download> trick fails — WKWebView opens the CSV INSIDE the app
  // window instead of downloading it. So we first try the pywebview-
  // exposed Python API (`save_csv`), which pops a native macOS/Windows
  // save dialog. Fallback for a normal browser keeps the Blob+anchor
  // path so /classification.csv still works when opened directly.
  async function downloadCsvFromBackend(path, fallbackFilename) {
    try {
      const res = await fetch(`${state.backend}${path}`, {
        headers: getApiHeaders(),
      });
      if (!res.ok) {
        showToast(await rtResponseError(res, 'exportFailed'), 'error');
        return;
      }

      // Pull filename out of Content-Disposition if present.
      let filename = fallbackFilename;
      const cd = res.headers.get('content-disposition') || '';
      const m = cd.match(/filename\*?=(?:UTF-8'')?"?([^";\n]+)"?/i);
      if (m && m[1]) {
        try { filename = decodeURIComponent(m[1]); }
        catch { filename = m[1]; }
      }

      // res.text() strips the backend's UTF-8 BOM while decoding; put it
      // back so German Excel detects the encoding when opening the file.
      let csvText = await res.text();
      if (!csvText.startsWith('\ufeff')) csvText = '\ufeff' + csvText;

      // Path 1: pywebview's native save dialog (desktop app).
      if (window.pywebview && window.pywebview.api && window.pywebview.api.save_csv) {
        const saved = await window.pywebview.api.save_csv(csvText, filename);
        if (saved) {
          showToast(RT.fmt('toastExported', { filename }));
        } else {
          showToast(RT.S.toastExportCancelled);
        }
        return;
      }

      // Path 2: fallback for plain browsers — Blob + <a download>.
      const blob = new Blob([csvText], { type: 'text/csv;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 0);
      showToast(RT.fmt('toastExported', { filename }));
    } catch (e) {
      console.warn('CSV export failed:', e);
      showToast(RT.apiError(0, null, 'exportFailed'), 'error');
    }
  }

  const exportBtn = $('#exportCsvBtn');
  if (exportBtn) {
    exportBtn.addEventListener('click', () =>
      downloadCsvFromBackend('/classification.csv', 'racetag-export.csv'));
  }

  // Tag inventory (Karlie-Krit prep): every tag read in the active race,
  // first-read order, as an import-ready start-list template.
  const exportTagsBtn = $('#exportTagsBtn');
  if (exportTagsBtn) {
    exportTagsBtn.addEventListener('click', () =>
      downloadCsvFromBackend('/tags.csv', 'racetag-tags.csv'));
  }

  // CSV file upload handler (W-013: now POSTs to /riders)
  $('#csvFile').addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      importCSVToBackend(decodeCsvBytes(ev.target.result)).catch((err) => {
        console.error('CSV import error:', err);
        setStatus(RT.S.csvImportFailed);
      });
    };
    reader.onerror = () => setStatus(RT.S.csvReadFailed);
    reader.readAsArrayBuffer(file);
    // Reset so re-selecting same file triggers change event again
    e.target.value = '';
  });

  $('#importCsvBtn').addEventListener('click', () => $('#csvFile').click());

  // Toggle import errors collapsible
  const errToggle = $('#importErrorsToggle');
  if (errToggle) {
    errToggle.addEventListener('click', () => {
      const errList = $('#importErrorList');
      if (errList) errList.hidden = !errList.hidden;
    });
  }

  // W-012: "Couple tag → rider" button
  const coupleBtn = $('#coupleTagBtn');
  if (coupleBtn) {
    coupleBtn.addEventListener('click', async () => {
      // W-075: the serial panel owns tag handling while it is open.
      if (couple.active) {
        showToast(RT.S.toastCoupleModeActive);
        return;
      }
      state.awaitingRead = true;
      setStatus(RT.S.holdTagNearAntenna);

      // If we already have a cached unknown tag, open modal immediately
      if (state.lastUnknownTag) {
        openRegisterModal(state.lastUnknownTag.tag_id);
        return;
      }

      // Also try fetching from the ring buffer in case of a recent read
      // that arrived before the SSE connection was established
      await fetchRecentUnknownTag();
      if (state.lastUnknownTag) {
        openRegisterModal(state.lastUnknownTag.tag_id);
      }
    });
  }

  // Start race button (explicit-start model)
  const startRaceBtn = $('#startRaceBtn');
  if (startRaceBtn) {
    startRaceBtn.addEventListener('click', async () => {
      try {
        const res = await fetch(`${state.backend}/race/start`, {
          method: 'POST',
          headers: getApiHeaders(),
        });
        if (res.ok) {
          const data = await res.json();
          state.raceStarted = true;
          state.raceStartedAt = data.started_at || null;
          renderRaceStatus();
          showToast(RT.S.toastRaceStarted);
        } else {
          showToast(await rtResponseError(res, 'raceStartFailed'), 'error');
        }
      } catch (err) {
        console.warn('Start race failed:', err);
        showToast(RT.apiError(0, null, 'raceStartFailed'), 'error');
      }
    });
  }

  // W-036: Reset race button
  const resetRaceBtn = $('#resetRaceBtn');
  if (resetRaceBtn) {
    resetRaceBtn.addEventListener('click', async () => {
      if (!confirm(RT.S.confirmResetRace)) return;
      try {
        const res = await fetch(`${state.backend}/race/reset`, {
          method: 'POST',
          headers: getApiHeaders(),
        });
        if (res.ok) {
          renderStandings([]);
          state.raceStarted = false;
          state.raceStartedAt = null;
          state.raceEnded = false;
          state.raceEndedAt = null;
          renderRaceStatus();
          showToast(RT.S.toastRaceReset);
        } else {
          showToast(await rtResponseError(res, 'raceResetFailed'), 'error');
        }
      } catch (err) {
        console.warn('Reset race failed:', err);
        showToast(RT.apiError(0, null, 'raceResetFailed'), 'error');
      }
    });
  }

  // W-036: Apply total laps button
  const applyLapsBtn = $('#applyLapsBtn');
  if (applyLapsBtn) {
    applyLapsBtn.addEventListener('click', async () => {
      const input = $('#totalLapsInput');
      const n = parseInt(input?.value ?? '', 10);
      if (!n || n < 1 || n > 999) {
        showToast(RT.S.assistantRaceLapsInvalid, 'warn');
        return;
      }
      try {
        const res = await fetch(`${state.backend}/race`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json', ...getApiHeaders() },
          body: JSON.stringify({ total_laps: n }),
        });
        if (res.ok) {
          state.totalLaps = n;
          renderStandings(state.lastStandings);
          showToast(RT.fmt('toastTotalLapsSet', { n }));
        } else {
          showToast(await rtResponseError(res, 'totalLapsSaveFailed'), 'error');
        }
      } catch (err) {
        console.warn('Set total laps failed:', err);
        showToast(RT.apiError(0, null, 'totalLapsSaveFailed'), 'error');
      }
    });
  }

  // W-051: Diagnostics panel — poll every 5 s while open
  const diagnosticsPanel = $('#diagnosticsPanel');
  let _diagnosticsInterval = null;
  if (diagnosticsPanel) {
    diagnosticsPanel.addEventListener('toggle', () => {
      if (diagnosticsPanel.open) {
        refreshDiagnostics();
        _diagnosticsInterval = setInterval(() => {
          if (diagnosticsPanel.open) {
            refreshDiagnostics();
          }
        }, 5000);
      } else {
        clearInterval(_diagnosticsInterval);
        _diagnosticsInterval = null;
      }
    });
  }

  // W-074: Settings button + modal
  const settingsBtn = $('#settingsBtn');
  if (settingsBtn) settingsBtn.addEventListener('click', openSettingsModal);

  const settingsSaveBtn = $('#settingsSaveBtn');
  if (settingsSaveBtn) settingsSaveBtn.addEventListener('click', submitSettingsModal);

  const settingsCancelBtn = $('#settingsCancelBtn');
  if (settingsCancelBtn) settingsCancelBtn.addEventListener('click', closeSettingsModal);

  // Close settings modal on backdrop click
  const settingsModal = $('#settingsModal');
  if (settingsModal) {
    settingsModal.addEventListener('click', (e) => {
      if (e.target === settingsModal) closeSettingsModal();
    });
  }

  // C4: reader discovery + reconnect; D6: desktop support tooling
  const readerDiscoverBtn = $('#readerDiscoverBtn');
  if (readerDiscoverBtn) {
    readerDiscoverBtn.addEventListener('click', () =>
      runReaderDiscovery('#readerDiscoverBtn', '#readerDiscoverStatus', '#readerCandidates'));
  }
  const readerRestartBtn = $('#readerRestartBtn');
  if (readerRestartBtn) readerRestartBtn.addEventListener('click', restartReader);
  ['#readerCandidates', '#assistantCandidates'].forEach((sel) => {
    const list = $(sel);
    if (list) list.addEventListener('click', onCandidateListClick);
  });
  const openDataFolderBtn = $('#openDataFolderBtn');
  if (openDataFolderBtn) openDataFolderBtn.addEventListener('click', openDataFolder);
  const supportBundleBtn = $('#supportBundleBtn');
  if (supportBundleBtn) supportBundleBtn.addEventListener('click', createSupportBundle);

  // D5: first-run assistant
  const assistantOpenBtn = $('#assistantOpenBtn');
  if (assistantOpenBtn) assistantOpenBtn.addEventListener('click', openAssistant);
  const assistantSkipBtn = $('#assistantSkipBtn');
  if (assistantSkipBtn) assistantSkipBtn.addEventListener('click', closeAssistant);
  const assistantBackBtn = $('#assistantBackBtn');
  if (assistantBackBtn) assistantBackBtn.addEventListener('click', () => assistantGoTo(assistantUi.step - 1));
  const assistantNextBtn = $('#assistantNextBtn');
  if (assistantNextBtn) assistantNextBtn.addEventListener('click', assistantNext);
  const assistantDiscoverBtn = $('#assistantDiscoverBtn');
  if (assistantDiscoverBtn) {
    assistantDiscoverBtn.addEventListener('click', () =>
      runReaderDiscovery('#assistantDiscoverBtn', '#assistantDiscoverStatus', '#assistantCandidates'));
  }
  const assistantAntennaResetBtn = $('#assistantAntennaResetBtn');
  if (assistantAntennaResetBtn) {
    assistantAntennaResetBtn.addEventListener('click', startAntennaTest);
  }
  ['#assistantReuseKeep', '#assistantReuseNew'].forEach((sel) => {
    const el = $(sel);
    if (el) el.addEventListener('change', updateAssistantFinishLabel);
  });
  ['#assistantRaceName', '#assistantRaceLaps'].forEach((sel) => {
    const el = $(sel);
    if (el) el.addEventListener('keydown', (e) => { if (e.key === 'Enter') assistantCreateRace(); });
  });

  // Submit settings on Enter in modal inputs
  ['#settingsReaderIp', '#settingsMinLap', '#settingsTotalLaps', '#settingsSnapshotInterval', '#settingsAntennaPower'].forEach((sel) => {
    const el = $(sel);
    if (el) el.addEventListener('keydown', (e) => { if (e.key === 'Enter') submitSettingsModal(); });
  });

  // W-012: Modal buttons
  const saveBtn = $('#modalSaveBtn');
  if (saveBtn) saveBtn.addEventListener('click', submitRegisterModal);

  const cancelBtn = $('#modalCancelBtn');
  if (cancelBtn) cancelBtn.addEventListener('click', closeRegisterModal);

  const retryBtn = $('#modalRetryBtn');
  if (retryBtn) retryBtn.addEventListener('click', submitRegisterModal);

  // Submit on Enter in modal inputs
  ['#modalBib', '#modalName'].forEach((sel) => {
    const el = $(sel);
    if (el) el.addEventListener('keydown', (e) => { if (e.key === 'Enter') submitRegisterModal(); });
  });

  // W-075: serial coupling mode wiring
  const coupleModeBtn = $('#coupleModeBtn');
  if (coupleModeBtn) {
    coupleModeBtn.addEventListener('click', () => {
      if (couple.active) coupleModeOff();
      else coupleModeOn();
    });
  }
  const coupleCloseBtn = $('#coupleModeCloseBtn');
  if (coupleCloseBtn) coupleCloseBtn.addEventListener('click', coupleModeOff);
  const coupleSaveBtn = $('#coupleSaveBtn');
  if (coupleSaveBtn) coupleSaveBtn.addEventListener('click', coupleSave);
  const coupleSkipBtn = $('#coupleSkipBtn');
  if (coupleSkipBtn) coupleSkipBtn.addEventListener('click', coupleSkip);
  const recoupleBtn = $('#coupleRecoupleBtn');
  if (recoupleBtn) {
    recoupleBtn.addEventListener('click', () => {
      if (couple.phase === 'info' && couple.infoTag) {
        coupleArm(couple.infoTag.tag_id, {
          recouple: true,
          prefillBib: couple.infoTag.bib,
          prefillName: couple.infoTag.name,
        });
      }
    });
  }
  const beepToggle = $('#coupleBeepToggle');
  if (beepToggle) {
    beepToggle.addEventListener('change', (e) => {
      couple.muted = !e.target.checked;
      rtStorageSet('racetag.coupleBeep', couple.muted ? 'off' : 'on');
    });
  }
  ['#coupleBib', '#coupleName'].forEach((sel) => {
    const el = $(sel);
    if (!el) return;
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        coupleSave();
      } else if (e.key === 'Escape') {
        // First Escape clears the inputs, second skips the armed tag.
        if ($('#coupleBib').value || $('#coupleName').value) {
          $('#coupleBib').value = '';
          $('#coupleName').value = '';
          coupleClearWarn();
          coupleSetInputsEnabled(couple.phase === 'armed');
          $('#coupleBib').focus();
        } else {
          coupleSkip();
        }
      }
    });
  });
  const coupleBibInput = $('#coupleBib');
  if (coupleBibInput) {
    coupleBibInput.addEventListener('input', () => {
      coupleClearWarn(); // typing invalidates a pending duplicate-bib confirm
      $('#coupleSaveBtn').disabled = couple.phase !== 'armed' || !coupleBibInput.value.trim();
    });
  }

  // W-077: auto-assign loop wiring
  const autoStartBtn = $('#coupleAutoStartBtn');
  if (autoStartBtn) autoStartBtn.addEventListener('click', coupleAutoStart);
  const autoStopBtn = $('#coupleAutoStopBtn');
  if (autoStopBtn) {
    autoStopBtn.addEventListener('click', () => coupleAutoStop(RT.S.toastCoupleAutoStopped));
  }
  const autoRanges = $('#coupleAutoRanges');
  if (autoRanges) {
    autoRanges.addEventListener('keydown', (e) => { if (e.key === 'Enter') coupleAutoStart(); });
  }

  // W-076: rider editor wiring
  const ridersBtn = $('#ridersBtn');
  if (ridersBtn) ridersBtn.addEventListener('click', () => openRidersModal());
  const ridersCloseBtn = $('#ridersCloseBtn');
  if (ridersCloseBtn) ridersCloseBtn.addEventListener('click', closeRidersModal);
  const ridersModal = $('#ridersModal');
  if (ridersModal) {
    ridersModal.addEventListener('click', (e) => {
      if (e.target === ridersModal) closeRidersModal();
    });
  }
  const ridersSearch = $('#ridersSearch');
  if (ridersSearch) ridersSearch.addEventListener('input', renderRidersUiList);
  const ridersList = $('#ridersList');
  if (ridersList) {
    ridersList.addEventListener('click', (e) => {
      const li = e.target.closest('li[data-tag-id]');
      if (li) selectRiderForEdit(li.dataset.tagId);
    });
  }
  const riderEditSaveBtn = $('#riderEditSaveBtn');
  if (riderEditSaveBtn) riderEditSaveBtn.addEventListener('click', saveRiderEdit);
  ['#riderEditBib', '#riderEditName', '#riderEditVerein', '#riderEditUci'].forEach((sel) => {
    const el = $(sel);
    if (el) el.addEventListener('keydown', (e) => { if (e.key === 'Enter') saveRiderEdit(); });
  });
  // Double-click a standings row → edit that rider (fulfils the old
  // "edit tag/rider on double-click" TODO via the same editor).
  const standingsBody = $('#standingsTable tbody');
  if (standingsBody) {
    standingsBody.addEventListener('dblclick', (e) => {
      const row = e.target.closest('tr');
      const carrier = row && row.querySelector('[data-tag-id]');
      if (carrier) openRidersModal(carrier.dataset.tagId);
    });
  }

  // Close modal on backdrop click
  const modal = $('#registerModal');
  if (modal) {
    modal.addEventListener('click', (e) => {
      if (e.target === modal) closeRegisterModal();
    });
  }

  // Manual lap correction: delegated handler on the standings table body.
  const standingsTable = $('#standingsTable');
  if (standingsTable) standingsTable.addEventListener('click', onStandingsTableClick);

  // TT view: click the Net-time header to toggle net-time sorting.
  const netHeader = $('#netTimeHeader');
  function applyNetHeaderState() {
    if (!netHeader) return;
    const active = state.sortMode === 'net';
    netHeader.textContent = active ? RT.S.standingsNetTimeSorted : RT.S.standingsNetTime;
    netHeader.classList.toggle('sort-active', active);
  }
  if (netHeader) {
    netHeader.addEventListener('click', () => {
      state.sortMode = state.sortMode === 'net' ? 'official' : 'net';
      rtStorageSet('racetag.sortMode', state.sortMode);
      applyNetHeaderState();
      renderStandings(state.lastStandings || []);
      showToast(state.sortMode === 'net' ? RT.S.toastSortNet : RT.S.toastSortOfficial);
    });
    applyNetHeaderState();
  }

  // Lap-edit modal
  const lapEditAdd = $('#lapEditAddBtn');
  if (lapEditAdd) lapEditAdd.addEventListener('click', submitLapEditAdd);
  const lapEditRemove = $('#lapEditRemoveBtn');
  if (lapEditRemove) lapEditRemove.addEventListener('click', submitLapEditRemove);
  const lapEditReset = $('#lapEditResetBtn');
  if (lapEditReset) lapEditReset.addEventListener('click', submitLapEditReset);
  const lapEditCancel = $('#lapEditCancelBtn');
  if (lapEditCancel) lapEditCancel.addEventListener('click', closeLapEditModal);
  const lapEditModal = $('#lapEditModal');
  if (lapEditModal) {
    lapEditModal.addEventListener('click', (e) => {
      if (e.target === lapEditModal) closeLapEditModal();
    });
  }
  const lapEditTs = $('#lapEditTimestamp');
  if (lapEditTs) lapEditTs.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') submitLapEditAdd();
  });

  // F3: status buttons inside the lap-edit modal (DNF/DNS/DSQ/Clear).
  document.querySelectorAll('#lapEditModal .status-btn').forEach((b) => {
    b.addEventListener('click', async () => {
      const modal = $('#lapEditModal');
      if (!modal || !modal.dataset.tagId) return;
      const status = b.dataset.status || '';
      const result = await setRiderStatus(modal.dataset.tagId, status);
      if (result) {
        showToast(status
          ? RT.fmt('toastStatusSet', { status: status.toUpperCase() })
          : RT.S.toastStatusCleared);
        closeLapEditModal();
      }
    });
  });
}

document.addEventListener('DOMContentLoaded', async () => {
  init();
  renderStatusBar();
  // Desktop detection first: it decides which backend URL to talk to.
  await loadAppConfig();
  applyAppMode();
  renderSettingsMeta();
  await connectToBackend();
  setInterval(refreshDiagnostics, ANTENNA_POLL_MS);
  setInterval(renderStatusBar, STATUS_BAR_TICK_MS);
  if (appInfo.desktop) {
    loadDesktopAppInfo();
    maybeShowAssistant();
  }
  checkForUpdate();
});

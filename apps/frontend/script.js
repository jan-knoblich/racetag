const $ = (sel) => document.querySelector(sel);

// API key injected at runtime by Docker (placeholder replaced on container start)
const __RACETAG_API_KEY__ = "__RACETAG_FRONTEND_API_KEY__";
// Backend URL injected at runtime by Docker (placeholder replaced on container start)
const __RACETAG_BACKEND_URL__ = "__RACETAG_FRONTEND_BACKEND_URL__";

const isPlaceholder = (v) => typeof v === 'string' && v.startsWith('__RACETAG_');

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
    localStorage.getItem('racetag.backend')
    || (!isPlaceholder(__RACETAG_BACKEND_URL__) && __RACETAG_BACKEND_URL__)
    || (typeof window !== 'undefined' && window.location && window.location.origin)
    || 'http://localhost:8600',
  showTagColumn: true,
  lastStandings: [],
  // TT view: 'official' (backend order) or 'net' (sorted by net time).
  // Persisted so a mid-session app restart keeps the TT result view.
  sortMode: localStorage.getItem('racetag.sortMode') === 'net' ? 'net' : 'official',
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
    setStatus('CSV file is empty or has no data rows');
    return;
  }
  const delimName = delimiter === ';' ? 'semicolon' : delimiter === '\t' ? 'tab' : 'comma';

  // First row is header — skip it. Optional Stammdaten columns are matched
  // BY HEADER NAME so legacy templates (col 4 = "kategorie (Import
  // ignoriert)" etc.) keep their ignore-semantics untouched.
  const headerCells = rows[0].map((h) => h.trim().toLowerCase());
  const vereinIdx = headerCells.findIndex((h) => h === 'verein');
  const uciIdx = headerCells.findIndex((h) => h === 'uci_id' || h === 'uci-id');
  const dataRows = rows.slice(1).filter(r => r.some(cell => cell !== ''));
  const total = dataRows.length;
  if (total === 0) {
    setStatus('No data rows found in CSV');
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
        tag_id: (row[0] || `(row ${i + 2})`).slice(0, 40),
        reason: `only ${row.length} column(s) — expected tag_id,bib,name (detected ${delimName} delimiter)`,
      });
      continue;
    }
    const [tag_id, bib, name] = row;
    if (!tag_id) {
      errors.push({ tag_id: `(row ${i + 2})`, reason: 'empty tag_id' });
      continue;
    }
    // A tag-pool template row nobody filled in (no bib, no name) carries zero
    // information — skip it so an unfilled template imports cleanly instead
    // of coupling dozens of blank riders.
    if (!bib && !name) {
      skippedEmpty++;
      continue;
    }

    setStatus(`Importing ${i + 1}/${total} riders (${errors.length} errors)\u2026`);

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
        const errText = await res.text().catch(() => `HTTP ${res.status}`);
        errors.push({ tag_id, reason: `HTTP ${res.status}: ${errText}` });
      }
    } catch (err) {
      errors.push({ tag_id, reason: err.message });
    }
  }

  // Summary toast
  const skippedNote = skippedEmpty ? `, ${skippedEmpty} empty skipped` : '';
  showToast(`Imported ${imported}/${total} riders${skippedNote}${errors.length ? ` (${errors.length} errors)` : ''}`);
  setStatus(`Import complete: ${imported}/${total} riders${skippedNote}`);

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
      showToast(`Registered bib ${bib} \u2013 ${name}`);
    } else if (res.status === 401) {
      if (errBanner) {
        errBanner.textContent = 'Authorisation failed (401). Check API key configuration.';
        errBanner.hidden = false;
      }
      // Keep modal open — show Retry button
      const retryBtn = $('#modalRetryBtn');
      if (retryBtn) retryBtn.hidden = false;
    } else {
      if (errBanner) {
        errBanner.textContent = `Error ${res.status}. Please try again.`;
        errBanner.hidden = false;
      }
    }
  } catch (err) {
    if (errBanner) {
      errBanner.textContent = `Network error: ${err.message}`;
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
  muted: localStorage.getItem('racetag.coupleBeep') === 'off',
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
    ? '1 weiterer neuer Tag wartet'
    : `${couple.queue.length} weitere neue Tags warten`;
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
  coupleSetCard('waiting', 'Tag an die Antenne halten…', '', null);
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
    coupleSetCard('recouple', 'NEU KOPPELN', opts.prefillBib ? `Nr. ${opts.prefillBib}` : '', tagId);
    $('#coupleBib').value = opts.prefillBib || '';
    $('#coupleName').value = opts.prefillName || '';
  } else {
    coupleSetCard('new', 'NEUER TAG', 'Nummer eingeben', tagId);
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
  coupleSetCard(
    'known',
    'Bereits gekoppelt',
    `Nr. ${data.bib}${data.name ? ` – ${data.name}` : ''}`,
    data.tag_id,
  );
  $('#coupleRecoupleBtn').hidden = false;
  if (couple.infoTimer) clearTimeout(couple.infoTimer);
  couple.infoTimer = setTimeout(() => {
    if (couple.phase === 'info') coupleToIdle();
  }, COUPLE_INFO_CLEAR_MS);
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
      showToast(`Bereits gekoppelt: Nr. ${data.bib}${data.name ? ` – ${data.name}` : ''}`);
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
  li.innerHTML = `${hh}:${mm} · Nr. ${htmlEscape(bib)} – `
    + `${htmlEscape(name || '—')} · ${tagSpan}`;
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
    warn.textContent = `Nr. ${bib} ist bereits an ${holder.name || coupleShortTag(holder.tag_id)}`
      + ' vergeben – nochmal Enter/Speichern zum trotzdem Koppeln';
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
      $('#coupleCounter').textContent = `${couple.sessionCount} gekoppelt`;
      // Optimistic cache insert so the dup-bib guard and the queue staleness
      // check see the new rider immediately; full refresh in the background.
      const rider = { tag_id: tagId, bib, name };
      couple.ridersByTag.set(tagId, rider);
      couple.ridersByBib.set(bib, rider);
      refreshCoupleRiders();
      coupleBeep('known');
      couplePopQueue();
    } else {
      const txt = await res.text().catch(() => '');
      const warn = $('#coupleWarn');
      warn.textContent = res.status === 401
        ? 'Authorisation failed (401). Check API key configuration.'
        : `Fehler ${res.status}: ${txt.slice(0, 120)}`;
      warn.hidden = false;
    }
  } catch (err) {
    const warn = $('#coupleWarn');
    warn.textContent = `Netzwerkfehler: ${err.message}`;
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
    rangesInput.value = localStorage.getItem('racetag.autoRanges') || '';
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
    if (!window.confirm('Ungespeicherte Kopplung verwerfen?')) return;
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
  if (couple.auto.running) coupleAutoStop('Auto-Zuweisung gestoppt — Rennen gewechselt');
  couple.queue = [];
  const divider = document.createElement('li');
  divider.className = 'couple-log-divider';
  divider.textContent = '— Rennen gewechselt —';
  const log = $('#coupleLog');
  log.insertBefore(divider, log.firstChild);
  coupleToIdle();
  refreshCoupleRiders();
  showToast('Koppel-Modus: Rennen gewechselt');
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
  coupleSetCard('new', 'AUTO — TAG SCHWENKEN', `Nr. ${a.current}`, null);
  $('#coupleCardTag').textContent = `${a.done} vergeben · noch ${a.queue.length + 1} Nummern`;
}

function coupleAutoStart() {
  const raw = $('#coupleAutoRanges').value;
  const nums = parseNumberRanges(raw);
  if (!nums || !nums.length) {
    showToast('Zirkel unlesbar — Format: 1-75 oder 101-175,181-190');
    return;
  }
  localStorage.setItem('racetag.autoRanges', raw);
  const free = nums.filter((n) => !couple.ridersByBib.has(String(n)));
  if (!free.length) {
    showToast('Alle Nummern dieses Zirkels sind schon vergeben');
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
  showToast(`Auto-Zuweisung: ${free.length} freie Nummern`);
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
    coupleAutoStop(`Zirkel komplett: ${couple.auto.done} Nummern vergeben`);
  }
}

let _coupleAutoSaving = false;
async function onCoupleAutoTagSeen(data) {
  if (data.registered) {
    showToast(`Tag ist schon Nr. ${data.bib}${data.name ? ` – ${data.name}` : ''} — anderen Tag nehmen`);
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
      $('#coupleCounter').textContent = `${couple.sessionCount} gekoppelt`;
      const rider = { tag_id: tagId, bib, name: '' };
      couple.ridersByTag.set(tagId, rider);
      couple.ridersByBib.set(bib, rider);
      refreshCoupleRiders();
      coupleBeep('known'); // audible "saved — next number is up"
      coupleAutoAdvance();
    } else {
      showToast(`Fehler ${res.status} beim Koppeln von Nr. ${bib}`);
    }
  } catch (err) {
    showToast(`Netzwerkfehler: ${err.message}`);
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
    li.innerHTML = `<strong>Nr. ${htmlEscape(r.bib)}</strong> ${htmlEscape(r.name || '—')}`
      + ` <span class="riders-list-tag">${htmlEscape(coupleShortTag(r.tag_id))}</span>`;
    list.appendChild(li);
  }
  if (!filtered.length) {
    const li = document.createElement('li');
    li.className = 'riders-list-empty';
    li.textContent = ridersUi.items.length
      ? 'Kein Treffer' : 'Keine Fahrer im aktiven Rennen (Master importiert?)';
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
      showToast(`Nr. ${bib} – ${name || '—'} gespeichert${
        typeof n === 'number' ? ` (${n} Rennen)` : ''}`);
      err.hidden = true;
      refreshRidersUiList();
      if (couple.active) refreshCoupleRiders();
    } else {
      const txt = await res.text().catch(() => '');
      err.textContent = `Fehler ${res.status}: ${txt.slice(0, 120)}`;
      err.hidden = false;
    }
  } catch (e) {
    err.textContent = `Netzwerkfehler: ${e.message}`;
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

  try {
    const res = await fetch(`${state.backend}/config`, { headers: getApiHeaders() });
    if (!res.ok) throw new Error(`GET /config failed: ${res.status}`);
    const cfg = await res.json();
    _settingsOriginal = cfg;

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
          snapInput.placeholder = 'No active race';
        } else {
          const r2 = await fetch(`${state.backend}/races/${activeId}`, { headers: getApiHeaders() });
          if (r2.ok) {
            const raceRow = await r2.json();
            _settingsOriginal.snapshot_interval_s = raceRow.snapshot_interval_s ?? null;
            _settingsOriginal.active_race_id = activeId;
            snapInput.value = raceRow.snapshot_interval_s ?? '';
          } else {
            snapInput.disabled = true;
            snapInput.placeholder = 'Could not load race';
          }
        }
      } catch (_e) {
        snapInput.disabled = true;
        snapInput.placeholder = 'Network error';
      }
    }
  } catch (err) {
    _settingsOriginal = {};
    if (errBanner) {
      errBanner.textContent = `Could not load config: ${err.message}`;
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

  try {
    if (Object.keys(patch).length > 0) {
      const res = await fetch(`${state.backend}/config`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', ...getApiHeaders() },
        body: JSON.stringify(patch),
      });
      if (!res.ok) {
        const body = await res.text().catch(() => `HTTP ${res.status}`);
        if (errBanner) {
          errBanner.textContent = `Save failed (${res.status}): ${body}`;
          errBanner.hidden = false;
        }
        return;
      }
    }

    if (snapChanged && activeRaceId) {
      const r2 = await fetch(`${state.backend}/races/${activeRaceId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', ...getApiHeaders() },
        body: JSON.stringify({ snapshot_interval_s: newSnap }),
      });
      if (!r2.ok) {
        const body = await r2.text().catch(() => `HTTP ${r2.status}`);
        if (errBanner) {
          errBanner.textContent = `Snapshot interval save failed (${r2.status}): ${body}`;
          errBanner.hidden = false;
        }
        return;
      }
    }

    closeSettingsModal();
    showToast('Settings saved');

    // If reader IP / antenna power changed, show a non-blocking note via a
    // second toast with delay — both are consumed by the reader-service at
    // spawn time, so they take effect on the next app restart.
    if ('reader_ip' in patch || 'antenna_power' in patch) {
      setTimeout(() => showToast('Reader IP / antenna power changes take effect on next app restart'), 3200);
    }
  } catch (err) {
    if (errBanner) {
      errBanner.textContent = `Network error: ${err.message}`;
      errBanner.hidden = false;
    }
  }
}

// ---------------------------------------------------------------------------
// Toast notification (bottom-right, auto-dismiss after 3 s)
// ---------------------------------------------------------------------------
function showToast(message) {
  let toast = $('#toastContainer');
  if (!toast) return;
  toast.textContent = message;
  toast.classList.add('toast--visible');
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => toast.classList.remove('toast--visible'), 3000);
}

// ---------------------------------------------------------------------------
// Standings helpers
// ---------------------------------------------------------------------------
function setStatus(text) {
  $('#status').textContent = text;
}

function saveBackend(url) {
  state.backend = url.replace(/\/$/, '');
  localStorage.setItem('racetag.backend', state.backend);
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
      gap = `+${p.laps_behind} Rd.`;
    } else if (typeof p.gap_ms === 'number') {
      gap = formatMs(p.gap_ms);
    } else {
      gap = '';
    }
    // W-012: prefer bib/name from server standings; fall back to 'N/A'/'Unknown'
    const bibRaw = p.bib;
    const bib = (bibRaw != null && bibRaw !== '') ? htmlEscape(bibRaw) : 'N/A';
    const name = p.name ? htmlEscape(p.name) : 'Unknown';
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
    const statusBadge = status
      ? ` <span class="status-badge status-${status}">${status.toUpperCase()}</span>`
      : '';

    // F5: missed-read annotation. A warning marker on the row invites the
    // operator to fix it with the (pre-filled) manual +1 — never automatic.
    const missed = p.suspected_missed_reads || 0;
    const missedMarker = missed > 0
      ? ` <span class="missed-read" title="${missed} Runde(n) evtl. vom Reader verpasst — klick +1 zum Nachtragen">⚠︎${missed > 1 ? '×' + missed : ''}</span>`
      : '';

    // Manual-lap-correction buttons. Disabled when the row has NO registered
    // rider (bib null/undefined). An empty string OR the literal '0' is still
    // a valid bib — the explicit null-check guards bib zero. (Review #22.)
    const noRider = (bibRaw == null);
    const disabledAttr = noRider ? 'disabled' : '';
    const lapActions = `
      <div class="lap-actions">
        <button class="lap-plus" data-tag-id="${tagId}" data-action="add"
                title="Credit one lap (server timestamp)" ${disabledAttr}>+1</button>
        <button class="lap-minus" data-tag-id="${tagId}" data-action="remove"
                title="Revoke the most recent lap" ${disabledAttr}>&minus;1</button>
        <button class="lap-edit" data-tag-id="${tagId}" data-action="edit"
                title="Edit lap / set status (DNF/DNS/DSQ)" ${disabledAttr}>&#9998;</button>
      </div>`;
    // W-030: route last_pass_time through formatTimestampForDisplay
    tr.innerHTML = `
      <td>${posCell}</td>
      <td class="tag-col"><span class="tag-id-copyable" data-tag-id="${tagId}" title="Click to copy tag ID">${tagId}</span></td>
      <td>${bib}</td>
      <td>${name}${statusBadge}</td>
      <td>${p.laps}${missedMarker}</td>
      <td class="${p.finished ? 'finished' : ''}">${p.finished ? 'Yes' : 'No'}</td>
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
      const txt = await res.text();
      showToast(`Add-lap failed (${res.status}): ${txt}`);
      return null;
    }
    return await res.json();
  } catch (err) {
    showToast(`Add-lap network error: ${err.message}`);
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
      const txt = await res.text();
      showToast(`Remove-lap failed (${res.status}): ${txt}`);
      return null;
    }
    return await res.json();
  } catch (err) {
    showToast(`Remove-lap network error: ${err.message}`);
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
      const txt = await res.text();
      showToast(`Status failed (${res.status}): ${txt}`);
      return null;
    }
    return await res.json();
  } catch (err) {
    showToast(`Status network error: ${err.message}`);
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
    showToast(`Lap added — now ${result.laps} laps`);
    if (errBanner) errBanner.hidden = true;
    closeLapEditModal();
  }
}

async function submitLapEditRemove() {
  const modal = $('#lapEditModal');
  const errBanner = $('#lapEditError');
  if (!modal || !modal.dataset.tagId) return;
  if (!confirm('Remove the most recent lap for this rider?')) return;
  const result = await manualLapRemove(modal.dataset.tagId);
  if (result) {
    showToast(`Lap removed — now ${result.laps} laps`);
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
  if (!confirm(`ALLE Durchgänge von ${label} löschen? Der Fahrer startet danach einen frischen Versuch.`)) return;
  try {
    const res = await fetch(`${state.backend}/riders/${encodeURIComponent(tag_id)}/passes`, {
      method: 'DELETE',
      headers: getApiHeaders(),
    });
    if (!res.ok) {
      const txt = await res.text();
      showToast(`Reset failed (${res.status}): ${txt}`);
      return;
    }
    const data = await res.json();
    showToast(`${label} zurückgesetzt (${data.deleted_events} Durchgänge gelöscht)`);
    closeLapEditModal();
  } catch (err) {
    showToast(`Reset network error: ${err.message}`);
  }
}

function _bibLabelFor(tag_id) {
  const p = (state.lastStandings || []).find((r) => r.tag_id === tag_id);
  if (p && p.bib != null && p.bib !== '') return `bib ${p.bib}`;
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
      showToast('Vermutete verpasste Runde — Zeitstempel vorbelegt, bitte bestätigen');
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
        showToast('Last pass was a while ago — set the timestamp');
        openLapEditModal(tag_id);
        return;
      }
    }
    const result = await manualLapAdd(tag_id);
    if (result) showToast(`Lap added — ${label} now ${result.laps} laps`);
  } else if (action === 'remove') {
    if (!confirm(`Remove the most recent lap for ${label}?`)) return;
    const result = await manualLapRemove(tag_id);
    if (result) showToast(`Lap removed — ${label} now ${result.laps} laps`);
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
    ? `${state.activeRaceName} — `
    : '';

  // F8: bell / laps-to-go suffix for the leader while the race is live.
  let ltgSuffix = '';
  if (state.finishing) {
    ltgSuffix = ' — 🏁 LAST LAP / finishing';
  } else if (typeof state.lapsToGo === 'number') {
    if (state.lapsToGo === 1) ltgSuffix = ' — 🔔 1 lap to go';
    else if (state.lapsToGo > 0) ltgSuffix = ` — ${state.lapsToGo} laps to go`;
  }

  const reopenBtn = $('#reopenRaceBtn');
  if (state.raceEnded && state.raceEndedAt) {
    const t = formatTimestampForDisplay(state.raceEndedAt);
    if (banner) banner.textContent = `${racePrefix}Ended at ${t}`;
    if (startBtn) { startBtn.disabled = true; startBtn.textContent = 'Race ended'; }
    if (endBtn) { endBtn.disabled = true; endBtn.textContent = 'Ended'; }
    if (reopenBtn) reopenBtn.hidden = false;
  } else if (state.raceStarted && state.raceStartedAt) {
    const t = formatTimestampForDisplay(state.raceStartedAt);
    if (banner) banner.textContent = `${racePrefix}Running since ${t}${ltgSuffix}`;
    if (startBtn) { startBtn.disabled = true; startBtn.textContent = 'Race started'; }
    if (endBtn) { endBtn.disabled = false; endBtn.textContent = 'End race'; }
    if (reopenBtn) reopenBtn.hidden = true;
  } else {
    if (banner) banner.textContent = `${racePrefix}Not started — press Start to begin`;
    if (startBtn) { startBtn.disabled = false; startBtn.textContent = 'Start race'; }
    if (endBtn) { endBtn.disabled = true; endBtn.textContent = 'End race'; }
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
      opt.textContent = `${r.name}${schedSuffix}`;
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
      showToast(`Activate failed: HTTP ${res.status}`);
      return;
    }
    showToast('Race switched');
    // After activate, re-pull everything that's race-scoped
    await Promise.all([
      loadRaces(),
      loadRaceConfig(),
      loadSnapshot(),
    ]);
  } catch (e) {
    showToast(`Activate error: ${e.message}`);
  }
}

// W-051: fetch antenna diagnostics and render into diagnostics table
async function refreshDiagnostics() {
  try {
    const res = await fetch(`${state.backend}/diagnostics/antennas?window_s=60`, {
      headers: getApiHeaders(),
    });
    if (!res.ok) return;
    const data = await res.json();
    const tbody = document.querySelector('#diagnosticsTable tbody');
    if (!tbody) return;
    tbody.innerHTML = '';
    const counts = data.counts || {};
    const antennas = Object.keys(counts).sort((a, b) => Number(a) - Number(b));
    if (antennas.length === 0) {
      const tr = document.createElement('tr');
      tr.innerHTML = '<td colspan="2" style="color:var(--muted)">No reads in last 60 s</td>';
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
      setStatus('Live');
      loadRaces();
      loadRaceConfig();
      loadSnapshot().catch(() => {});
      // W-075: rider cache may have drifted during the outage window.
      if (couple.active) refreshCoupleRiders();
    },
    onError: () => {}, // status handled by onStatusChange
    onStatusChange: (msg) => setStatus(msg),
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

        // W-075: serial coupling mode — live tag feed (ignored unless open)
        if (data?.type === 'tag_seen') {
          onCoupleTagSeen(data);
        }

        // W-036: race reset — clear standings table locally
        if (data?.type === 'race_reset') {
          renderStandings([]);
          showToast('Race has been reset');
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
    const ok = (val) => showToast(`Tag ID copied: ${val.slice(0, 12)}…`);
    const fail = () => showToast('Copy failed');
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

  $('#connectBtn').addEventListener('click', async () => {
    saveBackend(input.value);
    setStatus('Connecting\u2026');
    try {
      await loadSnapshot();
      connectSSE();
      fetchRecentUnknownTag();
      loadRaceConfig();
      loadRaces();
    } catch (e) {
      console.error(e);
      setStatus('Failed to connect');
    }
  });

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
        if (errBox) { errBox.textContent = 'Race name is required'; errBox.hidden = false; }
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
          if (errBox) { errBox.textContent = 'Duration must be at least 1 minute'; errBox.hidden = false; }
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
          const txt = await res.text();
          if (errBox) { errBox.textContent = `Create failed: ${res.status} ${txt}`; errBox.hidden = false; }
          return;
        }
        const created = await res.json();
        const modal = $('#newRaceModal');
        if (modal) modal.hidden = true;
        showToast(`Race "${created.name}" created`);
        if (activate) {
          await activateRace(created.id);
        } else {
          await loadRaces();
        }
      } catch (e) {
        if (errBox) { errBox.textContent = `Error: ${e.message}`; errBox.hidden = false; }
      }
    });
  }

  // End race button
  const endRaceBtn = $('#endRaceBtn');
  if (endRaceBtn) {
    endRaceBtn.addEventListener('click', async () => {
      if (!confirm('End the race? Standings will be frozen.')) return;
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
          showToast('Race ended');
        } else {
          showToast(`End failed: HTTP ${res.status}`);
        }
      } catch (e) {
        showToast(`End error: ${e.message}`);
      }
    });
  }

  // Reopen race — undo an accidental End click. Passes recorded while the
  // race was wrongly ended are recovered (they were persisted all along).
  const reopenRaceBtn = $('#reopenRaceBtn');
  if (reopenRaceBtn) {
    reopenRaceBtn.addEventListener('click', async () => {
      if (!confirm('Reopen the race? Passes recorded while it was ended will be counted.')) return;
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
          showToast('Race reopened');
        } else {
          showToast(`Reopen failed: HTTP ${res.status}`);
        }
      } catch (e) {
        showToast(`Reopen error: ${e.message}`);
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
        showToast(`Export failed: HTTP ${res.status}`);
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
          showToast(`Exported: ${filename}`);
        } else {
          showToast('Export cancelled');
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
      showToast(`Exported: ${filename}`);
    } catch (e) {
      showToast(`Export error: ${e.message}`);
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
        setStatus('Error during CSV import');
      });
    };
    reader.onerror = () => setStatus('Error reading CSV file');
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
        showToast('Koppel-Modus ist aktiv — Panel benutzen');
        return;
      }
      state.awaitingRead = true;
      setStatus('Hold a tag near the antenna\u2026');

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
          showToast('Race started');
        } else {
          showToast(`Start failed: HTTP ${res.status}`);
        }
      } catch (err) {
        showToast(`Start error: ${err.message}`);
      }
    });
  }

  // W-036: Reset race button
  const resetRaceBtn = $('#resetRaceBtn');
  if (resetRaceBtn) {
    resetRaceBtn.addEventListener('click', async () => {
      if (!confirm('Reset race? All lap data will be cleared.')) return;
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
          showToast('Race reset');
        } else {
          showToast(`Reset failed: HTTP ${res.status}`);
        }
      } catch (err) {
        showToast(`Reset error: ${err.message}`);
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
        showToast('Total laps must be 1–999');
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
          showToast(`Total laps set to ${n}`);
        } else {
          showToast(`Failed: HTTP ${res.status}`);
        }
      } catch (err) {
        showToast(`Error: ${err.message}`);
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
      localStorage.setItem('racetag.coupleBeep', couple.muted ? 'off' : 'on');
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
    autoStopBtn.addEventListener('click', () => coupleAutoStop('Auto-Zuweisung gestoppt'));
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
    netHeader.textContent = active ? 'Net time ▲' : 'Net time';
    netHeader.classList.toggle('sort-active', active);
  }
  if (netHeader) {
    netHeader.addEventListener('click', () => {
      state.sortMode = state.sortMode === 'net' ? 'official' : 'net';
      localStorage.setItem('racetag.sortMode', state.sortMode);
      applyNetHeaderState();
      renderStandings(state.lastStandings || []);
      showToast(state.sortMode === 'net'
        ? 'Sortiert nach Netto-Zeit (TT-Ergebnis)'
        : 'Offizielle Reihenfolge (Runden + Zeit)');
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
          ? `Status gesetzt: ${status.toUpperCase()}`
          : 'Status entfernt');
        closeLapEditModal();
      }
    });
  });
}

document.addEventListener('DOMContentLoaded', async () => {
  init();
  // Auto-connect on load using stored backend URL
  try {
    setStatus('Connecting\u2026');
    await loadSnapshot();
    connectSSE();
    fetchRecentUnknownTag();
    loadRaceConfig();
    loadRaces();
  } catch (e) {
    console.warn('Auto-connect failed, please set backend URL and click Connect');
    setStatus('Disconnected');
  }
});

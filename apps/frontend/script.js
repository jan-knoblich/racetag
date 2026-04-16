const $ = (sel) => document.querySelector(sel);

// API key injected at runtime by Docker (placeholder replaced on container start)
const __RACETAG_API_KEY__ = "__RACETAG_FRONTEND_API_KEY__";
// Backend URL injected at runtime by Docker (placeholder replaced on container start)
const __RACETAG_BACKEND_URL__ = "__RACETAG_FRONTEND_BACKEND_URL__";

const isPlaceholder = (v) => typeof v === 'string' && v.startsWith('__RACETAG_');

const state = {
  backend:
    ( localStorage.getItem('racetag.backend')
    || !isPlaceholder(__RACETAG_BACKEND_URL__) && __RACETAG_BACKEND_URL__)
    || 'http://localhost:8600',
  showTagColumn: true,
  lastStandings: [],
  es: null,
  // W-012: registration flow state
  awaitingRead: false,
  lastUnknownTag: null, // { tag_id, timestamp } | null
};

// ---------------------------------------------------------------------------
// W-035 — Robust CSV tokenizer
// Handles: UTF-8 BOM, quoted fields with embedded commas, CRLF, "" → ",
// trailing blank lines. Zero external dependencies.
// ---------------------------------------------------------------------------
function parseCSVRobust(text) {
  // Strip UTF-8 BOM if present
  if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1);

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
        // Unquoted field — read until comma or end of line
        let field = '';
        while (i < len && text[i] !== ',' && text[i] !== '\n' && text[i] !== '\r') {
          field += text[i++];
        }
        row.push(field.trim());
      }

      // After a field: consume comma (continue row) or newline/end (end row)
      if (i < len && text[i] === ',') {
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

// ---------------------------------------------------------------------------
// W-013 — Bulk CSV import: POST each row to /riders
// Replaces the old browser-only tagData map approach.
// ---------------------------------------------------------------------------
async function importCSVToBackend(csvText) {
  const rows = parseCSVRobust(csvText);
  if (rows.length < 2) {
    setStatus('CSV file is empty or has no data rows');
    return;
  }

  // First row is header — skip it
  const dataRows = rows.slice(1).filter(r => r.some(cell => cell !== ''));
  const total = dataRows.length;
  if (total === 0) {
    setStatus('No data rows found in CSV');
    return;
  }

  const errors = []; // { tag_id, reason }
  let imported = 0;

  // Show errors container (hidden until there are errors)
  const errContainer = $('#importErrors');
  const errList = $('#importErrorList');
  if (errContainer) errContainer.hidden = true;
  if (errList) errList.innerHTML = '';

  for (let i = 0; i < total; i++) {
    const row = dataRows[i];
    if (row.length < 3) continue;
    const [tag_id, bib, name] = row;
    if (!tag_id) continue;

    setStatus(`Importing ${i + 1}/${total} riders (${errors.length} errors)\u2026`);

    try {
      const res = await fetch(`${state.backend}/riders`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getApiHeaders() },
        body: JSON.stringify({ tag_id, bib, name }),
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
  showToast(`Imported ${imported}/${total} riders${errors.length ? ` (${errors.length} errors)` : ''}`);
  setStatus(`Import complete: ${imported}/${total} riders`);

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

function renderStandings(items) {
  state.lastStandings = items;
  const tbody = $('#standingsTable tbody');
  tbody.innerHTML = '';
  items.forEach((p, idx) => {
    const gap = typeof p.gap_ms === 'number' ? formatMs(p.gap_ms) : '';
    // W-012: prefer bib/name from server standings; fall back to 'N/A'/'Unknown'
    const bib = p.bib || 'N/A';
    const name = p.name || 'Unknown';
    const tr = document.createElement('tr');
    const total = typeof p.total_time_ms === 'number' ? secondsWithMs(p.total_time_ms) : '';
    // W-030: route last_pass_time through formatTimestampForDisplay
    tr.innerHTML = `
      <td>${idx + 1}</td>
      <td class="tag-col">${p.tag_id}</td>
      <td>${bib}</td>
      <td>${name}</td>
      <td>${p.laps}</td>
      <td class="${p.finished ? 'finished' : ''}">${p.finished ? 'Yes' : 'No'}</td>
      <td>${formatTimestampForDisplay(p.last_pass_time)}</td>
      <td>${gap}</td>
      <td>${total}</td>
    `;
    tbody.appendChild(tr);
  });
  applyTagColumnVisibility();
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
    onOpen: () => setStatus('Live'),
    onError: () => {}, // status handled by onStatusChange
    onStatusChange: (msg) => setStatus(msg),
    onMessage: (ev) => {
      try {
        const data = JSON.parse(ev.data);

        if (data?.type === 'standings') {
          renderStandings(data.items || []);
        }

        // W-012: handle unknown_tag SSE event
        if (data?.type === 'unknown_tag') {
          state.lastUnknownTag = { tag_id: data.tag_id, timestamp: data.timestamp };
          if (state.awaitingRead) {
            openRegisterModal(data.tag_id);
          }
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

  $('#connectBtn').addEventListener('click', async () => {
    saveBackend(input.value);
    setStatus('Connecting\u2026');
    try {
      await loadSnapshot();
      connectSSE();
      fetchRecentUnknownTag();
    } catch (e) {
      console.error(e);
      setStatus('Failed to connect');
    }
  });

  // CSV file upload handler (W-013: now POSTs to /riders)
  $('#csvFile').addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      importCSVToBackend(ev.target.result).catch((err) => {
        console.error('CSV import error:', err);
        setStatus('Error during CSV import');
      });
    };
    reader.onerror = () => setStatus('Error reading CSV file');
    reader.readAsText(file);
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

  // Close modal on backdrop click
  const modal = $('#registerModal');
  if (modal) {
    modal.addEventListener('click', (e) => {
      if (e.target === modal) closeRegisterModal();
    });
  }
}

document.addEventListener('DOMContentLoaded', async () => {
  init();
  // Auto-connect on load using stored backend URL
  try {
    setStatus('Connecting\u2026');
    await loadSnapshot();
    connectSSE();
    fetchRecentUnknownTag();
  } catch (e) {
    console.warn('Auto-connect failed, please set backend URL and click Connect');
    setStatus('Disconnected');
  }
});

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
  es: null,
  standingsByTag: new Map(),
};

// Mapping from tag_id to {bib, name}
const tagData = new Map();

// Parsing CSV and load tag data
function parseCSV(csvText) {
  const lines = csvText.trim().split('\n');
  if (lines.length < 2) {
    console.warn('CSV file is empty or has no data rows');
    return;
  }
  
  // Skip header line
  for (let i = 1; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) continue;
    
    // Parse CSV line (handle quoted fields if needed)
    const parts = line.split(',').map(p => p.trim().replace(/^"|"$/g, ''));
    if (parts.length >= 3) {
      const [tag_id, bib, name] = parts;
      tagData.set(tag_id, { bib, name });
    }
  }
  
  console.log(`Loaded ${tagData.size} tag mappings from CSV`);
  setStatus(`Loaded ${tagData.size} tags`);
}

// Load CSV file
function loadCSVFile(file) {
  const reader = new FileReader();
  reader.onload = (e) => {
    try {
      parseCSV(e.target.result);
    } catch (err) {
      console.error('Error parsing CSV:', err);
      setStatus('Error loading CSV file');
    }
  };
  reader.onerror = () => {
    console.error('Error reading file');
    setStatus('Error reading CSV file');
  };
  reader.readAsText(file);
}

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
  const tbody = $('#standingsTable tbody');
  tbody.innerHTML = '';
  items.forEach((p, idx) => {
    const gap = typeof p.gap_ms === 'number' ? formatMs(p.gap_ms) : '';
    const data = tagData.get(p.tag_id);
    const bib = data?.bib || 'N/A';
    const name = data?.name || 'Unknown';
    const tr = document.createElement('tr');
    const total = typeof p.total_time_ms === 'number' ? secondsWithMs(p.total_time_ms) : '';
    tr.innerHTML = `
      <td>${idx + 1}</td>
      <td class="tag-col">${p.tag_id}</td>
      <td>${bib}</td>
      <td>${name}</td>
      <td>${p.laps}</td>
      <td class="${p.finished ? 'finished' : ''}">${p.finished ? 'Yes' : 'No'}</td>
      <td>${p.last_pass_time || ''}</td>
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
  const s = (ms / 1000);
  // Always 3 decimals for ms
  return s.toFixed(3);
}

async function loadSnapshot() {
  const url = `${state.backend}/classification`;
  const headers = getApiHeaders();
  const res = await fetch(url, { headers });
  if (!res.ok) throw new Error(`GET /classification failed: ${res.status}`);
  const data = await res.json();
  renderStandings(data.standings || []);
}

function connectSSE() {
  const url = `${state.backend}/stream`;
  if (state.es) state.es.close();
  state.es = connectSSEWithHeaders(url, getApiHeaders(), {
    onOpen: () => setStatus('Connected'),
    onError: () => setStatus('Connection error'),
    onMessage: (ev) => {
      try {
        const data = JSON.parse(ev.data);
        if (data?.type === 'standings') {
          renderStandings(data.items || []);
        }
      } catch {
        // ignore non-JSON payloads
      }
    },
  });
}

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
    setStatus('Connecting...');
    try {
      await loadSnapshot();
      connectSSE();
    } catch (e) {
      console.error(e);
      setStatus('Failed to connect');
    }
  });
  
  // CSV file upload handler
  $('#csvFile').addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (file) {
      loadCSVFile(file);
    }
  });
  
  // Import CSV button triggers file input
  $('#importCsvBtn').addEventListener('click', () => {
    $('#csvFile').click();
  });
}

document.addEventListener('DOMContentLoaded', async () => {
  init();
  // Auto-connect on load using stored backend URL
  try {
    setStatus('Connecting...');
    await loadSnapshot();
    connectSSE();
  } catch (e) {
    console.warn('Auto-connect failed, please set backend URL and click Connect');
    setStatus('Disconnected');
  }
});


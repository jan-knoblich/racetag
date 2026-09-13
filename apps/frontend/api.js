// Build headers for API requests using a runtime-injected placeholder token.
function getApiHeaders() {
  const KEY = "__RACETAG_FRONTEND_API_KEY__"; // replaced at container start
  const isPlaceholder = (v) => typeof v === 'string' && v.startsWith('__RACETAG_');
  const headers = {};
  if (KEY && !isPlaceholder(KEY)) {
    headers['X-API-Key'] = KEY;
  }
  return headers;
}

// Stall watchdog: the backend sends a ": keepalive" comment after 15 s without
// data and a reader_status frame at least every 5 s. A stream that delivers no
// bytes at all for this long is dead (e.g. a half-open TCP connection after a
// Wi-Fi drop) even though read() never ends, so it is aborted and reopened.
const SSE_STALL_TIMEOUT_MS = 40000;

// SSE polyfill using fetch to allow sending custom headers.
// W-034: wraps connect logic in scheduleReconnect with exponential backoff.
//
// Connection state is reported two ways:
//   onStateChange(state, detail) — structured, drives the "Verbindung" pill:
//     'connecting'   {attempt}          a fetch to the stream is in flight
//     'live'         {}                 stream open, frames flowing
//     'reconnecting' {delayS, attempt}  stream failed/closed/stalled, retry scheduled
//     (`attempt` counts consecutive failures since the last successful open)
//   onStatusChange(text) — the same state as a ready-made German text.
// `stallTimeoutMs` (default SSE_STALL_TIMEOUT_MS, 0 disables) bounds the time
// without any received byte — keepalive comments included — before an attempt
// is aborted; it also bounds a fetch that never gets response headers.
function connectSSEWithHeaders(url, headers, {
  onOpen, onMessage, onError, onStatusChange, onStateChange, stallTimeoutMs = SSE_STALL_TIMEOUT_MS,
} = {}) {
  let closed = false;
  let reconnectDelay = 1000; // ms; reset to 1000 on successful open
  let reconnectTimer = null;
  let failures = 0;
  let abortCurrent = null; // aborts the attempt in flight (fetch + body)

  function statusText(state, detail) {
    if (!window.RT) return '';
    if (state === 'live') return RT.S.connLive;
    if (state === 'reconnecting') return RT.fmt('connReconnectingIn', { s: detail.delayS });
    return RT.S.connConnecting;
  }

  function notifyState(state, detail = {}) {
    onStateChange && onStateChange(state, detail);
    onStatusChange && onStatusChange(statusText(state, detail));
  }

  function scheduleReconnect() {
    if (closed) return;
    failures += 1;
    notifyState('reconnecting', { delayS: Math.round(reconnectDelay / 1000), attempt: failures });
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      if (!closed) connect();
    }, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, 15000);
  }

  function connect() {
    if (closed) return;
    const controller = new AbortController();
    let stallTimer = null;
    let finished = false; // this attempt has ended and scheduled its retry

    function clearStall() {
      if (stallTimer) clearTimeout(stallTimer);
      stallTimer = null;
    }

    function armStall() {
      clearStall();
      if (stallTimeoutMs > 0) {
        stallTimer = setTimeout(() => fail(new Error('SSE stalled: no data received')), stallTimeoutMs);
      }
    }

    // Ends this attempt exactly once: the abort below makes the pending
    // fetch/read() reject as well, which must not schedule a second retry.
    function fail(err) {
      if (finished) return;
      finished = true;
      clearStall();
      if (abortCurrent === abortAttempt) abortCurrent = null;
      controller.abort();
      if (closed) return;
      if (err) onError && onError(err);
      scheduleReconnect();
    }

    function abortAttempt() {
      finished = true;
      clearStall();
      controller.abort();
    }
    abortCurrent = abortAttempt;

    notifyState('connecting', { attempt: failures });
    armStall();

    fetch(url, {
      method: 'GET',
      headers: { 'Accept': 'text/event-stream', 'Cache-Control': 'no-cache', ...headers },
      signal: controller.signal,
    })
      .then((res) => {
        if (finished) return;
        if (!res.ok || !res.body) throw new Error(`SSE failed: ${res.status}`);
        // Successful connection — reset backoff delay and notify open
        reconnectDelay = 1000;
        failures = 0;
        armStall();
        notifyState('live');
        onOpen && onOpen();
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = '';
        function read() {
          reader.read().then(({ done, value }) => {
            if (finished) return;
            if (done) {
              // Stream closed by server — schedule reconnect
              fail(null);
              return;
            }
            armStall(); // any bytes, keepalive comments included, prove liveness
            buf += decoder.decode(value, { stream: true });
            let idx;
            while ((idx = buf.indexOf('\n\n')) !== -1) {
              const rawEvent = buf.slice(0, idx);
              buf = buf.slice(idx + 2);
              const lines = rawEvent.split('\n');
              const dataLines = lines.filter(l => l.startsWith('data:'));
              if (dataLines.length) {
                const data = dataLines.map(l => l.slice(5).trim()).join('\n');
                onMessage && onMessage({ data });
              }
            }
            read();
          }).catch((err) => {
            fail(err);
          });
        }
        read();
      })
      .catch((err) => {
        fail(err);
      });
  }

  connect();

  return {
    close() {
      closed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      reconnectTimer = null;
      if (abortCurrent) abortCurrent();
      abortCurrent = null;
    },
  };
}

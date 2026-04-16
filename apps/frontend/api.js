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

// SSE polyfill using fetch to allow sending custom headers.
// W-034: wraps connect logic in scheduleReconnect with exponential backoff.
function connectSSEWithHeaders(url, headers, { onOpen, onMessage, onError, onStatusChange } = {}) {
  const controller = new AbortController();
  const signal = controller.signal;
  let closed = false;
  let reconnectDelay = 1000; // ms; reset to 1000 on successful open
  let reconnectTimer = null;

  function notifyStatus(msg) {
    onStatusChange && onStatusChange(msg);
  }

  function scheduleReconnect() {
    if (closed) return;
    notifyStatus(`Reconnecting in ${Math.round(reconnectDelay / 1000)} s\u2026`);
    reconnectTimer = setTimeout(() => {
      if (!closed) connect();
    }, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, 15000);
  }

  function connect() {
    if (closed) return;

    fetch(url, {
      method: 'GET',
      headers: { 'Accept': 'text/event-stream', 'Cache-Control': 'no-cache', ...headers },
      signal,
    })
      .then((res) => {
        if (!res.ok || !res.body) throw new Error(`SSE failed: ${res.status}`);
        // Successful connection — reset backoff delay and notify open
        reconnectDelay = 1000;
        onOpen && onOpen();
        notifyStatus('Live');
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = '';
        function read() {
          reader.read().then(({ done, value }) => {
            if (done) {
              // Stream closed by server — schedule reconnect
              scheduleReconnect();
              return;
            }
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
            if (closed) return;
            onError && onError(err);
            scheduleReconnect();
          });
        }
        read();
      })
      .catch((err) => {
        if (closed) return;
        onError && onError(err);
        scheduleReconnect();
      });
  }

  connect();

  return {
    close() {
      closed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      controller.abort();
    },
  };
}

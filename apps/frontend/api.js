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

// SSE polyfill using fetch to allow sending headers
function connectSSEWithHeaders(url, headers, { onOpen, onMessage, onError } = {}) {
  const controller = new AbortController();
  const signal = controller.signal;

  fetch(url, {
    method: 'GET',
    headers: { 'Accept': 'text/event-stream', 'Cache-Control': 'no-cache', ...headers },
    signal,
  })
    .then((res) => {
      if (!res.ok || !res.body) throw new Error(`SSE failed: ${res.status}`);
      onOpen && onOpen();
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      function read() {
        reader.read().then(({ done, value }) => {
          if (done) return;
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
        }).catch((err) => onError && onError(err));
      }
      read();
    })
    .catch((err) => onError && onError(err));

  return {
    close() { controller.abort(); },
  };
}

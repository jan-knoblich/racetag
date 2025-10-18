from __future__ import annotations

import json
import queue
import threading
import time
from typing import Optional

from models import TagEvent
from utils import _ts
from .base import BackendClient

try:
    import requests
except Exception:  # pragma: no cover
    requests = None


class HttpBackendClient(BackendClient):
    def __init__(self, url: str, token: Optional[str] = None, batch_size: int = 10, flush_interval_ms: int = 50, queue_maxsize: int = 10000):
        self.url = url.rstrip("/")
        self.token = token
        self.batch_size = max(1, batch_size)
        self.flush_interval_ms = flush_interval_ms
        self._q: "queue.Queue[TagEvent]" = queue.Queue(maxsize=queue_maxsize)
        self._t: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        if requests is None:
            print(f"[{_ts()}] [BACKEND] 'requests' not available; HTTP client disabled")
            return
        self._stop.clear()
        self._t = threading.Thread(target=self._worker, daemon=True)
        self._t.start()
        print(f"[{_ts()}] [BACKEND] HTTP client started -> {self.url}")

    def stop(self) -> None:
        self._stop.set()
        if self._t and self._t.is_alive():
            self._t.join(timeout=1.5)
        print(f"[{_ts()}] [BACKEND] HTTP client stopped")

    def send(self, event: TagEvent) -> None:
        try:
            self._q.put_nowait(event)
        except queue.Full:
            print(f"[{_ts()}] [BACKEND] Queue full; dropping event tag={event.tag_id}")

    def _worker(self) -> None:
        session = requests.Session()
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        buf = []
        last_flush = time.monotonic()
        endpoint = f"{self.url}/events/tag/batch"

        while not self._stop.is_set():
            timeout = max(0.0, (self.flush_interval_ms / 1000.0) - (time.monotonic() - last_flush))
            try:
                ev = self._q.get(timeout=timeout)
                buf.append(ev.to_payload())
                if len(buf) >= self.batch_size:
                    self._flush(session, headers, endpoint, buf)
                    buf.clear()
                    last_flush = time.monotonic()
            except queue.Empty:
                if buf:
                    self._flush(session, headers, endpoint, buf)
                    buf.clear()
                last_flush = time.monotonic()
        if buf:
            self._flush(session, headers, endpoint, buf)

    def _flush(self, session, headers, endpoint: str, items) -> None:
        try:
            payload = {"events": items}
            resp = session.post(endpoint, headers=headers, data=json.dumps(payload), timeout=2.0)
            if resp.status_code >= 300:
                print(f"[{_ts()}] [BACKEND] POST batch failed {resp.status_code}: {resp.text[:200]}")
                return
            # Validate processed count strictly using new DTO
            try:
                data = resp.json()
            except Exception as e:
                raise RuntimeError(f"[{_ts()}] [BACKEND] Expected JSON response, got parse error: {e}")
            if not isinstance(data, dict) or "events_processed" not in data:
                raise RuntimeError(f"[{_ts()}] [BACKEND] Invalid response: 'events_processed' missing: {data!r}")
            processed = int(data["events_processed"])
            if processed != len(items):
                msg = f"[{_ts()}] [BACKEND] Batch mismatch: sent={len(items)} processed={processed}"
                print(msg)
                raise RuntimeError(msg)
        except Exception as e:
            print(f"[{_ts()}] [BACKEND] POST batch error: {e}")


    # Mapping moved to TagEvent.to_payload()

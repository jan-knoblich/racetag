from __future__ import annotations

import json
import os
import queue
import threading
import time
from typing import List, Optional

from models import TagEvent
from utils import get_logger
from .base import BackendClient

logger = get_logger("reader.backend.http")

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

# Spool file path (relative to the CWD at runtime, i.e. the reader-service root)
_SPOOL_PATH = os.path.join("logs", "spool.jsonl")

# Retry back-off delays in seconds
_RETRY_DELAYS = [0.2, 0.5, 1.0]


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
            logger.warning("[BACKEND] 'requests' not available; HTTP client disabled")
            return
        self._stop.clear()
        self._t = threading.Thread(target=self._worker, daemon=True)
        self._t.start()
        logger.info("[BACKEND] HTTP client started -> %s", self.url)

    def stop(self) -> None:
        self._stop.set()
        if self._t and self._t.is_alive():
            self._t.join(timeout=1.5)
        logger.info("[BACKEND] HTTP client stopped")

    def send(self, event: TagEvent) -> None:
        try:
            self._q.put_nowait(event)
        except queue.Full:
            logger.warning("[BACKEND] Queue full; dropping event tag=%s", event.tag_id)

    # ------------------------------------------------------------------
    # Internal worker
    # ------------------------------------------------------------------

    def _worker(self) -> None:
        session = requests.Session()
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-API-Key"] = str(self.token)

        buf: List[dict] = []
        last_flush = time.monotonic()
        endpoint = f"{self.url}/events/tag/batch"

        # Drain any previously spooled batches before processing new events.
        self._drain_spool(session, headers, endpoint)

        while not self._stop.is_set():
            timeout = max(0.0, (self.flush_interval_ms / 1000.0) - (time.monotonic() - last_flush))
            try:
                ev = self._q.get(timeout=timeout)
                buf.append(ev.to_payload())
                if len(buf) >= self.batch_size:
                    self._flush_with_retry(session, headers, endpoint, buf)
                    buf.clear()
                    last_flush = time.monotonic()
            except queue.Empty:
                if buf:
                    self._flush_with_retry(session, headers, endpoint, buf)
                    buf.clear()
                last_flush = time.monotonic()

        if buf:
            self._flush_with_retry(session, headers, endpoint, buf)

    # ------------------------------------------------------------------
    # Retry + spool logic (W-031)
    # ------------------------------------------------------------------

    def _flush_with_retry(self, session, headers, endpoint: str, items: List[dict]) -> None:
        """Attempt to POST *items* with up to 3 retries using exponential back-off.

        If all attempts fail, append the batch as a JSONL line to the spool file.
        If any attempt succeeds, drain the spool first (deliver old batches in order)
        before sending the current batch.
        """
        last_exc: Optional[Exception] = None

        for attempt, delay in enumerate(_RETRY_DELAYS, start=1):
            try:
                self._post_batch(session, headers, endpoint, items)
                # Success: drain any previously spooled data first (best-effort).
                # We attempt drain only after the first successful POST so we know
                # connectivity is restored.
                self._drain_spool(session, headers, endpoint)
                return
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "[BACKEND] POST attempt %d/%d failed: %s — retrying in %.1fs",
                    attempt, len(_RETRY_DELAYS), exc, delay,
                )
                time.sleep(delay)

        # All retries exhausted — spool to disk.
        logger.error(
            "[BACKEND] All %d retries failed (%s); spooling batch of %d events to %s",
            len(_RETRY_DELAYS), last_exc, len(items), _SPOOL_PATH,
        )
        self._spool_batch(items)

    def _post_batch(self, session, headers, endpoint: str, items: List[dict]) -> None:
        """POST a single batch.  Raises on connection errors, timeouts, or non-2xx responses."""
        payload = {"events": items}
        try:
            resp = session.post(endpoint, headers=headers, data=json.dumps(payload), timeout=2.0)
        except Exception as exc:
            raise RuntimeError(f"HTTP POST failed: {exc}") from exc

        if resp.status_code == 429 or resp.status_code >= 500:
            raise RuntimeError(f"Server error {resp.status_code}: {resp.text[:200]}")
        if resp.status_code >= 300:
            # 4xx (except 429) — log and discard (retrying will not help)
            logger.error("[BACKEND] POST batch rejected %d: %s", resp.status_code, resp.text[:200])
            return

        # Validate response structure
        try:
            data = resp.json()
        except Exception as exc:
            raise RuntimeError(f"Expected JSON response, parse error: {exc}") from exc
        if not isinstance(data, dict) or "events_processed" not in data:
            raise RuntimeError(f"Invalid response: 'events_processed' missing: {data!r}")
        processed = int(data["events_processed"])
        if processed != len(items):
            logger.warning("[BACKEND] Batch mismatch: sent=%d processed=%d", len(items), processed)

    # ------------------------------------------------------------------
    # Spool helpers
    # ------------------------------------------------------------------

    def _spool_batch(self, items: List[dict]) -> None:
        """Append *items* as one JSONL line to the spool file."""
        try:
            os.makedirs(os.path.dirname(_SPOOL_PATH) or ".", exist_ok=True)
            with open(_SPOOL_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"events": items}) + "\n")
        except OSError as exc:
            logger.error("[BACKEND] Failed to write spool file %s: %s", _SPOOL_PATH, exc)

    def _drain_spool(self, session, headers, endpoint: str) -> None:
        """Read the spool file and deliver all batches in order.

        If all batches are delivered successfully, truncate the file.
        If any batch fails, stop draining (preserve remaining lines for the next drain).
        """
        if not os.path.exists(_SPOOL_PATH):
            return
        try:
            with open(_SPOOL_PATH, "r", encoding="utf-8") as fh:
                lines = fh.readlines()
        except OSError as exc:
            logger.error("[BACKEND] Failed to read spool file %s: %s", _SPOOL_PATH, exc)
            return

        if not lines:
            return

        logger.info("[BACKEND] Draining spool: %d batche(s) to deliver", len(lines))
        delivered = 0
        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                delivered += 1
                continue
            try:
                obj = json.loads(line)
                items = obj.get("events", [])
                self._post_batch(session, headers, endpoint, items)
                delivered += 1
                logger.info("[BACKEND] Spool batch %d/%d delivered (%d events)", i + 1, len(lines), len(items))
            except Exception as exc:
                logger.error("[BACKEND] Spool batch %d/%d failed: %s — will retry later", i + 1, len(lines), exc)
                # Rewrite the file with the remaining undelivered lines.
                remaining = lines[i:]
                try:
                    with open(_SPOOL_PATH, "w", encoding="utf-8") as fh:
                        fh.writelines(remaining)
                except OSError as write_exc:
                    logger.error("[BACKEND] Failed to rewrite spool file: %s", write_exc)
                return

        # All lines delivered — truncate the spool file.
        try:
            with open(_SPOOL_PATH, "w", encoding="utf-8") as fh:
                pass  # truncate
        except OSError as exc:
            logger.error("[BACKEND] Failed to truncate spool file %s: %s", _SPOOL_PATH, exc)
        logger.info("[BACKEND] Spool fully drained (%d batche(s))", delivered)

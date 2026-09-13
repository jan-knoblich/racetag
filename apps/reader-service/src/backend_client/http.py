from __future__ import annotations

import json
import os
import queue
import threading
import time
from typing import List, Optional

from models import TagEvent
from utils import get_logger, resolve_log_dir
from .base import BackendClient

logger = get_logger("reader.backend.http")

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

# Spool file path — absolute, resolved via RACETAG_LOG_DIR / ~/.racetag/logs
# (AUDIT-2026-07 H1: the old CWD-relative "logs/spool.jsonl" silently failed
# for a Finder-launched .app whose CWD is the read-only "/", dropping batches
# instead of spooling them).
_SPOOL_PATH = os.path.join(resolve_log_dir(), "spool.jsonl")

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
            # 3 s covers the bounded shutdown path in _worker: one quick POST
            # (1 s timeout) plus spooling. The old 1.5 s killed the daemon
            # thread mid-retry and lost the final buffer (AUDIT-2026-07 H2).
            self._t.join(timeout=3.0)
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
            # Cap the blocking get so a stop() request is noticed promptly
            # even with a long flush interval — otherwise the worker sits in
            # get() past stop()'s join timeout and the shutdown flush below
            # never runs (AUDIT-2026-07 H2).
            timeout = min(timeout, 0.25)
            try:
                ev = self._q.get(timeout=timeout)
                buf.append(ev.to_payload())
                if len(buf) >= self.batch_size:
                    self._flush_with_retry(session, headers, endpoint, buf)
                    buf.clear()
                    last_flush = time.monotonic()
            except queue.Empty:
                # The capped get() can wake before the flush deadline; only
                # flush when the configured interval has actually elapsed.
                if (time.monotonic() - last_flush) < (self.flush_interval_ms / 1000.0):
                    continue
                if buf:
                    self._flush_with_retry(session, headers, endpoint, buf)
                    buf.clear()
                last_flush = time.monotonic()

        # ------------------------------------------------------------------
        # Shutdown path (AUDIT-2026-07 H2): never abandon undelivered events.
        # Drain everything still sitting in the queue into buf, make ONE
        # quick delivery attempt (no retries — the common shutdown case is a
        # dead backend, and a full retry cycle would outlive stop()'s join
        # timeout), then unconditionally spool whatever wasn't delivered.
        # ------------------------------------------------------------------
        try:
            while True:
                buf.append(self._q.get_nowait().to_payload())
        except queue.Empty:
            pass

        if buf:
            try:
                self._post_batch(session, headers, endpoint, buf, timeout=1.0)
                logger.info("[BACKEND] Final flush delivered %d event(s)", len(buf))
            except Exception as exc:
                logger.warning(
                    "[BACKEND] Final flush failed (%s); spooling %d event(s) to %s",
                    exc, len(buf), _SPOOL_PATH,
                )
                for i in range(0, len(buf), self.batch_size):
                    self._spool_batch(buf[i:i + self.batch_size])

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

    def _post_batch(self, session, headers, endpoint: str, items: List[dict], timeout: float = 2.0) -> None:
        """POST a single batch.  Raises on connection errors, timeouts, or non-2xx responses."""
        payload = {"events": items}
        try:
            resp = session.post(endpoint, headers=headers, data=json.dumps(payload), timeout=timeout)
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
        """Append *items* as one JSONL line to the spool file.

        flush + fsync per line: the spool is the last line of defence for
        race data, and a power loss mid-append must not be able to truncate
        an already-written line (a torn line used to wedge the drain — M1).
        """
        try:
            os.makedirs(os.path.dirname(_SPOOL_PATH) or ".", exist_ok=True)
            with open(_SPOOL_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"events": items}) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        except OSError as exc:
            logger.error("[BACKEND] Failed to write spool file %s: %s", _SPOOL_PATH, exc)

    def _quarantine_spool_line(self, raw_line: str) -> None:
        """Move an unparseable spool line to a sidecar file for forensics.

        The corrupt line must never stay at the head of the spool: the drain
        would re-fail on it forever and every batch behind it would be
        stranded (AUDIT-2026-07 M1).
        """
        corrupt_path = os.path.join(
            os.path.dirname(_SPOOL_PATH) or ".", "spool.corrupt.jsonl"
        )
        try:
            with open(corrupt_path, "a", encoding="utf-8") as fh:
                fh.write(raw_line.rstrip("\n") + "\n")
            logger.error(
                "[BACKEND] Corrupt spool line quarantined to %s", corrupt_path
            )
        except OSError as exc:
            # Even if quarantine fails we still drop the line from the spool —
            # keeping it would wedge the drain permanently.
            logger.error(
                "[BACKEND] Failed to quarantine corrupt spool line (%s); dropping it",
                exc,
            )

    def _drain_spool(self, session, headers, endpoint: str) -> None:
        """Read the spool file and deliver all batches in order.

        If all batches are delivered successfully, truncate the file.
        If a batch fails to DELIVER, stop draining (preserve remaining lines
        for the next drain). If a line fails to PARSE, quarantine it and keep
        going — a parse error never resolves by retrying, so treating it like
        a delivery failure used to wedge the drain forever (M1).
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
            stripped = line.strip()
            if not stripped:
                delivered += 1
                continue

            # Parse separately from delivery: corrupt lines are quarantined
            # and skipped, only delivery failures stop the drain.
            try:
                obj = json.loads(stripped)
                items = obj.get("events", [])
                if not isinstance(items, list):
                    raise ValueError(
                        f"'events' is not a list: {type(items).__name__}"
                    )
            except ValueError as exc:  # json.JSONDecodeError subclasses ValueError
                logger.error(
                    "[BACKEND] Spool line %d/%d unparseable: %s", i + 1, len(lines), exc
                )
                self._quarantine_spool_line(line)
                delivered += 1
                continue

            try:
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

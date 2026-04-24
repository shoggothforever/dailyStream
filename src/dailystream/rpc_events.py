"""Event bus for the DailyStream RPC server.

Provides a tiny thread-safe publish/subscribe mechanism so that long-running
Python code (AI analysis queue, workspace lifecycle, etc.) can emit JSON-RPC
notification messages that the RPC server writes to stdout.

Design principles
-----------------
* **Zero dependencies** — stdlib only (``threading``).
* **Process-local** — a single ``EventBus`` instance is shared across all
  dispatcher handlers within one ``rpc_server.serve()`` call.
* **Fire-and-forget** — subscribers must not raise; exceptions are logged
  and swallowed so one bad subscriber cannot block event flow.
* **Fan-out order is stable** — subscribers are invoked in registration
  order, serially (there is no background thread here).
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, List

logger = logging.getLogger(__name__)

# (method, params) → None
Subscriber = Callable[[str, dict], None]
# Returned by subscribe(); calling it unregisters the subscriber.
Unsubscribe = Callable[[], None]


class EventBus:
    """Thread-safe pub/sub bus for JSON-RPC notifications.

    The *method* argument of :meth:`publish` is the full JSON-RPC method
    name (e.g. ``"ai.analysis_completed"``).  *params* must be a JSON-
    serialisable ``dict``.

    Typical wiring inside ``rpc_server.serve()``::

        bus = EventBus()
        bus.subscribe(lambda m, p: write_message({"jsonrpc":"2.0",
                                                  "method": m,
                                                  "params": p}))
        dispatcher = Dispatcher(bus)
    """

    def __init__(self) -> None:
        self._subscribers: List[Subscriber] = []
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    def publish(self, method: str, params: dict) -> None:
        """Notify every subscriber.

        Subscriber exceptions are logged and swallowed — publishing must
        never fail for the publisher (typically a domain handler).
        """
        # Snapshot the subscriber list under lock, then release the lock
        # before invoking callbacks so a slow subscriber cannot block
        # other publishers.
        with self._lock:
            subs = list(self._subscribers)
        for sub in subs:
            try:
                sub(method, params)
            except Exception:  # noqa: BLE001
                logger.exception("EventBus subscriber raised for %s", method)

    # ------------------------------------------------------------------
    # Subscription
    # ------------------------------------------------------------------

    def subscribe(self, callback: Subscriber) -> Unsubscribe:
        """Register *callback* and return an unsubscribe function."""
        with self._lock:
            self._subscribers.append(callback)

        def _unsubscribe() -> None:
            with self._lock:
                try:
                    self._subscribers.remove(callback)
                except ValueError:
                    # Already removed — idempotent.
                    pass

        return _unsubscribe

    def clear(self) -> None:
        """Remove every subscriber (mainly for tests)."""
        with self._lock:
            self._subscribers.clear()

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

"""JSON-RPC 2.0 method dispatcher for DailyStream.

The dispatcher maps RPC method names like ``"workspace.create"`` to plain
Python callables.  It is intentionally small and free of I/O — the
``rpc_server`` module owns stdin/stdout, while this module only cares
about:

* registering handlers,
* validating incoming requests against the JSON-RPC 2.0 shape,
* invoking handlers and formatting responses / errors.

Error codes
-----------
We follow `JSON-RPC 2.0 <https://www.jsonrpc.org/specification#error_object>`_
with a few domain-specific extensions in the reserved ``-32000..-32099``
range:

* ``-32700`` Parse error (reserved for the server layer)
* ``-32600`` Invalid Request
* ``-32601`` Method not found
* ``-32602`` Invalid params
* ``-32603`` Internal error
* ``-32000`` Domain error (handler raised a recognised exception)
* ``-32001`` Workspace state conflict (e.g. "workspace already active")
* ``-32002`` Not found (e.g. pipeline not found)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from .rpc_events import EventBus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Error model
# ---------------------------------------------------------------------------


# JSON-RPC standard codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# Domain-specific codes (reserved -32000..-32099)
DOMAIN_ERROR = -32000
STATE_CONFLICT = -32001
NOT_FOUND = -32002


class RPCError(Exception):
    """Raised by handlers to surface a clean JSON-RPC error.

    Prefer :class:`RPCError` subclasses over ``raise ValueError`` inside
    handlers — unexpected exceptions are converted to ``-32603 Internal
    error`` and may leak stack traces.
    """

    code: int = DOMAIN_ERROR

    def __init__(self, message: str, *, code: Optional[int] = None,
                 data: Optional[dict] = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        self.data = data


class InvalidParams(RPCError):
    code = INVALID_PARAMS


class StateConflict(RPCError):
    code = STATE_CONFLICT


class NotFound(RPCError):
    code = NOT_FOUND


# ---------------------------------------------------------------------------
# Handler registration
# ---------------------------------------------------------------------------


@dataclass
class _HandlerEntry:
    method: str
    func: Callable[..., Any]
    # Optional JSON-Schema-ish dict describing params — reserved for
    # future validation; currently only used for ``app.get_schema``.
    schema: Optional[dict] = None


class Dispatcher:
    """Register RPC methods and handle incoming request dicts.

    The dispatcher never writes to stdout; it returns a response dict
    (or ``None`` for one-way notifications) that ``rpc_server`` is
    responsible for serialising.
    """

    def __init__(self, event_bus: Optional[EventBus] = None) -> None:
        self._handlers: Dict[str, _HandlerEntry] = {}
        self._event_bus = event_bus or EventBus()

    # ------------------------------------------------------------------
    # Registration API
    # ------------------------------------------------------------------

    def register(
        self,
        method: str,
        func: Callable[..., Any],
        *,
        schema: Optional[dict] = None,
    ) -> None:
        """Register *func* as the handler for *method*.

        Re-registering the same method overwrites the previous handler.
        This is handy for tests; production code should register each
        method exactly once at server startup.
        """
        if not method or not isinstance(method, str):
            raise ValueError("method must be a non-empty string")
        if not callable(func):
            raise ValueError("func must be callable")
        self._handlers[method] = _HandlerEntry(
            method=method, func=func, schema=schema
        )

    def method(
        self,
        method: str,
        *,
        schema: Optional[dict] = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator form of :meth:`register`.

        Example::

            @dispatcher.method("app.ping")
            def _ping(): return "pong"
        """

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.register(method, func, schema=schema)
            return func

        return decorator

    @property
    def event_bus(self) -> EventBus:
        return self._event_bus

    def methods(self) -> list[str]:
        return sorted(self._handlers.keys())

    def has(self, method: str) -> bool:
        return method in self._handlers

    # ------------------------------------------------------------------
    # Request handling
    # ------------------------------------------------------------------

    def handle(self, request: dict) -> Optional[dict]:
        """Dispatch a parsed JSON-RPC request dict.

        Returns a response dict, or ``None`` when the request is a
        notification (no ``id``) — in which case the server must not
        write anything back for this message.
        """
        # ---------------- validation ----------------
        if not isinstance(request, dict):
            return _error_response(None, INVALID_REQUEST,
                                   "Request must be a JSON object")

        is_notification = "id" not in request
        req_id = request.get("id")

        # jsonrpc version is recommended but not strictly required;
        # be lenient to keep integration trivial.
        if request.get("jsonrpc", "2.0") != "2.0":
            if is_notification:
                return None
            return _error_response(
                req_id, INVALID_REQUEST,
                "Only JSON-RPC 2.0 is supported",
            )

        method = request.get("method")
        if not isinstance(method, str) or not method:
            if is_notification:
                return None
            return _error_response(
                req_id, INVALID_REQUEST, "Missing or invalid 'method'"
            )

        params = request.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, (dict, list)):
            if is_notification:
                return None
            return _error_response(
                req_id, INVALID_PARAMS,
                "'params' must be an object or array",
            )

        entry = self._handlers.get(method)
        if entry is None:
            if is_notification:
                return None
            return _error_response(
                req_id, METHOD_NOT_FOUND,
                f"Method not found: {method}",
            )

        # ---------------- invoke ----------------
        try:
            if isinstance(params, dict):
                result = entry.func(**params)
            else:
                result = entry.func(*params)
        except RPCError as exc:
            if is_notification:
                return None
            return _error_response(
                req_id, exc.code, str(exc), data=exc.data,
            )
        except TypeError as exc:
            # Signature mismatch → invalid params.
            if is_notification:
                return None
            return _error_response(
                req_id, INVALID_PARAMS,
                f"Invalid params for {method}: {exc}",
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Handler for %s raised", method)
            if is_notification:
                return None
            return _error_response(
                req_id, INTERNAL_ERROR,
                f"Internal error: {exc.__class__.__name__}: {exc}",
            )

        if is_notification:
            return None

        return {"jsonrpc": "2.0", "id": req_id, "result": result}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _error_response(
    req_id: Any,
    code: int,
    message: str,
    *,
    data: Optional[dict] = None,
) -> dict:
    err: dict = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}

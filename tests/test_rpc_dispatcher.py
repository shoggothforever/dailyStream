"""Unit tests for dailystream.rpc_dispatcher.Dispatcher."""

import pytest

from dailystream.rpc_dispatcher import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    NOT_FOUND,
    STATE_CONFLICT,
    Dispatcher,
    InvalidParams,
    NotFound,
    RPCError,
    StateConflict,
)
from dailystream.rpc_events import EventBus


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_register_and_has(self):
        d = Dispatcher()
        d.register("ping", lambda: "pong")
        assert d.has("ping") is True
        assert d.has("nope") is False

    def test_decorator_register(self):
        d = Dispatcher()

        @d.method("greet")
        def _greet(name: str) -> str:
            return f"hi {name}"

        assert d.has("greet")

    def test_re_register_overwrites(self):
        d = Dispatcher()
        d.register("m", lambda: 1)
        d.register("m", lambda: 2)
        resp = d.handle({"jsonrpc": "2.0", "id": 1, "method": "m"})
        assert resp["result"] == 2

    def test_rejects_non_string_method(self):
        d = Dispatcher()
        with pytest.raises(ValueError):
            d.register("", lambda: None)

    def test_rejects_non_callable(self):
        d = Dispatcher()
        with pytest.raises(ValueError):
            d.register("x", "not callable")  # type: ignore[arg-type]

    def test_methods_listed_sorted(self):
        d = Dispatcher()
        d.register("b", lambda: None)
        d.register("a", lambda: None)
        assert d.methods() == ["a", "b"]


# ---------------------------------------------------------------------------
# Successful dispatch
# ---------------------------------------------------------------------------


class TestDispatchSuccess:
    def test_handle_no_params(self):
        d = Dispatcher()
        d.register("ping", lambda: "pong")
        resp = d.handle({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert resp == {"jsonrpc": "2.0", "id": 1, "result": "pong"}

    def test_handle_kwargs_params(self):
        d = Dispatcher()
        d.register("add", lambda a, b: a + b)
        resp = d.handle({
            "jsonrpc": "2.0", "id": 2,
            "method": "add", "params": {"a": 2, "b": 3},
        })
        assert resp["result"] == 5

    def test_handle_positional_params(self):
        d = Dispatcher()
        d.register("add", lambda a, b: a + b)
        resp = d.handle({
            "jsonrpc": "2.0", "id": 3,
            "method": "add", "params": [4, 5],
        })
        assert resp["result"] == 9

    def test_handle_null_id_is_still_request(self):
        d = Dispatcher()
        d.register("m", lambda: "ok")
        resp = d.handle({"jsonrpc": "2.0", "id": None, "method": "m"})
        assert resp == {"jsonrpc": "2.0", "id": None, "result": "ok"}

    def test_notification_returns_none(self):
        d = Dispatcher()
        d.register("m", lambda: "ok")
        # No 'id' → notification
        assert d.handle({"jsonrpc": "2.0", "method": "m"}) is None


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestDispatchErrors:
    def test_method_not_found(self):
        d = Dispatcher()
        resp = d.handle({"jsonrpc": "2.0", "id": 1, "method": "missing"})
        assert resp["error"]["code"] == METHOD_NOT_FOUND

    def test_invalid_request_not_a_dict(self):
        d = Dispatcher()
        resp = d.handle("not a dict")  # type: ignore[arg-type]
        assert resp["error"]["code"] == INVALID_REQUEST

    def test_missing_method(self):
        d = Dispatcher()
        resp = d.handle({"jsonrpc": "2.0", "id": 1})
        assert resp["error"]["code"] == INVALID_REQUEST

    def test_wrong_jsonrpc_version(self):
        d = Dispatcher()
        d.register("m", lambda: "ok")
        resp = d.handle({"jsonrpc": "1.0", "id": 1, "method": "m"})
        assert resp["error"]["code"] == INVALID_REQUEST

    def test_invalid_params_type(self):
        d = Dispatcher()
        d.register("m", lambda: None)
        resp = d.handle({
            "jsonrpc": "2.0", "id": 1, "method": "m",
            "params": "bad",
        })
        assert resp["error"]["code"] == INVALID_PARAMS

    def test_signature_mismatch_becomes_invalid_params(self):
        d = Dispatcher()
        d.register("needs_x", lambda x: x)
        resp = d.handle({
            "jsonrpc": "2.0", "id": 1, "method": "needs_x",
            "params": {"wrong": 1},
        })
        assert resp["error"]["code"] == INVALID_PARAMS

    def test_rpcerror_surfaces_cleanly(self):
        d = Dispatcher()

        def _h():
            raise NotFound("gone", data={"what": "x"})

        d.register("m", _h)
        resp = d.handle({"jsonrpc": "2.0", "id": 1, "method": "m"})
        assert resp["error"]["code"] == NOT_FOUND
        assert resp["error"]["data"] == {"what": "x"}
        assert "gone" in resp["error"]["message"]

    def test_state_conflict(self):
        d = Dispatcher()
        d.register("m", lambda: (_ for _ in ()).throw(
            StateConflict("busy")
        ))
        resp = d.handle({"jsonrpc": "2.0", "id": 1, "method": "m"})
        assert resp["error"]["code"] == STATE_CONFLICT

    def test_invalid_params_exception(self):
        d = Dispatcher()

        def _h():
            raise InvalidParams("bad thing")

        d.register("m", _h)
        resp = d.handle({"jsonrpc": "2.0", "id": 1, "method": "m"})
        assert resp["error"]["code"] == INVALID_PARAMS

    def test_unknown_exception_becomes_internal_error(self):
        d = Dispatcher()

        def _h():
            raise RuntimeError("boom")

        d.register("m", _h)
        resp = d.handle({"jsonrpc": "2.0", "id": 1, "method": "m"})
        assert resp["error"]["code"] == INTERNAL_ERROR
        assert "RuntimeError" in resp["error"]["message"]

    def test_notification_errors_are_suppressed(self):
        """Notifications (no id) never return an error body."""
        d = Dispatcher()
        d.register("m", lambda: (_ for _ in ()).throw(RuntimeError("bad")))
        assert d.handle({"jsonrpc": "2.0", "method": "m"}) is None
        assert d.handle({"jsonrpc": "2.0", "method": "missing"}) is None


# ---------------------------------------------------------------------------
# Event bus wiring
# ---------------------------------------------------------------------------


class TestEventBusWiring:
    def test_default_event_bus_created(self):
        d = Dispatcher()
        assert isinstance(d.event_bus, EventBus)

    def test_custom_event_bus_used(self):
        bus = EventBus()
        d = Dispatcher(event_bus=bus)
        assert d.event_bus is bus

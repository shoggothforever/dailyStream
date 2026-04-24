"""Unit tests for dailystream.rpc_events.EventBus."""

import threading

import pytest

from dailystream.rpc_events import EventBus


class TestEventBus:
    def test_publish_no_subscribers_is_safe(self):
        bus = EventBus()
        bus.publish("foo.bar", {"x": 1})  # should not raise

    def test_subscribe_and_publish_single(self):
        bus = EventBus()
        received = []
        bus.subscribe(lambda m, p: received.append((m, p)))
        bus.publish("m1", {"a": 1})
        assert received == [("m1", {"a": 1})]

    def test_multiple_subscribers_invoked_in_order(self):
        bus = EventBus()
        order = []
        bus.subscribe(lambda m, p: order.append(("a", m)))
        bus.subscribe(lambda m, p: order.append(("b", m)))
        bus.subscribe(lambda m, p: order.append(("c", m)))
        bus.publish("evt", {})
        assert order == [("a", "evt"), ("b", "evt"), ("c", "evt")]

    def test_unsubscribe(self):
        bus = EventBus()
        received = []
        unsub = bus.subscribe(lambda m, p: received.append(m))
        bus.publish("keep", {})
        unsub()
        bus.publish("drop", {})
        assert received == ["keep"]

    def test_unsubscribe_is_idempotent(self):
        bus = EventBus()
        unsub = bus.subscribe(lambda m, p: None)
        unsub()
        unsub()  # should not raise

    def test_clear_removes_all(self):
        bus = EventBus()
        bus.subscribe(lambda m, p: None)
        bus.subscribe(lambda m, p: None)
        assert bus.subscriber_count == 2
        bus.clear()
        assert bus.subscriber_count == 0

    def test_subscriber_exception_does_not_break_others(self, caplog):
        bus = EventBus()
        received = []

        def _bad(m, p):
            raise RuntimeError("boom")

        def _good(m, p):
            received.append(m)

        bus.subscribe(_bad)
        bus.subscribe(_good)
        bus.publish("evt", {})
        assert received == ["evt"]
        # The exception should have been logged
        assert any("boom" in rec.getMessage() or
                   "subscriber raised" in rec.getMessage().lower()
                   for rec in caplog.records)

    def test_publish_is_threadsafe(self):
        bus = EventBus()
        received: list[str] = []
        lock = threading.Lock()

        def _sub(m, p):
            with lock:
                received.append(m)

        bus.subscribe(_sub)

        threads = [
            threading.Thread(target=bus.publish, args=(f"m{i}", {}))
            for i in range(20)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(received) == 20

    def test_subscribe_during_publish_safe(self):
        """Subscribing from inside a subscriber must not corrupt iteration."""
        bus = EventBus()
        received = []
        added = []

        def _mid(m, p):
            bus.subscribe(lambda m2, p2: added.append(m2))
            received.append(m)

        bus.subscribe(_mid)
        bus.publish("first", {})  # only _mid runs; new sub is added
        bus.publish("second", {})
        # On the second publish both _mid and the newly added sub fire.
        assert received == ["first", "second"]
        assert added == ["second"]

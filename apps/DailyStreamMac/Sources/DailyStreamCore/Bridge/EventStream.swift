// EventStream.swift
// Typed event fan-out for notifications received from the Python core.
//
// `CoreBridge` forwards every incoming RPC notification (those without
// an `id`) into `BridgeEventCenter`.  Consumers obtain an
// `AsyncStream<BridgeEvent>` via `events()` and receive events in the
// order the core emitted them.

import Foundation

/// A JSON-RPC notification surfaced as a typed Swift value.
///
/// `method` is the raw dotted name (e.g. `"ai.analysis_completed"`);
/// `params` is the untyped payload — callers can `decode(as:)` into
/// a domain struct when they know the shape.
public struct BridgeEvent: Sendable {
    public let method: String
    public let params: RPCAny?

    public init(method: String, params: RPCAny?) {
        self.method = method
        self.params = params
    }
}

/// Fan-out hub for `BridgeEvent` values.  Thread-safe via an actor.
///
/// The design uses a single `AsyncStream.Continuation` per subscriber
/// so each consumer gets its own back-pressure-aware stream.  Dropping
/// the stream iterator automatically unsubscribes.
public actor BridgeEventCenter {
    private var continuations: [UUID: AsyncStream<BridgeEvent>.Continuation] = [:]

    public init() {}

    /// Subscribe and receive events until the returned stream is cancelled.
    public nonisolated func events() -> AsyncStream<BridgeEvent> {
        AsyncStream(BridgeEvent.self, bufferingPolicy: .unbounded) { continuation in
            let id = UUID()
            Task { await self.register(id: id, continuation: continuation) }
            continuation.onTermination = { [weak self] _ in
                guard let self else { return }
                Task { await self.unregister(id: id) }
            }
        }
    }

    /// Publish an event to every current subscriber.
    public func publish(_ event: BridgeEvent) {
        for c in continuations.values {
            c.yield(event)
        }
    }

    /// Close every stream (used during shutdown).
    public func finishAll() {
        for c in continuations.values {
            c.finish()
        }
        continuations.removeAll()
    }

    public var subscriberCount: Int { continuations.count }

    // MARK: - Private

    private func register(id: UUID, continuation: AsyncStream<BridgeEvent>.Continuation) {
        continuations[id] = continuation
    }

    private func unregister(id: UUID) {
        continuations.removeValue(forKey: id)
    }
}

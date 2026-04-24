// EventCenterTests.swift
import XCTest
@testable import DailyStreamCore

final class EventCenterTests: XCTestCase {

    func testSubscribeReceivesPublishedEvents() async throws {
        let center = BridgeEventCenter()
        let stream = center.events()

        // Give the subscription task a beat to register.
        try await Task.sleep(nanoseconds: 30_000_000)

        await center.publish(BridgeEvent(method: "a", params: nil))
        await center.publish(BridgeEvent(method: "b", params: .int(2)))

        var it = stream.makeAsyncIterator()
        let first = await it.next()
        let second = await it.next()

        XCTAssertEqual(first?.method, "a")
        XCTAssertEqual(second?.method, "b")
        XCTAssertEqual(second?.params?.intValue, 2)
    }

    func testTwoSubscribersGetIndependentCopies() async throws {
        let center = BridgeEventCenter()
        let s1 = center.events()
        let s2 = center.events()
        try await Task.sleep(nanoseconds: 30_000_000)

        await center.publish(BridgeEvent(method: "m", params: nil))

        var i1 = s1.makeAsyncIterator()
        var i2 = s2.makeAsyncIterator()
        let e1 = await i1.next()
        let e2 = await i2.next()
        XCTAssertEqual(e1?.method, "m")
        XCTAssertEqual(e2?.method, "m")
    }

    func testFinishAllClosesStreams() async throws {
        let center = BridgeEventCenter()
        let stream = center.events()
        try await Task.sleep(nanoseconds: 30_000_000)

        await center.finishAll()
        var it = stream.makeAsyncIterator()
        let next = await it.next()
        XCTAssertNil(next)
    }
}

// RPCMessageTests.swift
import XCTest
@testable import DailyStreamCore

final class RPCMessageTests: XCTestCase {

    // MARK: - RPCRequest encoding

    func testRequestEncodesJSONRPC2FrameWithId() throws {
        struct Params: Encodable, Sendable { let x: Int }
        let req = RPCRequest(id: 7, method: "do.thing", params: Params(x: 42))
        let data = try JSONEncoder().encode(req)
        let s = String(data: data, encoding: .utf8)!
        XCTAssertTrue(s.contains("\"jsonrpc\":\"2.0\""))
        XCTAssertTrue(s.contains("\"id\":7"))
        XCTAssertTrue(s.contains("\"method\":\"do.thing\""))
        XCTAssertTrue(s.contains("\"x\":42"))
    }

    func testEmptyParamsEncodesAsEmptyObject() throws {
        let req = RPCRequest(id: 1, method: "m", params: RPCEmptyParams())
        let data = try JSONEncoder().encode(req)
        let s = String(data: data, encoding: .utf8)!
        XCTAssertTrue(s.contains("\"params\":{}"))
    }

    // MARK: - RPCResponse decoding

    func testResponseWithResult() throws {
        let raw = #"{"jsonrpc":"2.0","id":3,"result":"pong"}"#
        let r: RPCResponse<String> = try JSONDecoder().decode(
            RPCResponse<String>.self, from: raw.data(using: .utf8)!
        )
        XCTAssertEqual(r.id, 3)
        XCTAssertEqual(r.result, "pong")
        XCTAssertNil(r.error)
    }

    func testResponseWithError() throws {
        let raw = #"{"jsonrpc":"2.0","id":3,"error":{"code":-32601,"message":"Method not found"}}"#
        let r: RPCResponse<String> = try JSONDecoder().decode(
            RPCResponse<String>.self, from: raw.data(using: .utf8)!
        )
        XCTAssertEqual(r.error?.code, -32601)
        XCTAssertEqual(r.error?.message, "Method not found")
    }

    func testResponseWithErrorData() throws {
        let raw = #"{"jsonrpc":"2.0","id":3,"error":{"code":-32001,"message":"busy","data":{"where":"ws"}}}"#
        let r: RPCResponse<String> = try JSONDecoder().decode(
            RPCResponse<String>.self, from: raw.data(using: .utf8)!
        )
        XCTAssertEqual(r.error?.data?.objectValue?["where"]?.stringValue, "ws")
    }

    // MARK: - RPCNotification decoding

    func testNotificationHasNoId() throws {
        let raw = #"{"jsonrpc":"2.0","method":"ai.progress","params":{"done":3,"total":10}}"#
        let n = try JSONDecoder().decode(
            RPCNotification.self, from: raw.data(using: .utf8)!
        )
        XCTAssertEqual(n.method, "ai.progress")
        XCTAssertEqual(n.params?.objectValue?["done"]?.intValue, 3)
    }

    // MARK: - RPCAny round-trip

    func testRPCAnyRoundTripsScalarsAndContainers() throws {
        let payload: RPCAny = .object([
            "n": .null,
            "b": .bool(true),
            "i": .int(42),
            "d": .double(3.14),
            "s": .string("hi"),
            "a": .array([.int(1), .string("two")]),
        ])
        let data = try JSONEncoder().encode(payload)
        let back = try JSONDecoder().decode(RPCAny.self, from: data)
        XCTAssertEqual(back, payload)
    }

    func testRPCAnyDecodeAsStruct() throws {
        struct Progress: Decodable { let done: Int; let total: Int }
        let any: RPCAny = .object([
            "done": .int(3),
            "total": .int(10),
        ])
        let p = try any.decode(as: Progress.self)
        XCTAssertEqual(p.done, 3)
        XCTAssertEqual(p.total, 10)
    }
}

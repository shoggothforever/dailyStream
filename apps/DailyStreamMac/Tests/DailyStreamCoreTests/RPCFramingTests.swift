// RPCFramingTests.swift
import XCTest
@testable import DailyStreamCore

final class RPCFramingTests: XCTestCase {

    func testEncodeLineTerminatesWithNewline() throws {
        let req = RPCRequest(id: 1, method: "m", params: RPCEmptyParams())
        let data = try RPCFraming.encodeLine(req)
        XCTAssertEqual(data.last, 0x0A)  // '\n'
        let asString = String(data: data, encoding: .utf8)!
        XCTAssertEqual(asString.filter { $0 == "\n" }.count, 1)
    }

    func testSplitLinesParsesCompleteLines() {
        var buf = Data("abc\n".utf8) + Data("def\n".utf8)
        let lines = RPCFraming.splitLines(from: &buf)
        XCTAssertEqual(lines, ["abc", "def"])
        XCTAssertTrue(buf.isEmpty)
    }

    func testSplitLinesPreservesPartialTail() {
        var buf = Data("abc\nde".utf8)
        let lines = RPCFraming.splitLines(from: &buf)
        XCTAssertEqual(lines, ["abc"])
        XCTAssertEqual(String(data: buf, encoding: .utf8), "de")
    }

    func testSplitLinesSkipsEmptyLines() {
        var buf = Data("\nok\n\n".utf8)
        let lines = RPCFraming.splitLines(from: &buf)
        XCTAssertEqual(lines, ["ok"])
    }

    func testClassifyResponse() {
        let line = #"{"jsonrpc":"2.0","id":5,"result":"x"}"#
        if case .response(let id, _) = RPCIncoming.classify(line: line) {
            XCTAssertEqual(id, 5)
        } else {
            XCTFail("expected response")
        }
    }

    func testClassifyNotification() {
        let line = #"{"jsonrpc":"2.0","method":"evt","params":{}}"#
        if case .notification(let m, _) = RPCIncoming.classify(line: line) {
            XCTAssertEqual(m, "evt")
        } else {
            XCTFail("expected notification")
        }
    }

    func testClassifyMalformed() {
        let line = "not json"
        if case .malformed = RPCIncoming.classify(line: line) { /* ok */ }
        else { XCTFail("expected malformed") }
    }
}

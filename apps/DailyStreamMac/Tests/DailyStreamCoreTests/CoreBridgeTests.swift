// CoreBridgeTests.swift
// Integration tests that actually spawn the Python RPC server.  They
// require `<repo>/.venv/bin/python` to exist (set up by `pip install -e .`).
//
// If the venv is missing the tests throw XCTSkip so the suite still
// passes on a bare checkout.

import XCTest
@testable import DailyStreamCore

final class CoreBridgeTests: XCTestCase {

    // MARK: - Test harness helpers

    /// Locate the repo root by walking up from this source file until we
    /// find a `pyproject.toml`.
    private static func repoRoot() -> URL? {
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<8 {
            url.deleteLastPathComponent()
            if FileManager.default.fileExists(
                atPath: url.appendingPathComponent("pyproject.toml").path
            ) {
                return url
            }
        }
        return nil
    }

    /// Build a `CoreBridgeConfig` that runs the RPC server via the
    /// project's venv Python.  Returns `nil` if the venv is missing.
    private static func venvBridgeConfig() -> CoreBridgeConfig? {
        guard let root = repoRoot() else { return nil }
        let python = root.appendingPathComponent(".venv/bin/python")
        guard FileManager.default.isExecutableFile(atPath: python.path) else {
            return nil
        }

        // We wrap the call in /usr/bin/env so `Process` can resolve
        // argv[0] as the full python executable; plumb `-m
        // dailystream.rpc_server` via a tiny shell trampoline.
        let trampoline = root.appendingPathComponent(
            "apps/DailyStreamMac/Tests/DailyStreamCoreTests/run_rpc_server.sh"
        )
        // Write trampoline on demand so we don't need to ship it.
        let script = """
        #!/usr/bin/env bash
        exec "\(python.path)" -m dailystream.rpc_server "$@"
        """
        do {
            try script.write(to: trampoline, atomically: true, encoding: .utf8)
            try FileManager.default.setAttributes(
                [.posixPermissions: 0o755], ofItemAtPath: trampoline.path
            )
        } catch {
            return nil
        }

        var env: [String: String] = [:]
        env["PYTHONPATH"] = root.appendingPathComponent("src").path
        // Hide expected DeprecationWarning from our own `dailystream.app`
        // import (it noises up stderr but does not affect protocol).
        env["PYTHONWARNINGS"] = "ignore"

        return CoreBridgeConfig(
            executableURL: trampoline,
            environment: env,
            defaultTimeout: 5,
            shutdownGraceSeconds: 2
        )
    }

    private func makeBridge() throws -> CoreBridge {
        guard let config = Self.venvBridgeConfig() else {
            throw XCTSkip("Project venv missing; skipping live-process tests.")
        }
        return CoreBridge(config: config)
    }

    // MARK: - Tests ------------------------------------------------

    func testPingRoundTrip() async throws {
        let bridge = try makeBridge()
        try await bridge.start()
        defer { Task { await bridge.shutdown() } }

        let pong = try await bridge.ping()
        XCTAssertEqual(pong, "pong")

        await bridge.shutdown()
    }

    func testVersion() async throws {
        let bridge = try makeBridge()
        try await bridge.start()
        let v = try await bridge.version()
        XCTAssertFalse(v.rpc_version.isEmpty)
        XCTAssertFalse(v.python_version.isEmpty)
        await bridge.shutdown()
    }

    func testMethodNotFoundSurfacesAsRpcError() async throws {
        let bridge = try makeBridge()
        try await bridge.start()

        do {
            let _: String = try await bridge.call(
                "does.not.exist", params: RPCEmptyParams()
            )
            XCTFail("expected RPC error")
        } catch let err as BridgeError {
            if case .rpcFailed(let rpcErr) = err {
                XCTAssertEqual(rpcErr.code, -32601)  // Method not found
            } else {
                XCTFail("expected .rpcFailed, got \(err)")
            }
        }

        await bridge.shutdown()
    }

    func testWorkspaceStatusWhenEmpty() async throws {
        struct Status: Decodable { let is_active: Bool }
        let bridge = try makeBridge()
        try await bridge.start()
        let s: Status = try await bridge.call(
            "workspace.status", params: RPCEmptyParams()
        )
        // `is_active` is true only if a workspace was left open from
        // a prior session; we only assert we can decode the shape.
        _ = s.is_active
        await bridge.shutdown()
    }

    func testStartCannotBeCalledTwice() async throws {
        let bridge = try makeBridge()
        try await bridge.start()
        do {
            try await bridge.start()
            XCTFail("expected alreadyStarted")
        } catch BridgeError.alreadyStarted {
            // ok
        }
        await bridge.shutdown()
    }

    func testCallBeforeStartFails() async throws {
        guard let config = Self.venvBridgeConfig() else {
            throw XCTSkip("venv missing")
        }
        let bridge = CoreBridge(config: config)
        do {
            _ = try await bridge.ping()
            XCTFail("expected notStarted")
        } catch BridgeError.notStarted {
            // ok
        }
    }
}

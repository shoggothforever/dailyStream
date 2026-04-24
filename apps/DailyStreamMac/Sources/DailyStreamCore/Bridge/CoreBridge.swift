// CoreBridge.swift
// The single point of contact between the Swift shell and the Python
// `dailystream-core` RPC server.
//
// Responsibilities
// ----------------
// * Spawn the Python process (or mock, for tests).
// * Serialize JSON-RPC requests onto stdin.
// * Demux responses (matched by `id`) and notifications (forwarded to
//   `BridgeEventCenter`) from stdout.
// * Surface errors as typed `BridgeError` values.
// * Shut down cleanly: send `app.shutdown`, close stdin, wait up to
//   `shutdownGraceSeconds`, then terminate forcefully.
//
// Threading
// ---------
// `CoreBridge` is an `actor` so all mutable state (pending requests,
// next-id counter, child process handle) is serialized automatically.
// stdout reading runs on a detached `Task` that owns the `FileHandle`
// and hops back into the actor to deliver results.

import Foundation

/// Configuration knobs for the bridge.  Defaults are tuned for a
/// launch from inside a signed `DailyStream.app`.
public struct CoreBridgeConfig: Sendable {
    /// Absolute path to the `dailystream-core` executable.  When nil,
    /// `CoreBridge` tries a handful of known locations (see
    /// ``CoreBridge/locateExecutable()``).
    public var executableURL: URL?

    /// Extra environment variables to pass to the child process.  The
    /// parent environment is inherited.
    public var environment: [String: String]

    /// Default timeout for `call(...)` when the caller doesn't specify one.
    public var defaultTimeout: TimeInterval

    /// Grace period for `app.shutdown` before the bridge SIGTERMs the
    /// child process.
    public var shutdownGraceSeconds: TimeInterval

    public init(
        executableURL: URL? = nil,
        environment: [String: String] = [:],
        defaultTimeout: TimeInterval = 30,
        shutdownGraceSeconds: TimeInterval = 3
    ) {
        self.executableURL = executableURL
        self.environment = environment
        self.defaultTimeout = defaultTimeout
        self.shutdownGraceSeconds = shutdownGraceSeconds
    }
}

/// Actor that owns the child Python process and every in-flight request.
public actor CoreBridge {
    // MARK: - Public state

    public nonisolated let events: BridgeEventCenter

    // MARK: - Private state

    private let config: CoreBridgeConfig
    private var process: Process?
    private var stdinHandle: FileHandle?
    private var stdoutHandle: FileHandle?
    private var readerBuffer: Data = Data()
    private var pending: [Int: CheckedContinuation<String, Error>] = [:]
    private var nextID: Int = 0
    private var isShutdownRequested = false

    // MARK: - Init

    public init(
        config: CoreBridgeConfig = CoreBridgeConfig(),
        events: BridgeEventCenter = BridgeEventCenter()
    ) {
        self.config = config
        self.events = events
    }

    // MARK: - Lifecycle

    /// Start the child process and perform an `app.ping` handshake.
    public func start() async throws {
        guard process == nil else { throw BridgeError.alreadyStarted }

        let exe = try config.executableURL ?? Self.locateExecutable()

        let proc = Process()
        proc.executableURL = exe
        proc.arguments = []
        var env = ProcessInfo.processInfo.environment
        for (k, v) in config.environment { env[k] = v }
        proc.environment = env

        let stdinPipe = Pipe()
        let stdoutPipe = Pipe()
        let stderrPipe = Pipe()
        proc.standardInput = stdinPipe
        proc.standardOutput = stdoutPipe
        proc.standardError = stderrPipe

        try proc.run()

        self.process = proc
        self.stdinHandle = stdinPipe.fileHandleForWriting
        self.stdoutHandle = stdoutPipe.fileHandleForReading

        // Wire the stdout pipe through `readabilityHandler`.  The
        // handler is invoked on a background dispatch queue whenever
        // new data is available; it never blocks.  We hop back onto
        // the actor via `Task { await ... }` to deliver classified
        // lines, which cleanly interleaves with any in-flight
        // `call(...)` awaiting a response.
        stdoutPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty {
                // EOF — detach the handler so we stop spinning.
                handle.readabilityHandler = nil
                return
            }
            guard let self else { return }
            Task { await self.ingest(data: data) }
        }

        // Drain stderr silently; content goes to system log later.
        stderrPipe.fileHandleForReading.readabilityHandler = { handle in
            _ = handle.availableData  // discarded
        }

        // Handshake — `app.ping` returns the string "pong".
        let pong: String = try await call("app.ping", params: RPCEmptyParams(),
                                          timeout: 5)
        _ = pong
    }

    /// Gracefully stop the child process.
    public func shutdown() async {
        guard process != nil else { return }
        isShutdownRequested = true

        // Fire-and-forget shutdown request; we don't care if it errors.
        do {
            let _: String = try await call(
                "app.shutdown", params: RPCEmptyParams(),
                timeout: config.shutdownGraceSeconds
            )
        } catch {
            // swallow
        }

        try? stdinHandle?.close()
        stdinHandle = nil
        // Detach readability handlers so the pipes can close cleanly.
        stdoutHandle?.readabilityHandler = nil
        stdoutHandle = nil

        // Give the process a moment to exit on its own.
        let deadline = Date().addingTimeInterval(config.shutdownGraceSeconds)
        while let p = process, p.isRunning, Date() < deadline {
            try? await Task.sleep(nanoseconds: 50_000_000) // 50ms
        }

        if let p = process, p.isRunning {
            p.terminate()
        }
        process = nil

        await events.finishAll()

        // Fail any still-pending requests.
        for (_, cont) in pending {
            cont.resume(throwing: BridgeError.processExited(code: -1))
        }
        pending.removeAll()
    }

    // MARK: - Calling

    /// Make a typed RPC call and await the response.
    public func call<P: Encodable & Sendable, R: Decodable & Sendable>(
        _ method: String,
        params: P,
        timeout: TimeInterval? = nil
    ) async throws -> R {
        guard process != nil else { throw BridgeError.notStarted }

        let id = nextRequestID()
        let request = RPCRequest(id: id, method: method, params: params)
        let line: Data
        do {
            line = try RPCFraming.encodeLine(request)
        } catch {
            throw BridgeError.encode(underlying: error)
        }

        guard let stdin = stdinHandle else { throw BridgeError.notStarted }
        do {
            try stdin.write(contentsOf: line)
        } catch {
            throw BridgeError.writeFailed(underlying: error)
        }

        let effectiveTimeout = timeout ?? config.defaultTimeout
        let rawLine: String = try await withThrowingTaskGroup(
            of: String.self, returning: String.self
        ) { group in
            group.addTask { [self] in
                try await withCheckedThrowingContinuation { cont in
                    // Register synchronously via a nonisolated helper that
                    // briefly re-enters the actor.  Because `pending` is
                    // actor-isolated, writes always happen in order with
                    // `deliverResponse`, even while `start()` is running
                    // higher up the stack.
                    Task { await self.registerPending(id: id, cont: cont) }
                }
            }
            group.addTask {
                try await Task.sleep(
                    nanoseconds: UInt64(effectiveTimeout * 1_000_000_000)
                )
                throw BridgeError.timeout(method: method)
            }
            let first = try await group.next()!
            group.cancelAll()
            return first
        }

        guard let data = rawLine.data(using: .utf8) else {
            throw BridgeError.decode(
                underlying: NSError(domain: "RPC", code: -1,
                                    userInfo: [NSLocalizedDescriptionKey: "non-utf8"]),
                rawLine: rawLine
            )
        }

        do {
            let response = try JSONDecoder().decode(RPCResponse<R>.self, from: data)
            if let err = response.error { throw BridgeError.rpcFailed(err) }
            if let result = response.result { return result }
            throw BridgeError.decode(
                underlying: NSError(domain: "RPC", code: -2,
                                    userInfo: [NSLocalizedDescriptionKey: "no result and no error"]),
                rawLine: rawLine
            )
        } catch let e as BridgeError {
            throw e
        } catch {
            throw BridgeError.decode(underlying: error, rawLine: rawLine)
        }
    }

    // MARK: - Internal: pending request bookkeeping

    private func registerPending(id: Int,
                                 cont: CheckedContinuation<String, Error>) {
        pending[id] = cont
    }

    private func deliverResponse(id: Int, rawLine: String) {
        if let cont = pending.removeValue(forKey: id) {
            cont.resume(returning: rawLine)
        }
        // Unknown id — ignore (could be a duplicate or a response to a
        // cancelled request).
    }

    private func deliverMalformedLine(reason: String, rawLine: String) {
        // Fail all pending requests defensively — the protocol is sick.
        for (_, cont) in pending {
            cont.resume(
                throwing: BridgeError.decode(
                    underlying: NSError(domain: "RPC", code: -3,
                                        userInfo: [NSLocalizedDescriptionKey: reason]),
                    rawLine: rawLine
                )
            )
        }
        pending.removeAll()
    }

    private func dispatchNotification(method: String, rawLine: String) async {
        struct Peek: Decodable { let params: RPCAny? }
        var params: RPCAny? = nil
        if let data = rawLine.data(using: .utf8),
           let peek = try? JSONDecoder().decode(Peek.self, from: data) {
            params = peek.params
        }
        await events.publish(BridgeEvent(method: method, params: params))
    }

    private func nextRequestID() -> Int {
        nextID += 1
        return nextID
    }

    // MARK: - Internal: stdout reader

    /// Ingest a chunk of stdout bytes, split into lines, and dispatch
    /// each classified line.  Runs on the actor (serial) so
    /// `readerBuffer` and `pending` are safe.
    private func ingest(data: Data) async {
        readerBuffer.append(data)
        let lines = RPCFraming.splitLines(from: &readerBuffer)
        for line in lines {
            switch RPCIncoming.classify(line: line) {
            case .response(let id, let raw):
                deliverResponse(id: id, rawLine: raw)
            case .notification(let method, let raw):
                await dispatchNotification(method: method, rawLine: raw)
            case .malformed(let reason, let raw):
                deliverMalformedLine(reason: reason, rawLine: raw)
            }
        }
    }

    // MARK: - Executable discovery

    /// Try a short list of known locations to find `dailystream-core`.
    ///
    /// 1. `$DAILYSTREAM_CORE` environment override.
    /// 2. Embedded inside the running bundle
    ///    (`.../Contents/Frameworks/Python.framework/Versions/*/bin/dailystream-core`).
    /// 3. Repo-local framework — walks up from the running executable
    ///    looking for a sibling `Frameworks/Python.framework/...` (works
    ///    with `swift run` during development).
    /// 4. `which dailystream-core`.
    public static func locateExecutable() throws -> URL {
        // 1. env override
        if let override = ProcessInfo.processInfo.environment["DAILYSTREAM_CORE"],
           !override.isEmpty,
           FileManager.default.isExecutableFile(atPath: override) {
            return URL(fileURLWithPath: override)
        }

        let frameworksRelative = "Frameworks/Python.framework/Versions/3.11/bin/dailystream-core"

        // 2. embedded inside the running bundle
        if let bundleExecURL = Bundle.main.executableURL {
            let candidates = [
                bundleExecURL
                    .deletingLastPathComponent()  // Contents/MacOS
                    .deletingLastPathComponent()  // Contents
                    .appendingPathComponent(frameworksRelative),
            ]
            for c in candidates where FileManager.default.isExecutableFile(atPath: c.path) {
                return c
            }
        }

        // 3. Walk up from the running executable looking for a
        //    sibling `Frameworks/` directory.  This covers:
        //    * `swift run` (.build/<triple>/debug/DailyStreamMac)
        //    * ad-hoc Xcode runs from DerivedData.
        if let exec = Bundle.main.executableURL {
            var url = exec.deletingLastPathComponent()
            for _ in 0..<6 {
                let candidate = url.appendingPathComponent(frameworksRelative)
                if FileManager.default.isExecutableFile(atPath: candidate.path) {
                    return candidate
                }
                url.deleteLastPathComponent()
            }
        }

        // 4. PATH lookup via /usr/bin/env
        let env = Process()
        env.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        env.arguments = ["which", "dailystream-core"]
        let pipe = Pipe()
        env.standardOutput = pipe
        env.standardError = Pipe()
        try? env.run()
        env.waitUntilExit()
        if env.terminationStatus == 0,
           let out = try? pipe.fileHandleForReading.readToEnd(),
           let path = String(data: out, encoding: .utf8)?
               .trimmingCharacters(in: .whitespacesAndNewlines),
           !path.isEmpty,
           FileManager.default.isExecutableFile(atPath: path) {
            return URL(fileURLWithPath: path)
        }

        throw BridgeError.cannotLocatePythonCore
    }
}

// MARK: - Internal handshake result types ------------------------------
// (`app.ping` / `app.shutdown` simply return strings so we read them as
//  `String` directly at call-sites above.)

// MARK: - Convenience typed wrappers -----------------------------------

public extension CoreBridge {
    /// `app.ping` → `"pong"`.
    @discardableResult
    func ping() async throws -> String {
        try await call("app.ping", params: RPCEmptyParams())
    }

    /// `app.version` → `{ rpc_version, python_version }`.
    func version() async throws -> VersionInfo {
        try await call("app.version", params: RPCEmptyParams())
    }

    struct VersionInfo: Decodable, Sendable {
        public let rpc_version: String
        public let python_version: String
    }
}

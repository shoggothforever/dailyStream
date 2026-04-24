// RPCMessage.swift
// Codable types for JSON-RPC 2.0 messages exchanged with the Python core.
//
// Contract (line-delimited JSON, one message per line)
// ----------------------------------------------------
// * Swift → Python: `RPCRequest<Params>` with a numeric `id`.
// * Python → Swift: `RPCResponse<Result>` echoing the same `id`.
// * Python → Swift: `RPCNotification` with no `id` (events).
// * `RPCAny` decodes unknown JSON payloads when a typed shape is not
//   available (common for event `params`).

import Foundation

// MARK: - Versioning -----------------------------------------------------

public enum RPCVersion {
    /// JSON-RPC version string the core understands.
    public static let jsonrpc = "2.0"
}

// MARK: - Request --------------------------------------------------------

/// Client → Server request frame.
public struct RPCRequest<Params: Encodable & Sendable>: Encodable, Sendable {
    public let jsonrpc: String
    public let id: Int
    public let method: String
    public let params: Params

    public init(id: Int, method: String, params: Params) {
        self.jsonrpc = RPCVersion.jsonrpc
        self.id = id
        self.method = method
        self.params = params
    }
}

/// A request with no params (server treats missing/empty `params` the
/// same way).  Having a dedicated type keeps call sites tidy.
public struct RPCEmptyParams: Codable, Sendable {
    public init() {}
}

// MARK: - Response -------------------------------------------------------

/// Error object embedded inside an error response.
public struct RPCError: Decodable, Sendable, Error, CustomStringConvertible {
    public let code: Int
    public let message: String
    public let data: RPCAny?

    public init(code: Int, message: String, data: RPCAny? = nil) {
        self.code = code
        self.message = message
        self.data = data
    }

    public var description: String {
        "RPCError(\(code)): \(message)"
    }
}

/// Server → Client response frame.  Exactly one of `result` / `error`
/// will be populated.
public struct RPCResponse<Result: Decodable & Sendable>: Decodable, Sendable {
    public let jsonrpc: String
    public let id: Int?
    public let result: Result?
    public let error: RPCError?
}

// MARK: - Notification ---------------------------------------------------

/// Server → Client notification (no `id` — fire-and-forget).
public struct RPCNotification: Decodable, Sendable {
    public let jsonrpc: String
    public let method: String
    public let params: RPCAny?
}

// MARK: - RPCAny (dynamic JSON) -----------------------------------------

/// A minimal dynamic JSON value so we can decode messages whose payload
/// shape we don't know at compile time (notifications, `config.get`,
/// etc.).  Small, ergonomic, and `Sendable` under Swift strict concurrency.
public enum RPCAny: Sendable, Equatable {
    case null
    case bool(Bool)
    case int(Int)
    case double(Double)
    case string(String)
    case array([RPCAny])
    case object([String: RPCAny])
}

extension RPCAny: Decodable {
    public init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() {
            self = .null
        } else if let b = try? c.decode(Bool.self) {
            self = .bool(b)
        } else if let i = try? c.decode(Int.self) {
            self = .int(i)
        } else if let d = try? c.decode(Double.self) {
            self = .double(d)
        } else if let s = try? c.decode(String.self) {
            self = .string(s)
        } else if let a = try? c.decode([RPCAny].self) {
            self = .array(a)
        } else if let o = try? c.decode([String: RPCAny].self) {
            self = .object(o)
        } else {
            throw DecodingError.dataCorruptedError(
                in: c, debugDescription: "Unsupported JSON value"
            )
        }
    }
}

extension RPCAny: Encodable {
    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .null: try c.encodeNil()
        case .bool(let b): try c.encode(b)
        case .int(let i): try c.encode(i)
        case .double(let d): try c.encode(d)
        case .string(let s): try c.encode(s)
        case .array(let a): try c.encode(a)
        case .object(let o): try c.encode(o)
        }
    }
}

public extension RPCAny {
    /// Decode this dynamic value into a concrete type.
    ///
    /// Re-serializes through JSONEncoder; fine for the small payloads
    /// we receive (<100 KB).
    func decode<T: Decodable>(as type: T.Type) throws -> T {
        let data = try JSONEncoder().encode(self)
        return try JSONDecoder().decode(type, from: data)
    }

    var stringValue: String? {
        if case .string(let s) = self { return s }
        return nil
    }
    var intValue: Int? {
        switch self {
        case .int(let i): return i
        case .double(let d): return Int(d)
        default: return nil
        }
    }
    var boolValue: Bool? {
        if case .bool(let b) = self { return b }
        return nil
    }
    var objectValue: [String: RPCAny]? {
        if case .object(let o) = self { return o }
        return nil
    }
    var arrayValue: [RPCAny]? {
        if case .array(let a) = self { return a }
        return nil
    }
}

// MARK: - Bridge errors --------------------------------------------------

/// Surface-level errors for callers of `CoreBridge`.
public enum BridgeError: Error, CustomStringConvertible {
    case notStarted
    case alreadyStarted
    case processExited(code: Int32)
    case decode(underlying: Error, rawLine: String)
    case encode(underlying: Error)
    case writeFailed(underlying: Error)
    case timeout(method: String)
    case rpcFailed(RPCError)
    case cannotLocatePythonCore

    public var description: String {
        switch self {
        case .notStarted: return "CoreBridge: not started"
        case .alreadyStarted: return "CoreBridge: already started"
        case .processExited(let c): return "CoreBridge: python process exited with code \(c)"
        case .decode(let e, let line):
            return "CoreBridge: failed to decode line: \(e) raw=\(line.prefix(200))"
        case .encode(let e): return "CoreBridge: failed to encode request: \(e)"
        case .writeFailed(let e): return "CoreBridge: write failed: \(e)"
        case .timeout(let m): return "CoreBridge: timeout calling \(m)"
        case .rpcFailed(let e): return "CoreBridge: \(e.description)"
        case .cannotLocatePythonCore:
            return "CoreBridge: cannot locate dailystream-core executable"
        }
    }
}

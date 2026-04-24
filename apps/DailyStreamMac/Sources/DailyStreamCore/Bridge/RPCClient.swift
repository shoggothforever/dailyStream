// RPCClient.swift
// Low-level framed-JSON IO helpers.
//
// These helpers are intentionally state-less: they work on raw `Data`
// buffers so they can be unit-tested without spawning a subprocess.
// `CoreBridge` glues them to `Process` / `Pipe` at runtime.

import Foundation

/// Encodes an `Encodable` value as one newline-terminated JSON line.
///
/// The Python core parses messages line by line (`for line in stdin`),
/// so every request must end with exactly one `\n` and contain no
/// embedded raw newlines (JSON encoding escapes any line breaks inside
/// string values automatically).
public enum RPCFraming {
    public static func encodeLine<T: Encodable>(_ value: T) throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.withoutEscapingSlashes]
        var data = try encoder.encode(value)
        data.append(0x0A) // '\n'
        return data
    }

    /// Split a buffer into complete lines.  Returns decoded strings and
    /// the trailing partial line (no `\n` yet).  Callers should keep
    /// the leftover for the next read.
    public static func splitLines(from buffer: inout Data) -> [String] {
        var lines: [String] = []
        while let newlineIdx = buffer.firstIndex(of: 0x0A) {
            let lineData = buffer.subdata(in: buffer.startIndex..<newlineIdx)
            // Drop the newline byte itself.
            buffer.removeSubrange(buffer.startIndex...newlineIdx)
            if let line = String(data: lineData, encoding: .utf8),
               !line.isEmpty {
                lines.append(line)
            }
        }
        return lines
    }
}

/// Classifier for a raw JSON line coming from the core.  Avoids a double
/// decode of every payload by letting the bridge decide which typed
/// response / notification shape to go after.
public enum RPCIncoming {
    case response(id: Int, rawLine: String)
    case notification(method: String, rawLine: String)
    case malformed(reason: String, rawLine: String)

    /// Cheap peek: only parses the "shape" fields (id / method).
    public static func classify(line: String) -> RPCIncoming {
        struct Peek: Decodable {
            let id: Int?
            let method: String?
        }
        guard let data = line.data(using: .utf8) else {
            return .malformed(reason: "non-utf8 input", rawLine: line)
        }
        do {
            let peek = try JSONDecoder().decode(Peek.self, from: data)
            if let id = peek.id {
                return .response(id: id, rawLine: line)
            }
            if let method = peek.method {
                return .notification(method: method, rawLine: line)
            }
            return .malformed(reason: "missing id and method", rawLine: line)
        } catch {
            return .malformed(reason: "\(error)", rawLine: line)
        }
    }
}

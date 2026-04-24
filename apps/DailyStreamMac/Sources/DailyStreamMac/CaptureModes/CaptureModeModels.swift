// CaptureModeModels.swift
// Swift mirror of the Python capture_modes data model.
//
// Shapes map 1:1 to `capture_modes.{Mode,Preset,Attachment,Source}.to_dict`
// so we can round-trip through the RPC boundary without any custom
// JSON mapping helpers.
//
// Design notes
// ------------
// * Each `Identifiable` struct uses a stable `id` string that Python
//   generated (slug of the name).  Swift never regenerates IDs — that
//   keeps hotkey registrations stable across saves.
// * `Attachment.params` is an opaque JSON object; we preserve it as a
//   `[String: JSONValue]` so the Designer UI can render arbitrary
//   parameter schemas without needing a Swift struct per attachment.

import Foundation

// MARK: - JSON value helper ---------------------------------------------

/// A minimal JSON value type used to store free-form attachment params
/// without losing type information during encode/decode.
public enum JSONValue: Codable, Equatable, Sendable, Hashable {
    case string(String)
    case int(Int)
    case double(Double)
    case bool(Bool)
    case array([JSONValue])
    case object([String: JSONValue])
    case null

    public init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() {
            self = .null; return
        }
        if let b = try? c.decode(Bool.self) {
            self = .bool(b); return
        }
        if let i = try? c.decode(Int.self) {
            self = .int(i); return
        }
        if let d = try? c.decode(Double.self) {
            self = .double(d); return
        }
        if let s = try? c.decode(String.self) {
            self = .string(s); return
        }
        if let a = try? c.decode([JSONValue].self) {
            self = .array(a); return
        }
        if let o = try? c.decode([String: JSONValue].self) {
            self = .object(o); return
        }
        throw DecodingError.dataCorruptedError(
            in: c, debugDescription: "Unsupported JSON value"
        )
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .null:         try c.encodeNil()
        case .bool(let b):  try c.encode(b)
        case .int(let i):   try c.encode(i)
        case .double(let d): try c.encode(d)
        case .string(let s): try c.encode(s)
        case .array(let a): try c.encode(a)
        case .object(let o): try c.encode(o)
        }
    }

    // Convenience accessors used by the Designer UI --------------------

    public var stringValue: String? { if case .string(let v) = self { return v } else { return nil } }
    public var intValue: Int? {
        switch self {
        case .int(let v): return v
        case .double(let v): return Int(v)
        default: return nil
        }
    }
    public var doubleValue: Double? {
        switch self {
        case .double(let v): return v
        case .int(let v): return Double(v)
        default: return nil
        }
    }
    public var boolValue: Bool? { if case .bool(let v) = self { return v } else { return nil } }
    public var arrayValue: [JSONValue]? { if case .array(let v) = self { return v } else { return nil } }
    public var objectValue: [String: JSONValue]? { if case .object(let v) = self { return v } else { return nil } }
}

// MARK: - Enumerations ---------------------------------------------------

public enum CaptureSourceKind: String, Codable, CaseIterable, Sendable, Equatable {
    case interactive
    case fullscreen
    case region
    case window
    case clipboard

    public var label: String {
        switch self {
        case .interactive: return "Free Selection"
        case .fullscreen:  return "Full Screen"
        case .region:      return "Fixed Region"
        case .window:      return "Window"
        case .clipboard:   return "Clipboard"
        }
    }

    public var iconSystemName: String {
        switch self {
        case .interactive: return "scissors"
        case .fullscreen:  return "display"
        case .region:      return "rectangle.dashed"
        case .window:      return "macwindow"
        case .clipboard:   return "doc.on.clipboard"
        }
    }
}

public enum AttachmentKind: String, Codable, CaseIterable, Sendable {
    case strategy
    case feedback
    case windowCtrl = "window_ctrl"
    case post
    case delivery

    public var label: String {
        switch self {
        case .strategy:   return "Strategy"
        case .feedback:   return "Feedback"
        case .windowCtrl: return "Window Control"
        case .post:       return "Post-processing"
        case .delivery:   return "Delivery"
        }
    }

    public var isSingleChoice: Bool {
        self == .strategy || self == .delivery
    }

    public var order: Int {
        switch self {
        case .strategy:   return 0
        case .feedback:   return 1
        case .windowCtrl: return 2
        case .post:       return 3
        case .delivery:   return 4
        }
    }
}

// MARK: - Core entities --------------------------------------------------

public struct CaptureSource: Codable, Equatable, Sendable, Hashable {
    public var kind: CaptureSourceKind
    public var region: String?

    public init(kind: CaptureSourceKind = .interactive, region: String? = nil) {
        self.kind = kind
        self.region = region
    }
}

public struct CaptureAttachment: Codable, Identifiable, Equatable, Sendable, Hashable {
    public var id: String
    public var params: [String: JSONValue]

    public init(id: String, params: [String: JSONValue] = [:]) {
        self.id = id
        self.params = params
    }
}

public struct CapturePreset: Codable, Identifiable, Equatable, Sendable, Hashable {
    public var id: String
    public var name: String
    public var emoji: String
    public var source: CaptureSource
    public var attachments: [CaptureAttachment]
    public var hotkey: String?

    public init(
        id: String,
        name: String,
        emoji: String = "📸",
        source: CaptureSource = CaptureSource(),
        attachments: [CaptureAttachment] = [],
        hotkey: String? = nil
    ) {
        self.id = id
        self.name = name
        self.emoji = emoji
        self.source = source
        self.attachments = attachments
        self.hotkey = hotkey
    }
}

public struct CaptureMode: Codable, Identifiable, Equatable, Sendable, Hashable {
    public var id: String
    public var name: String
    public var emoji: String
    public var presets: [CapturePreset]

    public init(
        id: String,
        name: String,
        emoji: String = "🗂",
        presets: [CapturePreset] = []
    ) {
        self.id = id
        self.name = name
        self.emoji = emoji
        self.presets = presets
    }
}

public struct CaptureModesState: Codable, Equatable, Sendable {
    public var modes: [CaptureMode]
    public var activeModeID: String?

    enum CodingKeys: String, CodingKey {
        case modes
        case activeModeID = "active_mode_id"
    }

    public init(modes: [CaptureMode] = [], activeModeID: String? = nil) {
        self.modes = modes
        self.activeModeID = activeModeID
    }

    public var activeMode: CaptureMode? {
        guard let id = activeModeID else { return nil }
        return modes.first { $0.id == id }
    }
}

// MARK: - Attachment catalog entry --------------------------------------

public struct AttachmentParamSchema: Codable, Equatable, Sendable, Hashable {
    public var kind: String          // int / float / bool / string / string_list / enum / tag_list
    public var defaultValue: JSONValue?
    public var help: String?
    public var enumValues: [String]?
    public var min: Double?
    public var max: Double?

    enum CodingKeys: String, CodingKey {
        case kind
        case defaultValue = "default"
        case help
        case enumValues = "enum"
        case min
        case max
    }
}

public struct AttachmentCatalogEntry: Codable, Identifiable, Equatable, Sendable, Hashable {
    public var id: String
    public var kind: AttachmentKind
    public var label: String
    public var description: String
    public var icon: String
    public var paramsSchema: [String: AttachmentParamSchema]
    public var mutuallyExclusive: [String]

    enum CodingKeys: String, CodingKey {
        case id, kind, label, description, icon
        case paramsSchema = "params_schema"
        case mutuallyExclusive = "mutually_exclusive"
    }

    public var isSingleChoice: Bool { kind.isSingleChoice }
}

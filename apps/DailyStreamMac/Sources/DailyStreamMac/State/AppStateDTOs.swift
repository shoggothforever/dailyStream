// AppStateDTOs.swift
// Shared DTO shapes consumed by AppState across files.
//
// Kept at `internal` visibility (module-private) because:
//   * `listenForEvents` in AppState.swift decodes them from JSON-RPC events,
//   * `handlePresetExecuted` in AppState+CaptureModes.swift re-uses them,
// and both files need to reference the same types.

import Foundation
import DailyStreamCore

// MARK: - Public DTOs --------------------------------------------------

/// Screenshot preset as returned by the Python core.
public struct ScreenshotPreset: Decodable, Identifiable, Sendable, Equatable {
    public let name: String
    public let region: String
    public let hotkey: String?

    public var id: String { name }
}

// MARK: - Internal DTOs ------------------------------------------------

/// Minimal DTO used only for discarding `workspace.open` return shape —
/// we re-read state via `refreshStatus` afterwards.
struct WorkspaceSummaryDTO: Decodable {}

/// Event payload shapes used by `listenForEvents` — all optional fields
/// because the Python side may omit them on failure paths.
struct IntervalEventDTO: Decodable {
    let mode_id: String?
    let preset_id: String?
    let seconds: Int?
    let max_count: Int?
    let captured: Int?
}

struct NotificationDTO: Decodable {
    let title: String
    let body: String
}

struct SoundDTO: Decodable {
    let volume: Double?
}

struct HookFailedDTO: Decodable {
    let kind: String?
    let command: String?
    let error: String?
    let stderr: String?
    let returncode: Int?
}

struct FrameDTO: Decodable {
    let path: String?
    let index: Int
    let source_kind: String
    let skipped: Bool
    let error: String?
    let post_artifacts: [String: JSONValue]?
}

struct ExecutionReportDTO: Decodable {
    let mode_id: String
    let preset_id: String
    let preset_name: String
    let silent: Bool
    let cancelled: Bool
    let error: String?
    let frames: [FrameDTO]
}

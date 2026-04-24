// AppState.swift
// Observable global state consumed by menu bar and future UI surfaces.
//
// Design notes
// ------------
// * All mutations happen on the MainActor so SwiftUI views stay happy.
// * The store owns the `CoreBridge` and is the only component that calls
//   into it.  Views call methods on `AppState` (e.g. `newWorkspace()`)
//   rather than touching the bridge directly.

import Foundation
import SwiftUI
import DailyStreamCore

/// Visual state of the menu bar icon.
public enum MenuBarIconState: Sendable {
    case idle        // no active workspace
    case recording   // workspace active
    case capturing   // brief transient (screenshot / clipboard)
}

/// Lightweight workspace snapshot for the menu.
public struct WorkspaceSummary: Sendable, Equatable {
    public let isActive: Bool
    public let title: String?
    public let activePipeline: String?
    public let pipelines: [String]

    public static let inactive = WorkspaceSummary(
        isActive: false, title: nil, activePipeline: nil, pipelines: []
    )
}

@MainActor
public final class AppState: ObservableObject {
    // MARK: - Published state

    @Published public private(set) var iconState: MenuBarIconState = .idle
    @Published public private(set) var workspace: WorkspaceSummary = .inactive
    @Published public private(set) var coreReady: Bool = false
    @Published public private(set) var lastError: String? = nil
    @Published public private(set) var toastMessage: ToastMessage? = nil
    @Published public private(set) var aiDefaultMode: String = "off"
    @Published public private(set) var presets: [ScreenshotPreset] = []

    // MARK: - Dependencies

    public let bridge: CoreBridge

    public init(bridge: CoreBridge? = nil) {
        self.bridge = bridge ?? CoreBridge()
    }

    // MARK: - Lifecycle

    /// Boot the Python core and pull the current workspace status.
    public func boot() async {
        do {
            try await bridge.start()
            coreReady = true
            await refreshStatus()
            await refreshAiDefaultMode()
            await refreshPresets()
            Task { await self.listenForEvents() }
        } catch {
            coreReady = false
            lastError = "Core failed to start: \(error)"
        }
    }

    public func shutdown() async {
        await bridge.shutdown()
        coreReady = false
    }

    // MARK: - Workspace actions

    public func refreshStatus() async {
        struct StatusDTO: Decodable {
            let is_active: Bool
            let title: String?
            let active_pipeline: String?
            let pipelines: [String]?
        }
        do {
            let s: StatusDTO = try await bridge.call(
                "workspace.status", params: RPCEmptyParams()
            )
            workspace = WorkspaceSummary(
                isActive: s.is_active,
                title: s.title,
                activePipeline: s.active_pipeline,
                pipelines: s.pipelines ?? []
            )
            iconState = s.is_active ? .recording : .idle
        } catch {
            lastError = "status failed: \(error)"
        }
    }

    public func refreshAiDefaultMode() async {
        struct Result: Decodable { let key: String; let value: String }
        struct Params: Encodable, Sendable { let key: String }
        do {
            let r: Result = try await bridge.call(
                "config.get", params: Params(key: "ai_default_mode")
            )
            aiDefaultMode = r.value
        } catch {
            aiDefaultMode = "off"
        }
    }

    /// Create a new workspace via `workspace.create`.
    public func createWorkspace(_ values: NewWorkspaceValues) async {
        struct Params: Encodable, Sendable {
            let path: String?
            let title: String?
            let ai_mode: String
        }
        struct Result: Decodable {
            let workspace_dir: String
            let ai_mode: String
        }
        do {
            let r: Result = try await bridge.call(
                "workspace.create",
                params: Params(
                    path: values.folder?.path,
                    title: values.title,
                    ai_mode: values.aiMode
                )
            )
            await refreshStatus()
            showToast(
                title: "Workspace created",
                subtitle: "AI: \(r.ai_mode) · \(r.workspace_dir)"
            )
        } catch {
            showToast(title: "Create failed", subtitle: "\(error)")
        }
    }

    /// End the current workspace, surfacing the timeline path if any.
    public func endWorkspace() async {
        struct Result: Decodable { let timeline_report: String? }
        do {
            let r: Result = try await bridge.call(
                "workspace.end", params: RPCEmptyParams()
            )
            await refreshStatus()
            if let report = r.timeline_report {
                showToast(title: "Workspace ended", subtitle: report)
            } else {
                showToast(title: "Workspace ended")
            }
        } catch {
            showToast(title: "End failed", subtitle: "\(error)")
        }
    }

    /// Open an existing workspace directory.  Mirrors
    /// `_on_open_workspace` logic: if the chosen folder is itself a
    /// workspace, open it directly; otherwise look for the most recent
    /// sub-folder containing a `workspace_meta.json`.
    public func openWorkspaceAt(_ folder: URL) async {
        struct Params: Encodable, Sendable { let path: String }
        do {
            let _: WorkspaceSummaryDTO = try await bridge.call(
                "workspace.open", params: Params(path: folder.path)
            )
            await refreshStatus()
            showToast(title: "Workspace opened",
                      subtitle: workspace.title ?? folder.lastPathComponent)
        } catch {
            // Fall back: scan immediate sub-directories and try the
            // most recent one.
            if let latest = mostRecentWorkspaceChild(of: folder) {
                do {
                    let _: WorkspaceSummaryDTO = try await bridge.call(
                        "workspace.open", params: Params(path: latest.path)
                    )
                    await refreshStatus()
                    showToast(title: "Workspace opened",
                              subtitle: workspace.title ?? latest.lastPathComponent)
                    return
                } catch {
                    showToast(title: "Open failed", subtitle: "\(error)")
                    return
                }
            }
            showToast(title: "Open failed", subtitle: "No workspace_meta.json found")
        }
    }

    // MARK: - Capture actions

    /// Drag-to-select a screen region and return the coordinates
    /// without actually saving a screenshot.  Used by Create Preset.
    public func selectRegion() async -> String? {
        struct Result: Decodable { let region: String }
        do {
            let r: Result = try await bridge.call(
                "capture.select_region", params: RPCEmptyParams()
            )
            return r.region
        } catch BridgeError.rpcFailed(let err) where err.code == -32001 {
            return nil  // silent cancel
        } catch {
            showToast(title: "Region select failed", subtitle: "\(error)")
            return nil
        }
    }

    public func refreshPresets() async {
        struct Result: Decodable { let presets: [ScreenshotPreset] }
        do {
            let r: Result = try await bridge.call(
                "preset.list", params: RPCEmptyParams()
            )
            presets = r.presets
        } catch {
            presets = []
        }
    }

    public func createPreset(_ values: PresetValues) async {
        struct Params: Encodable, Sendable {
            let name: String
            let region: String
            let hotkey: String?
        }
        struct Result: Decodable { let preset: ScreenshotPreset }
        do {
            let _: Result = try await bridge.call(
                "preset.create",
                params: Params(name: values.name,
                               region: values.region,
                               hotkey: values.hotkey)
            )
            await refreshPresets()
            var subtitle = values.region
            if let hk = values.hotkey, !hk.isEmpty {
                subtitle += "  [\(hk)]"
            }
            showToast(title: "Preset saved: \(values.name)",
                      subtitle: subtitle)
        } catch {
            showToast(title: "Preset save failed", subtitle: "\(error)")
        }
    }

    public func deletePreset(name: String) async {
        struct Params: Encodable, Sendable { let name: String }
        struct Result: Decodable { let deleted: String }
        do {
            let _: Result = try await bridge.call(
                "preset.delete", params: Params(name: name)
            )
            await refreshPresets()
            showToast(title: "Preset deleted", subtitle: name)
        } catch {
            showToast(title: "Preset delete failed", subtitle: "\(error)")
        }
    }

    /// Create a new pipeline and activate it immediately.  Matches the
    /// rumps flow which always calls `activate_pipeline` after create.
    public func createPipeline(_ values: NewPipelineValues) async {
        struct Params: Encodable, Sendable {
            let name: String
            let description: String
            let goal: String
        }
        struct Result: Decodable { let name: String; let active: Bool }
        do {
            let _: Result = try await bridge.call(
                "pipeline.create",
                params: Params(name: values.name,
                               description: values.description,
                               goal: values.goal)
            )
            await refreshStatus()
            showToast(title: "Pipeline created",
                      subtitle: values.name)
        } catch {
            showToast(title: "Create failed", subtitle: "\(error)")
        }
    }

    /// Switch to a different pipeline.
    public func switchPipeline(to name: String) async {
        struct Params: Encodable, Sendable { let name: String }
        struct Result: Decodable { let active: String }
        do {
            let _: Result = try await bridge.call(
                "pipeline.switch", params: Params(name: name)
            )
            await refreshStatus()
        } catch {
            showToast(title: "Switch failed", subtitle: "\(error)")
        }
    }

    /// Grab the clipboard and present the capture HUD.
    ///
    /// Semantics match `_do_clipboard`:
    /// * empty clipboard → toast "Clipboard Empty"
    /// * image in clipboard → save via `capture.clipboard.save_image`,
    ///   pass the resulting path to the HUD
    /// * user cancel → discard (image file, if any, is kept on disk —
    ///   matches rumps behaviour)
    public func captureClipboard() async {
        guard workspace.isActive,
              let pipeline = workspace.activePipeline else {
            showToast(title: "No active pipeline",
                      subtitle: "Create and activate one first.")
            return
        }

        struct Grab: Decodable {
            let content: String?
            let type: String
        }
        let grab: Grab
        do {
            grab = try await bridge.call(
                "capture.clipboard.grab", params: RPCEmptyParams()
            )
        } catch {
            showToast(title: "Clipboard read failed", subtitle: "\(error)")
            return
        }

        guard let rawContent = grab.content, !rawContent.isEmpty else {
            showToast(title: "Clipboard empty")
            return
        }

        // Image needs saving to disk first.
        var kind = grab.type
        var actualContent = rawContent
        var thumbnailURL: URL? = nil
        if rawContent == "__clipboard_image__" || kind == "image" {
            struct Result: Decodable { let path: String }
            do {
                let r: Result = try await bridge.call(
                    "capture.clipboard.save_image", params: RPCEmptyParams()
                )
                actualContent = r.path
                kind = "image"
                thumbnailURL = URL(fileURLWithPath: r.path)
            } catch {
                showToast(title: "Clipboard image save failed",
                          subtitle: "\(error)")
                return
            }
        }

        let result: ClipboardCaptureResult? = await HUDHost.shared.present { close in
            ClipboardCaptureView(
                kind: kind,
                content: actualContent,
                pipeline: pipeline,
                thumbnailURL: thumbnailURL,
                onClose: close
            )
        }

        switch result {
        case .save(let desc):
            await feedByKind(kind: kind, content: actualContent,
                             description: desc, pipeline: pipeline)
        case .cancel, .none:
            // Kept on disk — matches rumps behaviour for images.
            break
        }
    }

    private func feedByKind(kind: String, content: String,
                            description: String, pipeline: String) async {
        struct Params: Encodable, Sendable {
            let content: String?
            let path: String?
            let description: String
            let pipeline: String?
        }
        struct Entry: Decodable { let entry_index: Int; let pipeline: String }
        let method: String
        let params: Params
        switch kind {
        case "image":
            method = "feed.image"
            params = Params(content: nil, path: content,
                            description: description, pipeline: pipeline)
        case "url":
            method = "feed.url"
            params = Params(content: content, path: nil,
                            description: description, pipeline: pipeline)
        default:
            method = "feed.text"
            params = Params(content: content, path: nil,
                            description: description, pipeline: pipeline)
        }
        do {
            let _: Entry = try await bridge.call(method, params: params)
            showToast(title: "Saved to \(pipeline)",
                      subtitle: description.isEmpty ? kind : description)
        } catch {
            showToast(title: "Save failed", subtitle: "\(error)")
        }
    }

    /// Trigger a screenshot via the core.
    ///
    /// Semantics match the Python rumps flow:
    /// * user cancels screencapture → **silently** return (no toast)
    /// * on success → ask for a description via HUD;
    /// * description HUD cancelled → **delete** the screenshot file;
    /// * description HUD saved → call `feed.image`, emit success toast.
    public func takeScreenshot(mode: String = "interactive",
                               region: String? = nil,
                               presetName: String? = nil) async {
        guard workspace.isActive,
              let pipeline = workspace.activePipeline else {
            showToast(title: "No active pipeline",
                      subtitle: "Create and activate one first.")
            return
        }

        iconState = .capturing
        defer { Task { await self.refreshStatus() } }

        struct CaptureParams: Encodable, Sendable {
            let mode: String
            let region: String?
        }
        struct CaptureResult: Decodable { let path: String }

        let capture: CaptureResult
        do {
            capture = try await bridge.call(
                "capture.screenshot",
                params: CaptureParams(mode: mode, region: region)
            )
        } catch BridgeError.rpcFailed(let err) where err.code == -32001 {
            // The Python side raises StateConflict with this code when
            // screencapture exits non-zero (user pressed Esc).  That is
            // NOT an error from the user's perspective.
            return
        } catch {
            showToast(title: "Screenshot failed", subtitle: "\(error)")
            return
        }

        // Ask for a description.
        let fileURL = URL(fileURLWithPath: capture.path)
        let result: ScreenshotDescResult? = await HUDHost.shared.present { close in
            ScreenshotDescView(
                filename: fileURL.lastPathComponent,
                pipeline: pipeline,
                presetName: presetName,
                thumbnailURL: fileURL,
                onClose: close
            )
        }

        switch result {
        case .save(let desc):
            await feedImage(path: capture.path, description: desc,
                            pipeline: pipeline)
        case .cancel, .none:
            // Cancel (HUD Esc) or dismissed → delete the orphan file.
            try? FileManager.default.removeItem(at: fileURL)
        }
    }

    private func feedImage(path: String, description: String,
                           pipeline: String) async {
        struct Params: Encodable, Sendable {
            let path: String
            let description: String
            let pipeline: String?
        }
        struct Entry: Decodable {
            let entry_index: Int
            let pipeline: String
        }
        do {
            let _: Entry = try await bridge.call(
                "feed.image",
                params: Params(path: path, description: description,
                               pipeline: pipeline)
            )
            let shortName = URL(fileURLWithPath: path).lastPathComponent
            showToast(
                title: "Saved to \(pipeline)",
                subtitle: description.isEmpty ? shortName : description
            )
        } catch {
            showToast(title: "Save failed", subtitle: "\(error)")
        }
    }

    // MARK: - Toast

    public func showToast(title: String, subtitle: String? = nil) {
        toastMessage = ToastMessage(title: title, subtitle: subtitle)
    }

    public func dismissToast() {
        toastMessage = nil
    }

    // MARK: - Event consumption

    private func listenForEvents() async {
        for await evt in bridge.events.events() {
            switch evt.method {
            case "workspace.changed":
                await refreshStatus()
            case "ai.analysis_completed":
                showToast(title: "AI analysis ready")
            default:
                break
            }
        }
    }

    // MARK: - Internal helpers

    private func mostRecentWorkspaceChild(of folder: URL) -> URL? {
        let fm = FileManager.default
        guard let children = try? fm.contentsOfDirectory(
            at: folder, includingPropertiesForKeys: [.contentModificationDateKey],
            options: [.skipsHiddenFiles]
        ) else { return nil }
        let candidates = children.filter { url in
            var isDir: ObjCBool = false
            let exists = fm.fileExists(atPath: url.appendingPathComponent("workspace_meta.json").path, isDirectory: &isDir)
            return exists && !isDir.boolValue
        }
        return candidates.sorted { (a, b) in
            let ad = (try? a.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
            let bd = (try? b.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
            return ad > bd
        }.first
    }
}

// MARK: - Toast --------------------------------------------------------

public struct ToastMessage: Identifiable, Equatable, Sendable {
    public let id = UUID()
    public let title: String
    public let subtitle: String?
    public let createdAt: Date = Date()
}

// MARK: - DTOs shared between AppState call sites ----------------------

/// Screenshot preset as returned by the Python core.
public struct ScreenshotPreset: Decodable, Identifiable, Sendable, Equatable {
    public let name: String
    public let region: String
    public let hotkey: String?

    public var id: String { name }
}

/// Minimal DTO used only for discarding `workspace.open` return shape —
/// we re-read state via `refreshStatus` afterwards.
private struct WorkspaceSummaryDTO: Decodable {}

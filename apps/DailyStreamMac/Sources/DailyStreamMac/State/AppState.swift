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
import UniformTypeIdentifiers
import UserNotifications
import DailyStreamCore

/// Visual state of the menu bar icon.
public enum MenuBarIconState: Sendable {
    case idle        // no active workspace
    case recording   // workspace active
    case capturing   // brief transient (screenshot / clipboard)
    case flashing    // feedback burst (``flash_menubar`` attachment)
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
    @Published public private(set) var screenshotMode: String = "interactive"
    @Published public private(set) var presets: [ScreenshotPreset] = []
    /// Called whenever the preset list changes so HotkeyManager can
    /// re-register dynamic shortcuts.
    public var onPresetsChanged: (([ScreenshotPreset]) -> Void)?

    // -- Capture Mode Designer state -----------------------------------

    /// Full Mode/Preset/Attachment state mirrored from the Python core.
    @Published public private(set) var captureModes: CaptureModesState = .init()
    /// Available attachments (static catalog from Python).
    @Published public private(set) var attachmentCatalog: [AttachmentCatalogEntry] = []
    /// List of currently-running interval captures keyed by
    /// "mode_id/preset_id" — used by the menu bar to show a "Stop" item.
    @Published public private(set) var runningIntervals: Set<String> = []
    /// Called whenever the active Mode's preset list changes so the
    /// HotkeyManager can re-register its bindings.
    public var onActiveModePresetsChanged: (([CapturePreset]) -> Void)?

    /// Convenience accessor used by HotkeyManager + MenuBar UI.
    public var activeModePresets: [CapturePreset] {
        captureModes.activeMode?.presets ?? []
    }
    /// Remembers the last workspace dir so the user can quickly reopen it.
    @Published public private(set) var lastWorkspacePath: String? = nil

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
            await refreshScreenshotMode()
            await refreshPresets()
            await refreshAttachmentCatalog()
            await refreshCaptureModes()
            await refreshLastWorkspacePath()
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

    public func refreshScreenshotMode() async {
        struct Result: Decodable { let key: String; let value: String }
        struct Params: Encodable, Sendable { let key: String }
        do {
            let r: Result = try await bridge.call(
                "config.get", params: Params(key: "screenshot_mode")
            )
            screenshotMode = r.value
        } catch {
            screenshotMode = "interactive"
        }
    }

    /// Populate lastWorkspacePath from the most recent workspace on disk.
    private func refreshLastWorkspacePath() async {
        // Skip if we already have an active workspace or a remembered path
        if workspace.isActive || lastWorkspacePath != nil { return }
        struct RecentItem: Decodable {
            let workspace_path: String
        }
        struct Params: Encodable, Sendable { let limit: Int }
        do {
            let items: [RecentItem] = try await bridge.call(
                "workspace.list_recent", params: Params(limit: 1)
            )
            if let first = items.first {
                lastWorkspacePath = first.workspace_path
            }
        } catch {}
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
            showError(title: "Create failed", error: error)
        }
    }

    /// End the current workspace, surfacing the timeline path if any.
    /// Automatically opens Daily Review on success.
    public func endWorkspace() async {
        struct Result: Decodable { let timeline_report: String? }
        do {
            // Remember the workspace path for quick reopen.
            let wsPath = await workspaceDirPath()

            // Grab structured data BEFORE ending (workspace is still active).
            let reviewData = try? await fetchReviewData()

            let r: Result = try await bridge.call(
                "workspace.end", params: RPCEmptyParams()
            )
            lastWorkspacePath = wsPath
            await refreshStatus()
            if let report = r.timeline_report {
                showToast(title: "Workspace ended", subtitle: report)
            } else {
                showToast(title: "Workspace ended")
            }

            // Show Daily Review window if we got data.
            if let data = reviewData {
                DailyReviewWindow.shared.show(data: data, bridge: bridge)
            }
        } catch {
            showError(title: "End failed", error: error)
        }
    }

    /// Quickly reopen the last ended workspace (same directory).
    public func reopenLastWorkspace() async {
        guard let path = lastWorkspacePath else {
            showToast(title: "No recent workspace")
            return
        }
        await openWorkspaceAt(URL(fileURLWithPath: path))
    }

    /// Fetch structured timeline data for the Daily Review window.
    func fetchReviewData() async throws -> ReviewData? {
        let data: ReviewData = try await bridge.call(
            "timeline.export_structured", params: RPCEmptyParams()
        )
        if data.entries.isEmpty { return nil }
        return data
    }

    /// Manually open the Daily Review window for the current workspace.
    public func showDailyReview() async {
        do {
            if let data = try await fetchReviewData() {
                DailyReviewWindow.shared.show(data: data, bridge: bridge)
            } else {
                showToast(title: "No entries to review")
            }
        } catch {
            showError(title: "Review unavailable", error: error)
        }
    }

    /// Open the native Markdown stream viewer for the current workspace.
    public func showStreamViewer() async {
        guard let path = await workspaceDirPath() else {
            showToast(title: "No active workspace")
            return
        }
        StreamViewerWindow.shared.show(workspacePath: path)
    }

    /// Open stream.md in the user's preferred editor (VS Code / default).
    public func openStreamInEditor() async {
        guard let path = await workspaceDirPath() else {
            showToast(title: "No active workspace")
            return
        }
        let vm = StreamViewerViewModel(workspacePath: path)
        vm.openInEditor()
    }

    /// Fetch the workspace_dir from the core.
    private func workspaceDirPath() async -> String? {
        struct Status: Decodable {
            let is_active: Bool
            let workspace_dir: String?
        }
        do {
            let s: Status = try await bridge.call(
                "workspace.status", params: RPCEmptyParams()
            )
            return s.is_active ? s.workspace_dir : nil
        } catch {
            return nil
        }
    }

    /// Open an existing workspace directory.  Mirrors
    /// `_on_open_workspace` logic: if the chosen folder is itself a
    /// workspace, open it directly; otherwise look for the most recent
    /// sub-folder containing a `workspace_meta.json`.
    public func openWorkspaceAt(_ folder: URL) async {
        struct Params: Encodable, Sendable { let path: String; let force: Bool }

        // First attempt: open the folder directly (force-end any active ws).
        do {
            let _: WorkspaceSummaryDTO = try await bridge.call(
                "workspace.open", params: Params(path: folder.path, force: true)
            )
            await refreshStatus()
            showToast(title: "Workspace opened",
                      subtitle: workspace.title ?? folder.lastPathComponent)
            return
        } catch {
            // Likely NotFound — the chosen folder isn't a workspace
            // directory.  Fall through to directory scanning below.
        }

        // Second attempt: scan child directories (1–2 levels deep)
        // for the most recently modified workspace.
        guard let latest = mostRecentWorkspaceChild(of: folder) else {
            showToast(title: "Open failed",
                      subtitle: "No workspace found in \(folder.lastPathComponent)")
            return
        }
        do {
            let _: WorkspaceSummaryDTO = try await bridge.call(
                "workspace.open", params: Params(path: latest.path, force: true)
            )
            await refreshStatus()
            showToast(title: "Workspace opened",
                      subtitle: workspace.title ?? latest.lastPathComponent)
        } catch {
            showError(title: "Open failed", error: error)
        }
    }

    // MARK: - Capture actions

    /// Drag-to-select a screen region using the native Swift overlay.
    /// Returns the region string "x,y,w,h" or nil on cancel.
    public func selectRegion() async -> String? {
        return await CaptureOverlay.selectRegion()
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
        onPresetsChanged?(presets)
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
            showError(title: "Preset save failed", error: error)
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
            showError(title: "Preset delete failed", error: error)
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
            showError(title: "Create failed", error: error)
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
            showError(title: "Switch failed", error: error)
        }
    }

    /// Show the Spotlight-style pipeline quick-switcher.
    public func showPipelinePicker() async {
        guard workspace.isActive else {
            showToast(title: "No active workspace")
            return
        }

        let result: PipelinePickerResult? = await HUDHost.shared.present { close in
            PipelinePickerView(
                pipelines: workspace.pipelines,
                activePipeline: workspace.activePipeline,
                onClose: close
            )
        }
        guard let result else { return }

        switch result {
        case .switchTo(let name):
            await switchPipeline(to: name)
            showToast(title: "Pipeline: \(name)")
        case .create(let name):
            await createPipeline(NewPipelineValues(
                name: name, description: "", goal: ""
            ))
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
            showError(title: "Clipboard read failed", error: error)
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
            showError(title: "Save failed", error: error)
        }
    }

    /// Trigger a screenshot via the core.
    ///
    /// Semantics match the Python rumps flow:
    /// * user cancels screencapture → **silently** return (no toast)
    /// * on success → ask for a description via HUD;
    /// * description HUD cancelled → **delete** the screenshot file;
    /// * description HUD saved → call `feed.image`, emit success toast.
    public func takeScreenshot(mode: String? = nil,
                               region: String? = nil,
                               presetName: String? = nil) async {
        guard workspace.isActive,
              let pipeline = workspace.activePipeline else {
            showToast(title: "No active pipeline",
                      subtitle: "Create and activate one first.")
            return
        }

        // Use the configured screenshot_mode unless explicitly overridden.
        let effectiveMode = mode ?? screenshotMode

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
                params: CaptureParams(mode: effectiveMode, region: region)
            )
        } catch BridgeError.rpcFailed(let err) where err.code == -32001 {
            // The Python side raises StateConflict with this code when
            // screencapture exits non-zero (user pressed Esc).  That is
            // NOT an error from the user's perspective.
            return
        } catch {
            showError(title: "Screenshot failed", error: error)
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
            showError(title: "Save failed", error: error)
        }
    }

    // MARK: - Toast

    public func showToast(title: String, subtitle: String? = nil,
                          kind: ToastKind = .success) {
        toastMessage = ToastMessage(title: title, subtitle: subtitle,
                                    kind: kind)
    }

    /// Present a red-icon error toast.  Uses :func:`describeError` to
    /// produce a reader-friendly subtitle (unwraps ``RPCError`` so the
    /// user sees the Python-side message instead of
    /// ``RPCError(-32002): …`` raw dump).
    public func showError(title: String, error: Error) {
        showToast(title: title, subtitle: describeError(error), kind: .error)
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
            case "capture_modes.changed":
                await refreshCaptureModes()
            case "capture_modes.interval_started":
                if let info = try? evt.params?.decode(as: IntervalEventDTO.self),
                   let mode = info.mode_id, let preset = info.preset_id {
                    runningIntervals.insert("\(mode)/\(preset)")
                }
            case "capture_modes.interval_stopped":
                if let info = try? evt.params?.decode(as: IntervalEventDTO.self),
                   let mode = info.mode_id, let preset = info.preset_id {
                    runningIntervals.remove("\(mode)/\(preset)")
                }
            case "capture.flash_menubar":
                flashMenuBarIcon()
            case "capture.sound":
                if let payload = try? evt.params?.decode(as: SoundDTO.self) {
                    playShutterSound(volume: payload.volume ?? 0.5)
                } else {
                    playShutterSound(volume: 0.5)
                }
            case "capture.notification":
                if let payload = try? evt.params?.decode(as: NotificationDTO.self) {
                    postSystemNotification(title: payload.title, body: payload.body)
                }
            case "capture.mode_preset_executed":
                if let rep = try? evt.params?.decode(as: ExecutionReportDTO.self) {
                    await handlePresetExecuted(report: rep)
                }
            case "capture.hook_failed":
                if let info = try? evt.params?.decode(as: HookFailedDTO.self) {
                    let kind = info.kind ?? "hook"
                    let msg = info.error
                        ?? info.stderr
                        ?? "exit code \(info.returncode.map(String.init) ?? "?")"
                    showToast(
                        title: "⚠️ \(kind) failed",
                        subtitle: String(msg.prefix(120))
                    )
                }
            case "capture.quick_tags_prompt":
                // Reserved for an inline tag HUD.  The payload is
                // already defined on the Python side; render it once
                // the Designer ships a dedicated UI.
                break
            default:
                break
            }
        }
    }

    // MARK: - Internal helpers

    /// Scan for the most recently modified workspace directory inside
    /// ``folder``.  Supports both direct children and the standard
    /// two-level layout ``<root>/<yymmdd>/<name>/workspace_meta.json``.
    private func mostRecentWorkspaceChild(of folder: URL) -> URL? {
        let fm = FileManager.default
        guard let children = try? fm.contentsOfDirectory(
            at: folder, includingPropertiesForKeys: [.contentModificationDateKey],
            options: [.skipsHiddenFiles]
        ) else { return nil }

        var candidates: [URL] = []

        for child in children {
            var isDir: ObjCBool = false
            guard fm.fileExists(atPath: child.path, isDirectory: &isDir),
                  isDir.boolValue else { continue }

            // Level 1: child itself has workspace_meta.json
            let metaL1 = child.appendingPathComponent("workspace_meta.json")
            if fm.fileExists(atPath: metaL1.path) {
                candidates.append(child)
                continue
            }

            // Level 2: child is a date-folder (e.g. 260404) containing
            // workspace sub-dirs with workspace_meta.json.
            if let grandchildren = try? fm.contentsOfDirectory(
                at: child, includingPropertiesForKeys: [.contentModificationDateKey],
                options: [.skipsHiddenFiles]
            ) {
                for gc in grandchildren {
                    var gcIsDir: ObjCBool = false
                    guard fm.fileExists(atPath: gc.path, isDirectory: &gcIsDir),
                          gcIsDir.boolValue else { continue }
                    let metaL2 = gc.appendingPathComponent("workspace_meta.json")
                    if fm.fileExists(atPath: metaL2.path) {
                        candidates.append(gc)
                    }
                }
            }
        }

        return candidates.sorted { (a, b) in
            let ad = (try? a.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
            let bd = (try? b.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
            return ad > bd
        }.first
    }
}

// MARK: - Toast --------------------------------------------------------

public enum ToastKind: Sendable, Equatable {
    case info          // generic neutral (blue)
    case success       // default green check
    case warning       // amber
    case error         // red — used by showError

    public var iconName: String {
        switch self {
        case .info:    return "info.circle.fill"
        case .success: return "checkmark.circle.fill"
        case .warning: return "exclamationmark.triangle.fill"
        case .error:   return "xmark.octagon.fill"
        }
    }

    public var tint: Color {
        switch self {
        case .info:    return DSColor.accent
        case .success: return DSColor.success
        case .warning: return .orange
        case .error:   return .red
        }
    }
}

public struct ToastMessage: Identifiable, Equatable, Sendable {
    public let id = UUID()
    public let title: String
    public let subtitle: String?
    public let kind: ToastKind
    public let createdAt: Date = Date()

    public init(title: String, subtitle: String? = nil,
                kind: ToastKind = .success) {
        self.title = title
        self.subtitle = subtitle
        self.kind = kind
    }
}

/// Human-readable formatting for arbitrary errors surfaced in toasts.
///
/// Special cases:
///  * ``RPCError`` → ``"<message> (code <code>)"`` (uses the message
///    the Python side crafted — far more actionable than the default
///    ``"RPCError(-32002): …"`` ``CustomStringConvertible``).
///  * ``BridgeError.rpcFailed`` → unwraps and delegates.
///  * Everything else → ``localizedDescription``.
public func describeError(_ error: Error) -> String {
    if let rpc = error as? RPCError {
        return "\(rpc.message) (code \(rpc.code))"
    }
    if case let BridgeError.rpcFailed(rpc) = error {
        return "\(rpc.message) (code \(rpc.code))"
    }
    return error.localizedDescription
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

// MARK: - Capture Mode Designer (AppState extension) --------------------

/// Event payload shapes used by `listenForEvents` — all optional fields
/// because the Python side may omit them on failure paths.
private struct IntervalEventDTO: Decodable {
    let mode_id: String?
    let preset_id: String?
    let seconds: Int?
    let max_count: Int?
    let captured: Int?
}

private struct NotificationDTO: Decodable {
    let title: String
    let body: String
}

private struct SoundDTO: Decodable {
    let volume: Double?
}

private struct HookFailedDTO: Decodable {
    let kind: String?
    let command: String?
    let error: String?
    let stderr: String?
    let returncode: Int?
}

private struct FrameDTO: Decodable {
    let path: String?
    let index: Int
    let source_kind: String
    let skipped: Bool
    let error: String?
    let post_artifacts: [String: JSONValue]?
}

private struct ExecutionReportDTO: Decodable {
    let mode_id: String
    let preset_id: String
    let preset_name: String
    let silent: Bool
    let cancelled: Bool
    let error: String?
    let frames: [FrameDTO]
}

extension AppState {
    // MARK: - Loaders --------------------------------------------------

    public func refreshAttachmentCatalog() async {
        struct Result: Decodable { let catalog: [AttachmentCatalogEntry] }
        do {
            let r: Result = try await bridge.call(
                "capture_modes.list_attachment_catalog",
                params: RPCEmptyParams()
            )
            attachmentCatalog = r.catalog
        } catch {
            attachmentCatalog = []
        }
    }

    public func refreshCaptureModes() async {
        do {
            let r: CaptureModesState = try await bridge.call(
                "capture_modes.list_modes", params: RPCEmptyParams()
            )
            captureModes = r
            onActiveModePresetsChanged?(activeModePresets)
        } catch {
            // Leave the previous state untouched on failure.
        }
    }

    // MARK: - Mutators --------------------------------------------------

    public func switchActiveMode(_ modeID: String) async {
        struct Params: Encodable, Sendable { let mode_id: String }
        struct Result: Decodable { let active_mode_id: String }
        do {
            let _: Result = try await bridge.call(
                "capture_modes.switch_active_mode",
                params: Params(mode_id: modeID)
            )
            await refreshCaptureModes()
            if let name = captureModes.activeMode?.name {
                showToast(title: "Mode: \(name)")
            }
        } catch {
            showError(title: "Mode switch failed", error: error)
        }
    }

    /// Create or replace a whole Mode (used by the Designer's Save button).
    public func saveMode(_ mode: CaptureMode) async {
        struct Params: Encodable, Sendable { let mode: CaptureMode }
        struct Result: Decodable { let mode: CaptureMode; let created: Bool }
        do {
            let _: Result = try await bridge.call(
                "capture_modes.save_mode",
                params: Params(mode: mode)
            )
            await refreshCaptureModes()
        } catch {
            showError(title: "Save mode failed", error: error)
        }
    }

    public func deleteMode(_ modeID: String) async {
        struct Params: Encodable, Sendable { let mode_id: String }
        struct Result: Decodable { let deleted: String; let active_mode_id: String? }
        do {
            let _: Result = try await bridge.call(
                "capture_modes.delete_mode",
                params: Params(mode_id: modeID)
            )
            await refreshCaptureModes()
        } catch {
            showError(title: "Delete mode failed", error: error)
        }
    }

    // MARK: - Template library ------------------------------------------

    public func listTemplates() async -> [CaptureModeTemplate] {
        struct Result: Decodable { let templates: [CaptureModeTemplate] }
        do {
            let r: Result = try await bridge.call(
                "capture_modes.list_templates", params: RPCEmptyParams()
            )
            return r.templates
        } catch {
            showToast(title: "Load templates failed",
                      subtitle: "\(error)")
            return []
        }
    }

    public func installTemplate(_ templateID: String,
                                replaceExisting: Bool = false) async {
        struct Params: Encodable, Sendable {
            let template_id: String
            let replace_existing: Bool
        }
        struct Result: Decodable {
            let mode_id: String
            let replaced: Bool
        }
        do {
            let r: Result = try await bridge.call(
                "capture_modes.install_template",
                params: Params(template_id: templateID,
                               replace_existing: replaceExisting)
            )
            await refreshCaptureModes()
            showToast(
                title: r.replaced ? "Template replaced" : "Template installed",
                subtitle: "Mode: \(r.mode_id)"
            )
        } catch {
            showError(title: "Install failed", error: error)
        }
    }

    /// Export a Mode as template JSON and write it to disk at the path
    /// the user picks via NSSavePanel.  Returns the written URL.
    @discardableResult
    public func exportModeToFile(_ modeID: String) async -> URL? {
        struct Params: Encodable, Sendable {
            let mode_id: String
            let author: String
        }
        struct Result: Decodable { let template: JSONValue }
        do {
            let r: Result = try await bridge.call(
                "capture_modes.export_mode",
                params: Params(mode_id: modeID, author: "user")
            )
            // Encode back to pretty JSON for the save panel.
            let enc = JSONEncoder()
            enc.outputFormatting = [.prettyPrinted, .sortedKeys]
            let data = try enc.encode(r.template)
            return await MainActor.run { () -> URL? in
                let panel = NSSavePanel()
                panel.nameFieldStringValue = "\(modeID).dstemplate.json"
                panel.allowedContentTypes = [.json]
                NSApp.activate(ignoringOtherApps: true)
                guard panel.runModal() == .OK, let url = panel.url else {
                    return nil
                }
                do {
                    try data.write(to: url)
                    showToast(title: "Template exported",
                              subtitle: url.lastPathComponent)
                    return url
                } catch {
                    showError(title: "Write failed", error: error)
                    return nil
                }
            }
        } catch {
            showError(title: "Export failed", error: error)
            return nil
        }
    }

    /// Import a template from a local JSON file and install it.
    public func importTemplateFromFile() async {
        let url: URL? = await MainActor.run { () -> URL? in
            let panel = NSOpenPanel()
            panel.canChooseFiles = true
            panel.canChooseDirectories = false
            panel.allowsMultipleSelection = false
            panel.allowedContentTypes = [.json]
            panel.prompt = "Import"
            panel.message = "Pick a DailyStream template JSON"
            NSApp.activate(ignoringOtherApps: true)
            return panel.runModal() == .OK ? panel.url : nil
        }
        guard let url else { return }
        do {
            let data = try Data(contentsOf: url)
            let decoded = try JSONDecoder().decode(JSONValue.self, from: data)
            struct Params: Encodable, Sendable {
                let template: JSONValue
                let replace_existing: Bool
            }
            struct Result: Decodable {
                let mode_id: String
                let replaced: Bool
            }
            let r: Result = try await bridge.call(
                "capture_modes.install_template",
                params: Params(template: decoded, replace_existing: false)
            )
            await refreshCaptureModes()
            showToast(title: "Template installed",
                      subtitle: "Mode: \(r.mode_id)")
        } catch {
            showError(title: "Import failed", error: error)
        }
    }

    public func savePreset(modeID: String, preset: CapturePreset) async {
        struct Params: Encodable, Sendable {
            let mode_id: String
            let preset: CapturePreset
        }
        struct Result: Decodable { let preset: CapturePreset; let created: Bool }
        do {
            let _: Result = try await bridge.call(
                "capture_modes.save_preset",
                params: Params(mode_id: modeID, preset: preset)
            )
            await refreshCaptureModes()
        } catch {
            showError(title: "Save preset failed", error: error)
        }
    }

    public func deletePreset(modeID: String, presetID: String) async {
        struct Params: Encodable, Sendable {
            let mode_id: String
            let preset_id: String
        }
        struct Result: Decodable { let deleted: String }
        do {
            let _: Result = try await bridge.call(
                "capture_modes.delete_preset",
                params: Params(mode_id: modeID, preset_id: presetID)
            )
            await refreshCaptureModes()
        } catch {
            showError(title: "Delete preset failed", error: error)
        }
    }

    // MARK: - Preset execution (from hotkey OR menu) -------------------

    /// Called by HotkeyManager on keyDown.
    public func onPresetHotkeyDown(modeID: String, presetID: String,
                                   presetName: String) async {
        guard let preset = captureModes.activeMode?.presets
                .first(where: { $0.id == presetID }) else { return }
        await executePreset(preset, modeID: modeID, fromHotkey: true)
    }

    /// Central dispatcher used by hotkeys + Designer "Test" button.
    public func executePreset(_ preset: CapturePreset,
                              modeID: String,
                              fromHotkey: Bool = false) async {
        guard workspace.isActive,
              let pipeline = workspace.activePipeline else {
            showToast(title: "No active pipeline",
                      subtitle: "Create and activate one first.")
            return
        }
        _ = pipeline  // silence unused warning on some builds

        // Long-running strategies run on the Python side via their own
        // lifecycle — start once, stop with a second press.
        let strategyID = preset.attachments.first { a in
            attachmentCatalog.first { $0.id == a.id }?.kind == .strategy
        }?.id ?? "single"

        if strategyID == "interval" {
            let key = "\(modeID)/\(preset.id)"
            if runningIntervals.contains(key) {
                await stopInterval(modeID: modeID, presetID: preset.id)
            } else {
                await startInterval(modeID: modeID, preset: preset)
            }
            return
        }

        let silent = preset.attachments.contains { $0.id == "silent_save" }
                   || strategyID == "burst"

        // ───────────────────────────────────────────────────────────
        // Non-silent single shot → route through the battle-tested
        // ``capture.screenshot`` RPC (the same one ⌘1 has always used).
        //
        // The "Capture Mode" executor path kept tripping over a
        // screencapture quirk: user ESC during interactive selection
        // returns rc=0 without producing a file, but the executor still
        // populated ``frame.path`` → Swift opened a HUD → user typed a
        // description → ``feed.image`` failed with "Image not found"
        // → the "Save failed" toast.  Rather than patch that chain we
        // reuse the old RPC which has a clean cancel contract
        // (``StateConflict -32001``) and is known to work.
        //
        // This limits Capture Mode features for non-silent single
        // presets to: source kind, region, hide_cursor.  Post-processing
        // attachments (auto_ocr / ai_analyze) are only honoured on the
        // silent / burst / interval paths for now — they never worked
        // reliably on the interactive path anyway because the HUD
        // raced ahead of the async AI call.
        // ───────────────────────────────────────────────────────────
        if strategyID == "single" && !silent {
            // Clipboard preset: just reuse the dedicated clipboard RPC
            // chain — it already handles text/image dispatch + HUD.
            if preset.source.kind == .clipboard {
                await captureClipboard()
                return
            }
            let shotMode = mapSourceKindToShotMode(preset.source.kind)
            let region = preset.source.kind == .region
                ? preset.source.region
                : nil
            await takeScreenshot(mode: shotMode, region: region,
                                 presetName: preset.name)
            return
        }

        // Silent / burst → full execute_preset flow (silent delivery is
        // handled end-to-end on the Python side and does not need a HUD).
        struct Params: Encodable, Sendable {
            let mode_id: String
            let preset_id: String
            let silent: Bool
        }
        iconState = .capturing
        defer { Task { await self.refreshStatus() } }

        do {
            let report: ExecutionReportDTO = try await bridge.call(
                "capture_modes.execute_preset",
                params: Params(mode_id: modeID, preset_id: preset.id,
                               silent: silent)
            )
            await handlePresetExecuted(report: report)
        } catch BridgeError.rpcFailed(let err) where err.code == -32001 {
            // Cancelled — same contract as the legacy screenshot path.
            return
        } catch {
            showError(title: "Capture failed", error: error)
        }
    }

    /// Map the Preset source enum onto the ``screencapture`` mode string
    /// that ``capture.screenshot`` expects.  ``region`` is passed as a
    /// separate param so this helper returns the placeholder mode the
    /// Python side ignores when region is non-nil.
    private func mapSourceKindToShotMode(_ kind: CaptureSourceKind) -> String {
        switch kind {
        case .interactive, .window:  return "interactive"
        case .fullscreen:            return "fullscreen"
        case .region:                return "interactive"  // overridden by region
        case .clipboard:             return "interactive"  // unused for clipboard path
        }
    }

    public func startInterval(modeID: String, preset: CapturePreset) async {
        struct Params: Encodable, Sendable {
            let mode_id: String
            let preset_id: String
        }
        struct Result: Decodable { let running: Bool; let seconds: Int? }
        do {
            let r: Result = try await bridge.call(
                "capture_modes.start_interval",
                params: Params(mode_id: modeID, preset_id: preset.id)
            )
            if r.running {
                runningIntervals.insert("\(modeID)/\(preset.id)")
                showToast(title: "Started: \(preset.name)",
                          subtitle: r.seconds.map { "every \($0)s" })
            }
        } catch {
            showError(title: "Start failed", error: error)
        }
    }

    public func stopInterval(modeID: String, presetID: String) async {
        struct Params: Encodable, Sendable {
            let mode_id: String
            let preset_id: String
        }
        struct Result: Decodable { let running: Bool }
        do {
            let _: Result = try await bridge.call(
                "capture_modes.stop_interval",
                params: Params(mode_id: modeID, preset_id: presetID)
            )
            runningIntervals.remove("\(modeID)/\(presetID)")
        } catch {
            // already stopped — ignore
        }
    }

    // MARK: - Post-execution delivery + feedback -----------------------

    fileprivate func handlePresetExecuted(report: ExecutionReportDTO) async {
        guard workspace.isActive,
              let pipeline = workspace.activePipeline else { return }

        // Silent executions are already fed into the pipeline on the
        // Python side — we just show a compact toast.
        if report.silent {
            let n = report.frames.filter { $0.path != nil && !$0.skipped }.count
            if n > 0 {
                showToast(title: "\(report.preset_name) saved",
                          subtitle: "\(n) frame\(n == 1 ? "" : "s") → \(pipeline)")
            } else {
                // Surface the (first) frame error so user sees *why*
                // nothing was captured instead of silent silence.
                let firstError = report.frames
                    .compactMap { $0.error }
                    .first ?? "no frames captured"
                showToast(title: "\(report.preset_name): nothing captured",
                          subtitle: firstError, kind: .warning)
            }
            return
        }

        // Non-silent single shot: open the description HUD for each
        // captured frame in sequence (usually 1).  When the Preset
        // includes ``ai_analyze`` or ``auto_ocr`` we pre-fill the
        // description so the user just has to tweak or ⏎ to save.
        var presentedAny = false
        for frame in report.frames {
            if frame.skipped {
                // Skipped frames (user ESC / screencapture fail) should
                // NOT trigger the red "Save failed" — they're a normal
                // cancel path.  Show a neutral hint instead.
                let msg = frame.error ?? "capture cancelled"
                showToast(title: "Capture skipped",
                          subtitle: msg, kind: .info)
                continue
            }
            guard let p = frame.path else { continue }
            // Guard against phantom paths (file vanished before we
            // could open the HUD): do not open HUD on a missing file.
            if !FileManager.default.fileExists(atPath: p) {
                showToast(title: "Capture incomplete",
                          subtitle: "Screenshot file was not written: \(URL(fileURLWithPath: p).lastPathComponent). Check Screen Recording permission for DailyStream.",
                          kind: .warning)
                continue
            }
            presentedAny = true
            let fileURL = URL(fileURLWithPath: p)
            // AI description wins over raw OCR because it's already a
            // sentence; fall back to OCR if AI isn't available.
            let aiText = frame.post_artifacts?["ai_description"]?.stringValue ?? ""
            let ocrText = frame.post_artifacts?["ocr_text"]?.stringValue ?? ""
            let prefill: String
            let source: String?
            if !aiText.isEmpty {
                prefill = aiText
                source = "AI"
            } else if !ocrText.isEmpty {
                prefill = ocrText
                source = "OCR"
            } else {
                prefill = ""
                source = nil
            }
            let result: ScreenshotDescResult? = await HUDHost.shared.present { close in
                ScreenshotDescView(
                    filename: fileURL.lastPathComponent,
                    pipeline: pipeline,
                    presetName: report.preset_name,
                    thumbnailURL: fileURL,
                    initialText: prefill,
                    initialTextSource: source,
                    onClose: close
                )
            }
            switch result {
            case .save(let desc):
                await feedImage(path: p, description: desc,
                                pipeline: pipeline)
            case .cancel, .none:
                try? FileManager.default.removeItem(at: fileURL)
            }
        }
    }

    // MARK: - Small feedback helpers -----------------------------------

    fileprivate func flashMenuBarIcon() {
        // Two-pulse flash so the user actually notices.  We always
        // restore to the workspace-determined resting state (recording
        // vs idle) rather than trusting whatever transient state we
        // started from.
        let resting: MenuBarIconState = workspace.isActive ? .recording : .idle
        let originalMessage = toastMessage
        _ = originalMessage  // keep compiler happy in some builds

        Task { @MainActor in
            for _ in 0..<2 {
                self.iconState = .flashing
                try? await Task.sleep(nanoseconds: 140_000_000)
                self.iconState = resting
                try? await Task.sleep(nanoseconds: 100_000_000)
            }
        }
    }

    /// Play the system "Grab" sound (same shutter tone the OS uses for
    /// ``⌘⇧4``).  Falls back to the user's default alert sound if Grab
    /// isn't installed on the current macOS release.
    fileprivate func playShutterSound(volume: Double) {
        let candidates = ["Grab", "Ping", "Pop", "Tink"]
        var picked: NSSound? = nil
        for name in candidates {
            if let s = NSSound(named: NSSound.Name(name)) {
                picked = s
                break
            }
        }
        let sound = picked ?? NSSound(named: NSSound.Name("Funk"))
        guard let sound else { return }
        sound.volume = Float(max(0.0, min(1.0, volume)))
        sound.play()
    }

    fileprivate func postSystemNotification(title: String, body: String) {
        // UserNotifications asserts when the host process lacks a
        // proper Bundle (i.e. when running via `swift run` outside an
        // .app).  In that case, surface the message as an in-app toast
        // instead of crashing.
        guard Bundle.main.bundleIdentifier != nil else {
            showToast(title: title, subtitle: body)
            return
        }
        let center = UNUserNotificationCenter.current()
        center.requestAuthorization(options: [.alert, .sound]) { _, _ in }
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        let req = UNNotificationRequest(
            identifier: UUID().uuidString,
            content: content,
            trigger: nil
        )
        center.add(req, withCompletionHandler: nil)
    }
}

// AppState.swift
// Observable global state consumed by menu bar and future UI surfaces.
//
// Design notes
// ------------
// * All mutations happen on the MainActor so SwiftUI views stay happy.
// * The store owns the `CoreBridge` and is the only component that calls
//   into it.  Views call methods on `AppState` (e.g. `newWorkspace()`)
//   rather than touching the bridge directly.
//
// Related files
// -------------
// * ``ToastModels.swift``          — ToastKind / ToastMessage / describeError
// * ``AppStateDTOs.swift``         — ScreenshotPreset + shared event DTOs
// * ``AppState+CaptureModes.swift``— Mode/Preset/Template CRUD + executor
// * ``AppState+Feedback.swift``    — menubar flash / sound / notifications

import Foundation
import SwiftUI
import UniformTypeIdentifiers
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

    // `internal(set)` on properties that cross-file extensions (e.g.
    // AppState+Feedback, AppState+CaptureModes) need to mutate.
    @Published public internal(set) var iconState: MenuBarIconState = .idle
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
    @Published public internal(set) var captureModes: CaptureModesState = .init()
    /// Available attachments (static catalog from Python).
    @Published public internal(set) var attachmentCatalog: [AttachmentCatalogEntry] = []
    /// List of currently-running interval captures keyed by
    /// "mode_id/preset_id" — used by the menu bar to show a "Stop" item.
    @Published public internal(set) var runningIntervals: Set<String> = []
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

    /// Internal setter used by cross-file extensions (e.g. the feedback
    /// helpers) that need to poke ``iconState`` during animations.
    func setIconState(_ state: MenuBarIconState) {
        iconState = state
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

    /// Internal so the Capture Modes extension can reuse it when the
    /// executor returns a frame needing HUD-driven delivery.
    func feedImage(path: String, description: String,
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

// AppState+CaptureModes.swift
// Capture Mode Designer surface of AppState — Mode/Preset/Template CRUD
// plus the preset-execution dispatcher and its HUD delivery logic.
//
// Split out of AppState.swift purely to keep each file under ~900 lines.
// No behavioural changes.

import Foundation
import SwiftUI
import UniformTypeIdentifiers
import DailyStreamCore

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
        // attachments (ai_analyze) are only honoured on the silent /
        // burst / interval paths for now — they never worked reliably
        // on the interactive path anyway because the HUD raced ahead
        // of the async AI call.
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

    func handlePresetExecuted(report: ExecutionReportDTO) async {
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
        // includes ``ai_analyze`` we pre-fill the description so the
        // user just has to tweak or ⏎ to save.
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
            let fileURL = URL(fileURLWithPath: p)
            let aiText = frame.post_artifacts?["ai_description"]?.stringValue ?? ""
            let prefill = aiText
            let source: String? = aiText.isEmpty ? nil : "AI"
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
}

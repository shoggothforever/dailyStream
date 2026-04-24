// MenuBarContent.swift
// The dropdown shown when the user clicks the DailyStream menu bar icon.
//
// Layout (top → bottom)
// ─────────────────────
// 1. Status header (workspace + active mode + running intervals)
// 2. Workspace commands (create / open / end)
// 3. Capture Mode submenu
//    ├── ⚙️  Capture Mode Designer…
//    ├── Mode:  <name>  ▶  (list of modes with checkmark)
//    ├── ──── divider ────
//    ├── [presets of the active Mode]
//    ├── ──── divider ────
//    └── Running Intervals submenu (only if any are running)
// 4. Clipboard capture
// 5. Refresh / About / Quit

import SwiftUI
import AppKit
import KeyboardShortcuts

public struct MenuBarContent: View {
    @ObservedObject var state: AppState

    public init(state: AppState) { self.state = state }

    public var body: some View {
        Group {
            statusHeader

            // Running intervals — prominent top-level stop controls so
            // the user never has to hunt for the off switch.
            runningIntervalsHeader

            Divider()

            // Workspace commands
            if !state.workspace.isActive {
                Button("New Workspace…") { Task { await promptNewWorkspace() } }
                Button("Open Workspace…") { Task { await promptOpenWorkspace() } }
                if state.lastWorkspacePath != nil {
                    Button("Reopen Last Workspace") {
                        Task { await state.reopenLastWorkspace() }
                    }
                }
            } else {
                Button("New Pipeline…") { Task { await promptNewPipeline() } }
                if state.workspace.pipelines.count > 1 {
                    pipelineSwitcher
                }
                Divider()
                Button("View Stream…") {
                    Task { await state.showStreamViewer() }
                }
                Button("End Workspace") {
                    Task { await state.endWorkspace() }
                }
                .keyboardShortcut("e", modifiers: [.command, .shift])
            }

            Divider()

            // Capture (Mode-aware)
            captureMenu

            Button {
                Task { await state.captureClipboard() }
            } label: {
                Label("Clipboard", systemImage: "doc.on.clipboard")
            }
            .keyboardShortcut(.init("2"), modifiers: [.command])
            .disabled(!state.workspace.isActive ||
                      state.workspace.activePipeline == nil)

            Divider()

            Button("Refresh Status") {
                Task { await state.refreshStatus() }
            }

            Divider()

            Button("About DailyStream") {
                AboutWindowController.shared.show()
            }

            Button("Quit") {
                Task {
                    await state.shutdown()
                    NSApplication.shared.terminate(nil)
                }
            }
            .keyboardShortcut("q", modifiers: [.command])
        }
    }

    // MARK: - Status header

    @ViewBuilder
    private var statusHeader: some View {
        if !state.coreReady {
            Text("Core starting…").foregroundStyle(.secondary)
        } else if state.workspace.isActive,
                  let title = state.workspace.title {
            Text(title).font(.headline)
            if let pipeline = state.workspace.activePipeline {
                Text("Pipeline: \(pipeline)")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
            if let mode = state.captureModes.activeMode {
                Text("Mode: \(mode.emoji) \(mode.name)")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
        } else {
            Text("No active workspace").foregroundStyle(.secondary)
        }
    }

    /// Top-level "Stop X" buttons for every running interval — always
    /// one click away regardless of how deep the Capture menu is.
    @ViewBuilder
    private var runningIntervalsHeader: some View {
        let running = resolvedRunningIntervals()
        if !running.isEmpty {
            Divider()
            Text("● Running (\(running.count))")
                .font(.caption)
                .foregroundStyle(.orange)
            ForEach(running, id: \.key) { item in
                Button {
                    Task {
                        await state.stopInterval(
                            modeID: item.modeID,
                            presetID: item.presetID
                        )
                    }
                } label: {
                    Label("Stop \(item.display)", systemImage: "stop.circle")
                }
            }
            Button("Stop All Intervals") {
                Task { await stopAllIntervals() }
            }
            .keyboardShortcut(".", modifiers: [.command, .shift])
        }
    }

    private func stopAllIntervals() async {
        for item in resolvedRunningIntervals() {
            await state.stopInterval(
                modeID: item.modeID, presetID: item.presetID
            )
        }
    }

    /// Resolve `state.runningIntervals` (raw "mode_id/preset_id" keys)
    /// into displayable entries with real mode+preset names/emojis.
    private func resolvedRunningIntervals() -> [RunningItem] {
        state.runningIntervals.sorted().compactMap { key in
            let parts = key.split(separator: "/", maxSplits: 1).map(String.init)
            guard parts.count == 2 else { return nil }
            let modeID = parts[0]
            let presetID = parts[1]
            let mode = state.captureModes.modes.first { $0.id == modeID }
            let preset = mode?.presets.first { $0.id == presetID }
            let display: String
            if let preset, let mode {
                display = "\(preset.emoji) \(preset.name)  (\(mode.name))"
            } else {
                display = key
            }
            return RunningItem(
                key: key, modeID: modeID,
                presetID: presetID, display: display
            )
        }
    }

    // MARK: - Capture menu

    @ViewBuilder
    private var captureMenu: some View {
        Menu {
            Button("⚙️  Capture Mode Designer…") {
                CaptureModeDesignerWindow.shared.show(state: state)
            }

            Divider()

            if !state.captureModes.modes.isEmpty {
                Menu {
                    ForEach(state.captureModes.modes) { mode in
                        Button(action: {
                            Task { await state.switchActiveMode(mode.id) }
                        }) {
                            if mode.id == state.captureModes.activeModeID {
                                Label("\(mode.emoji) \(mode.name)",
                                      systemImage: "checkmark")
                            } else {
                                Text("\(mode.emoji) \(mode.name)")
                            }
                        }
                    }
                } label: {
                    if let m = state.captureModes.activeMode {
                        Label("Mode: \(m.name)", systemImage: "square.stack.3d.up")
                    } else {
                        Label("Mode", systemImage: "square.stack.3d.up")
                    }
                }

                Divider()
            }

            // Active Mode's presets
            let presets = state.activeModePresets
            if presets.isEmpty {
                Text("No presets in this Mode")
                    .foregroundStyle(.secondary)
            } else {
                ForEach(presets) { preset in
                    Button(action: {
                        Task {
                            guard let modeID = state.captureModes.activeModeID else { return }
                            await state.executePreset(preset, modeID: modeID)
                        }
                    }) {
                        Text(presetLabel(preset, state: state))
                    }
                }
            }

            // Running intervals (stop controls)
            let running = resolvedRunningIntervals()
            if !running.isEmpty {
                Divider()
                Menu("Stop Interval") {
                    ForEach(running, id: \.key) { item in
                        Button(item.display) {
                            Task {
                                await state.stopInterval(
                                    modeID: item.modeID,
                                    presetID: item.presetID
                                )
                            }
                        }
                    }
                }
            }

            Divider()

            // Legacy "take a screenshot right now" shortcut remains for
            // users who haven't touched their Mode.
            Button("Quick Free Selection") {
                Task { await state.takeScreenshot(mode: "interactive") }
            }
            .keyboardShortcut(.init("1"), modifiers: [.command])
        } label: {
            Label("Screenshot", systemImage: "camera")
        }
        .disabled(!state.workspace.isActive ||
                  state.workspace.activePipeline == nil)
    }

    // MARK: - Pipeline switcher (sub-menu)

    @ViewBuilder
    private var pipelineSwitcher: some View {
        Menu("Switch Pipeline") {
            ForEach(state.workspace.pipelines, id: \.self) { name in
                Button(action: {
                    Task { await state.switchPipeline(to: name) }
                }) {
                    if name == state.workspace.activePipeline {
                        Label(name, systemImage: "checkmark")
                    } else {
                        Text(name)
                    }
                }
            }
        }
    }

    // MARK: - HUD glue

    private func promptNewWorkspace() async {
        let values: NewWorkspaceValues? = await HUDHost.shared.present { close in
            NewWorkspaceView(onClose: close)
        }
        guard let values else { return }
        await state.createWorkspace(values)
    }

    private func promptOpenWorkspace() async {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = "Open"
        NSApp.activate(ignoringOtherApps: true)
        guard panel.runModal() == .OK, let url = panel.url else { return }
        await state.openWorkspaceAt(url)
    }

    private func promptNewPipeline() async {
        let values: NewPipelineValues? = await HUDHost.shared.present { close in
            NewPipelineView(onClose: close)
        }
        guard let values else { return }
        await state.createPipeline(values)
    }

    private func presetLabel(_ p: CapturePreset, state: AppState) -> String {
        let modeID = state.captureModes.activeModeID ?? ""
        let key = "\(modeID)/\(p.id)"
        let isRunning = state.runningIntervals.contains(key)
        var label = "\(p.emoji) \(p.name)"
        if isRunning {
            label = "● " + label + "  (tap to stop)"
        }
        if let hk = p.hotkey, !hk.isEmpty {
            label += "  [\(hk)]"
        }
        return label
    }
}

// MARK: - Private helpers -----------------------------------------------

/// Displayable snapshot of one running interval.
private struct RunningItem {
    let key: String
    let modeID: String
    let presetID: String
    let display: String
}

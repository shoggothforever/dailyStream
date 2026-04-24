// MenuBarContent.swift
// The dropdown shown when the user clicks the DailyStream menu bar icon.

import SwiftUI
import AppKit
import KeyboardShortcuts

public struct MenuBarContent: View {
    @ObservedObject var state: AppState

    public init(state: AppState) { self.state = state }

    public var body: some View {
        Group {
            statusHeader

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

            // Capture
            Menu {
                Button("Free Selection") {
                    Task { await state.takeScreenshot(mode: "interactive") }
                }
                .keyboardShortcut(.init("1"), modifiers: [.command])

                if !state.presets.isEmpty {
                    Divider()
                    ForEach(state.presets) { preset in
                        Button(presetLabel(preset)) {
                            Task {
                                await state.takeScreenshot(
                                    region: preset.region,
                                    presetName: preset.name
                                )
                            }
                        }
                    }
                }

                Divider()
                Button("New Preset…") {
                    Task { await promptCreatePreset() }
                }
                if !state.presets.isEmpty {
                    Menu("Delete Preset") {
                        ForEach(state.presets) { preset in
                            Button(preset.name) {
                                Task { await state.deletePreset(name: preset.name) }
                            }
                        }
                    }
                }
            } label: {
                Label("Screenshot", systemImage: "camera")
            }
            .disabled(!state.workspace.isActive ||
                      state.workspace.activePipeline == nil)

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
        } else {
            Text("No active workspace").foregroundStyle(.secondary)
        }
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

    private func promptCreatePreset() async {
        guard let region = await state.selectRegion() else { return }
        let values: PresetValues? = await HUDHost.shared.present { close in
            PresetNameView(region: region, onClose: close)
        }
        guard let values else { return }
        await state.createPreset(values)
    }

    private func presetLabel(_ p: ScreenshotPreset) -> String {
        if let hk = p.hotkey, !hk.isEmpty {
            return "\(p.name)  [\(hk)]"
        }
        return p.name
    }
}

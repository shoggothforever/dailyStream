// MenuBarContent.swift
// The dropdown shown when the user clicks the DailyStream menu bar icon.

import SwiftUI
import KeyboardShortcuts

public struct MenuBarContent: View {
    @ObservedObject var state: AppState

    public init(state: AppState) { self.state = state }

    public var body: some View {
        Group {
            // Workspace status header
            if state.workspace.isActive,
               let title = state.workspace.title {
                Text(title)
                    .font(.headline)
                if let pipeline = state.workspace.activePipeline {
                    Text("Active pipeline: \(pipeline)")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                Divider()
            } else if !state.coreReady {
                Text("Core starting…")
                    .foregroundStyle(.secondary)
                Divider()
            } else {
                Text("No active workspace")
                    .foregroundStyle(.secondary)
                Divider()
            }

            // Capture actions
            Button {
                Task { await state.takeScreenshot(mode: "interactive") }
            } label: {
                Label("Screenshot", systemImage: "camera")
            }
            .keyboardShortcut(.init("1"), modifiers: [.command])

            Button {
                state.showToast(title: "Clipboard capture (WIP)")
            } label: {
                Label("Clipboard", systemImage: "doc.on.clipboard")
            }
            .keyboardShortcut(.init("2"), modifiers: [.command])

            Divider()

            // Workspace actions (wired up to RPC in M1 end-to-end smoke test)
            Button("Refresh Status") {
                Task { await state.refreshStatus() }
            }

            Divider()

            Button("Quit") {
                Task {
                    await state.shutdown()
                    NSApplication.shared.terminate(nil)
                }
            }
            .keyboardShortcut("q", modifiers: [.command])
        }
    }
}

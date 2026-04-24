// NewWorkspaceView.swift
// Single HUD that replaces the 3-step rumps dialog flow
// (title → folder → ai_mode) with one consolidated panel.
//
// Semantic contract (must match `DailyStreamApp._on_start_workspace`)
// --------------------------------------------------------------------
// * Title is optional — empty means "use workspace_id".
// * Folder defaults to config.default_workspace_path (empty → core-side
//   default, i.e. ~/Desktop/dailyStream).  A `Change…` button opens
//   the standard NSOpenPanel.
// * AI mode is a segmented control with three options; default comes
//   from config.ai_default_mode (fetched lazily).
// * Esc or Cancel → onClose(nil) — bridge is NOT invoked.
// * Enter or Create → onClose(NewWorkspaceValues(...)).

import SwiftUI
import AppKit

public struct NewWorkspaceValues: Sendable {
    public let title: String?       // nil/empty → use workspace_id
    public let folder: URL?         // nil → use core default
    public let aiMode: String       // "off" | "realtime" | "daily_report"
}

struct NewWorkspaceView: View {
    let onClose: (NewWorkspaceValues?) -> Void
    @State private var title: String = ""
    @State private var folder: URL? = nil
    @State private var aiMode: String = "off"
    @FocusState private var focusedField: Field?

    enum Field { case title }

    var body: some View {
        HUDFrame {
            VStack(alignment: .leading, spacing: 14) {
                // Header
                HStack(spacing: 10) {
                    Image(systemName: "folder.badge.plus")
                        .font(.system(size: 20, weight: .regular))
                        .foregroundStyle(DSColor.accent)
                    VStack(alignment: .leading, spacing: 0) {
                        Text("New Workspace")
                            .font(.system(size: 15, weight: .semibold))
                        Text("Start a new recording session")
                            .font(DSFont.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                }

                // Title input
                HUDTextField(
                    text: $title,
                    placeholder: "Workspace title (optional)",
                    onSubmit: submit
                )
                .focused($focusedField, equals: .title)

                // Folder row
                HStack(spacing: 8) {
                    Image(systemName: "folder")
                        .foregroundStyle(.secondary)
                    Text(folderLabel)
                        .font(DSFont.body)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                    Spacer()
                    Button("Change…", action: pickFolder)
                        .controlSize(.small)
                }

                // AI mode
                HStack(spacing: 10) {
                    Text("AI")
                        .font(DSFont.caption)
                        .foregroundStyle(.secondary)
                    Picker("", selection: $aiMode) {
                        Text("Off").tag("off")
                        Text("Realtime").tag("realtime")
                        Text("Daily Report").tag("daily_report")
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                }

                Divider().opacity(0.3)

                // Footer
                HStack {
                    HUDHintBar(
                        left: "⎋ Cancel",
                        right: "↩ Create"
                    )
                }
            }
        }
        .onAppear { focusedField = .title }
    }

    // MARK: - Helpers

    private var folderLabel: String {
        if let f = folder {
            return f.path
        }
        return "Default (core decides)"
    }

    private func pickFolder() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = "Choose"
        // NSOpenPanel must attach to an active app to receive focus.
        NSApp.activate(ignoringOtherApps: true)
        if panel.runModal() == .OK, let url = panel.url {
            folder = url
        }
    }

    private func submit() {
        let trimmed = title.trimmingCharacters(in: .whitespacesAndNewlines)
        onClose(
            NewWorkspaceValues(
                title: trimmed.isEmpty ? nil : trimmed,
                folder: folder,
                aiMode: aiMode
            )
        )
    }
}

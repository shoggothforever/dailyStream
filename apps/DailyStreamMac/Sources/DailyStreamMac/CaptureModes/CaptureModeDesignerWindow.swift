// CaptureModeDesignerWindow.swift
// Standalone window hosting the three-column Capture Mode Designer:
//
//     ┌──────────────┬──────────────┬────────────────────────┐
//     │  Modes       │  Presets     │  Editor                │
//     │  ───────     │  ───────     │  ───────────────────── │
//     │  ◎ Default   │  📸 Free sel │  Name / Emoji          │
//     │  • Gaming    │  📋 Clipboard│  Hotkey recorder       │
//     │  + New Mode  │  + Add preset│  Source                │
//     │              │              │  Attachment groups     │
//     │              │              │  Test · Save · Delete  │
//     └──────────────┴──────────────┴────────────────────────┘
//
// All mutations go through AppState methods so Python is the source of
// truth.  The Designer only maintains a local copy of the *currently
// edited* preset so the user can cancel their changes.

import AppKit
import SwiftUI

@MainActor
final class CaptureModeDesignerWindow {
    static let shared = CaptureModeDesignerWindow()

    private var window: NSWindow?

    private init() {}

    func show(state: AppState) {
        let content = CaptureModeDesignerView(state: state) { [weak self] in
            self?.close()
        }
        if let existing = window {
            existing.contentViewController = NSHostingController(rootView: content)
            existing.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }
        let w = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1000, height: 680),
            styleMask: [.titled, .closable, .resizable, .miniaturizable],
            backing: .buffered,
            defer: false
        )
        w.title = "Capture Mode Designer"
        w.isReleasedWhenClosed = false
        w.center()
        w.contentViewController = NSHostingController(rootView: content)
        w.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        self.window = w
    }

    func close() {
        window?.orderOut(nil)
    }
}

// MARK: - Root designer view --------------------------------------------

struct CaptureModeDesignerView: View {
    @ObservedObject var state: AppState
    let onClose: () -> Void

    @State private var selectedModeID: String? = nil
    @State private var selectedPresetID: String? = nil

    var body: some View {
        HSplitView {
            modesColumn
                .frame(minWidth: 200, idealWidth: 220, maxWidth: 260)

            presetsColumn
                .frame(minWidth: 220, idealWidth: 240, maxWidth: 300)

            editorColumn
                .frame(minWidth: 440)
        }
        .frame(minWidth: 900, minHeight: 600)
        .onAppear { initialSelection() }
        .onChange(of: selectedModeID) { _ in
            // Reset preset selection when mode changes.
            let presets = currentMode?.presets ?? []
            selectedPresetID = presets.first?.id
        }
        .onChange(of: state.captureModes) { _ in
            initialSelection()
        }
    }

    // MARK: - Derived state

    private var currentMode: CaptureMode? {
        guard let id = selectedModeID else { return nil }
        return state.captureModes.modes.first { $0.id == id }
    }

    private var currentPreset: CapturePreset? {
        guard let mode = currentMode, let id = selectedPresetID else { return nil }
        return mode.presets.first { $0.id == id }
    }

    private func initialSelection() {
        if selectedModeID == nil {
            selectedModeID = state.captureModes.activeModeID
                ?? state.captureModes.modes.first?.id
        }
        if selectedPresetID == nil {
            selectedPresetID = currentMode?.presets.first?.id
        }
    }

    // MARK: - Column: Modes

    private var modesColumn: some View {
        VStack(alignment: .leading, spacing: 0) {
            columnHeader(title: "Modes")
            List(selection: $selectedModeID) {
                ForEach(state.captureModes.modes) { mode in
                    HStack {
                        Text("\(mode.emoji) \(mode.name)")
                        Spacer()
                        if mode.id == state.captureModes.activeModeID {
                            Image(systemName: "checkmark.seal.fill")
                                .foregroundStyle(.green)
                        }
                    }
                    .tag(mode.id as String?)
                    .contextMenu {
                        if mode.id != state.captureModes.activeModeID {
                            Button("Set as Active") {
                                Task { await state.switchActiveMode(mode.id) }
                            }
                        }
                        Button("Delete Mode", role: .destructive) {
                            Task { await state.deleteMode(mode.id) }
                        }
                    }
                }
            }
            .listStyle(.sidebar)

            Divider()
            HStack {
                Button {
                    Task { await createMode() }
                } label: {
                    Label("New Mode", systemImage: "plus")
                }
                .buttonStyle(.borderless)
                Spacer()
                if let id = selectedModeID,
                   id != state.captureModes.activeModeID {
                    Button("Activate") {
                        Task { await state.switchActiveMode(id) }
                    }
                    .controlSize(.small)
                }
            }
            .padding(8)
        }
        .background(Color(nsColor: .underPageBackgroundColor))
    }

    // MARK: - Column: Presets

    private var presetsColumn: some View {
        VStack(alignment: .leading, spacing: 0) {
            columnHeader(title: currentMode.map { "Presets — \($0.name)" } ?? "Presets")
            List(selection: $selectedPresetID) {
                if let mode = currentMode {
                    ForEach(mode.presets) { preset in
                        VStack(alignment: .leading, spacing: 2) {
                            Text("\(preset.emoji) \(preset.name)")
                                .font(.system(size: 13, weight: .medium))
                            HStack(spacing: 6) {
                                Text(preset.source.kind.label)
                                    .font(.system(size: 10))
                                    .foregroundStyle(.secondary)
                                if let hk = preset.hotkey, !hk.isEmpty {
                                    Text(hk)
                                        .font(.system(size: 10, design: .monospaced))
                                        .foregroundStyle(.tertiary)
                                }
                            }
                        }
                        .tag(preset.id as String?)
                        .contextMenu {
                            Button("Delete", role: .destructive) {
                                Task {
                                    await state.deletePreset(
                                        modeID: mode.id, presetID: preset.id
                                    )
                                }
                            }
                        }
                    }
                } else {
                    Text("Select a Mode")
                        .foregroundStyle(.secondary)
                        .padding(.vertical, 8)
                }
            }
            .listStyle(.inset(alternatesRowBackgrounds: true))

            Divider()
            HStack {
                Button {
                    Task { await createPreset() }
                } label: {
                    Label("New Preset", systemImage: "plus")
                }
                .buttonStyle(.borderless)
                .disabled(currentMode == nil)
                Spacer()
                Button("Close") { onClose() }
                    .keyboardShortcut(.cancelAction)
                    .controlSize(.small)
            }
            .padding(8)
        }
    }

    // MARK: - Column: Editor

    @ViewBuilder
    private var editorColumn: some View {
        if let preset = currentPreset, let mode = currentMode {
            CapturePresetEditorView(
                state: state,
                mode: mode,
                preset: preset,
                onSave: { edited in
                    Task { await state.savePreset(modeID: mode.id, preset: edited) }
                },
                onDelete: {
                    Task {
                        await state.deletePreset(
                            modeID: mode.id, presetID: preset.id
                        )
                    }
                },
                onTest: {
                    Task {
                        // The editor's own draft has already been saved
                        // by the time the user clicks Test.  Here we
                        // just execute whatever the server currently
                        // has for this preset.
                        await state.executePreset(preset, modeID: mode.id)
                    }
                }
            )
            // Force SwiftUI to rebuild the editor (resetting @State)
            // whenever the selection changes — without this, the old
            // ``draft`` / ``lastSaved`` values linger and the pane
            // shows the previous preset's fields.
            .id("editor-\(mode.id)-\(preset.id)")
        } else {
            VStack {
                Spacer()
                Text("Select a Preset to edit")
                    .foregroundStyle(.secondary)
                Spacer()
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    // MARK: - Helpers

    private func columnHeader(title: String) -> some View {
        HStack {
            Text(title)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(.secondary)
            Spacer()
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

    private func createMode() async {
        let name = await promptText(
            title: "New Mode",
            placeholder: "e.g. Gaming"
        )
        guard let name, !name.isEmpty else { return }
        let id = slugify(name)
        let m = CaptureMode(id: id, name: name, presets: [])
        await state.saveMode(m)
        selectedModeID = id
    }

    private func createPreset() async {
        guard let mode = currentMode else { return }
        let name = await promptText(
            title: "New Preset",
            placeholder: "e.g. Highlight Burst"
        )
        guard let name, !name.isEmpty else { return }
        let id = slugify(name)
        let p = CapturePreset(
            id: id,
            name: name,
            source: CaptureSource(kind: .interactive),
            attachments: [
                CaptureAttachment(id: "single"),
                CaptureAttachment(id: "current_pipeline"),
            ],
            hotkey: nil
        )
        await state.savePreset(modeID: mode.id, preset: p)
        selectedPresetID = id
    }

    /// Modal text prompt built on top of NSAlert (keeps us off SwiftUI's
    /// sheet machinery which doesn't play well with menu-bar-only apps).
    private func promptText(title: String, placeholder: String) async -> String? {
        await MainActor.run {
            let alert = NSAlert()
            alert.messageText = title
            alert.addButton(withTitle: "Create")
            alert.addButton(withTitle: "Cancel")
            let tf = NSTextField(frame: NSRect(x: 0, y: 0, width: 240, height: 24))
            tf.placeholderString = placeholder
            alert.accessoryView = tf
            NSApp.activate(ignoringOtherApps: true)
            let resp = alert.runModal()
            if resp == .alertFirstButtonReturn {
                let v = tf.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
                return v.isEmpty ? nil : v
            }
            return nil
        }
    }

    private func slugify(_ s: String) -> String {
        var out = ""
        for ch in s.lowercased() {
            if ch.isLetter || ch.isNumber {
                out.append(ch)
            } else if ch == " " || ch == "_" || ch == "-" {
                out.append("-")
            }
        }
        while out.contains("--") {
            out = out.replacingOccurrences(of: "--", with: "-")
        }
        return out.trimmingCharacters(in: CharacterSet(charactersIn: "-"))
    }
}

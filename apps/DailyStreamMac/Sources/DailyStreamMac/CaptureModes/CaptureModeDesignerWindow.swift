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
            Self.presentWithoutLeavingFullScreenSpace(existing)
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
        // Allow the Designer to appear on top of a full-screen video
        // (e.g. QuickTime / Safari / IINA in native fullscreen Space)
        // instead of yanking the user back to the desktop Space.
        //
        // * `.canJoinAllSpaces`   — show on whichever Space is front.
        // * `.fullScreenAuxiliary`— treat as auxiliary UI for any
        //                           fullscreen Space so we don't cause
        //                           that Space to exit fullscreen.
        w.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        // `.floating` keeps us above the front app's content window
        // without elevating to the shielding level used by the region
        // overlay.
        w.level = .floating
        // Regular windows (i.e. .titled) accept first-responder
        // automatically; we just want to avoid the global activation
        // that would switch Spaces.
        w.hidesOnDeactivate = false
        Self.presentWithoutLeavingFullScreenSpace(w)
        self.window = w
    }

    func close() {
        window?.orderOut(nil)
    }

    /// Bring ``win`` to front *without* calling
    /// ``NSApp.activate(ignoringOtherApps:)`` which on a
    /// `.accessory`-policy app triggers a Space switch and kicks
    /// fullscreen video players (QuickTime, Safari, IINA, …) out of
    /// their fullscreen Space.  Using ``orderFrontRegardless`` +
    /// ``makeKey`` keeps the window visible on the current Space while
    /// still letting it receive keyboard input.
    private static func presentWithoutLeavingFullScreenSpace(_ win: NSWindow) {
        win.orderFrontRegardless()
        win.makeKey()
    }

    /// Run `operation` with the Designer window temporarily hidden so
    /// it doesn't occlude the screen during a fullscreen region
    /// selection.  The window is restored to its previous visibility
    /// after the operation finishes (including on cancel / throw).
    ///
    /// We use `orderOut` instead of `miniaturize` because
    /// miniaturizing would bounce the user out of the current
    /// (possibly fullscreen) Space on restore.
    func withHiddenWindow<T>(_ operation: () async -> T) async -> T {
        let w = window
        let wasVisible = w?.isVisible ?? false
        if wasVisible {
            w?.orderOut(nil)
        }
        let result = await operation()
        if wasVisible {
            // Restore on the current Space without triggering a
            // Space switch.
            if let w { Self.presentWithoutLeavingFullScreenSpace(w) }
        }
        return result
    }
}

// MARK: - Root designer view --------------------------------------------

struct CaptureModeDesignerView: View {
    @ObservedObject var state: AppState
    let onClose: () -> Void

    @State private var selectedModeID: String? = nil
    @State private var selectedPresetID: String? = nil
    @State private var showTemplateBrowser: Bool = false

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
                Menu {
                    Button {
                        Task { await createMode() }
                    } label: {
                        Label("Blank Mode", systemImage: "square.dashed")
                    }
                    Button {
                        showTemplateBrowser = true
                    } label: {
                        Label("From Template…", systemImage: "books.vertical")
                    }
                    Button {
                        Task { await state.importTemplateFromFile() }
                    } label: {
                        Label("Import from File…", systemImage: "square.and.arrow.down")
                    }
                    if let id = selectedModeID {
                        Divider()
                        Button {
                            Task { await state.exportModeToFile(id) }
                        } label: {
                            Label("Export Current Mode…",
                                  systemImage: "square.and.arrow.up")
                        }
                    }
                } label: {
                    Label("New", systemImage: "plus")
                }
                .menuStyle(.borderlessButton)
                .fixedSize()

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
        .sheet(isPresented: $showTemplateBrowser) {
            TemplateBrowserSheet(
                state: state,
                onInstalled: { modeID in
                    selectedModeID = modeID
                    showTemplateBrowser = false
                },
                onDismiss: { showTemplateBrowser = false }
            )
        }
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

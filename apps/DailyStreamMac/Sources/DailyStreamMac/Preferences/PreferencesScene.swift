// PreferencesScene.swift
// SwiftUI Settings scene with 5 panes.
// Accessed via the standard menu → Settings (⌘,).
//
// NOTE: At M5 we read/write config via the Python core RPC
// (config.get / config.set) so there is no local persistence
// duplication.  The Settings scene is purely a presentation layer.

import SwiftUI
import KeyboardShortcuts

struct PreferencesView: View {
    enum Tab: String, CaseIterable, Identifiable {
        case general = "General"
        case hotkeys = "Hotkeys"
        case capture = "Capture"
        case ai = "AI"
        case sync = "Sync"
        var id: String { rawValue }
    }

    @State private var selectedTab: Tab = .general

    var body: some View {
        TabView(selection: $selectedTab) {
            GeneralPane().tabItem {
                Label("General", systemImage: "gear")
            }.tag(Tab.general)

            HotkeysPane().tabItem {
                Label("Hotkeys", systemImage: "keyboard")
            }.tag(Tab.hotkeys)

            CapturePane().tabItem {
                Label("Capture", systemImage: "camera")
            }.tag(Tab.capture)

            AIPane().tabItem {
                Label("AI", systemImage: "sparkles")
            }.tag(Tab.ai)

            SyncPane().tabItem {
                Label("Sync", systemImage: "arrow.triangle.2.circlepath")
            }.tag(Tab.sync)
        }
        .frame(width: 520, height: 400)
        .padding()
    }
}

// MARK: - Panes --------------------------------------------------------

struct GeneralPane: View {
    var body: some View {
        Form {
            Section {
                Text("DailyStream")
                    .font(.headline)
                Text("General settings will be configurable here.\nFor now, edit ~/.dailystream/config.json directly.")
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
    }
}

struct HotkeysPane: View {
    var body: some View {
        Form {
            Section("Screenshot") {
                KeyboardShortcuts.Recorder("Shortcut:", name: .screenshot)
            }
            Section("Clipboard Capture") {
                KeyboardShortcuts.Recorder("Shortcut:", name: .clipboardCapture)
            }
            Section("Pipeline Switcher") {
                KeyboardShortcuts.Recorder("Shortcut:", name: .pipelinePicker)
            }
            Section {
                Text("Preset-specific hotkeys are managed from the\nScreenshot menu → presets.")
                    .font(DSFont.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
    }
}

struct CapturePane: View {
    @ObservedObject private var state = AppHost.shared.state

    var body: some View {
        Form {
            Section("Capture Mode Designer") {
                Text("Design Modes, build Presets from atomic Attachments, and bind hotkeys.  Switching Mode swaps the whole set of bindings — shortcuts from other Modes are never active at the same time.")
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)

                if let active = state.captureModes.activeMode {
                    HStack {
                        Text("Active Mode")
                        Spacer()
                        Text("\(active.emoji) \(active.name)")
                            .foregroundStyle(.secondary)
                    }
                    HStack {
                        Text("Presets")
                        Spacer()
                        Text("\(active.presets.count)")
                            .foregroundStyle(.secondary)
                    }
                }

                HStack {
                    Spacer()
                    Button("Open Designer…") {
                        CaptureModeDesignerWindow.shared.show(state: state)
                    }
                    .buttonStyle(.borderedProminent)
                }
            }

            Section("Running Intervals") {
                if state.runningIntervals.isEmpty {
                    Text("No interval captures are currently running.")
                        .font(.system(size: 11))
                        .foregroundStyle(.tertiary)
                } else {
                    ForEach(Array(state.runningIntervals).sorted(), id: \.self) { key in
                        HStack {
                            Image(systemName: "circle.fill")
                                .foregroundStyle(.orange)
                                .font(.system(size: 8))
                            Text(key)
                                .font(.system(.body, design: .monospaced))
                            Spacer()
                            Button("Stop") {
                                Task { await stopInterval(key: key) }
                            }
                            .controlSize(.small)
                        }
                    }
                }
            }
        }
        .formStyle(.grouped)
    }

    private func stopInterval(key: String) async {
        let parts = key.split(separator: "/", maxSplits: 1).map(String.init)
        guard parts.count == 2 else { return }
        await state.stopInterval(modeID: parts[0], presetID: parts[1])
    }
}

struct AIPane: View {
    var body: some View {
        Form {
            Section {
                Text("AI configuration (model, timeout, batch size, etc.)\nwill be wired to config.get / config.set in a future update.")
                    .foregroundStyle(.secondary)
            }
            Section {
                Text("API key: set via the DAILYSTREAM_AI_KEY\nenvironment variable (recommended).")
                    .font(DSFont.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
    }
}

struct SyncPane: View {
    var body: some View {
        Form {
            Section {
                Text("Sync backend (Markdown / Obsidian / both / none)\nconfiguration coming soon.")
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
    }
}

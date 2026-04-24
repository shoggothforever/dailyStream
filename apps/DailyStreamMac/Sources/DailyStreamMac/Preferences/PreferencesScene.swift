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
    var body: some View {
        Form {
            Section {
                Text("Capture mode and preset management coming soon.")
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
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

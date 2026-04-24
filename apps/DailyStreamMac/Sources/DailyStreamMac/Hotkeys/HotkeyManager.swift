// HotkeyManager.swift
// Thin wrapper around sindresorhus/KeyboardShortcuts for our two built-in
// global shortcuts.  Per-preset hotkeys will be added in a later
// milestone once preset management reaches the Swift side.

import SwiftUI
import KeyboardShortcuts

/// All registered shortcut names live here so the Preferences pane can
/// enumerate them later via `KeyboardShortcuts.Name.allCases`.
public extension KeyboardShortcuts.Name {
    /// `⌘1` by default — interactive drag-to-select screenshot.
    static let screenshot = Self(
        "screenshot",
        default: .init(.one, modifiers: [.command])
    )

    /// `⌘2` by default — capture whatever the clipboard holds right now.
    static let clipboardCapture = Self(
        "clipboardCapture",
        default: .init(.two, modifiers: [.command])
    )
}

@MainActor
public final class HotkeyManager {
    private unowned let state: AppState

    public init(state: AppState) {
        self.state = state
    }

    /// Called exactly once after `AppState.boot()` succeeds.
    public func install() {
        KeyboardShortcuts.onKeyDown(for: .screenshot) { [weak state] in
            guard let state else { return }
            Task { await state.takeScreenshot() }
        }
        KeyboardShortcuts.onKeyDown(for: .clipboardCapture) { [weak state] in
            guard let state else { return }
            Task { await state.captureClipboard() }
        }
    }
}

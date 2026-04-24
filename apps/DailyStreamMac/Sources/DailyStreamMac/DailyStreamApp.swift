// DailyStreamApp.swift
// SwiftUI entry point.  A menu-bar-only app built around `MenuBarExtra`.
//
// The app launches with `.accessory` activation policy so no Dock
// entry appears and no main window is created; all UI lives in the
// menu bar extra + the HUD / Toast panels we manage ourselves.

import SwiftUI
import AppKit
import DailyStreamCore

@main
struct DailyStreamApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    /// We can't read `appDelegate.state` directly from a Scene builder
    /// before `NSApplicationDelegate.applicationDidFinishLaunching(_:)`
    /// has run.  Instead, we read the shared singleton the delegate
    /// installs, which is populated synchronously in `application
    /// WillFinishLaunching(_:)`.
    var body: some Scene {
        MenuBarExtra {
            MenuBarContent(state: AppHost.shared.state)
                .toastOverlay(host: AppHost.shared)
        } label: {
            MenuBarIcon(state: AppHost.shared.state.iconState)
        }
        .menuBarExtraStyle(.menu)
    }
}

// MARK: - Delegate ------------------------------------------------------

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationWillFinishLaunching(_ notification: Notification) {
        // Install the singleton state *before* Scene builders query it.
        _ = AppHost.shared
        NSApp.setActivationPolicy(.accessory)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        Task { @MainActor in
            await AppHost.shared.boot()
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        // Best-effort graceful shutdown.
        let sem = DispatchSemaphore(value: 0)
        Task { @MainActor in
            await AppHost.shared.state.shutdown()
            sem.signal()
        }
        _ = sem.wait(timeout: .now() + 3)
    }
}

// MARK: - App host ------------------------------------------------------

/// Holds globally-shared references.  `MenuBarExtra`'s label / content
/// closures are evaluated on every state change; reading from a
/// singleton avoids churn.
@MainActor
final class AppHost {
    static let shared = AppHost()

    let state: AppState
    let hotkeys: HotkeyManager

    private init() {
        let st = AppState()
        self.state = st
        self.hotkeys = HotkeyManager(state: st)
    }

    /// Called once from the AppDelegate after app launch.
    func boot() async {
        await state.boot()
        hotkeys.install()
    }
}

// MARK: - Toast overlay glue --------------------------------------------

private struct ToastHostModifier: ViewModifier {
    let host: AppHost
    @ObservedObject var state: AppState

    init(host: AppHost) {
        self.host = host
        self.state = host.state
    }

    func body(content: Content) -> some View {
        content
            .onChange(of: state.toastMessage) { newValue in
                if let m = newValue {
                    ToastCenter.shared.show(m)
                }
            }
    }
}

extension View {
    func toastOverlay(host: AppHost) -> some View {
        modifier(ToastHostModifier(host: host))
    }
}

// AppState+Feedback.swift
// Lightweight feedback helpers (menubar flash, shutter sound, system
// notification) consumed by `listenForEvents` in AppState.swift.
//
// Split out of the main store file for readability only — no behaviour
// changes.  All methods are `internal` (default) so the main file's
// event loop can dispatch to them.

import Foundation
import AppKit
import UserNotifications

extension AppState {
    func flashMenuBarIcon() {
        // Two-pulse flash so the user actually notices.  We always
        // restore to the workspace-determined resting state (recording
        // vs idle) rather than trusting whatever transient state we
        // started from.
        let resting: MenuBarIconState = workspace.isActive ? .recording : .idle
        let originalMessage = toastMessage
        _ = originalMessage  // keep compiler happy in some builds

        Task { @MainActor in
            for _ in 0..<2 {
                self.setIconState(.flashing)
                try? await Task.sleep(nanoseconds: 140_000_000)
                self.setIconState(resting)
                try? await Task.sleep(nanoseconds: 100_000_000)
            }
        }
    }

    /// Play the system "Grab" sound (same shutter tone the OS uses for
    /// ``⌘⇧4``).  Falls back to the user's default alert sound if Grab
    /// isn't installed on the current macOS release.
    func playShutterSound(volume: Double) {
        let candidates = ["Grab", "Ping", "Pop", "Tink"]
        var picked: NSSound? = nil
        for name in candidates {
            if let s = NSSound(named: NSSound.Name(name)) {
                picked = s
                break
            }
        }
        let sound = picked ?? NSSound(named: NSSound.Name("Funk"))
        guard let sound else { return }
        sound.volume = Float(max(0.0, min(1.0, volume)))
        sound.play()
    }

    func postSystemNotification(title: String, body: String) {
        // UserNotifications asserts when the host process lacks a
        // proper Bundle (i.e. when running via `swift run` outside an
        // .app).  In that case, surface the message as an in-app toast
        // instead of crashing.
        guard Bundle.main.bundleIdentifier != nil else {
            showToast(title: title, subtitle: body)
            return
        }
        let center = UNUserNotificationCenter.current()
        center.requestAuthorization(options: [.alert, .sound]) { _, _ in }
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        let req = UNNotificationRequest(
            identifier: UUID().uuidString,
            content: content,
            trigger: nil
        )
        center.add(req, withCompletionHandler: nil)
    }
}

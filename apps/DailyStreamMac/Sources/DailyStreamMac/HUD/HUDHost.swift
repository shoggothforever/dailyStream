// HUDHost.swift
// Async/await-first façade for showing modal HUD dialogs.
//
// Usage
// -----
// ```
// let result: NewWorkspaceResult? = await HUDHost.shared.present { close in
//     NewWorkspaceView { values in close(values) }
// }
// ```
//
// `close(value)` resumes the awaiting task with `value`; pressing Esc or
// calling `close(nil)` resumes with `nil` (cancellation).  The host
// enforces one-HUD-at-a-time: a second `present` while one is already
// on screen will cancel the previous one.

import AppKit
import SwiftUI

@MainActor
final class HUDHost {
    static let shared = HUDHost()

    private var controller: HUDController?
    private var activeContinuation: CheckedContinuation<Any?, Never>?

    private init() {}

    /// Show a SwiftUI view inside a spotlight-positioned HUD and await a
    /// typed result.  The view factory receives a `close` callback that
    /// resumes the awaiting caller.
    ///
    /// Returns `nil` on user cancellation (Esc or `close(nil)`).
    func present<V: View, T>(
        @ViewBuilder _ builder: (@escaping (T?) -> Void) -> V
    ) async -> T? {
        // If another HUD is on screen, cancel it first so continuations
        // don't leak.  This matches menu-bar conventions (only one
        // floating dialog visible at a time).
        cancelActive()

        let value = await withCheckedContinuation { (cont: CheckedContinuation<Any?, Never>) in
            self.activeContinuation = cont

            let closeClosure: (T?) -> Void = { [weak self] value in
                Task { @MainActor in
                    self?.finish(with: value as Any?)
                }
            }

            let ctrl = HUDController(placement: .spotlight)
            let view = builder(closeClosure)
            ctrl.setContent(view)
            ctrl.show(onKeyDown: { [weak self] key in
                if key == .escape {
                    Task { @MainActor in self?.finish(with: nil) }
                    return true
                }
                return false
            })
            self.controller = ctrl
        }
        return value as? T
    }

    /// Convenience wrapper that ignores the result — for pure
    /// "close-when-done" dialogs (confirmations).
    func dismiss() {
        finish(with: nil)
    }

    private func finish(with value: Any?) {
        controller?.hide()
        controller = nil
        if let cont = activeContinuation {
            activeContinuation = nil
            cont.resume(returning: value)
        }
    }

    private func cancelActive() {
        guard activeContinuation != nil else { return }
        finish(with: nil)
    }
}

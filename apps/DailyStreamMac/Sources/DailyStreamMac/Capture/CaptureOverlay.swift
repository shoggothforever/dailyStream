// CaptureOverlay.swift
// Manages the full-screen overlay windows for screenshot region selection.
//
// For each connected NSScreen we create a borderless, topmost NSWindow
// containing a `SelectionCanvas` + `MagnifierView`.  The user drags on
// any screen to define the selection rectangle.  On completion the
// region string ("x,y,w,h" in global screen coordinates) is delivered
// via an async continuation.
//
// Usage from AppState
// -------------------
// ```
// let region: String? = await CaptureOverlay.selectRegion()
// ```
//
// The returned string is nil when the user presses Esc.

import AppKit
import Quartz

@MainActor
enum CaptureOverlay {
    /// Prevent the coordinator from being deallocated while the
    /// overlay is active.  Cleared in `handleResult`.
    private static var activeCoordinator: OverlayCoordinator?

    /// Show full-screen overlays on every screen.  Returns the selected
    /// region as "x,y,w,h" in global screen-pixel coordinates, or nil
    /// when cancelled.
    static func selectRegion() async -> String? {
        await withCheckedContinuation { cont in
            let coordinator = OverlayCoordinator(continuation: cont)
            activeCoordinator = coordinator
            coordinator.show()
        }
    }

    /// Called by the coordinator when it finishes, so we can release it.
    fileprivate static func coordinatorDidFinish() {
        activeCoordinator = nil
    }
}

// MARK: - Coordinator ---------------------------------------------------

@MainActor
fileprivate final class OverlayCoordinator {
    private var windows: [NSWindow] = []
    private var continuation: CheckedContinuation<String?, Never>?
    private var delivered = false

    init(continuation: CheckedContinuation<String?, Never>) {
        self.continuation = continuation
    }

    func show() {
        for screen in NSScreen.screens {
            let win = createWindow(for: screen)
            windows.append(win)
            // `orderFrontRegardless` avoids a Space switch when the
            // frontmost app is in fullscreen (native fullscreen Space).
            // Combined with the `.canJoinAllSpaces` collectionBehavior
            // set below, the overlay paints on top of the fullscreen
            // video without kicking it out of its Space.
            win.orderFrontRegardless()
            win.makeKey()
        }
        // NOTE: intentionally NOT calling
        // `NSApp.activate(ignoringOtherApps: true)` — on a
        // `.accessory`-policy app that triggers a Space switch and the
        // user would see their fullscreen player snap back to desktop.
        // The shielding-level overlay + makeKey is enough to receive
        // key events.
    }

    private func createWindow(for screen: NSScreen) -> NSWindow {
        let frame = screen.frame
        let win = NSWindow(
            contentRect: frame,
            styleMask: [.borderless],
            backing: .buffered,
            defer: false,
            screen: screen
        )
        win.level = NSWindow.Level(rawValue: Int(CGShieldingWindowLevel()))
        // Make the overlay show on *whichever* Space is currently
        // front, including native-fullscreen Spaces hosting a video
        // player.  Without this the window is only visible on the
        // desktop Space and the OS would switch Spaces to show it,
        // collapsing the user's fullscreen session.
        win.collectionBehavior = [
            .canJoinAllSpaces,
            .fullScreenAuxiliary,
            .stationary,
            .ignoresCycle,
        ]
        win.isOpaque = false
        win.backgroundColor = .clear
        win.ignoresMouseEvents = false
        win.acceptsMouseMovedEvents = true
        win.hasShadow = false

        let canvas = SelectionCanvas(frame: frame)
        canvas.onComplete = { [weak self] rect in
            self?.handleResult(rect: rect, screen: screen)
        }
        win.contentView = canvas
        win.makeFirstResponder(canvas)

        return win
    }

    private func handleResult(rect: NSRect?, screen: NSScreen) {
        guard !delivered else { return }
        delivered = true

        // Dismiss all overlay windows
        for w in windows {
            w.orderOut(nil)
        }
        windows.removeAll()

        guard let rect else {
            continuation?.resume(returning: nil)
            continuation = nil
            CaptureOverlay.coordinatorDidFinish()
            return
        }

        // Convert from AppKit coordinates (origin = bottom-left of screen)
        // to global screen coordinates (origin = top-left of main screen).
        let screenFrame = screen.frame
        let mainScreenHeight = NSScreen.screens.first?.frame.height ?? screenFrame.height

        let x = Int(screenFrame.origin.x + rect.origin.x)
        // AppKit y=0 is bottom; screen y=0 is top
        let y = Int(mainScreenHeight - (screenFrame.origin.y + rect.origin.y + rect.height))
        let w = Int(rect.width)
        let h = Int(rect.height)

        let region = "\(x),\(y),\(w),\(h)"
        continuation?.resume(returning: region)
        continuation = nil
        CaptureOverlay.coordinatorDidFinish()
    }
}

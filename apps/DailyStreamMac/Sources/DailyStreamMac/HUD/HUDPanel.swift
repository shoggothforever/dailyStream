// HUDPanel.swift
// Floating borderless NSPanel that hosts SwiftUI content and becomes
// the key window on demand.  Used as the substrate for every HUD we
// build in M2+ (QuickCapture, NewWorkspace, ScreenshotDesc, Confirm…).
//
// Behaviour
// ---------
// * borderless + nonactivatingPanel → does NOT steal focus from the
//   currently-active app when shown, which matches menu-bar-accessory
//   app conventions (we do not want to trample Safari / VS Code).
// * becomesKeyOnlyIfNeeded = true → the panel still accepts key events
//   for our own text fields, but clicks outside dismiss it cleanly.
// * We explicitly `makeKeyAndOrderFront:` after display so embedded
//   NSTextField instances receive keystrokes.  Without this a fresh
//   panel stays non-key and the text cursor never blinks.

import AppKit
import SwiftUI

/// Non-activating floating panel that can still become key so embedded
/// text fields receive keystrokes.
final class HUDPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}

/// Lightweight placement hint — HUDs differ in where they want to live
/// on screen.
enum HUDPlacement {
    /// Horizontally centered, 1/3 down from the top of the main screen
    /// (Spotlight-style).
    case spotlight
    /// Bottom-right corner with a 24pt margin (Toasts).
    case bottomRight
}

enum HUDConstants {
    static let cornerRadius: CGFloat = 16
    static let defaultWidth: CGFloat = 480
}

@MainActor
final class HUDController {
    private let panel: HUDPanel
    private let hostingController: NSHostingController<AnyView>
    private let placement: HUDPlacement

    /// Key-event monitor installed while the panel is on-screen.
    private var keyMonitor: Any?
    /// KVO observation for content size changes.
    private var sizeObservation: NSKeyValueObservation?

    init(placement: HUDPlacement,
         width: CGFloat = HUDConstants.defaultWidth) {
        self.placement = placement
        let size = NSSize(width: width, height: 120)

        let panel = HUDPanel(
            contentRect: NSRect(origin: .zero, size: size),
            styleMask: [.borderless, .nonactivatingPanel, .utilityWindow],
            backing: .buffered,
            defer: false
        )
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .transient]
        panel.isMovable = true
        panel.hidesOnDeactivate = false
        panel.titleVisibility = .hidden
        panel.isReleasedWhenClosed = false
        self.panel = panel

        self.hostingController = NSHostingController(rootView: AnyView(EmptyView()))
        panel.contentViewController = hostingController
    }

    /// Swap the SwiftUI content, re-layout, and re-position.
    func setContent<V: View>(_ view: V) {
        hostingController.rootView = AnyView(view)
        hostingController.view.layoutSubtreeIfNeeded()
        let fit = hostingController.view.fittingSize
        let width = max(fit.width, HUDConstants.defaultWidth)
        let height = max(fit.height, 100)
        panel.setContentSize(NSSize(width: width, height: height))
        reposition()
    }

    func show(onKeyDown: ((HUDKey) -> Bool)? = nil) {
        reposition()
        panel.orderFrontRegardless()
        panel.makeKeyAndOrderFront(nil)
        installKeyMonitor(onKeyDown: onKeyDown)
        startObservingSize()
    }

    func hide() {
        stopObservingSize()
        removeKeyMonitor()
        panel.orderOut(nil)
    }

    // MARK: - Dynamic size tracking

    /// Observe the hosting view's intrinsicContentSize changes so the
    /// panel grows/shrinks as the SwiftUI content changes (e.g. multi-line
    /// text fields expanding).
    private func startObservingSize() {
        stopObservingSize()
        sizeObservation = hostingController.view.observe(
            \.frame, options: [.new]
        ) { [weak self] _, _ in
            Task { @MainActor in
                self?.updatePanelSize()
            }
        }
    }

    private func stopObservingSize() {
        sizeObservation?.invalidate()
        sizeObservation = nil
    }

    private func updatePanelSize() {
        hostingController.view.layoutSubtreeIfNeeded()
        let fit = hostingController.view.fittingSize
        let width = max(fit.width, HUDConstants.defaultWidth)
        let height = max(fit.height, 100)
        let currentSize = panel.frame.size
        // Only resize if height actually changed (avoid infinite loops)
        if abs(currentSize.height - height) > 1 {
            // Preserve the panel's top edge (grow downward)
            let oldFrame = panel.frame
            let newOrigin = NSPoint(
                x: oldFrame.origin.x,
                y: oldFrame.origin.y + oldFrame.height - height
            )
            panel.setFrame(
                NSRect(origin: newOrigin, size: NSSize(width: width, height: height)),
                display: true,
                animate: false
            )
        }
    }

    // MARK: - Positioning

    private func reposition() {
        guard let screen = NSScreen.main else { return }
        let visible = screen.visibleFrame
        let frame = panel.frame
        switch placement {
        case .spotlight:
            let x = visible.midX - frame.width / 2
            let y = visible.maxY - frame.height - visible.height * 0.30
            panel.setFrameOrigin(NSPoint(x: x, y: y))
        case .bottomRight:
            let margin: CGFloat = 24
            let x = visible.maxX - frame.width - margin
            let y = visible.minY + margin
            panel.setFrameOrigin(NSPoint(x: x, y: y))
        }
    }

    // MARK: - Key handling

    private func installKeyMonitor(onKeyDown: ((HUDKey) -> Bool)?) {
        removeKeyMonitor()
        guard let onKeyDown else { return }
        keyMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            guard let self else { return event }
            // Only react when the event is aimed at OUR panel.
            guard event.window === self.panel else { return event }
            let key: HUDKey?
            switch event.keyCode {
            case 53: key = .escape
            case 36, 76: key = .enter  // Return / keypad Enter
            default: key = nil
            }
            if let key, onKeyDown(key) {
                return nil  // consumed
            }
            return event
        }
    }

    private func removeKeyMonitor() {
        if let keyMonitor {
            NSEvent.removeMonitor(keyMonitor)
        }
        keyMonitor = nil
    }
}

enum HUDKey {
    case escape
    case enter
}

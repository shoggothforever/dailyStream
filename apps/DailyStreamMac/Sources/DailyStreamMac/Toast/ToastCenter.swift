// ToastCenter.swift
// Borderless, floating NSWindow anchored to the bottom-right corner of
// the main screen.  Displays a single `ToastMessage` at a time and auto-
// dismisses after a short delay.
//
// We opted for an NSWindow (rather than a SwiftUI-only `.overlay`) so
// the toast can float above other apps while the menu bar shell is not
// activated (the app runs with `.accessory` activation policy).

import AppKit
import SwiftUI

@MainActor
public final class ToastCenter: ObservableObject {
    public static let shared = ToastCenter()

    private var window: NSPanel?
    private var dismissWorkItem: DispatchWorkItem?

    private init() {}

    public func show(_ message: ToastMessage,
                     duration: TimeInterval = 2.5) {
        ensureWindow()
        guard let window else { return }

        // Swap SwiftUI content each time — the window host is re-used.
        let content = ToastView(message: message) { [weak self] in
            self?.dismiss()
        }
        window.contentViewController = NSHostingController(rootView: content)
        sizeWindow()
        positionWindowBottomRight()
        window.orderFrontRegardless()

        dismissWorkItem?.cancel()
        let wi = DispatchWorkItem { [weak self] in self?.dismiss() }
        dismissWorkItem = wi
        DispatchQueue.main.asyncAfter(deadline: .now() + duration, execute: wi)
    }

    public func dismiss() {
        dismissWorkItem?.cancel()
        dismissWorkItem = nil
        window?.orderOut(nil)
    }

    // MARK: - Window plumbing

    private func ensureWindow() {
        guard window == nil else { return }
        let panel = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 320, height: 72),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.level = .statusBar
        panel.collectionBehavior = [.canJoinAllSpaces, .stationary,
                                    .ignoresCycle]
        panel.isMovable = false
        self.window = panel
    }

    private func sizeWindow() {
        guard let window else { return }
        // Fit to SwiftUI intrinsic size (+ a little margin).
        window.contentViewController?.view.layoutSubtreeIfNeeded()
        if let fit = window.contentViewController?.view.fittingSize {
            window.setContentSize(NSSize(width: max(fit.width, 280),
                                         height: max(fit.height, 56)))
        }
    }

    private func positionWindowBottomRight() {
        guard let window,
              let screen = NSScreen.main else { return }
        let margin: CGFloat = 24
        let frame = window.frame
        let visible = screen.visibleFrame
        window.setFrameOrigin(NSPoint(
            x: visible.maxX - frame.width - margin,
            y: visible.minY + margin
        ))
    }
}

// MARK: - SwiftUI view -------------------------------------------------

struct ToastView: View {
    let message: ToastMessage
    let onClose: () -> Void
    @State private var appeared = false

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 22))
                .foregroundStyle(DSColor.success)
            VStack(alignment: .leading, spacing: 2) {
                Text(message.title)
                    .font(.system(size: 13, weight: .semibold))
                if let sub = message.subtitle {
                    Text(sub)
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
            }
            Spacer()
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .background {
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .fill(.ultraThinMaterial)
                .shadow(color: .black.opacity(0.18),
                        radius: 12, x: 0, y: 6)
        }
        .frame(minWidth: 280, maxWidth: 360, minHeight: 56)
        .opacity(appeared ? 1 : 0)
        .offset(x: appeared ? 0 : 12)
        .animation(.dsHudIn, value: appeared)
        .onAppear { appeared = true }
        .onTapGesture { onClose() }
    }
}

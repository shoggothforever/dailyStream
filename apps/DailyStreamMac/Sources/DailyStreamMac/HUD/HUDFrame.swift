// HUDFrame.swift
// Shared chrome for every HUD SwiftUI view: hud-window material
// background, 16pt corner radius, soft shadow, consistent padding.

import SwiftUI

/// Wraps HUD content with our standard chrome.  Use as the outermost
/// view of any `HUDHost.present` builder.
struct HUDFrame<Content: View>: View {
    let content: Content
    init(@ViewBuilder _ content: () -> Content) {
        self.content = content()
    }

    var body: some View {
        content
            .padding(.horizontal, 20)
            .padding(.vertical, 16)
            .frame(minWidth: HUDConstants.defaultWidth,
                   maxWidth: HUDConstants.defaultWidth,
                   alignment: .leading)
            .background {
                RoundedRectangle(cornerRadius: HUDConstants.cornerRadius,
                                 style: .continuous)
                    .fill(Material.ultraThin)
                    .shadow(color: .black.opacity(0.18),
                            radius: 24, x: 0, y: 8)
            }
            .overlay {
                RoundedRectangle(cornerRadius: HUDConstants.cornerRadius,
                                 style: .continuous)
                    .stroke(Color.white.opacity(0.08), lineWidth: 0.5)
            }
            .clipShape(
                RoundedRectangle(cornerRadius: HUDConstants.cornerRadius,
                                 style: .continuous)
            )
    }
}

/// Bottom hint line — "↩ Continue  ⎋ Cancel" style.
struct HUDHintBar: View {
    let left: String?
    let right: String?

    init(left: String? = nil, right: String? = nil) {
        self.left = left
        self.right = right
    }

    var body: some View {
        HStack {
            if let left {
                Text(left)
                    .font(DSFont.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if let right {
                Text(right)
                    .font(DSFont.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

/// A text input for HUD panels with correct CJK IME handling.
///
/// - `singleLine: true` (default) — native NSTextField, fixed height.
/// - `singleLine: false` — NSTextView-based, grows up to 5 lines;
///   Shift+Enter inserts a newline.
///
/// In both modes, Enter submits only when the IME is NOT composing.
struct HUDTextField: View {
    @Binding var text: String
    let placeholder: String
    var singleLine: Bool = true
    var onSubmit: (() -> Void)? = nil

    @State private var multiLineHeight: CGFloat = 36

    private var displayHeight: CGFloat {
        singleLine ? 36 : min(max(multiLineHeight, 36), 120)
    }

    var body: some View {
        Group {
            if singleLine {
                HUDSingleLineField(
                    text: $text,
                    placeholder: placeholder,
                    onSubmit: onSubmit
                )
            } else {
                HUDMultiLineField(
                    text: $text,
                    placeholder: placeholder,
                    intrinsicHeight: $multiLineHeight,
                    onSubmit: onSubmit
                )
            }
        }
        .frame(height: displayHeight)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(Color(nsColor: .controlBackgroundColor))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(Color.secondary.opacity(0.2), lineWidth: 0.5)
        )
        .animation(.easeOut(duration: 0.1), value: displayHeight)
    }
}

// MARK: - Single-line: NSTextField wrapper

/// Uses a real NSTextField — native focus ring, placeholder, Tab order,
/// and IME all work out of the box.
private struct HUDSingleLineField: NSViewRepresentable {
    @Binding var text: String
    let placeholder: String
    var onSubmit: (() -> Void)?

    func makeCoordinator() -> Coordinator { Coordinator(parent: self) }

    func makeNSView(context: Context) -> NSTextField {
        let field = HUDNSTextField()
        field.stringValue = text
        field.placeholderString = placeholder
        field.isBordered = false
        field.drawsBackground = false
        field.font = .systemFont(ofSize: 15)
        field.textColor = .textColor
        field.focusRingType = .none
        field.lineBreakMode = .byTruncatingTail
        field.cell?.wraps = false
        field.cell?.isScrollable = true
        field.delegate = context.coordinator
        field.target = context.coordinator
        field.action = #selector(Coordinator.enterPressed(_:))
        return field
    }

    func updateNSView(_ field: NSTextField, context: Context) {
        if field.stringValue != text {
            field.stringValue = text
        }
    }

    final class Coordinator: NSObject, NSTextFieldDelegate {
        var parent: HUDSingleLineField
        init(parent: HUDSingleLineField) { self.parent = parent }

        func controlTextDidChange(_ obj: Notification) {
            guard let field = obj.object as? NSTextField else { return }
            parent.text = field.stringValue
        }

        @objc func enterPressed(_ sender: NSTextField) {
            // NSTextField action fires on Enter.
            // If the field editor has marked text (IME composing),
            // the action is not fired by AppKit, so we're safe here.
            parent.onSubmit?()
        }
    }
}

/// Custom NSTextField that accepts first mouse in non-activating panels.
private final class HUDNSTextField: NSTextField {
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override var acceptsFirstResponder: Bool { true }
}

// MARK: - Multi-line: NSTextView wrapper

private struct HUDMultiLineField: NSViewRepresentable {
    @Binding var text: String
    let placeholder: String
    @Binding var intrinsicHeight: CGFloat
    var onSubmit: (() -> Void)?

    func makeCoordinator() -> Coordinator { Coordinator(parent: self) }

    func makeNSView(context: Context) -> NSScrollView {
        let scrollView = NSScrollView()
        scrollView.hasVerticalScroller = false
        scrollView.hasHorizontalScroller = false
        scrollView.drawsBackground = false
        scrollView.borderType = .noBorder

        let textView = HUDNSTextView()
        textView.isRichText = false
        textView.allowsUndo = true
        textView.font = .systemFont(ofSize: 15)
        textView.textColor = .textColor
        textView.insertionPointColor = .textColor
        textView.drawsBackground = false
        textView.isVerticallyResizable = true
        textView.isHorizontallyResizable = false
        textView.textContainerInset = NSSize(width: 4, height: 6)
        textView.textContainer?.widthTracksTextView = true
        textView.textContainer?.lineBreakMode = .byWordWrapping
        textView.delegate = context.coordinator
        textView.singleLine = false

        // Placeholder via attributed string on empty
        textView.placeholderText = placeholder

        let coordinator = context.coordinator
        textView.submitHandler = {
            coordinator.parent.onSubmit?()
        }

        scrollView.documentView = textView
        return scrollView
    }

    func updateNSView(_ scrollView: NSScrollView, context: Context) {
        guard let textView = scrollView.documentView as? HUDNSTextView else { return }
        if textView.string != text {
            textView.string = text
            textView.needsDisplay = true
        }
    }

    final class Coordinator: NSObject, NSTextViewDelegate {
        var parent: HUDMultiLineField
        init(parent: HUDMultiLineField) { self.parent = parent }

        func textDidChange(_ notification: Notification) {
            guard let tv = notification.object as? NSTextView else { return }
            parent.text = tv.string
            recalcHeight(tv)
        }

        func recalcHeight(_ tv: NSTextView) {
            guard let container = tv.textContainer,
                  let manager = tv.layoutManager else { return }
            manager.ensureLayout(for: container)
            let usedRect = manager.usedRect(for: container)
            let inset = tv.textContainerInset
            let total = usedRect.height + inset.height * 2 + 4
            DispatchQueue.main.async {
                self.parent.intrinsicHeight = total
            }
        }
    }
}

/// Custom NSTextView for multi-line HUD input.
/// - Draws placeholder text when empty.
/// - Accepts first mouse click in non-activating panels.
/// - Handles IME correctly (Enter during composition confirms IME, not submit).
final class HUDNSTextView: NSTextView {
    var submitHandler: (() -> Void)?
    var singleLine: Bool = false
    var placeholderText: String = ""

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        // Draw placeholder when empty and not first responder or empty
        if string.isEmpty && !hasMarkedText() {
            let attrs: [NSAttributedString.Key: Any] = [
                .foregroundColor: NSColor.secondaryLabelColor,
                .font: font ?? .systemFont(ofSize: 15),
            ]
            let inset = textContainerInset
            let rect = NSRect(
                x: inset.width + 5,
                y: inset.height,
                width: bounds.width - inset.width * 2 - 10,
                height: bounds.height - inset.height * 2
            )
            placeholderText.draw(in: rect, withAttributes: attrs)
        }
    }

    override func becomeFirstResponder() -> Bool {
        needsDisplay = true
        return super.becomeFirstResponder()
    }

    override func keyDown(with event: NSEvent) {
        if event.keyCode == 36 || event.keyCode == 76 {
            if hasMarkedText() {
                super.keyDown(with: event)
                return
            }
            if !singleLine && event.modifierFlags.contains(.shift) {
                super.keyDown(with: event)
            } else {
                submitHandler?()
            }
            return
        }
        super.keyDown(with: event)
    }
}

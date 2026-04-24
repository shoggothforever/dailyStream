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

/// A large text field intended for the main HUD input.
struct HUDTextField: View {
    @Binding var text: String
    let placeholder: String
    var onSubmit: (() -> Void)? = nil

    var body: some View {
        TextField(placeholder, text: $text, onCommit: { onSubmit?() })
            .textFieldStyle(.plain)
            .font(.system(size: 16))
            .padding(.vertical, 8)
            .padding(.horizontal, 10)
            .background(
                RoundedRectangle(cornerRadius: 8)
                    .fill(Color.secondary.opacity(0.08))
            )
    }
}

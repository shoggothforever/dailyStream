// ConfirmView.swift
// Simple confirmation HUD — used where a NSAlert with "Continue / Cancel"
// would have fit.  Not currently triggered by anything at M2.7 (the
// rumps flow has no confirmation dialogs at all), but having the HUD
// ready means future features (e.g. "Delete pipeline?" in M2.6) can
// drop it in without introducing a new UI pattern.

import SwiftUI

public enum ConfirmResult: Sendable {
    case confirm
    case cancel
}

struct ConfirmView: View {
    let title: String
    let message: String
    let confirmLabel: String
    let destructive: Bool
    let onClose: (ConfirmResult) -> Void

    init(
        title: String,
        message: String,
        confirmLabel: String = "Continue",
        destructive: Bool = false,
        onClose: @escaping (ConfirmResult) -> Void
    ) {
        self.title = title
        self.message = message
        self.confirmLabel = confirmLabel
        self.destructive = destructive
        self.onClose = onClose
    }

    var body: some View {
        HUDFrame {
            VStack(alignment: .leading, spacing: 14) {
                HStack(spacing: 10) {
                    Image(systemName: destructive ?
                          "exclamationmark.triangle.fill" :
                          "questionmark.circle")
                        .font(.system(size: 20))
                        .foregroundStyle(destructive ? Color.orange : DSColor.accent)
                    VStack(alignment: .leading, spacing: 0) {
                        Text(title)
                            .font(.system(size: 15, weight: .semibold))
                        Text(message)
                            .font(DSFont.body)
                            .foregroundStyle(.secondary)
                            .lineLimit(3)
                    }
                    Spacer()
                }

                Divider().opacity(0.3)

                HStack {
                    Spacer()
                    Button("Cancel") { onClose(.cancel) }
                        .keyboardShortcut(.cancelAction)
                    Button(confirmLabel) { onClose(.confirm) }
                        .keyboardShortcut(.defaultAction)
                        .tint(destructive ? .red : DSColor.accent)
                }
            }
        }
    }
}

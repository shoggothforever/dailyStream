// PresetNameView.swift
// HUD to name a newly captured screenshot preset region and optionally
// bind it to a global hotkey.
//
// Semantic contract (must match `_on_create_preset`)
// --------------------------------------------------
// * Name is required — Esc or empty name cancels the whole flow and
//   no preset is persisted.
// * Hotkey is optional — empty means no global binding.
// * Hotkey format matches the Python config: `<cmd>+3`, `<ctrl>+<shift>+a`,
//   etc.  At M2.6 we accept free-form text input; a proper
//   NSEvent-based recorder will arrive in M5 Preferences.

import SwiftUI

public struct PresetValues: Sendable {
    public let name: String
    public let region: String    // "x,y,w,h"
    public let hotkey: String?   // nil / empty → no hotkey
}

struct PresetNameView: View {
    let region: String
    let onClose: (PresetValues?) -> Void

    @State private var name: String = ""
    @State private var hotkey: String = ""

    var body: some View {
        HUDFrame {
            VStack(alignment: .leading, spacing: 14) {
                HStack(spacing: 10) {
                    Image(systemName: "rectangle.dashed.badge.record")
                        .font(.system(size: 20))
                        .foregroundStyle(DSColor.accent)
                    VStack(alignment: .leading, spacing: 0) {
                        Text("New Screenshot Preset")
                            .font(.system(size: 15, weight: .semibold))
                        Text("Region: \(region)")
                            .font(DSFont.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                }

                HUDTextField(
                    text: $name,
                    placeholder: "Name (required)",
                    onSubmit: submit
                )

                HUDTextField(
                    text: $hotkey,
                    placeholder: "Hotkey (optional, e.g. <cmd>+3)",
                    onSubmit: submit
                )

                Divider().opacity(0.3)

                HUDHintBar(left: nil, right: "⎋ Cancel  ↩ Save")
            }
        }
    }

    private func submit() {
        let trimmedName = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedName.isEmpty else { return }
        let trimmedHotkey = hotkey.trimmingCharacters(in: .whitespacesAndNewlines)
        onClose(
            PresetValues(
                name: trimmedName,
                region: region,
                hotkey: trimmedHotkey.isEmpty ? nil : trimmedHotkey
            )
        )
    }
}

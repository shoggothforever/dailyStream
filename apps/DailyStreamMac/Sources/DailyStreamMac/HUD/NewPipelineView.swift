// NewPipelineView.swift
// Consolidates the Python 3-step rumps flow (name → description → goal)
// into a single HUD.
//
// Semantic contract (must match `_on_create_pipeline`)
// ----------------------------------------------------
// * Name is required (empty or Esc cancels).
// * Description and goal are optional.
// * On create, the new pipeline is immediately activated (matches
//   rumps behaviour `self.wm.activate_pipeline(name)` on line 465).

import SwiftUI

public struct NewPipelineValues: Sendable {
    public let name: String
    public let description: String
    public let goal: String
}

struct NewPipelineView: View {
    let onClose: (NewPipelineValues?) -> Void

    @State private var name: String = ""
    @State private var description: String = ""
    @State private var goal: String = ""

    var body: some View {
        HUDFrame {
            VStack(alignment: .leading, spacing: 14) {
                HStack(spacing: 10) {
                    Image(systemName: "square.stack.3d.up")
                        .font(.system(size: 20))
                        .foregroundStyle(DSColor.accent)
                    VStack(alignment: .leading, spacing: 0) {
                        Text("New Pipeline")
                            .font(.system(size: 15, weight: .semibold))
                        Text("Group related captures under one topic")
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
                    text: $description,
                    placeholder: "Description (optional)",
                    singleLine: false,
                    onSubmit: submit
                )

                HUDTextField(
                    text: $goal,
                    placeholder: "Goal (optional)",
                    singleLine: false,
                    onSubmit: submit
                )

                Divider().opacity(0.3)

                HUDHintBar(left: "⇥ next field", right: "⎋ Cancel  ↩ Create")
            }
        }
    }

    private func submit() {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }  // name is required
        onClose(
            NewPipelineValues(
                name: trimmed,
                description: description.trimmingCharacters(in: .whitespacesAndNewlines),
                goal: goal.trimmingCharacters(in: .whitespacesAndNewlines)
            )
        )
    }
}

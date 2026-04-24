// PipelinePickerView.swift
// Spotlight-style pipeline quick-switcher, triggered by a global
// hotkey (default ⌘3).  Shows the list of pipelines in the current
// workspace; typing filters the list; Enter switches; Esc cancels.
//
// Also supports creating a new pipeline inline — if the typed text
// doesn't match any existing pipeline, "Create ‹name›" appears as
// the last option.

import SwiftUI

struct PipelinePickerView: View {
    let pipelines: [String]
    let activePipeline: String?
    let onClose: (PipelinePickerResult?) -> Void

    @State private var query: String = ""
    @State private var selectedIndex: Int = 0

    private var filtered: [PickerItem] {
        let q = query.trimmingCharacters(in: .whitespaces).lowercased()
        var items: [PickerItem] = pipelines
            .filter { q.isEmpty || $0.lowercased().contains(q) }
            .map { .existing($0) }

        // If query is non-empty and doesn't exactly match an existing
        // pipeline, offer to create a new one.
        if !q.isEmpty && !pipelines.contains(where: { $0.lowercased() == q }) {
            let name = query.trimmingCharacters(in: .whitespaces)
            items.append(.create(name))
        }
        return items
    }

    var body: some View {
        HUDFrame {
            VStack(alignment: .leading, spacing: 10) {
                // Search field
                HStack(spacing: 8) {
                    Image(systemName: "magnifyingglass")
                        .foregroundStyle(.secondary)
                        .font(.system(size: 14))
                    HUDTextField(
                        text: $query,
                        placeholder: "Switch pipeline…",
                        onSubmit: confirmSelection
                    )
                }

                // Results list
                if filtered.isEmpty {
                    Text("No pipelines")
                        .font(DSFont.caption)
                        .foregroundStyle(.secondary)
                        .padding(.vertical, 4)
                } else {
                    VStack(spacing: 2) {
                        ForEach(Array(filtered.enumerated()), id: \.element.id) { idx, item in
                            pickerRow(item, isSelected: idx == clampedIndex)
                                .onTapGesture {
                                    selectedIndex = idx
                                    confirmSelection()
                                }
                        }
                    }
                    .padding(.vertical, 2)
                }

                Divider().opacity(0.3)

                HUDHintBar(
                    left: "⌘3",
                    right: "↑↓ Navigate  ↩ Switch  ⎋ Cancel"
                )
            }
        }
        .onAppear {
            // Pre-select the active pipeline
            if let active = activePipeline,
               let idx = filtered.firstIndex(where: {
                   if case .existing(let name) = $0 { return name == active }
                   return false
               }) {
                selectedIndex = idx
            }
        }
        // Keyboard navigation via local key monitor
        .background(
            KeyboardNavigationHelper(
                itemCount: filtered.count,
                selectedIndex: $selectedIndex,
                onConfirm: confirmSelection,
                onCancel: { onClose(nil) }
            )
        )
    }

    private var clampedIndex: Int {
        filtered.isEmpty ? 0 : min(selectedIndex, filtered.count - 1)
    }

    private func pickerRow(_ item: PickerItem, isSelected: Bool) -> some View {
        HStack(spacing: 8) {
            switch item {
            case .existing(let name):
                Image(systemName: name == activePipeline ? "checkmark.circle.fill" : "circle")
                    .font(.system(size: 13))
                    .foregroundStyle(name == activePipeline ? DSColor.accent : .secondary)
                Text(name)
                    .font(.system(size: 14))
                Spacer()
                if name == activePipeline {
                    Text("active")
                        .font(DSFont.caption)
                        .foregroundStyle(.tertiary)
                }
            case .create(let name):
                Image(systemName: "plus.circle")
                    .font(.system(size: 13))
                    .foregroundStyle(DSColor.success)
                Text("Create \"\(name)\"")
                    .font(.system(size: 14, weight: .medium))
                    .foregroundStyle(DSColor.success)
                Spacer()
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(
            RoundedRectangle(cornerRadius: 6)
                .fill(isSelected ? Color.accentColor.opacity(0.12) : Color.clear)
        )
        .contentShape(Rectangle())
    }

    private func confirmSelection() {
        let items = filtered
        guard !items.isEmpty else { return }
        let idx = clampedIndex
        let item = items[idx]
        switch item {
        case .existing(let name):
            onClose(.switchTo(name))
        case .create(let name):
            onClose(.create(name))
        }
    }
}

// MARK: - Model

enum PipelinePickerResult: Sendable {
    case switchTo(String)
    case create(String)
}

private enum PickerItem: Identifiable {
    case existing(String)
    case create(String)

    var id: String {
        switch self {
        case .existing(let n): return "e:\(n)"
        case .create(let n): return "c:\(n)"
        }
    }
}

// MARK: - Keyboard navigation helper

/// Invisible NSView that captures ↑↓ arrow keys for list navigation.
/// Works inside HUDPanel's nonactivatingPanel.
private struct KeyboardNavigationHelper: NSViewRepresentable {
    let itemCount: Int
    @Binding var selectedIndex: Int
    let onConfirm: () -> Void
    let onCancel: () -> Void

    func makeNSView(context: Context) -> KeyNavView {
        let v = KeyNavView()
        v.handler = context.coordinator
        return v
    }

    func updateNSView(_ v: KeyNavView, context: Context) {
        context.coordinator.parent = self
    }

    func makeCoordinator() -> Coordinator { Coordinator(parent: self) }

    final class Coordinator {
        var parent: KeyboardNavigationHelper
        init(parent: KeyboardNavigationHelper) { self.parent = parent }

        func handleKey(_ keyCode: UInt16) -> Bool {
            switch keyCode {
            case 125: // ↓
                if parent.itemCount > 0 {
                    parent.selectedIndex = min(parent.selectedIndex + 1,
                                               parent.itemCount - 1)
                }
                return true
            case 126: // ↑
                parent.selectedIndex = max(parent.selectedIndex - 1, 0)
                return true
            default:
                return false
            }
        }
    }

    final class KeyNavView: NSView {
        weak var handler: Coordinator?
        override var acceptsFirstResponder: Bool { true }
        override func keyDown(with event: NSEvent) {
            if handler?.handleKey(event.keyCode) == true { return }
            super.keyDown(with: event)
        }
    }
}

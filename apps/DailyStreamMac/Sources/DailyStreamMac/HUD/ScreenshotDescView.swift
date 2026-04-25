// ScreenshotDescView.swift
// HUD shown after a successful screenshot.  Replaces the rumps
// "Add a description" alert.
//
// Semantic contract (must match `_do_screenshot`)
// -----------------------------------------------
// * User submits (Enter or Save) → onClose(.save(text)), caller persists.
// * User cancels (Esc or Cancel) → onClose(.cancel), caller DELETES
//   the file from disk (mirrors `path.unlink(missing_ok=True)` in app.py
//   line 624).
// * Empty description + Save is allowed (desc=="").
// * When ``initialText`` is non-empty (e.g. pre-filled by the
//   ``ai_analyze`` Attachment) the hint bar shows a small badge so the
//   user knows the content is suggested and fully editable.

import SwiftUI
import AppKit

enum ScreenshotDescResult: Sendable {
    case save(String)
    case cancel
}

struct ScreenshotDescView: View {
    let filename: String
    let pipeline: String
    let presetName: String?       // non-nil when triggered by a preset
    let thumbnailURL: URL?
    /// Pre-filled description; typically the AI-generated summary for
    /// Presets that include the ``ai_analyze`` Attachment.  ``""`` means
    /// "no suggestion".
    let initialText: String
    /// Hint shown next to the text field when ``initialText`` is
    /// present (e.g. "AI"), giving the user context about where
    /// the suggestion came from.
    let initialTextSource: String?

    let onClose: (ScreenshotDescResult) -> Void

    @State private var text: String

    init(
        filename: String,
        pipeline: String,
        presetName: String?,
        thumbnailURL: URL?,
        initialText: String = "",
        initialTextSource: String? = nil,
        onClose: @escaping (ScreenshotDescResult) -> Void
    ) {
        self.filename = filename
        self.pipeline = pipeline
        self.presetName = presetName
        self.thumbnailURL = thumbnailURL
        self.initialText = initialText
        self.initialTextSource = initialTextSource
        self.onClose = onClose
        _text = State(initialValue: initialText)
    }

    var body: some View {
        HUDFrame {
            VStack(alignment: .leading, spacing: 14) {
                header

                if let thumbnailURL {
                    ThumbnailView(url: thumbnailURL)
                        .frame(maxWidth: .infinity, maxHeight: 200)
                        .clipShape(RoundedRectangle(cornerRadius: 10,
                                                    style: .continuous))
                        .clipped()
                }

                HUDTextField(
                    text: $text,
                    placeholder: "Description (optional)",
                    singleLine: false,
                    onSubmit: submit
                )

                Divider().opacity(0.3)

                HUDHintBar(
                    left: hintLeft,
                    right: "⎋ Discard  ⇧↩ Newline  ↩ Save"
                )
            }
        }
    }

    // MARK: - Subviews

    private var header: some View {
        HStack(spacing: 10) {
            Image(systemName: "camera.viewfinder")
                .font(.system(size: 20))
                .foregroundStyle(DSColor.accent)
            VStack(alignment: .leading, spacing: 0) {
                Text("Screenshot captured")
                    .font(.system(size: 15, weight: .semibold))
                Text("→ \(pipeline) · \(filename)")
                    .font(DSFont.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            Spacer()
        }
    }

    private var hintLeft: String? {
        var parts: [String] = []
        if let presetName { parts.append("preset · \(presetName)") }
        if !initialText.isEmpty {
            parts.append("✨ prefilled · \(initialTextSource ?? "suggestion")")
        }
        return parts.isEmpty ? nil : parts.joined(separator: "  ·  ")
    }

    private func submit() {
        onClose(.save(text.trimmingCharacters(in: .whitespacesAndNewlines)))
    }
}

/// Screenshot thumbnail that fits within the HUD width and a max height.
private struct ThumbnailView: View {
    let url: URL

    var body: some View {
        if let nsImage = NSImage(contentsOf: url) {
            Image(nsImage: nsImage)
                .resizable()
                .aspectRatio(contentMode: .fit)
                .frame(maxWidth: .infinity, maxHeight: 200)
                .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        } else {
            RoundedRectangle(cornerRadius: 10)
                .fill(Color.secondary.opacity(0.1))
                .frame(height: 60)
                .overlay {
                    Image(systemName: "photo")
                        .foregroundStyle(.secondary)
                }
        }
    }
}

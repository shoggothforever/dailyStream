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

    let onClose: (ScreenshotDescResult) -> Void

    @State private var text: String = ""
    @FocusState private var focused: Bool

    var body: some View {
        HUDFrame {
            VStack(alignment: .leading, spacing: 14) {
                header

                if let thumbnailURL {
                    ThumbnailView(url: thumbnailURL)
                        .frame(height: 110)
                        .frame(maxWidth: .infinity)
                        .clipShape(RoundedRectangle(cornerRadius: 10,
                                                    style: .continuous))
                }

                HUDTextField(
                    text: $text,
                    placeholder: "Description (optional) — ↩ to save",
                    onSubmit: submit
                )
                .focused($focused)

                Divider().opacity(0.3)

                HUDHintBar(
                    left: presetName.map { "preset · \($0)" },
                    right: "⎋ Discard  ↩ Save"
                )
            }
        }
        .onAppear { focused = true }
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

    private func submit() {
        onClose(.save(text.trimmingCharacters(in: .whitespacesAndNewlines)))
    }
}

/// Simple NSImageView-backed thumbnail loader — avoids SwiftUI's
/// async image load overhead for local files.
private struct ThumbnailView: NSViewRepresentable {
    let url: URL

    func makeNSView(context: Context) -> NSImageView {
        let v = NSImageView()
        v.imageScaling = .scaleProportionallyUpOrDown
        v.imageFrameStyle = .none
        v.wantsLayer = true
        v.layer?.cornerRadius = 10
        v.layer?.masksToBounds = true
        v.image = NSImage(contentsOf: url)
        return v
    }

    func updateNSView(_ v: NSImageView, context: Context) {
        v.image = NSImage(contentsOf: url)
    }
}

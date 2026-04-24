// ClipboardCaptureView.swift
// HUD shown after grabbing the clipboard.  Mirrors the rumps clipboard
// dialog: preview the content (truncated), let the user add an optional
// description, save via `feed.text` / `feed.url` / `feed.image`.
//
// Semantic contract (must match `_do_clipboard`)
// ----------------------------------------------
// * Clipboard type is pre-detected (text / url / image) by the core.
// * User cancel → discard.  Note: for image, the PNG file has already
//   been written to the screenshots directory; rumps version leaves it
//   behind too, so we intentionally DO NOT delete it.  This matches
//   existing behaviour and is arguably correct: the user may want to
//   recover that image later.

import SwiftUI

enum ClipboardCaptureResult: Sendable {
    case save(String)
    case cancel
}

struct ClipboardCaptureView: View {
    let kind: String          // "text" | "url" | "image"
    let content: String       // raw text / url / image path
    let pipeline: String
    let thumbnailURL: URL?    // non-nil when kind == "image"

    let onClose: (ClipboardCaptureResult) -> Void

    @State private var text: String = ""
    @FocusState private var focused: Bool

    private var preview: String {
        if content.count > 80 {
            return String(content.prefix(80)) + "…"
        }
        return content
    }

    var body: some View {
        HUDFrame {
            VStack(alignment: .leading, spacing: 14) {
                HStack(spacing: 10) {
                    Image(systemName: icon)
                        .font(.system(size: 20))
                        .foregroundStyle(DSColor.accent)
                    VStack(alignment: .leading, spacing: 0) {
                        Text("Clipboard → \(pipeline)")
                            .font(.system(size: 15, weight: .semibold))
                        Text("Kind: \(kind)")
                            .font(DSFont.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                }

                if kind == "image", let thumbnailURL {
                    ThumbnailImageView(url: thumbnailURL)
                        .frame(height: 110)
                        .clipShape(RoundedRectangle(cornerRadius: 10,
                                                    style: .continuous))
                } else {
                    Text(preview)
                        .font(DSFont.mono)
                        .padding(10)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(
                            RoundedRectangle(cornerRadius: 8)
                                .fill(Color.secondary.opacity(0.06))
                        )
                        .lineLimit(4)
                        .truncationMode(.tail)
                }

                HUDTextField(
                    text: $text,
                    placeholder: "Description (optional)",
                    onSubmit: { onClose(.save(trimmed)) }
                )
                .focused($focused)

                Divider().opacity(0.3)

                HUDHintBar(left: nil, right: "⎋ Discard  ↩ Save")
            }
        }
        .onAppear { focused = true }
    }

    private var trimmed: String {
        text.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var icon: String {
        switch kind {
        case "url":   return "link"
        case "image": return "photo"
        default:      return "doc.on.clipboard"
        }
    }
}

/// Small wrapper — duplicated from ScreenshotDescView so each HUD
/// source file is self-contained.
private struct ThumbnailImageView: NSViewRepresentable {
    let url: URL
    func makeNSView(context: Context) -> NSImageView {
        let v = NSImageView()
        v.imageScaling = .scaleProportionallyUpOrDown
        v.imageFrameStyle = .none
        v.image = NSImage(contentsOf: url)
        return v
    }
    func updateNSView(_ v: NSImageView, context: Context) {
        v.image = NSImage(contentsOf: url)
    }
}

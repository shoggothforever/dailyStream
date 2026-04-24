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
                    ClipboardThumbnail(url: thumbnailURL)
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
                    singleLine: false,
                    onSubmit: { onClose(.save(trimmed)) }
                )

                Divider().opacity(0.3)

                HUDHintBar(left: nil, right: "⎋ Discard  ⇧↩ Newline  ↩ Save")
            }
        }
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

private struct ClipboardThumbnail: View {
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

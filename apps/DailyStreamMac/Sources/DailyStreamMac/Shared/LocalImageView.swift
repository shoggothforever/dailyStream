// LocalImageView.swift
// Reusable async image loader with a shared NSCache.
//
// Used by both StreamViewerWindow and DailyReviewWindow to:
//   * Load NSImage on a background thread (never blocks the main
//     thread during scrolling / initial render).
//   * Share a process-wide NSCache keyed by "path|mtime", so switching
//     between the Stream viewer and Daily Review never re-decodes the
//     same screenshot.
//   * Fall back to a URLSession fetch for http(s) URLs.
//
// This component intentionally has no external dependencies beyond
// AppKit + SwiftUI, so it can be freely reused from any surface.

import AppKit
import SwiftUI

/// Shared NSCache for decoded `NSImage`s.  Keyed by `"path|mtime"` so
/// that edits to the file (or file replacement) invalidate the entry
/// automatically.
enum LocalImageCache {
    static let shared: NSCache<NSString, NSImage> = {
        let cache = NSCache<NSString, NSImage>()
        cache.countLimit = 128
        return cache
    }()
}

/// Last-modified timestamp helper used as part of the cache key.
func fileModTime(_ path: String) -> TimeInterval {
    let attrs = try? FileManager.default.attributesOfItem(atPath: path)
    let mtime = attrs?[.modificationDate] as? Date
    return mtime?.timeIntervalSinceReferenceDate ?? 0
}

/// Async image view that loads from disk (preferred) or the network.
///
/// While loading it shows a small `ProgressView` placeholder.  Loads
/// happen on a detached user-initiated Task so the UI thread stays
/// responsive even when many rows are rendered at once.
struct LocalImageView: View {
    let url: URL?

    /// Optional maximum width.  `StreamViewer` wants 700pt wide hero
    /// shots; `DailyReview` wants compact 160pt-tall thumbnails — both
    /// cases are expressible via `.frame` modifiers on the returned
    /// view, but a default maxWidth avoids surprising full-width
    /// images by default.
    var maxWidth: CGFloat? = 700

    /// Optional maximum height — `DailyReview` uses this to cap
    /// timeline thumbnails.
    var maxHeight: CGFloat? = nil

    /// Corner radius for the loaded image + placeholder.
    var cornerRadius: CGFloat = 6

    @State private var nsImage: NSImage?
    @State private var loaded = false

    var body: some View {
        Group {
            if let nsImage {
                Image(nsImage: nsImage)
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .frame(maxWidth: maxWidth, maxHeight: maxHeight)
                    .clipShape(RoundedRectangle(cornerRadius: cornerRadius))
            } else if loaded {
                // Load attempted but failed — render a subtle broken
                // placeholder so the UI doesn't collapse silently.
                HStack(spacing: 6) {
                    Image(systemName: "photo.badge.exclamationmark")
                        .foregroundStyle(.secondary)
                    if let url {
                        Text(url.lastPathComponent)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                .padding(8)
                .background(
                    RoundedRectangle(cornerRadius: cornerRadius)
                        .fill(Color.secondary.opacity(0.06))
                )
            } else {
                RoundedRectangle(cornerRadius: cornerRadius)
                    .fill(Color.secondary.opacity(0.06))
                    .frame(height: maxHeight ?? 60)
                    .overlay {
                        ProgressView()
                            .scaleEffect(0.6)
                    }
            }
        }
        .task(id: url) {
            await loadImage()
        }
    }

    private func loadImage() async {
        guard let url else {
            loaded = true
            return
        }

        let resolved = url.absoluteURL

        if resolved.isFileURL || resolved.scheme == nil {
            let filePath = resolved.path
            let img: NSImage? = await Task.detached(priority: .userInitiated) { () -> NSImage? in
                let cacheKey = "\(filePath)|\(fileModTime(filePath))" as NSString
                if let cached = LocalImageCache.shared.object(forKey: cacheKey) {
                    return cached
                }
                guard let image = NSImage(contentsOfFile: filePath) else {
                    return nil
                }
                LocalImageCache.shared.setObject(image, forKey: cacheKey)
                return image
            }.value

            if !Task.isCancelled {
                nsImage = img
                loaded = true
            }
        } else if let scheme = resolved.scheme?.lowercased(),
                  scheme == "http" || scheme == "https" {
            do {
                let (data, _) = try await URLSession.shared.data(from: resolved)
                if !Task.isCancelled, let img = NSImage(data: data) {
                    nsImage = img
                }
            } catch {}
            loaded = true
        } else {
            loaded = true
        }
    }
}

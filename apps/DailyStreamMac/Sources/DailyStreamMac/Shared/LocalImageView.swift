// LocalImageView.swift
// Reusable async image loader with a shared NSCache.
//
// Used by both StreamViewerWindow and DailyReviewWindow to:
//   * Load NSImage on a background thread (never blocks the main
//     thread during scrolling / initial render).
//   * Share a process-wide NSCache so switching between the Stream
//     viewer and Daily Review never re-decodes the same screenshot.
//   * Pre-decode bitmaps in the background so the *first paint* on the
//     main thread is just a blit rather than a PNG/JPEG decode (NSImage
//     normally defers decoding until first draw, which would otherwise
//     stall whichever thread happens to render the row).
//   * **Downsample** to the caller's display size via ImageIO so a
//     2560×1600 retina screenshot doesn't sit in memory as 16 MB of
//     RGBA when the row only needs a 160-px thumbnail.  This is the
//     single biggest reason the Daily Review window stays responsive
//     when a pipeline has 20+ screenshots.
//   * Hit the cache **synchronously** when the bitmap is already in
//     memory — this is what lets a previously-visited Daily Review tab
//     re-open instantly instead of flashing through placeholders again.
//   * Fall back to a URLSession fetch for http(s) URLs.
//
// This component intentionally has no external dependencies beyond
// AppKit + SwiftUI + ImageIO, so it can be freely reused from any
// surface.

import AppKit
import SwiftUI
import ImageIO

/// Shared cache for decoded `NSImage`s.
///
/// The cache is keyed by ``CacheKey`` (path + last-modified time) so
/// that edits to the file invalidate stale entries automatically.
/// Internally we keep two layers:
///   * ``imageCache`` — the decoded `NSImage` ready to draw.
///   * ``mtimeCache`` — the last-known mtime per path, so the hot
///     synchronous lookup doesn't pay for a `stat()` syscall on every
///     row when the user scrolls or switches tabs.  The mtime check is
///     re-validated lazily when something falls out of `mtimeCache`.
enum LocalImageCache {
    fileprivate static let imageCache: NSCache<NSString, NSImage> = {
        let cache = NSCache<NSString, NSImage>()
        cache.countLimit = 256
        // Cap total memory used by decoded bitmaps.  Without this a
        // workspace with dozens of full-resolution screenshots can
        // easily consume hundreds of MB and trigger swap pressure on
        // smaller machines.  256 MB is generous but safe.
        cache.totalCostLimit = 256 * 1024 * 1024
        return cache
    }()

    /// path -> mtime (timeIntervalSinceReferenceDate).  Only mutated
    /// from background threads holding ``mtimeLock``.
    nonisolated(unsafe) private static var mtimeCache: [String: TimeInterval] = [:]
    private static let mtimeLock = NSLock()

    fileprivate static func cachedMtime(for path: String) -> TimeInterval? {
        mtimeLock.lock()
        defer { mtimeLock.unlock() }
        return mtimeCache[path]
    }

    fileprivate static func storeMtime(_ mtime: TimeInterval, for path: String) {
        mtimeLock.lock()
        mtimeCache[path] = mtime
        mtimeLock.unlock()
    }

    fileprivate static func cacheKey(
        path: String, mtime: TimeInterval, maxPixelSize: Int?
    ) -> NSString {
        // Including maxPixelSize prevents Daily Review's 160-px
        // thumbnails from being served to StreamViewer's 700-pt hero
        // shots (which would render blurry).
        let suffix = maxPixelSize.map { "@\($0)" } ?? ""
        return "\(path)|\(mtime)\(suffix)" as NSString
    }

    /// Synchronous fast-path used by ``LocalImageView`` to avoid the
    /// async hop on cache hits.  Only succeeds when we already know
    /// the file's mtime *and* the decoded image is in the cache; any
    /// uncertainty falls through to the async path.
    @MainActor
    fileprivate static func cachedImage(
        forPath path: String, maxPixelSize: Int?
    ) -> NSImage? {
        guard let mtime = cachedMtime(for: path) else { return nil }
        return imageCache.object(forKey: cacheKey(
            path: path, mtime: mtime, maxPixelSize: maxPixelSize
        ))
    }
}

/// Bounded concurrency gate for image decoding.
///
/// Without this, switching to a pipeline with ~20 screenshots fires off
/// ~20 simultaneous `Task.detached` jobs that all hit disk + JPEG
/// decode in parallel.  Apple's URLCache effectively does the same
/// thing; we just enforce a small cap so a single tab switch can't
/// saturate every CPU core.
actor ImageLoadGate {
    static let shared = ImageLoadGate(limit: 4)

    private let limit: Int
    private var active = 0
    private var waiters: [CheckedContinuation<Void, Never>] = []

    init(limit: Int) {
        self.limit = limit
    }

    func enter() async {
        if active < limit {
            active += 1
            return
        }
        await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
            waiters.append(cont)
        }
    }

    func leave() {
        if let next = waiters.first {
            waiters.removeFirst()
            // The slot stays occupied — we're just handing it to the
            // next waiter — so don't decrement `active`.
            next.resume()
        } else {
            active -= 1
        }
    }
}

/// Last-modified timestamp helper used as part of the cache key.
/// Performs a `stat()` so callers should avoid hammering it from the
/// main thread — ``LocalImageView`` only invokes it from background
/// tasks.
func fileModTime(_ path: String) -> TimeInterval {
    let attrs = try? FileManager.default.attributesOfItem(atPath: path)
    let mtime = attrs?[.modificationDate] as? Date
    return mtime?.timeIntervalSinceReferenceDate ?? 0
}

/// Async image view that loads from disk (preferred) or the network.
///
/// While loading it shows a small `ProgressView` placeholder.  Loads
/// happen on a detached user-initiated Task so the UI thread stays
/// responsive even when many rows are rendered at once.  When the
/// image is already cached the view bypasses the async hop entirely
/// and renders on the very first frame.
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

    /// Target maximum dimension in **pixels** (i.e. accounting for
    /// retina @2x).  ``nil`` means "no downsampling, decode at native
    /// resolution" — used by the StreamViewer hero shots.  The Daily
    /// Review thumbnails pass a small cap here so the bitmap held in
    /// memory roughly matches what gets rendered on screen.
    private var maxPixelSize: Int? {
        // Pick the larger of width / height (in points) and double it
        // for retina.  Falling back to "no cap" when neither is set
        // preserves the StreamViewer behaviour that wants full-fidelity
        // images.
        let pt: CGFloat? = [maxWidth, maxHeight]
            .compactMap { $0 }
            .max()
        guard let pt else { return nil }
        return Int(pt * 2)
    }

    /// Synchronous cache lookup for the disk-file fast path.  Keeps
    /// the work off the main thread except for an `NSCache` get + dict
    /// read, both of which are O(1) and lock-cheap.
    private func quickCachedImage() -> NSImage? {
        guard let url, url.isFileURL || url.scheme == nil else { return nil }
        return LocalImageCache.cachedImage(
            forPath: url.absoluteURL.path,
            maxPixelSize: maxPixelSize
        )
    }

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
        // Synchronously populate `nsImage` when we already have the
        // bitmap cached.  This is the difference between "tab opens
        // instantly" and "tab flashes placeholders for a frame" when
        // re-visiting a previously loaded pipeline.
        .onAppear {
            if nsImage == nil, let img = quickCachedImage() {
                nsImage = img
                loaded = true
            }
        }
        .task(id: url) {
            // If the synchronous path already populated `nsImage`,
            // skip the async work entirely.
            if nsImage != nil { return }
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
            let pxSize = maxPixelSize
            // Bound concurrency so a tab switch with N images doesn't
            // launch N parallel decoders; the gate releases as each
            // job finishes so the queue drains in waves.
            await ImageLoadGate.shared.enter()
            let img: NSImage? = await Task.detached(priority: .userInitiated) {
                () -> NSImage? in
                let mtime = fileModTime(filePath)
                let key = LocalImageCache.cacheKey(
                    path: filePath, mtime: mtime, maxPixelSize: pxSize
                )
                if let cached = LocalImageCache.imageCache.object(forKey: key) {
                    // Refresh the mtime side-cache so the next sync
                    // lookup hits without a stat().
                    LocalImageCache.storeMtime(mtime, for: filePath)
                    return cached
                }
                guard let image = Self.decodeImage(
                    path: filePath, maxPixelSize: pxSize
                ) else {
                    return nil
                }
                let cost = Self.byteCost(image)
                LocalImageCache.imageCache.setObject(
                    image, forKey: key, cost: cost
                )
                LocalImageCache.storeMtime(mtime, for: filePath)
                return image
            }.value
            await ImageLoadGate.shared.leave()

            if !Task.isCancelled {
                nsImage = img
                loaded = true
            }
        } else if let scheme = resolved.scheme?.lowercased(),
                  scheme == "http" || scheme == "https" {
            do {
                let (data, _) = try await URLSession.shared.data(from: resolved)
                if !Task.isCancelled, let img = NSImage(data: data) {
                    _ = Self.byteCost(img)
                    nsImage = img
                }
            } catch {}
            loaded = true
        } else {
            loaded = true
        }
    }

    /// Decode a file into an `NSImage`, optionally downsampling to fit
    /// within ``maxPixelSize`` pixels on the longest edge.
    ///
    /// Uses ImageIO directly so we can both:
    ///   * Force the bitmap to materialise on the calling thread
    ///     (rather than NSImage's lazy first-draw decode), and
    ///   * Generate a thumbnail in a single pass — avoiding the
    ///     full-resolution intermediate bitmap that the naive
    ///     `NSImage(contentsOfFile:)` path always allocates.
    ///
    /// Marked ``nonisolated`` so the detached background task can call
    /// it without hopping back to the MainActor that owns
    /// ``LocalImageView``.
    nonisolated private static func decodeImage(
        path: String, maxPixelSize: Int?
    ) -> NSImage? {
        let fileURL = URL(fileURLWithPath: path) as CFURL
        guard let src = CGImageSourceCreateWithURL(fileURL, nil) else {
            return nil
        }

        let cgImage: CGImage?
        if let maxPixelSize {
            let opts: [CFString: Any] = [
                kCGImageSourceCreateThumbnailFromImageAlways: true,
                kCGImageSourceCreateThumbnailWithTransform: true,
                kCGImageSourceShouldCacheImmediately: true,
                kCGImageSourceThumbnailMaxPixelSize: maxPixelSize,
            ]
            cgImage = CGImageSourceCreateThumbnailAtIndex(src, 0, opts as CFDictionary)
        } else {
            let opts: [CFString: Any] = [
                kCGImageSourceShouldCacheImmediately: true,
            ]
            cgImage = CGImageSourceCreateImageAtIndex(src, 0, opts as CFDictionary)
        }

        guard let cg = cgImage else { return nil }
        // Wrap CGImage in an NSImage at the *pixel* size so SwiftUI's
        // ``Image(nsImage:)`` doesn't try to upscale a downsampled
        // bitmap back to logical points.
        let pxSize = NSSize(width: cg.width, height: cg.height)
        return NSImage(cgImage: cg, size: pxSize)
    }

    /// Approximate byte cost of an `NSImage`'s decoded bitmap, used as
    /// the cache cost.  4 bytes per pixel (RGBA) is a conservative
    /// upper bound for whatever representation NSImage retains.
    nonisolated private static func byteCost(_ image: NSImage) -> Int {
        var rect = NSRect(origin: .zero, size: image.size)
        guard let cg = image.cgImage(
            forProposedRect: &rect, context: nil, hints: nil
        ) else {
            return 0
        }
        return cg.width * cg.height * 4
    }
}

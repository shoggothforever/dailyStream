// StreamViewerWindow.swift
// Native Markdown viewer for stream.md — renders the workspace's
// live markdown file with MarkdownUI and supports opening in an
// external editor.

import AppKit
import SwiftUI
import MarkdownUI
import DailyStreamCore

// MARK: - Window controller

@MainActor
final class StreamViewerWindow {
    static let shared = StreamViewerWindow()

    private var window: NSWindow?

    private init() {}

    /// Open (or bring forward) the stream viewer for a given workspace path.
    func show(workspacePath: String) {
        let vm = StreamViewerViewModel(workspacePath: workspacePath)
        let content = StreamViewerContent(viewModel: vm) { [weak self] in
            self?.close()
        }

        if let existing = window {
            existing.contentViewController = NSHostingController(rootView: content)
            existing.makeKeyAndOrderFront(nil)
            return
        }

        let w = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 820, height: 700),
            styleMask: [.titled, .closable, .resizable, .miniaturizable,
                        .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        w.isReleasedWhenClosed = false
        w.titlebarAppearsTransparent = true
        w.title = "Stream"
        w.center()
        w.contentViewController = NSHostingController(rootView: content)
        w.contentMinSize = NSSize(width: 520, height: 400)
        w.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        self.window = w
    }

    func close() {
        window?.orderOut(nil)
    }
}

// MARK: - View model

@MainActor
final class StreamViewerViewModel: ObservableObject {
    let workspacePath: String
    @Published var markdownContent: String = ""
    @Published var title: String = "Stream"

    private var fileMonitor: DispatchSourceFileSystemObject?
    private nonisolated(unsafe) var fileDescriptor: Int32 = -1

    init(workspacePath: String) {
        self.workspacePath = workspacePath
        reload()
        startWatching()
    }

    deinit {
        fileMonitor?.cancel()
        fileMonitor = nil
    }

    var streamURL: URL {
        URL(fileURLWithPath: workspacePath).appendingPathComponent("stream.md")
    }

    func reload() {
        let url = streamURL
        guard FileManager.default.fileExists(atPath: url.path) else {
            markdownContent = "*No stream.md found in this workspace.*"
            return
        }
        do {
            let raw = try String(contentsOf: url, encoding: .utf8)
            markdownContent = raw
            // Extract title from first # heading
            if let firstLine = raw.components(separatedBy: .newlines).first,
               firstLine.hasPrefix("# ") {
                title = String(firstLine.dropFirst(2)).trimmingCharacters(in: .whitespaces)
            }
        } catch {
            markdownContent = "*Failed to read stream.md: \(error.localizedDescription)*"
        }
    }

    func openInEditor() {
        let url = streamURL
        guard FileManager.default.fileExists(atPath: url.path) else { return }

        // Try VS Code first, then fall back to default .md handler
        let vscodePaths = [
            "/Applications/Visual Studio Code.app",
            "/usr/local/bin/code",
            "/opt/homebrew/bin/code",
        ]
        for path in vscodePaths {
            if FileManager.default.fileExists(atPath: path) {
                if path.hasSuffix(".app") {
                    let config = NSWorkspace.OpenConfiguration()
                    NSWorkspace.shared.open(
                        [url],
                        withApplicationAt: URL(fileURLWithPath: path),
                        configuration: config
                    )
                } else {
                    // CLI binary
                    let task = Process()
                    task.executableURL = URL(fileURLWithPath: path)
                    task.arguments = [url.path]
                    try? task.run()
                }
                return
            }
        }
        // Fallback: open with system default
        NSWorkspace.shared.open(url)
    }

    func openInFinder() {
        let dir = URL(fileURLWithPath: workspacePath)
        NSWorkspace.shared.selectFile(streamURL.path, inFileViewerRootedAtPath: dir.path)
    }

    // MARK: - File watching (fsevents)

    private func startWatching() {
        let path = streamURL.path
        fileDescriptor = open(path, O_EVTONLY)
        guard fileDescriptor >= 0 else { return }
        let source = DispatchSource.makeFileSystemObjectSource(
            fileDescriptor: fileDescriptor,
            eventMask: [.write, .rename, .delete],
            queue: .main
        )
        source.setEventHandler { [weak self] in
            self?.reload()
        }
        source.setCancelHandler { [weak self] in
            if let fd = self?.fileDescriptor, fd >= 0 {
                Darwin.close(fd)
                self?.fileDescriptor = -1
            }
        }
        source.resume()
        fileMonitor = source
    }

    private func stopWatching() {
        fileMonitor?.cancel()
        fileMonitor = nil
    }
}

// MARK: - Custom image provider for local file:// images

/// Loads images from local disk for MarkdownUI.
/// MarkdownUI's default provider only handles http(s); this handles file:// URLs.
struct LocalImageProvider: ImageProvider {
    func makeImage(url: URL?) -> some View {
        LocalImageView(url: url)
    }
}

/// Async image view that loads from disk or falls back to network.
private struct LocalImageView: View {
    let url: URL?
    @State private var nsImage: NSImage?
    @State private var loaded = false

    var body: some View {
        Group {
            if let nsImage {
                Image(nsImage: nsImage)
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .frame(maxWidth: 700)
                    .clipShape(RoundedRectangle(cornerRadius: 6))
            } else if loaded {
                // Load attempted but failed
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
                    RoundedRectangle(cornerRadius: 6)
                        .fill(Color.secondary.opacity(0.06))
                )
            } else {
                // Loading placeholder
                RoundedRectangle(cornerRadius: 6)
                    .fill(Color.secondary.opacity(0.06))
                    .frame(height: 60)
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

        // Check if it's a local file
        if resolved.isFileURL || resolved.scheme == nil {
            let filePath = resolved.path
            // Load on background thread
            let img: NSImage? = await Task.detached(priority: .userInitiated) { () -> NSImage? in
                // Check cache first
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
            // Network images — load via URLSession
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

private func fileModTime(_ path: String) -> TimeInterval {
    let attrs = try? FileManager.default.attributesOfItem(atPath: path)
    let mtime = attrs?[.modificationDate] as? Date
    return mtime?.timeIntervalSinceReferenceDate ?? 0
}

/// Simple NSCache for loaded images, keyed by path+mtime.
private enum LocalImageCache {
    static let shared: NSCache<NSString, NSImage> = {
        let cache = NSCache<NSString, NSImage>()
        cache.countLimit = 128
        return cache
    }()
}

// MARK: - SwiftUI view

struct StreamViewerContent: View {
    @ObservedObject var viewModel: StreamViewerViewModel
    let onClose: () -> Void

    /// Base URL for resolving relative image paths in Markdown.
    private var imageBaseURL: URL {
        URL(fileURLWithPath: viewModel.workspacePath, isDirectory: true)
    }

    var body: some View {
        VStack(spacing: 0) {
            toolbar
            Divider()
            ScrollView {
                Markdown(viewModel.markdownContent, imageBaseURL: imageBaseURL)
                    .markdownTheme(.gitHub)
                    .markdownImageProvider(LocalImageProvider())
                    .textSelection(.enabled)
                    .padding(24)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .frame(minWidth: 520, minHeight: 400)
        .background(Color(nsColor: .windowBackgroundColor))
    }

    private var toolbar: some View {
        HStack(spacing: 12) {
            Image(systemName: "doc.richtext")
                .font(.system(size: 16))
                .foregroundStyle(DSColor.accent)
            Text(viewModel.title)
                .font(.system(size: 15, weight: .semibold))
            Spacer()
            Button {
                viewModel.reload()
            } label: {
                Image(systemName: "arrow.clockwise")
                    .font(.system(size: 13))
            }
            .buttonStyle(.borderless)
            .help("Refresh")

            Button {
                viewModel.openInEditor()
            } label: {
                Image(systemName: "pencil.line")
                    .font(.system(size: 13))
            }
            .buttonStyle(.borderless)
            .help("Open in editor (VS Code)")

            Button {
                viewModel.openInFinder()
            } label: {
                Image(systemName: "folder")
                    .font(.system(size: 13))
            }
            .buttonStyle(.borderless)
            .help("Show in Finder")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }
}

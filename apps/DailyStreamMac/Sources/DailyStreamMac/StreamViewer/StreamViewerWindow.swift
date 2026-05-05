// StreamViewerWindow.swift
// Native Markdown viewer for workspace stream files.
//
// Layout handled here:
//     <workspace>/stream.md                    ← index page (pure links)
//     <workspace>/pipelines/<name>/stream.md   ← per-pipeline content
//
// The viewer opens the top-level index first, then lets the user drill
// into each pipeline by clicking the links inside the Markdown. A
// back-to-index button appears whenever a per-pipeline file is loaded.

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

        // Default to filling the screen's visible area (excluding menu bar / Dock).
        let screenFrame = NSScreen.main?.visibleFrame
            ?? NSRect(x: 0, y: 0, width: 820, height: 700)

        let w = NSWindow(
            contentRect: screenFrame,
            styleMask: [.titled, .closable, .resizable, .miniaturizable,
                        .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        w.isReleasedWhenClosed = false
        w.titlebarAppearsTransparent = true
        w.title = "Stream"
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

    /// The Markdown file currently displayed. Defaults to the workspace's
    /// top-level `stream.md` (the index page); drilling into a pipeline
    /// link updates this to the matching per-pipeline file.
    @Published private(set) var currentURL: URL
    @Published var markdownContent: String = ""
    @Published var title: String = "Stream"

    /// True when viewing a per-pipeline file (shows the "back to index" button).
    var isViewingPipeline: Bool {
        currentURL.path != indexURL.path
    }

    private var fileMonitor: DispatchSourceFileSystemObject?
    private nonisolated(unsafe) var fileDescriptor: Int32 = -1

    init(workspacePath: String) {
        self.workspacePath = workspacePath
        self.currentURL = URL(fileURLWithPath: workspacePath)
            .appendingPathComponent("stream.md")
        reload()
        startWatching()
    }

    deinit {
        fileMonitor?.cancel()
        fileMonitor = nil
    }

    /// The workspace-level index page.
    var indexURL: URL {
        URL(fileURLWithPath: workspacePath).appendingPathComponent("stream.md")
    }

    /// Base URL for resolving relative image paths inside the *current* file.
    /// This is the directory containing `currentURL`, so both the top-level
    /// index and per-pipeline files render images correctly regardless of
    /// how many `..` segments appear in the Markdown.
    var imageBaseURL: URL {
        currentURL.deletingLastPathComponent()
    }

    /// Switch the viewer to a different Markdown file (e.g. after the user
    /// clicks a pipeline link). The file watcher is re-pointed to the new
    /// file so live updates keep working.
    func load(_ url: URL) {
        stopWatching()
        currentURL = url
        reload()
        startWatching()
    }

    /// Navigate back to the top-level index page.
    func navigateToIndex() {
        load(indexURL)
    }

    func reload() {
        guard FileManager.default.fileExists(atPath: currentURL.path) else {
            if currentURL.path == indexURL.path {
                markdownContent = "*No stream.md found in this workspace.*"
            } else {
                markdownContent = "*File not found: \(currentURL.lastPathComponent)*"
            }
            return
        }
        do {
            let raw = try String(contentsOf: currentURL, encoding: .utf8)
            markdownContent = raw
            // Extract title from the first `# ` heading.
            if let firstLine = raw.components(separatedBy: .newlines).first,
               firstLine.hasPrefix("# ") {
                title = String(firstLine.dropFirst(2))
                    .trimmingCharacters(in: .whitespaces)
            } else {
                title = currentURL.deletingPathExtension().lastPathComponent
            }
        } catch {
            markdownContent = "*Failed to read file: \(error.localizedDescription)*"
        }
    }

    func openInEditor() {
        guard FileManager.default.fileExists(atPath: currentURL.path) else { return }

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
                        [currentURL],
                        withApplicationAt: URL(fileURLWithPath: path),
                        configuration: config
                    )
                } else {
                    // CLI binary
                    let task = Process()
                    task.executableURL = URL(fileURLWithPath: path)
                    task.arguments = [currentURL.path]
                    try? task.run()
                }
                return
            }
        }
        // Fallback: open with system default
        NSWorkspace.shared.open(currentURL)
    }

    func openInFinder() {
        let dir = URL(fileURLWithPath: workspacePath)
        NSWorkspace.shared.selectFile(currentURL.path, inFileViewerRootedAtPath: dir.path)
    }

    // MARK: - File watching (fsevents)

    private func startWatching() {
        let path = currentURL.path
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

// LocalImageView + LocalImageCache + fileModTime now live in
// ``Shared/LocalImageView.swift`` so the Daily Review window can share
// the exact same NSCache instance (no duplicate decodes when the user
// pops open both surfaces for the same workspace).

// MARK: - SwiftUI view

struct StreamViewerContent: View {
    @ObservedObject var viewModel: StreamViewerViewModel
    let onClose: () -> Void

    /// Anchor IDs for scroll-to-top / scroll-to-bottom.
    private enum ScrollAnchor: Hashable {
        case top, bottom
    }

    var body: some View {
        VStack(spacing: 0) {
            toolbar
            Divider()
            ScrollViewReader { proxy in
                ScrollView {
                    // Invisible top anchor
                    Color.clear
                        .frame(height: 0)
                        .id(ScrollAnchor.top)

                    Markdown(viewModel.markdownContent, imageBaseURL: viewModel.imageBaseURL)
                        .markdownTheme(.gitHub)
                        .markdownImageProvider(LocalImageProvider())
                        .textSelection(.enabled)
                        .environment(\.openURL, OpenURLAction { url in
                            handleLink(url)
                        })
                        .padding(24)
                        .frame(maxWidth: .infinity, alignment: .leading)

                    // Invisible bottom anchor
                    Color.clear
                        .frame(height: 0)
                        .id(ScrollAnchor.bottom)
                }
                // Reset scroll position to top whenever the file switches.
                .onChange(of: viewModel.currentURL) { _ in
                    withAnimation(.easeOut(duration: 0.2)) {
                        proxy.scrollTo(ScrollAnchor.top, anchor: .top)
                    }
                }
                .overlay(alignment: .bottomTrailing) {
                    scrollButtons(proxy: proxy)
                }
            }
        }
        .frame(minWidth: 520, minHeight: 400)
        .background(Color(nsColor: .windowBackgroundColor))
    }

    // MARK: - Link handling

    /// Intercept clicks on relative `.md` links to switch the viewer to
    /// that file; leave every other scheme (http/https/mailto/absolute
    /// file://) to the system default handler.
    private func handleLink(_ url: URL) -> OpenURLAction.Result {
        // MarkdownUI passes relative URLs through without resolving them;
        // the parent view did set `imageBaseURL` but link URLs arrive as
        // bare "pipelines/foo/stream.md". Treat URLs with no scheme — or
        // an explicit file:// scheme — as local paths to be resolved
        // against the current file's directory.
        let rawScheme = url.scheme?.lowercased()
        if rawScheme == nil || rawScheme == "file" {
            // Decode percent-encoded path segments so unicode pipeline
            // names round-trip correctly.
            let relativePath = url.relativePath.removingPercentEncoding
                ?? url.relativePath
            let base = viewModel.imageBaseURL
            let target = URL(fileURLWithPath: relativePath,
                             relativeTo: base).standardizedFileURL

            // Only hijack Markdown files; anything else (images, pdfs…)
            // should open in the default app.
            if target.pathExtension.lowercased() == "md" {
                viewModel.load(target)
                return .handled
            }
        }
        return .systemAction
    }

    // MARK: - Scroll-to buttons (floating, bottom-right)

    private func scrollButtons(proxy: ScrollViewProxy) -> some View {
        VStack(spacing: 6) {
            Button {
                withAnimation(.easeInOut(duration: 0.3)) {
                    proxy.scrollTo(ScrollAnchor.top, anchor: .top)
                }
            } label: {
                Image(systemName: "arrow.up.to.line")
                    .font(.system(size: 12, weight: .medium))
                    .frame(width: 28, height: 28)
            }
            .buttonStyle(.plain)
            .background(
                Circle()
                    .fill(.ultraThinMaterial)
                    .shadow(color: .black.opacity(0.12), radius: 4, y: 2)
            )
            .help("Scroll to top (⌘↑)")

            Button {
                withAnimation(.easeInOut(duration: 0.3)) {
                    proxy.scrollTo(ScrollAnchor.bottom, anchor: .bottom)
                }
            } label: {
                Image(systemName: "arrow.down.to.line")
                    .font(.system(size: 12, weight: .medium))
                    .frame(width: 28, height: 28)
            }
            .buttonStyle(.plain)
            .background(
                Circle()
                    .fill(.ultraThinMaterial)
                    .shadow(color: .black.opacity(0.12), radius: 4, y: 2)
            )
            .help("Scroll to bottom (⌘↓)")
        }
        .padding(12)
    }

    // MARK: - Toolbar

    private var toolbar: some View {
        HStack(spacing: 12) {
            // Back-to-index button appears only when viewing a pipeline file.
            if viewModel.isViewingPipeline {
                Button {
                    viewModel.navigateToIndex()
                } label: {
                    Image(systemName: "chevron.left")
                        .font(.system(size: 13, weight: .semibold))
                }
                .buttonStyle(.borderless)
                .help("Back to index")
            }

            Image(systemName: "doc.richtext")
                .font(.system(size: 16))
                .foregroundStyle(DSColor.accent)
            Text(viewModel.title)
                .font(.system(size: 15, weight: .semibold))
                .lineLimit(1)
                .truncationMode(.middle)
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

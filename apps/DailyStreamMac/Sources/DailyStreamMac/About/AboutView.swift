// AboutView.swift
// Simple About window matching Apple HIG conventions.

import SwiftUI

struct AboutView: View {
    var body: some View {
        VStack(spacing: 16) {
            // App icon placeholder (SF Symbol)
            Image(systemName: "waveform.circle.fill")
                .resizable()
                .aspectRatio(contentMode: .fit)
                .frame(width: 100, height: 100)
                .foregroundStyle(
                    .linearGradient(
                        colors: [DSColor.accent, DSColor.capturing],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                )

            Text("DailyStream")
                .font(.system(size: 22, weight: .bold))
            Text("A minimal daily recording stream for macOS")
                .font(DSFont.body)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)

            Text("v0.3.0")
                .font(DSFont.caption)
                .foregroundStyle(.tertiary)

            Divider().frame(width: 200)

            Link("GitHub Repository",
                 destination: URL(string: "https://github.com/shoggothforever/dailyStream")!)
                .font(DSFont.caption)

            Text("MIT License")
                .font(.system(size: 10))
                .foregroundStyle(.quaternary)
        }
        .padding(32)
        .frame(width: 320)
    }
}

@MainActor
final class AboutWindowController {
    static let shared = AboutWindowController()
    private var window: NSWindow?

    func show() {
        if let w = window {
            w.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }
        let w = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 320, height: 420),
            styleMask: [.titled, .closable],
            backing: .buffered,
            defer: false
        )
        w.title = "About DailyStream"
        w.isReleasedWhenClosed = false
        w.center()
        w.contentViewController = NSHostingController(rootView: AboutView())
        w.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        self.window = w
    }
}

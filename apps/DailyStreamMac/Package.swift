// swift-tools-version: 5.9
// DailyStreamMac — native Swift shell for DailyStream.

import PackageDescription

let package = Package(
    name: "DailyStreamMac",
    platforms: [
        .macOS(.v13),  // MenuBarExtra requires macOS 13 Ventura.
    ],
    products: [
        .library(
            name: "DailyStreamCore",
            targets: ["DailyStreamCore"]
        ),
        .executable(
            name: "DailyStreamMac",
            targets: ["DailyStreamMac"]
        ),
    ],
    dependencies: [
        // Global hotkey registration + SwiftUI recorder.
        // MIT-licensed, maintained by Sindre Sorhus.
        .package(
            url: "https://github.com/sindresorhus/KeyboardShortcuts",
            from: "2.0.0"
        ),
        // Markdown rendering in SwiftUI (pure Swift, no WebView).
        .package(
            url: "https://github.com/gonzalezreal/swift-markdown-ui",
            from: "2.4.0"
        ),
    ],
    targets: [
        .target(
            name: "DailyStreamCore",
            path: "Sources/DailyStreamCore"
        ),
        .executableTarget(
            name: "DailyStreamMac",
            dependencies: [
                "DailyStreamCore",
                "KeyboardShortcuts",
                .product(name: "MarkdownUI", package: "swift-markdown-ui"),
            ],
            path: "Sources/DailyStreamMac",
            resources: []
        ),
        .testTarget(
            name: "DailyStreamCoreTests",
            dependencies: ["DailyStreamCore"],
            path: "Tests/DailyStreamCoreTests",
            exclude: ["run_rpc_server.sh"]
        ),
    ]
)

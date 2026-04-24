// swift-tools-version: 5.9
// DailyStreamMac — native Swift shell for DailyStream.
//
// Layout
// ------
// * ``DailyStreamCore``     : bridge / RPC types / event stream (UI-free,
//                              unit-testable on any platform).
// * ``DailyStreamCoreTests``: XCTest suite for the core library.
// * (M1.2+) ``DailyStreamMac`` executable will be added as a second
//   target once menu bar UI work begins.
//
// Intentionally no Xcode project file at M1.1 — Swift Package Manager
// lets us run ``swift build`` / ``swift test`` from the terminal and
// CI, which is what M0's bundle spike also exercises.  Adding an
// Xcode project later is additive and non-invasive.

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
    ],
    targets: [
        .target(
            name: "DailyStreamCore",
            path: "Sources/DailyStreamCore"
        ),
        .testTarget(
            name: "DailyStreamCoreTests",
            dependencies: ["DailyStreamCore"],
            path: "Tests/DailyStreamCoreTests",
            exclude: ["run_rpc_server.sh"]
        ),
    ]
)

// AppState.swift
// Observable global state consumed by menu bar and future UI surfaces.
//
// Design notes
// ------------
// * All mutations happen on the MainActor so SwiftUI views stay happy.
// * The store owns the `CoreBridge` and is the only component that calls
//   into it.  Views call methods on `AppState` (e.g. `newWorkspace()`)
//   rather than touching the bridge directly.

import Foundation
import SwiftUI
import DailyStreamCore

/// Visual state of the menu bar icon.
public enum MenuBarIconState: Sendable {
    case idle        // no active workspace
    case recording   // workspace active
    case capturing   // brief transient (screenshot / clipboard)
}

/// Lightweight workspace snapshot for the menu.
public struct WorkspaceSummary: Sendable, Equatable {
    public let isActive: Bool
    public let title: String?
    public let activePipeline: String?
    public let pipelines: [String]

    public static let inactive = WorkspaceSummary(
        isActive: false, title: nil, activePipeline: nil, pipelines: []
    )
}

@MainActor
public final class AppState: ObservableObject {
    // MARK: - Published state

    @Published public private(set) var iconState: MenuBarIconState = .idle
    @Published public private(set) var workspace: WorkspaceSummary = .inactive
    @Published public private(set) var coreReady: Bool = false
    @Published public private(set) var lastError: String? = nil
    @Published public private(set) var toastMessage: ToastMessage? = nil

    // MARK: - Dependencies

    public let bridge: CoreBridge

    public init(bridge: CoreBridge? = nil) {
        self.bridge = bridge ?? CoreBridge()
    }

    // MARK: - Lifecycle

    /// Boot the Python core and pull the current workspace status.
    public func boot() async {
        do {
            try await bridge.start()
            coreReady = true
            await refreshStatus()
            Task { await self.listenForEvents() }
        } catch {
            coreReady = false
            lastError = "Core failed to start: \(error)"
        }
    }

    public func shutdown() async {
        await bridge.shutdown()
        coreReady = false
    }

    // MARK: - Actions

    public func refreshStatus() async {
        struct StatusDTO: Decodable {
            let is_active: Bool
            let title: String?
            let active_pipeline: String?
            let pipelines: [String]?
        }
        do {
            let s: StatusDTO = try await bridge.call(
                "workspace.status", params: RPCEmptyParams()
            )
            workspace = WorkspaceSummary(
                isActive: s.is_active,
                title: s.title,
                activePipeline: s.active_pipeline,
                pipelines: s.pipelines ?? []
            )
            iconState = s.is_active ? .recording : .idle
        } catch {
            lastError = "status failed: \(error)"
        }
    }

    /// Trigger a screenshot via the core and show a toast when done.
    public func takeScreenshot(mode: String = "interactive") async {
        struct Params: Encodable, Sendable { let mode: String }
        struct Result: Decodable { let path: String }
        iconState = .capturing
        defer { Task { await self.refreshStatus() } }
        do {
            let r: Result = try await bridge.call(
                "capture.screenshot",
                params: Params(mode: mode)
            )
            showToast(title: "Screenshot saved", subtitle: r.path)
        } catch {
            showToast(title: "Screenshot failed", subtitle: "\(error)")
        }
    }

    public func showToast(title: String, subtitle: String? = nil) {
        toastMessage = ToastMessage(title: title, subtitle: subtitle)
    }

    public func dismissToast() {
        toastMessage = nil
    }

    // MARK: - Event consumption

    private func listenForEvents() async {
        for await evt in bridge.events.events() {
            switch evt.method {
            case "workspace.changed":
                await refreshStatus()
            case "feed.entry_added":
                // Could update an in-memory recent-entries buffer later.
                break
            case "ai.analysis_completed":
                showToast(title: "AI analysis ready")
            default:
                break
            }
        }
    }
}

// MARK: - Toast --------------------------------------------------------

public struct ToastMessage: Identifiable, Equatable, Sendable {
    public let id = UUID()
    public let title: String
    public let subtitle: String?
    public let createdAt: Date = Date()
}

// ToastModels.swift
// Toast-related value types + error formatting, extracted from AppState.swift
// to keep the monolithic store file under control.
//
// Nothing here is AppState-specific: ToastKind / ToastMessage are plain
// `Sendable` value types consumed by ToastCenter.swift and any view that
// surfaces transient feedback.

import Foundation
import SwiftUI
import DailyStreamCore

// MARK: - Toast --------------------------------------------------------

public enum ToastKind: Sendable, Equatable {
    case info          // generic neutral (blue)
    case success       // default green check
    case warning       // amber
    case error         // red — used by showError

    public var iconName: String {
        switch self {
        case .info:    return "info.circle.fill"
        case .success: return "checkmark.circle.fill"
        case .warning: return "exclamationmark.triangle.fill"
        case .error:   return "xmark.octagon.fill"
        }
    }

    public var tint: Color {
        switch self {
        case .info:    return DSColor.accent
        case .success: return DSColor.success
        case .warning: return .orange
        case .error:   return .red
        }
    }
}

public struct ToastMessage: Identifiable, Equatable, Sendable {
    public let id = UUID()
    public let title: String
    public let subtitle: String?
    public let kind: ToastKind
    public let createdAt: Date = Date()

    public init(title: String, subtitle: String? = nil,
                kind: ToastKind = .success) {
        self.title = title
        self.subtitle = subtitle
        self.kind = kind
    }
}

/// Human-readable formatting for arbitrary errors surfaced in toasts.
///
/// Special cases:
///  * ``RPCError`` → ``"<message> (code <code>)"`` (uses the message
///    the Python side crafted — far more actionable than the default
///    ``"RPCError(-32002): …"`` ``CustomStringConvertible``).
///  * ``BridgeError.rpcFailed`` → unwraps and delegates.
///  * Everything else → ``localizedDescription``.
public func describeError(_ error: Error) -> String {
    if let rpc = error as? RPCError {
        return "\(rpc.message) (code \(rpc.code))"
    }
    if case let BridgeError.rpcFailed(rpc) = error {
        return "\(rpc.message) (code \(rpc.code))"
    }
    return error.localizedDescription
}

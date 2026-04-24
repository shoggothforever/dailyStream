// MenuBarIcon.swift
// SF Symbol icon that swaps based on `MenuBarIconState`.

import SwiftUI

public struct MenuBarIcon: View {
    public let state: MenuBarIconState

    public init(state: MenuBarIconState) { self.state = state }

    public var body: some View {
        Image(systemName: symbolName)
            .symbolRenderingMode(.hierarchical)
            .foregroundStyle(tint)
            .accessibilityLabel(accessibilityLabel)
            .help(accessibilityLabel)
    }

    private var symbolName: String {
        switch state {
        case .idle:       return "circle"
        case .recording:  return "record.circle.fill"
        case .capturing:  return "camera.aperture"
        case .flashing:   return "bolt.fill"
        }
    }

    private var tint: Color {
        switch state {
        case .idle:       return .primary
        case .recording:  return DSColor.accent
        case .capturing:  return DSColor.capturing
        case .flashing:   return .green
        }
    }

    private var accessibilityLabel: String {
        switch state {
        case .idle:       return "DailyStream (idle)"
        case .recording:  return "DailyStream (recording)"
        case .capturing:  return "DailyStream (capturing)"
        case .flashing:   return "DailyStream (flash)"
        }
    }
}

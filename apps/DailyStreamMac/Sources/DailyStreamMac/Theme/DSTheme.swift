// DSTheme.swift
// Typography + semantic colors shared across menu bar, HUD, and future
// Daily Review / Preferences surfaces.

import SwiftUI

public enum DSFont {
    /// Titles inside Daily Review / Preferences (32pt bold).
    public static let display = Font.system(size: 32, weight: .bold, design: .default)
    /// Section headings (17pt medium).
    public static let subheading = Font.system(size: 17, weight: .medium)
    /// Default body text (13pt regular).
    public static let body = Font.system(size: 13)
    /// Secondary 11pt text (metadata, keyboard hints).
    public static let caption = Font.system(size: 11)
    /// Monospaced for timestamps / keyboard hints.
    public static let mono = Font.system(size: 12, design: .monospaced)
}

public enum DSColor {
    /// Apple blue (accent).
    public static let accent = Color(red: 0x0A / 255, green: 0x84 / 255, blue: 0xFF / 255)
    /// Secondary accent for recording state.
    public static let recording = Color(red: 0xFF / 255, green: 0x45 / 255, blue: 0x3A / 255)
    /// AI / capture transient state.
    public static let capturing = Color(red: 0x5E / 255, green: 0x5C / 255, blue: 0xE6 / 255)
    /// Success indicator.
    public static let success = Color(red: 0x30 / 255, green: 0xD1 / 255, blue: 0x58 / 255)
}

public extension Animation {
    /// 0.18s spring — default HUD / Toast fade.
    static var dsHudIn: Animation {
        .spring(response: 0.28, dampingFraction: 0.85)
    }
    /// 0.12s ease-out — fast dismissals.
    static var dsHudOut: Animation {
        .easeOut(duration: 0.12)
    }
}

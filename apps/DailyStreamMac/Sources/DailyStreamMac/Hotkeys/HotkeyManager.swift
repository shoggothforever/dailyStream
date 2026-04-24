// HotkeyManager.swift
// Two-layer hotkey system:
//  1. sindresorhus/KeyboardShortcuts — static ⌘1 (screenshot) and ⌘2
//     (clipboard) that can be customised from Settings.
//  2. CGEventTap — dynamic per-preset hotkeys (e.g. <option>+1) that are
//     re-registered whenever the preset list changes.

import AppKit
import Carbon.HIToolbox
import KeyboardShortcuts

/// All registered shortcut names live here so the Preferences pane can
/// enumerate them later via `KeyboardShortcuts.Name.allCases`.
public extension KeyboardShortcuts.Name {
    /// `⌘1` by default — interactive drag-to-select screenshot.
    static let screenshot = Self(
        "screenshot",
        default: .init(.one, modifiers: [.command])
    )

    /// `⌘2` by default — capture whatever the clipboard holds right now.
    static let clipboardCapture = Self(
        "clipboardCapture",
        default: .init(.two, modifiers: [.command])
    )

    /// `⌘3` by default — quick pipeline switcher (Spotlight-style).
    static let pipelinePicker = Self(
        "pipelinePicker",
        default: .init(.three, modifiers: [.command])
    )
}

// MARK: - Hotkey string parser -------------------------------------------

/// Parsed representation of a hotkey string like `<option>+1`.
private struct ParsedHotkey: Equatable {
    let keyCode: Int64
    let modifierMask: UInt64  // CGEventFlags bits
}

/// macOS virtual key codes (US keyboard layout — hardware codes are
/// layout-independent).
private let kKeyCodes: [String: Int64] = [
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7,
    "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15,
    "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22,
    "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29,
    "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "l": 37,
    "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44,
    "n": 45, "m": 46, ".": 47, "`": 50, " ": 49,
    "return": 36, "tab": 48, "space": 49, "delete": 51, "escape": 53,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97,
    "f7": 98, "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111,
    "up": 126, "down": 125, "left": 123, "right": 124,
]

/// Modifier name → CGEventFlags bit.
private let kModifierMap: [String: UInt64] = [
    "<cmd>":     UInt64(CGEventFlags.maskCommand.rawValue),
    "<command>": UInt64(CGEventFlags.maskCommand.rawValue),
    "<ctrl>":    UInt64(CGEventFlags.maskControl.rawValue),
    "<control>": UInt64(CGEventFlags.maskControl.rawValue),
    "<shift>":   UInt64(CGEventFlags.maskShift.rawValue),
    "<alt>":     UInt64(CGEventFlags.maskAlternate.rawValue),
    "<option>":  UInt64(CGEventFlags.maskAlternate.rawValue),
]

/// Bitmask keeping only the modifier flags we care about.
private let kModifierCareMask: UInt64 =
    UInt64(CGEventFlags.maskCommand.rawValue)
    | UInt64(CGEventFlags.maskControl.rawValue)
    | UInt64(CGEventFlags.maskShift.rawValue)
    | UInt64(CGEventFlags.maskAlternate.rawValue)

/// Parse a hotkey string like `<option>+1` into keyCode + modifier mask.
/// Returns `nil` when parsing fails.
private func parseHotkey(_ str: String) -> ParsedHotkey? {
    let parts = str.lowercased().trimmingCharacters(in: .whitespaces).split(separator: "+")
    var keyCode: Int64 = -1
    var modifiers: UInt64 = 0

    for raw in parts {
        let part = String(raw).trimmingCharacters(in: .whitespaces)
        if let mod = kModifierMap[part] {
            modifiers |= mod
        } else if let kc = kKeyCodes[part] {
            keyCode = kc
        } else {
            return nil  // unknown token
        }
    }
    guard keyCode >= 0 else { return nil }
    return ParsedHotkey(keyCode: keyCode, modifierMask: modifiers)
}

// MARK: - PresetHotkeyTap -----------------------------------------------

/// Manages a CGEventTap on a background thread, dispatching matched
/// keyDown events to registered callbacks.
///
/// Thread-safety: `bindings` is protected by `lock`.  The event-tap
/// callback reads under the lock; mutations happen on the main thread
/// via `updateBindings`.
private final class PresetHotkeyTap {
    struct Binding {
        let parsed: ParsedHotkey
        let presetName: String
        let region: String
    }

    private var bindings: [Binding] = []
    private let lock = NSLock()

    /// Weak back-reference used to schedule work on the main actor.
    weak var state: AppState?

    fileprivate var tap: CFMachPort?
    private var runLoopSource: CFRunLoopSource?
    private var thread: Thread?
    private var bgRunLoop: CFRunLoop?

    // MARK: Binding management

    /// Replace the full set of bindings.  Safe to call repeatedly.
    func updateBindings(_ newBindings: [Binding]) {
        lock.lock()
        bindings = newBindings
        lock.unlock()
    }

    // MARK: Lifecycle

    func start() {
        guard tap == nil else { return }

        // The C callback must be a plain function pointer — we smuggle
        // `self` through the `userInfo` opaque pointer.
        let refcon = Unmanaged.passUnretained(self).toOpaque()

        let eventMask = (1 << CGEventType.keyDown.rawValue)
        guard let newTap = CGEvent.tapCreate(
            tap: .cgSessionEventTap,
            place: .headInsertEventTap,
            options: .listenOnly,
            eventsOfInterest: CGEventMask(eventMask),
            callback: presetTapCallback,
            userInfo: refcon
        ) else {
            // Accessibility permission not granted.
            return
        }
        tap = newTap
        runLoopSource = CFMachPortCreateRunLoopSource(nil, newTap, 0)

        let t = Thread { [weak self] in
            guard let self, let src = self.runLoopSource else { return }
            self.bgRunLoop = CFRunLoopGetCurrent()
            CFRunLoopAddSource(self.bgRunLoop, src, .commonModes)
            CGEvent.tapEnable(tap: newTap, enable: true)
            CFRunLoopRun()
        }
        t.name = "PresetHotkeyTap"
        t.qualityOfService = .userInteractive
        t.start()
        thread = t
    }

    func stop() {
        if let t = tap {
            CGEvent.tapEnable(tap: t, enable: false)
        }
        if let rl = bgRunLoop {
            CFRunLoopStop(rl)
        }
        tap = nil
        runLoopSource = nil
        thread = nil
        bgRunLoop = nil
    }

    // MARK: Matching

    /// Called from the C callback on the tap thread.
    fileprivate func handleEvent(_ event: CGEvent) {
        let keyCode = event.getIntegerValueField(.keyboardEventKeycode)
        let flags = UInt64(event.flags.rawValue) & kModifierCareMask

        lock.lock()
        let match = bindings.first {
            $0.parsed.keyCode == keyCode && $0.parsed.modifierMask == flags
        }
        lock.unlock()

        guard let match else { return }

        // Dispatch onto the MainActor so we can safely call AppState.
        let region = match.region
        let name = match.presetName
        Task { @MainActor [weak state] in
            guard let state else { return }
            await state.takeScreenshot(region: region, presetName: name)
        }
    }
}

/// Plain-C function used as the CGEventTap callback.
private func presetTapCallback(
    proxy: CGEventTapProxy,
    type: CGEventType,
    event: CGEvent,
    userInfo: UnsafeMutableRawPointer?
) -> Unmanaged<CGEvent>? {
    guard let userInfo else { return Unmanaged.passUnretained(event) }

    if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
        // Re-enable the tap (macOS disables it on timeout).
        if let tapInstance = Unmanaged<PresetHotkeyTap>.fromOpaque(userInfo)
            .takeUnretainedValue().tap {
            CGEvent.tapEnable(tap: tapInstance, enable: true)
        }
        return Unmanaged.passUnretained(event)
    }

    guard type == .keyDown else { return Unmanaged.passUnretained(event) }

    let tapInstance = Unmanaged<PresetHotkeyTap>.fromOpaque(userInfo)
        .takeUnretainedValue()
    tapInstance.handleEvent(event)

    return Unmanaged.passUnretained(event)
}

// MARK: - HotkeyManager --------------------------------------------------

@MainActor
public final class HotkeyManager {
    private unowned let state: AppState
    private let presetTap = PresetHotkeyTap()

    public init(state: AppState) {
        self.state = state
        presetTap.state = state
    }

    /// Called exactly once after `AppState.boot()` succeeds.
    public func install() {
        // 1. Static shortcuts via KeyboardShortcuts library.
        KeyboardShortcuts.onKeyDown(for: .screenshot) { [weak state] in
            guard let state else { return }
            Task { await state.takeScreenshot() }
        }
        KeyboardShortcuts.onKeyDown(for: .clipboardCapture) { [weak state] in
            guard let state else { return }
            Task { await state.captureClipboard() }
        }
        KeyboardShortcuts.onKeyDown(for: .pipelinePicker) { [weak state] in
            guard let state else { return }
            Task { await state.showPipelinePicker() }
        }

        // 2. Start the CGEventTap for dynamic preset hotkeys.
        presetTap.start()

        // 3. Sync current presets.
        syncPresetHotkeys(state.presets)
    }

    /// Re-register preset hotkeys.  Call whenever `AppState.presets`
    /// changes (after create / delete / refresh).
    public func syncPresetHotkeys(_ presets: [ScreenshotPreset]) {
        var bindings: [PresetHotkeyTap.Binding] = []
        for preset in presets {
            guard let hotkeyStr = preset.hotkey, !hotkeyStr.isEmpty,
                  let parsed = parseHotkey(hotkeyStr) else { continue }
            bindings.append(.init(
                parsed: parsed,
                presetName: preset.name,
                region: preset.region
            ))
        }
        presetTap.updateBindings(bindings)
    }

    /// Tear down the event tap.
    public func teardown() {
        presetTap.stop()
    }
}

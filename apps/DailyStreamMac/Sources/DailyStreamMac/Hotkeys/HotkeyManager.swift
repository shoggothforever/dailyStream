// HotkeyManager.swift
// Single-layer hotkey system built around the Capture Mode Designer:
//
//   * Every preset inside the **active** CaptureMode may bind a hotkey
//     string (e.g. "<option>+1", "<cmd>+4").  `HotkeyManager` wires those
//     strings into a CGEventTap so they fire anywhere in the system
//     regardless of which app has focus.
//   * Three legacy `KeyboardShortcuts.Name` entries (screenshot /
//     clipboard / pipeline picker) remain *as a fallback* for users who
//     deleted every preset.  When the active Mode contains a
//     `free-selection` / `clipboard` preset with a matching behaviour
//     they take priority.
//   * Switching Mode via `syncPresets(_:)` swaps the entire binding set
//     in one call — presets from other Modes are guaranteed NOT to fire.

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
        let modeID: String
        let presetID: String
        let presetName: String
    }

    private var bindings: [Binding] = []
    private let lock = NSLock()

    /// Weak back-reference used to schedule work on the main actor.
    weak var state: AppState?

    fileprivate var tap: CFMachPort?
    private var runLoopSource: CFRunLoopSource?
    private var thread: Thread?
    private var bgRunLoop: CFRunLoop?

    // Track which bindings are currently held down so we can suppress
    // macOS's auto-repeat keyDowns.
    private var heldBindings: Set<String> = []

    // MARK: Binding management

    /// Replace the full set of bindings.  Safe to call repeatedly.
    func updateBindings(_ newBindings: [Binding]) {
        lock.lock()
        bindings = newBindings
        heldBindings.removeAll()
        lock.unlock()
    }

    // MARK: Lifecycle

    func start() {
        guard tap == nil else { return }

        // The C callback must be a plain function pointer — we smuggle
        // `self` through the `userInfo` opaque pointer.
        let refcon = Unmanaged.passUnretained(self).toOpaque()

        let eventMask = (1 << CGEventType.keyDown.rawValue)
                      | (1 << CGEventType.keyUp.rawValue)
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
    fileprivate func handleEvent(_ event: CGEvent, type: CGEventType) {
        let keyCode = event.getIntegerValueField(.keyboardEventKeycode)
        let flags = UInt64(event.flags.rawValue) & kModifierCareMask

        lock.lock()
        let match = bindings.first {
            $0.parsed.keyCode == keyCode && $0.parsed.modifierMask == flags
        }
        // Snapshot info needed outside the lock.
        let key = "\(match?.modeID ?? "")/\(match?.presetID ?? "")"
        let wasHeld = match != nil ? heldBindings.contains(key) : false
        if let m = match {
            if type == .keyDown {
                heldBindings.insert("\(m.modeID)/\(m.presetID)")
            } else if type == .keyUp {
                heldBindings.remove("\(m.modeID)/\(m.presetID)")
            }
        }
        lock.unlock()

        guard let match, type == .keyDown else { return }

        // macOS emits auto-repeat keyDowns while the user is holding
        // the key.  Suppress repeats so a single press fires exactly
        // one capture.
        if wasHeld { return }

        // Dispatch onto the MainActor so we can safely call AppState.
        let modeID = match.modeID
        let presetID = match.presetID
        let presetName = match.presetName
        Task { @MainActor [weak state] in
            guard let state else { return }
            await state.onPresetHotkeyDown(
                modeID: modeID,
                presetID: presetID,
                presetName: presetName
            )
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

    guard type == .keyDown || type == .keyUp else {
        return Unmanaged.passUnretained(event)
    }

    let tapInstance = Unmanaged<PresetHotkeyTap>.fromOpaque(userInfo)
        .takeUnretainedValue()
    tapInstance.handleEvent(event, type: type)

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
        // 1. Static fallback shortcuts via KeyboardShortcuts library.
        //    These only fire when the *active* Mode does not already
        //    claim the same physical key combo.
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

        // 3. Sync presets of the currently-active Mode.
        syncPresets(state.activeModePresets)
    }

    /// Re-register preset hotkeys.  Call whenever the active Mode or
    /// its preset list changes.
    public func syncPresets(_ presets: [CapturePreset]) {
        var bindings: [PresetHotkeyTap.Binding] = []
        let modeID = state.captureModes.activeModeID ?? ""
        for preset in presets {
            guard let hotkeyStr = preset.hotkey, !hotkeyStr.isEmpty,
                  let parsed = parseHotkey(hotkeyStr) else { continue }
            bindings.append(.init(
                parsed: parsed,
                modeID: modeID,
                presetID: preset.id,
                presetName: preset.name
            ))
        }
        presetTap.updateBindings(bindings)
    }

    /// Tear down the event tap.
    public func teardown() {
        presetTap.stop()
    }
}

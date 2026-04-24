// CapturePresetEditorView.swift
// The right-hand column of the Capture Mode Designer.  Edits a single
// `CapturePreset` locally (a draft copy), exposes Save / Delete / Test
// actions, and renders:
//
//   * Name + Emoji
//   * Hotkey text field (with inline validation)
//   * Source kind picker (with region picker for REGION sources)
//   * Attachment section per Kind
//     └── STRATEGY / FEEDBACK / WINDOW_CTRL / POST / DELIVERY
//         (single-choice kinds render as Picker; multi-choice as Toggle grid)
//
// Param forms are generated from the attachment catalog's
// ``params_schema`` so adding new attachments on the Python side
// automatically shows up in the UI without any Swift changes.

import AppKit
import SwiftUI

struct CapturePresetEditorView: View {
    @ObservedObject var state: AppState

    let mode: CaptureMode
    let preset: CapturePreset

    var onSave: (CapturePreset) -> Void
    var onDelete: () -> Void
    var onTest: () -> Void

    // Local editing buffer — initialised from `preset` and compared
    // against ``lastSaved`` to compute the "dirty" flag used by Save.
    @State private var draft: CapturePreset
    @State private var lastSaved: CapturePreset

    init(
        state: AppState,
        mode: CaptureMode,
        preset: CapturePreset,
        onSave: @escaping (CapturePreset) -> Void,
        onDelete: @escaping () -> Void,
        onTest: @escaping () -> Void
    ) {
        self.state = state
        self.mode = mode
        self.preset = preset
        self.onSave = onSave
        self.onDelete = onDelete
        self.onTest = onTest
        _draft = State(initialValue: preset)
        _lastSaved = State(initialValue: preset)
    }

    private var isDirty: Bool { draft != lastSaved }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                headerSection
                hotkeySection
                sourceSection
                attachmentsSection
                Spacer(minLength: 12)
                footerButtons
            }
            .padding(20)
        }
        .background(Color(nsColor: .textBackgroundColor))
        .onChange(of: preset.id) { _ in
            // The user picked a different preset in the middle column —
            // reset both our local draft and the "last saved" snapshot.
            draft = preset
            lastSaved = preset
        }
        .onChange(of: preset) { newValue in
            // Same preset id but its content changed on the server
            // (e.g. we just saved it).  Refresh our baseline so the
            // Save button goes back to disabled.
            if newValue.id == lastSaved.id && newValue != lastSaved {
                lastSaved = newValue
                // Keep user edits if they've diverged; otherwise
                // mirror the server copy.
                if draft == lastSaved { draft = newValue }
            }
        }
    }

    // MARK: - Sections -------------------------------------------------

    private var headerSection: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Preset")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                HStack(spacing: 8) {
                    TextField("Emoji", text: Binding(
                        get: { draft.emoji },
                        set: { draft.emoji = $0 }
                    ))
                    .frame(width: 44)

                    TextField("Name", text: Binding(
                        get: { draft.name },
                        set: { draft.name = $0 }
                    ))
                    .font(.system(size: 18, weight: .semibold))
                }
            }
        }
    }

    private var hotkeySection: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Hotkey")
                .font(.caption)
                .foregroundStyle(.secondary)
            HotkeyTextField(
                value: Binding(
                    get: { draft.hotkey ?? "" },
                    set: { draft.hotkey = $0.isEmpty ? nil : $0 }
                )
            )
            HStack(spacing: 6) {
                Text("Format: ")
                    .foregroundStyle(.secondary)
                Text("<cmd>+1")
                    .font(.system(size: 10, design: .monospaced))
                    .padding(.horizontal, 4)
                    .background(
                        RoundedRectangle(cornerRadius: 3)
                            .fill(Color.secondary.opacity(0.12))
                    )
                Text("<option>+<shift>+f1")
                    .font(.system(size: 10, design: .monospaced))
                    .padding(.horizontal, 4)
                    .background(
                        RoundedRectangle(cornerRadius: 3)
                            .fill(Color.secondary.opacity(0.12))
                    )
                Spacer()
                if let err = hotkeyError {
                    Text(err)
                        .foregroundStyle(.red)
                        .font(.system(size: 10))
                }
            }
            .font(.system(size: 10))
            .foregroundStyle(.tertiary)
            Text("Modifiers: <cmd>, <option>, <ctrl>, <shift>.  Leave empty to unbind.")
                .font(.system(size: 10))
                .foregroundStyle(.tertiary)
        }
    }

    /// Live validation message shown below the hotkey field — `nil`
    /// means the current value is legal (or empty).
    private var hotkeyError: String? {
        guard let hk = draft.hotkey, !hk.isEmpty else { return nil }
        return HotkeyString.validate(hk)
    }

    private var sourceSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Source")
                .font(.caption)
                .foregroundStyle(.secondary)
            Picker("", selection: Binding(
                get: { draft.source.kind },
                set: { draft.source.kind = $0 }
            )) {
                ForEach(CaptureSourceKind.allCases, id: \.self) { k in
                    Text(k.label).tag(k)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()

            if draft.source.kind == .region {
                HStack {
                    TextField("x,y,w,h", text: Binding(
                        get: { draft.source.region ?? "" },
                        set: { draft.source.region = $0.isEmpty ? nil : $0 }
                    ))
                    Button("Pick…") {
                        Task { await pickRegion() }
                    }
                }
            }
        }
    }

    private var attachmentsSection: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Attachments")
                .font(.caption)
                .foregroundStyle(.secondary)

            ForEach(AttachmentKind.allCases.sorted { $0.order < $1.order },
                    id: \.self) { kind in
                kindSection(kind: kind)
            }
        }
    }

    private var footerButtons: some View {
        HStack {
            Button(role: .destructive) {
                onDelete()
            } label: {
                Label("Delete Preset", systemImage: "trash")
            }
            .buttonStyle(.bordered)

            Spacer()

            if isDirty {
                Text("Unsaved changes")
                    .font(.system(size: 11))
                    .foregroundStyle(.orange)
                    .padding(.trailing, 4)
            }

            Button("Test") {
                onTest()
            }
            .buttonStyle(.bordered)

            Button("Save") {
                let saved = draft
                onSave(saved)
                lastSaved = saved
            }
            .buttonStyle(.borderedProminent)
            .disabled(!isDirty || draft.name.isEmpty)
            .keyboardShortcut(.return, modifiers: [.command])
        }
    }

    // MARK: - Attachment Kind section ---------------------------------

    @ViewBuilder
    private func kindSection(kind: AttachmentKind) -> some View {
        let catalog = state.attachmentCatalog.filter { $0.kind == kind }
        if !catalog.isEmpty {
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Text(kind.label)
                        .font(.system(size: 13, weight: .semibold))
                    if kind.isSingleChoice {
                        Text("(pick one)")
                            .font(.system(size: 10))
                            .foregroundStyle(.tertiary)
                    } else {
                        Text("(multi-select)")
                            .font(.system(size: 10))
                            .foregroundStyle(.tertiary)
                    }
                    Spacer()
                }

                if kind.isSingleChoice {
                    singleChoiceRow(kind: kind, catalog: catalog)
                } else {
                    multiChoiceGrid(kind: kind, catalog: catalog)
                }
            }
            .padding(10)
            .background(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(Color.secondary.opacity(0.05))
            )
        }
    }

    @ViewBuilder
    private func singleChoiceRow(kind: AttachmentKind,
                                 catalog: [AttachmentCatalogEntry]) -> some View {
        let currentID = draft.attachments.first(where: { a in
            state.attachmentCatalog.first { $0.id == a.id }?.kind == kind
        })?.id

        HStack(spacing: 8) {
            ForEach(catalog) { entry in
                Button {
                    setSingleChoice(kind: kind, entry: entry)
                } label: {
                    Label(entry.label, systemImage: entry.icon)
                        .font(.system(size: 12))
                        .padding(.horizontal, 10)
                        .padding(.vertical, 6)
                        .background(
                            RoundedRectangle(cornerRadius: 6)
                                .fill(currentID == entry.id
                                      ? Color.accentColor.opacity(0.2)
                                      : Color.clear)
                        )
                        .overlay(
                            RoundedRectangle(cornerRadius: 6)
                                .stroke(currentID == entry.id
                                        ? Color.accentColor
                                        : Color.secondary.opacity(0.3),
                                        lineWidth: 1)
                        )
                }
                .buttonStyle(.plain)
                .help(entry.description)
            }
            Spacer()
        }

        // Parameter form for the currently-selected single-choice entry.
        if let currentID,
           let entry = catalog.first(where: { $0.id == currentID }),
           !entry.paramsSchema.isEmpty {
            AttachmentParamsForm(
                entry: entry,
                params: bindingForAttachmentParams(id: currentID)
            )
            .padding(.top, 4)
        }
    }

    @ViewBuilder
    private func multiChoiceGrid(kind: AttachmentKind,
                                 catalog: [AttachmentCatalogEntry]) -> some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 190), spacing: 6)],
                  alignment: .leading, spacing: 6) {
            ForEach(catalog) { entry in
                VStack(alignment: .leading, spacing: 4) {
                    Toggle(isOn: Binding(
                        get: { containsAttachment(id: entry.id) },
                        set: { toggleMultiChoice(entry: entry, enabled: $0) }
                    )) {
                        Label(entry.label, systemImage: entry.icon)
                            .font(.system(size: 12))
                    }
                    .toggleStyle(.checkbox)
                    .help(entry.description)

                    if containsAttachment(id: entry.id),
                       !entry.paramsSchema.isEmpty {
                        AttachmentParamsForm(
                            entry: entry,
                            params: bindingForAttachmentParams(id: entry.id)
                        )
                        .padding(.leading, 22)
                    }
                }
            }
        }
    }

    // MARK: - Helpers ------------------------------------------------------

    private func containsAttachment(id: String) -> Bool {
        draft.attachments.contains { $0.id == id }
    }

    private func toggleMultiChoice(entry: AttachmentCatalogEntry, enabled: Bool) {
        if enabled {
            if !containsAttachment(id: entry.id) {
                // Remove anything mutually exclusive first.
                draft.attachments.removeAll { entry.mutuallyExclusive.contains($0.id) }
                let defaults = defaultParams(for: entry)
                draft.attachments.append(
                    CaptureAttachment(id: entry.id, params: defaults)
                )
            }
        } else {
            draft.attachments.removeAll { $0.id == entry.id }
        }
    }

    private func setSingleChoice(kind: AttachmentKind,
                                 entry: AttachmentCatalogEntry) {
        // Drop any existing attachment of the same kind.
        draft.attachments.removeAll { a in
            state.attachmentCatalog.first { $0.id == a.id }?.kind == kind
        }
        // Add the new one with default params.
        let defaults = defaultParams(for: entry)
        draft.attachments.append(
            CaptureAttachment(id: entry.id, params: defaults)
        )
    }

    private func defaultParams(for entry: AttachmentCatalogEntry)
        -> [String: JSONValue] {
        var out: [String: JSONValue] = [:]
        for (k, v) in entry.paramsSchema {
            if let d = v.defaultValue { out[k] = d }
        }
        return out
    }

    private func bindingForAttachmentParams(id: String)
        -> Binding<[String: JSONValue]> {
        Binding(
            get: {
                draft.attachments.first { $0.id == id }?.params ?? [:]
            },
            set: { newVal in
                if let idx = draft.attachments.firstIndex(where: { $0.id == id }) {
                    draft.attachments[idx].params = newVal
                }
            }
        )
    }

    // MARK: - Region picker integration -------------------------------

    private func pickRegion() async {
        if let r = await state.selectRegion() {
            draft.source.region = r
        }
    }
}

// MARK: - Hotkey text field + validation -------------------------------

/// Plain editable SwiftUI text field for typing hotkey strings like
/// `"<cmd>+1"` or `"<option>+<shift>+f1"`.  Validation is intentionally
/// **non-blocking**: we show an inline error but still let the user
/// save a partial value so they can finish typing without losing focus.
struct HotkeyTextField: View {
    @Binding var value: String

    var body: some View {
        TextField("e.g.  <option>+1   or   <cmd>+<shift>+s", text: Binding(
            get: { value },
            set: { value = $0.trimmingCharacters(in: .whitespacesAndNewlines) }
        ))
        .textFieldStyle(.roundedBorder)
        .font(.system(.body, design: .monospaced))
        .autocorrectionDisabled(true)
    }
}

/// Validator mirroring the Python `kKeyCodes` / `kModifierMap` set so
/// the Swift side and Python side agree on what's legal.
enum HotkeyString {
    /// Returns `nil` when the string is empty or a valid hotkey; a
    /// short, user-facing error message otherwise.
    static func validate(_ raw: String) -> String? {
        let s = raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if s.isEmpty { return nil }

        let parts = s.split(separator: "+").map {
            $0.trimmingCharacters(in: .whitespaces)
        }
        guard !parts.isEmpty else { return "Empty hotkey" }

        var keyCount = 0
        for token in parts {
            if modifierTokens.contains(token) { continue }
            if keyTokens.contains(token) {
                keyCount += 1
                continue
            }
            return "Unknown token: \(token)"
        }

        if keyCount == 0 {
            return "Missing key (e.g. '1', 'a', 'f3')"
        }
        if keyCount > 1 {
            return "Only one key is allowed"
        }
        return nil
    }

    private static let modifierTokens: Set<String> = [
        "<cmd>", "<command>",
        "<ctrl>", "<control>",
        "<shift>",
        "<alt>", "<option>",
    ]

    private static let keyTokens: Set<String> = {
        var s: Set<String> = [
            "return", "tab", "space", "delete", "escape",
            "up", "down", "left", "right",
        ]
        // letters
        for scalar in UnicodeScalar("a").value ... UnicodeScalar("z").value {
            s.insert(String(UnicodeScalar(scalar)!))
        }
        // digits
        for d in 0...9 { s.insert("\(d)") }
        // function keys
        for i in 1...12 { s.insert("f\(i)") }
        // common punctuation the parser recognises
        for p in ["-", "=", "[", "]", "\\", ";", "'", ",", ".", "/", "`"] {
            s.insert(p)
        }
        return s
    }()
}


// MARK: - Attachment params form ----------------------------------------

/// Renders the parameter editor for one Attachment based on its
/// ``params_schema``.  Supported kinds: int / float / bool / enum /
/// string.  Unknown kinds render a read-only JSON text field.
struct AttachmentParamsForm: View {
    let entry: AttachmentCatalogEntry
    @Binding var params: [String: JSONValue]

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(entry.paramsSchema.sorted(by: { $0.key < $1.key }),
                    id: \.key) { key, spec in
                paramRow(name: key, spec: spec)
            }
        }
    }

    @ViewBuilder
    private func paramRow(name: String, spec: AttachmentParamSchema) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(name)
                .font(.system(size: 11))
                .foregroundStyle(.secondary)
                .frame(width: 90, alignment: .trailing)
            switch spec.kind {
            case "int":
                TextField("", value: Binding(
                    get: { params[name]?.intValue ?? (spec.defaultValue?.intValue ?? 0) },
                    set: { params[name] = .int($0) }
                ), formatter: NumberFormatter())
                .frame(maxWidth: 100)
            case "float":
                TextField("", value: Binding(
                    get: { params[name]?.doubleValue ?? (spec.defaultValue?.doubleValue ?? 0.0) },
                    set: { params[name] = .double($0) }
                ), formatter: NumberFormatter())
                .frame(maxWidth: 100)
            case "bool":
                Toggle("", isOn: Binding(
                    get: { params[name]?.boolValue ?? (spec.defaultValue?.boolValue ?? false) },
                    set: { params[name] = .bool($0) }
                ))
                .labelsHidden()
            case "enum":
                Picker("", selection: Binding(
                    get: { params[name]?.stringValue ?? (spec.defaultValue?.stringValue ?? "") },
                    set: { params[name] = .string($0) }
                )) {
                    ForEach(spec.enumValues ?? [], id: \.self) { v in
                        Text(v).tag(v)
                    }
                }
                .labelsHidden()
                .frame(maxWidth: 160)
            default:
                TextField("", text: Binding(
                    get: { params[name]?.stringValue ?? (spec.defaultValue?.stringValue ?? "") },
                    set: { params[name] = .string($0) }
                ))
            }

            if let help = spec.help, !help.isEmpty {
                Text(help)
                    .font(.system(size: 10))
                    .foregroundStyle(.tertiary)
                    .lineLimit(1)
            }
            Spacer()
        }
    }
}

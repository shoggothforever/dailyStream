// TemplateBrowserSheet.swift
// Modal sheet that lists available Mode templates (built-in + user-
// installed) and lets the user preview + install one with a click.

import AppKit
import SwiftUI

struct TemplateBrowserSheet: View {
    @ObservedObject var state: AppState

    var onInstalled: (String) -> Void
    var onDismiss: () -> Void

    @State private var templates: [CaptureModeTemplate] = []
    @State private var selected: CaptureModeTemplate? = nil
    @State private var loading: Bool = true

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            HSplitView {
                templateList
                    .frame(minWidth: 260, idealWidth: 280, maxWidth: 340)
                previewPane
                    .frame(minWidth: 360)
            }
            Divider()
            footer
        }
        .frame(width: 820, height: 520)
        .task { await load() }
    }

    private var header: some View {
        HStack(spacing: 10) {
            Image(systemName: "books.vertical.fill")
                .font(.system(size: 20))
                .foregroundStyle(DSColor.accent)
            VStack(alignment: .leading, spacing: 2) {
                Text("Template Library")
                    .font(.system(size: 15, weight: .semibold))
                Text("Pick a starter Mode.  Your existing Modes are never overwritten — a unique id is assigned automatically.")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
            Spacer()
        }
        .padding(14)
    }

    private var templateList: some View {
        List(selection: Binding(
            get: { selected?.templateID },
            set: { newValue in
                selected = templates.first { $0.templateID == newValue }
            }
        )) {
            if loading {
                HStack {
                    ProgressView().controlSize(.small)
                    Text("Loading templates…")
                        .foregroundStyle(.secondary)
                }
            } else if templates.isEmpty {
                Text("No templates available.")
                    .foregroundStyle(.secondary)
            } else {
                ForEach(templates) { tpl in
                    VStack(alignment: .leading, spacing: 4) {
                        Text("\(tpl.emoji)  \(tpl.title)")
                            .font(.system(size: 13, weight: .medium))
                        if !tpl.description.isEmpty {
                            Text(tpl.description)
                                .font(.system(size: 11))
                                .foregroundStyle(.secondary)
                                .lineLimit(2)
                        }
                        if !tpl.tags.isEmpty {
                            HStack(spacing: 4) {
                                ForEach(tpl.tags, id: \.self) { t in
                                    Text(t)
                                        .font(.system(size: 10))
                                        .padding(.horizontal, 5)
                                        .padding(.vertical, 1)
                                        .background(
                                            Capsule()
                                                .fill(Color.secondary.opacity(0.12))
                                        )
                                }
                            }
                        }
                    }
                    .tag(tpl.templateID)
                }
            }
        }
        .listStyle(.sidebar)
    }

    @ViewBuilder
    private var previewPane: some View {
        if let tpl = selected {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    HStack(spacing: 10) {
                        Text(tpl.emoji)
                            .font(.system(size: 36))
                        VStack(alignment: .leading, spacing: 2) {
                            Text(tpl.title)
                                .font(.system(size: 18, weight: .semibold))
                            Text("by \(tpl.author)")
                                .font(.system(size: 11))
                                .foregroundStyle(.secondary)
                        }
                    }

                    if !tpl.description.isEmpty {
                        Text(tpl.description)
                            .font(.system(size: 12))
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }

                    if !tpl.prerequisites.isEmpty {
                        VStack(alignment: .leading, spacing: 4) {
                            Label("Before you use this",
                                  systemImage: "info.circle")
                                .font(.system(size: 12, weight: .semibold))
                            ForEach(tpl.prerequisites, id: \.self) { p in
                                Text("• \(p)")
                                    .font(.system(size: 11))
                                    .foregroundStyle(.secondary)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                        .padding(10)
                        .background(
                            RoundedRectangle(cornerRadius: 8,
                                             style: .continuous)
                                .fill(Color.yellow.opacity(0.12))
                        )
                    }

                    Divider()

                    VStack(alignment: .leading, spacing: 6) {
                        Text("Presets")
                            .font(.system(size: 13, weight: .semibold))
                        ForEach(tpl.mode.presets) { preset in
                            HStack(alignment: .top, spacing: 8) {
                                Text(preset.emoji)
                                VStack(alignment: .leading, spacing: 2) {
                                    HStack(spacing: 6) {
                                        Text(preset.name)
                                            .font(.system(size: 12, weight: .medium))
                                        if let hk = preset.hotkey, !hk.isEmpty {
                                            Text(hk)
                                                .font(.system(size: 10,
                                                              design: .monospaced))
                                                .foregroundStyle(.tertiary)
                                        }
                                    }
                                    Text(preset.source.kind.label)
                                        .font(.system(size: 10))
                                        .foregroundStyle(.secondary)
                                    if !preset.attachments.isEmpty {
                                        Text(preset.attachments
                                             .map(\.id)
                                             .joined(separator: " · "))
                                            .font(.system(size: 10))
                                            .foregroundStyle(.tertiary)
                                    }
                                }
                            }
                            .padding(.vertical, 4)
                        }
                    }
                }
                .padding(18)
            }
        } else {
            VStack {
                Spacer()
                Text("Select a template on the left")
                    .foregroundStyle(.secondary)
                Spacer()
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private var footer: some View {
        HStack {
            Spacer()
            Button("Close") { onDismiss() }
                .keyboardShortcut(.cancelAction)
            Button("Install") {
                Task {
                    guard let tpl = selected else { return }
                    await state.installTemplate(tpl.templateID)
                    onInstalled(tpl.mode.id)
                }
            }
            .buttonStyle(.borderedProminent)
            .disabled(selected == nil)
            .keyboardShortcut(.defaultAction)
        }
        .padding(14)
    }

    private func load() async {
        loading = true
        let list = await state.listTemplates()
        templates = list
        if selected == nil { selected = list.first }
        loading = false
    }
}

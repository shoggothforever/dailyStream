// DailyReviewWindow.swift
// Standalone NSWindow that shows a beautiful "today's review" after
// ending a workspace.  Data flow:
//
//   1. `timeline.export_summary`  → hero + stats + pipeline summaries
//      (the window opens *instantly* on this payload).
//   2. `timeline.export_pipeline_entries(name)` is then fanned out in
//      parallel for each pipeline, populating the timeline as entries
//      arrive.  Images load asynchronously via the shared
//      ``LocalImageView`` / ``LocalImageCache`` so scrolling stays
//      smooth even for 80+ screenshots.
//
// The older ``timeline.export_structured`` full-payload path is still
// supported via ``show(data:)`` for the manual ``showDailyReview`` menu
// item.

import AppKit
import SwiftUI
import DailyStreamCore

@MainActor
final class DailyReviewWindow {
    static let shared = DailyReviewWindow()

    private var window: NSWindow?

    private init() {}

    /// Open the window with a lightweight summary only.  Pipeline
    /// entries are expected to be streamed in later via
    /// ``DailyReviewVM.appendPipelineEntries``.
    func show(summary: ReviewSummary, bridge: CoreBridge) -> DailyReviewVM {
        let vm = DailyReviewVM(summary: summary, bridge: bridge)
        present(vm: vm)
        return vm
    }

    /// Open the window with a fully-populated ``ReviewData`` payload
    /// (legacy path — used by ``showDailyReview`` which still calls
    /// ``timeline.export_structured``).
    func show(data: ReviewData, bridge: CoreBridge) {
        let vm = DailyReviewVM(data: data, bridge: bridge)
        present(vm: vm)
    }

    private func present(vm: DailyReviewVM) {
        let content = DailyReviewContent(vm: vm) { [weak self] in
            self?.close()
        }

        if let existing = window {
            existing.contentViewController = NSHostingController(rootView: content)
            existing.makeKeyAndOrderFront(nil)
            return
        }

        let w = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 800, height: 900),
            styleMask: [.titled, .closable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        w.isReleasedWhenClosed = false
        w.titlebarAppearsTransparent = true
        w.titleVisibility = .hidden
        w.center()
        w.contentViewController = NSHostingController(rootView: content)
        w.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        self.window = w
    }

    func close() {
        window?.orderOut(nil)
    }
}

// MARK: - View model (mutable, supports edit/delete) --------------------

@MainActor
final class DailyReviewVM: ObservableObject {
    /// The primary mutable source of truth — starts with header/stats
    /// from summary and grows as per-pipeline entries stream in.
    @Published var data: ReviewData
    @Published var editingEntry: ReviewData.Entry? = nil
    @Published var editText: String = ""

    /// Pipelines whose entries haven't arrived yet.  Drives the
    /// "loading …" affordance in the timeline.
    @Published var loadingPipelines: Set<String> = []
    let bridge: CoreBridge

    /// Initialise from a full ``ReviewData`` payload (legacy).
    init(data: ReviewData, bridge: CoreBridge) {
        self.data = data
        self.bridge = bridge
    }

    /// Initialise from a summary-only payload.  Timeline is empty until
    /// pipelines are appended via ``appendPipelineEntries``.
    init(summary: ReviewSummary, bridge: CoreBridge) {
        self.data = ReviewData(
            workspace: summary.workspace,
            stats: summary.stats,
            entries: [],
            pipeline_summaries: summary.pipeline_summaries,
            daily_summary: summary.daily_summary
        )
        self.bridge = bridge
        // Mark every pipeline as "still loading" so the UI can show a
        // placeholder row until its entries land.
        self.loadingPipelines = Set(summary.workspace.pipelines)
    }

    /// Called from ``AppState.endWorkspace`` once a pipeline's entries
    /// arrive.  Merges the new entries into the timeline in
    /// chronological order and drops the pipeline from the loading set.
    func appendPipelineEntries(pipeline: String, entries: [ReviewData.Entry]) {
        var updated = data
        updated.entries.append(contentsOf: entries)
        // Keep the timeline sorted by timestamp so mixing pipelines
        // doesn't produce a jumbled order as they arrive out of turn.
        updated.entries.sort { $0.timestamp < $1.timestamp }
        data = updated
        loadingPipelines.remove(pipeline)
    }

    /// Notify the VM that loading a pipeline failed / yielded nothing;
    /// drops it from the loading set so the spinner disappears.
    func finishPipelineLoad(_ pipeline: String) {
        loadingPipelines.remove(pipeline)
    }

    func deleteEntry(_ entry: ReviewData.Entry) async {
        // Find the entry's pipeline-local index
        let pipelineEntries = data.entries.filter { $0.pipeline == entry.pipeline }
        guard let localIdx = pipelineEntries.firstIndex(where: { $0.id == entry.id }) else { return }

        struct Params: Encodable, Sendable {
            let pipeline: String
            let entry_index: Int
            let delete_file: Bool
        }
        struct Result: Decodable { let deleted: Bool }
        do {
            let _: Result = try await bridge.call(
                "feed.delete",
                params: Params(pipeline: entry.pipeline,
                               entry_index: localIdx,
                               delete_file: false)
            )
        } catch {
            print("feed.delete failed: \(error)")
            return
        }
        // Remove from local data — mutate the whole struct to trigger @Published
        var updated = data
        updated.entries.removeAll { $0.id == entry.id }
        data = updated
    }

    func startEditing(_ entry: ReviewData.Entry) {
        editingEntry = entry
        editText = entry.description
    }

    func cancelEditing() {
        editingEntry = nil
        editText = ""
    }

    func saveEditing() async {
        guard let entry = editingEntry else { return }
        let pipelineEntries = data.entries.filter { $0.pipeline == entry.pipeline }
        guard let localIdx = pipelineEntries.firstIndex(where: { $0.id == entry.id }) else { return }

        struct Params: Encodable, Sendable {
            let pipeline: String
            let entry_index: Int
            let description: String
        }
        struct Result: Decodable { let updated: Bool }
        let newDesc = editText.trimmingCharacters(in: .whitespacesAndNewlines)
        do {
            let _: Result = try await bridge.call(
                "feed.update",
                params: Params(pipeline: entry.pipeline,
                               entry_index: localIdx,
                               description: newDesc)
            )
        } catch {
            print("feed.update failed: \(error)")
            editingEntry = nil
            editText = ""
            return
        }
        // Update local data — mutate the whole struct to trigger @Published
        var updated = data
        if let idx = updated.entries.firstIndex(where: { $0.id == entry.id }) {
            updated.entries[idx] = ReviewData.Entry(
                timestamp: entry.timestamp,
                pipeline: entry.pipeline,
                input_type: entry.input_type,
                description: newDesc,
                input_content: entry.input_content,
                ai_description: entry.ai_description,
                ai_category: entry.ai_category,
                ai_elements: entry.ai_elements
            )
        }
        data = updated
        editingEntry = nil
        editText = ""
    }
}

// MARK: - Data model ----------------------------------------------------

/// Lightweight payload returned by ``timeline.export_summary``.
/// Shares ``Workspace`` / ``Stats`` / ``PipelineSummary`` / ``DailySummary``
/// with ``ReviewData`` so the two shapes stay in lock-step.
struct ReviewSummary: Decodable, Sendable {
    let workspace: ReviewData.Workspace
    let stats: ReviewData.Stats?
    let pipeline_summaries: [ReviewData.PipelineSummary]?
    let daily_summary: ReviewData.DailySummary?
}

/// Payload returned by ``timeline.export_pipeline_entries``.
struct PipelineEntriesResponse: Decodable, Sendable {
    let pipeline: String
    let entries: [ReviewData.Entry]
}

struct ReviewData: Decodable, Sendable {
    struct Workspace: Decodable, Sendable {
        let workspace_id: String
        let title: String?
        let created_at: String
        let ended_at: String?
        let ai_mode: String
        let pipelines: [String]
    }
    struct Stats: Decodable, Sendable {
        let total_entries: Int?
        let type_counts: [String: Int]?
        let pipeline_count: Int?
        let ai_categories: [String: Int]?
        let top_elements: [TopElement]?
    }
    struct TopElement: Decodable, Sendable {
        let name: String
        let count: Int
    }
    struct Entry: Decodable, Sendable, Identifiable {
        let timestamp: String
        let pipeline: String
        let input_type: String
        let description: String
        let input_content: String
        let ai_description: String?
        let ai_category: String?
        let ai_elements: [String]?

        var id: String { "\(pipeline)_\(timestamp)" }
    }
    struct PipelineSummary: Decodable, Sendable {
        let name: String
        let entry_count: Int
        let description: String?
        let goal: String?
    }
    struct DailySummary: Decodable, Sendable {
        let overall_summary: String?
        let pipeline_summaries: [String: String]?
        let generated_at: String?
        let model: String?
    }

    let workspace: Workspace
    let stats: Stats?
    var entries: [Entry]
    let pipeline_summaries: [PipelineSummary]?
    let daily_summary: DailySummary?
}

// MARK: - SwiftUI view --------------------------------------------------

struct DailyReviewContent: View {
    @ObservedObject var vm: DailyReviewVM
    let onClose: () -> Void

    private var data: ReviewData { vm.data }

    var body: some View {
        ScrollView {
            // Lazy vertical stack — only the rows near the viewport are
            // materialised, so opening a workspace with 80+ entries
            // no longer blocks the main thread building view trees.
            LazyVStack(alignment: .leading, spacing: 24) {
                heroSection
                statsStrip
                pipelineSummariesSection
                timelineSection
                if let summary = data.daily_summary,
                   let overall = summary.overall_summary, !overall.isEmpty {
                    aiSummarySection(summary)
                }
                footerButtons
            }
            .padding(32)
        }
        .frame(minWidth: 700, minHeight: 600)
        .background(Color(nsColor: .windowBackgroundColor))
    }

    // MARK: - Hero

    private var heroSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(displayDate)
                .font(.system(size: 32, weight: .bold, design: .default))
            Text(data.workspace.title ?? data.workspace.workspace_id)
                .font(.system(size: 17, weight: .medium))
                .foregroundStyle(.secondary)
            if data.workspace.ai_mode != "off" {
                Text("AI Mode: \(data.workspace.ai_mode)")
                    .font(DSFont.caption)
                    .foregroundStyle(.tertiary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - Stats strip

    private var statsStrip: some View {
        HStack(spacing: 16) {
            statCard(label: "Entries",
                     value: "\(data.stats?.total_entries ?? data.entries.count)",
                     icon: "list.bullet.rectangle")
            statCard(label: "Pipelines",
                     value: "\(data.stats?.pipeline_count ?? data.workspace.pipelines.count)",
                     icon: "square.stack.3d.up")
            if let images = data.stats?.type_counts?["image"] {
                statCard(label: "Screenshots",
                         value: "\(images)",
                         icon: "camera")
            }
            if let topCat = data.stats?.ai_categories?.max(by: { $0.value < $1.value }) {
                statCard(label: "Top Category",
                         value: topCat.key,
                         icon: "sparkles")
            }
        }
    }

    private func statCard(label: String, value: String, icon: String) -> some View {
        VStack(spacing: 6) {
            Image(systemName: icon)
                .font(.system(size: 18))
                .foregroundStyle(DSColor.accent)
            Text(value)
                .font(.system(size: 20, weight: .bold))
            Text(label)
                .font(DSFont.caption)
                .foregroundStyle(.secondary)
        }
        .frame(minWidth: 140, maxWidth: .infinity)
        .padding(.vertical, 16)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(.quaternary)
        )
    }

    // MARK: - Timeline waterfall

    private var timelineSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Text("Timeline")
                    .font(.system(size: 20, weight: .semibold))
                // When pipelines are still streaming in, show a
                // compact spinner so the user knows more rows are
                // coming rather than thinking the view is empty.
                if !vm.loadingPipelines.isEmpty {
                    HStack(spacing: 4) {
                        ProgressView()
                            .scaleEffect(0.5)
                            .frame(width: 12, height: 12)
                        Text("loading \(vm.loadingPipelines.count) pipeline\(vm.loadingPipelines.count == 1 ? "" : "s")…")
                            .font(DSFont.caption)
                            .foregroundStyle(.tertiary)
                    }
                }
            }
            ForEach(data.entries) { entry in
                entryRow(entry)
            }
        }
    }

    private func entryRow(_ entry: ReviewData.Entry) -> some View {
        HStack(alignment: .top, spacing: 12) {
            // Timestamp tick
            Text(shortTime(entry.timestamp))
                .font(DSFont.mono)
                .foregroundStyle(.secondary)
                .frame(width: 70, alignment: .trailing)

            // Color bar for pipeline
            RoundedRectangle(cornerRadius: 2)
                .fill(pipelineColor(entry.pipeline))
                .frame(width: 4)

            // Card
            VStack(alignment: .leading, spacing: 6) {
                // Type + pipeline label + action buttons
                HStack(spacing: 4) {
                    Image(systemName: typeIcon(entry.input_type))
                        .foregroundStyle(.secondary)
                        .font(.system(size: 12))
                    Text(entry.pipeline)
                        .font(DSFont.caption)
                        .foregroundStyle(.secondary)
                    Spacer()
                    // Edit button
                    Button {
                        vm.startEditing(entry)
                    } label: {
                        Image(systemName: "pencil")
                            .font(.system(size: 11))
                            .foregroundStyle(.secondary)
                    }
                    .buttonStyle(.borderless)
                    .help("Edit description")
                    // Delete button
                    Button {
                        Task { await vm.deleteEntry(entry) }
                    } label: {
                        Image(systemName: "trash")
                            .font(.system(size: 11))
                            .foregroundStyle(.secondary)
                    }
                    .buttonStyle(.borderless)
                    .help("Delete entry")
                }

                // Description — editable or static
                if vm.editingEntry?.id == entry.id {
                    // Inline edit mode
                    VStack(alignment: .leading, spacing: 6) {
                        TextField("Description", text: $vm.editText)
                            .textFieldStyle(.roundedBorder)
                            .font(DSFont.body)
                        HStack(spacing: 8) {
                            Button("Save") {
                                Task { await vm.saveEditing() }
                            }
                            .buttonStyle(.borderedProminent)
                            .controlSize(.small)
                            Button("Cancel") {
                                vm.cancelEditing()
                            }
                            .controlSize(.small)
                        }
                    }
                } else if !entry.description.isEmpty {
                    Text(entry.description)
                        .font(DSFont.body)
                }

                // Type-specific content rendering
                switch entry.input_type {
                case "url":
                    if let url = URL(string: entry.input_content) {
                        Link(destination: url) {
                            HStack(spacing: 4) {
                                Image(systemName: "arrow.up.right.square")
                                    .font(.system(size: 11))
                                Text(entry.input_content)
                                    .font(DSFont.caption)
                                    .lineLimit(2)
                                    .truncationMode(.middle)
                            }
                            .foregroundStyle(DSColor.accent)
                        }
                    } else {
                        Text(entry.input_content)
                            .font(DSFont.caption)
                            .foregroundStyle(.secondary)
                    }

                case "text":
                    if !entry.input_content.isEmpty,
                       entry.input_content != entry.description {
                        Text(entry.input_content.prefix(300))
                            .font(DSFont.caption)
                            .foregroundStyle(.secondary)
                            .padding(8)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(
                                RoundedRectangle(cornerRadius: 6)
                                    .fill(Color.secondary.opacity(0.06))
                            )
                    }

                case "image":
                    // Async on a background thread + shared NSCache.
                    // Replaces the previous main-thread synchronous
                    // ``NSImage(contentsOfFile:)`` that blocked the UI
                    // when a workspace had many screenshots.
                    LocalImageView(
                        url: URL(fileURLWithPath: entry.input_content),
                        maxWidth: nil,
                        maxHeight: 160,
                        cornerRadius: 8
                    )

                default:
                    EmptyView()
                }

                // AI analysis
                if let ai = entry.ai_description, !ai.isEmpty {
                    HStack(alignment: .top, spacing: 4) {
                        Image(systemName: "sparkles")
                            .font(.system(size: 10))
                            .foregroundStyle(.purple)
                        Text(ai)
                            .font(DSFont.caption)
                            .foregroundStyle(.tertiary)
                            .italic()
                    }
                }

                // AI elements tags
                if let elements = entry.ai_elements, !elements.isEmpty {
                    FlowLayout(spacing: 4) {
                        ForEach(elements, id: \.self) { tag in
                            Text(tag)
                                .font(.system(size: 10))
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(
                                    Capsule().fill(Color.secondary.opacity(0.1))
                                )
                        }
                    }
                }
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(.background)
                    .shadow(color: .black.opacity(0.06), radius: 4, y: 2)
            )
        }
        .padding(.vertical, 2)
    }

    // MARK: - Pipeline summaries

    @ViewBuilder
    private var pipelineSummariesSection: some View {
        if let summaries = data.pipeline_summaries, !summaries.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                Text("Pipelines")
                    .font(.system(size: 20, weight: .semibold))
                ForEach(summaries, id: \.name) { ps in
                    HStack(alignment: .top, spacing: 10) {
                        RoundedRectangle(cornerRadius: 2)
                            .fill(pipelineColor(ps.name))
                            .frame(width: 4, height: 40)
                        VStack(alignment: .leading, spacing: 2) {
                            HStack {
                                Text(ps.name)
                                    .font(.system(size: 14, weight: .medium))
                                Spacer()
                                Text("\(ps.entry_count) entries")
                                    .font(DSFont.caption)
                                    .foregroundStyle(.secondary)
                            }
                            if let desc = ps.description, !desc.isEmpty {
                                Text(desc)
                                    .font(DSFont.caption)
                                    .foregroundStyle(.secondary)
                            }
                            if let goal = ps.goal, !goal.isEmpty {
                                Text("🎯 \(goal)")
                                    .font(DSFont.caption)
                                    .foregroundStyle(.tertiary)
                            }
                        }
                    }
                    .padding(10)
                    .background(
                        RoundedRectangle(cornerRadius: 8, style: .continuous)
                            .fill(.quaternary)
                    )
                }
            }
        }
    }

    // MARK: - AI Summary

    private func aiSummarySection(_ s: ReviewData.DailySummary) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("AI Daily Summary", systemImage: "sparkles")
                .font(.system(size: 20, weight: .semibold))
            if let overall = s.overall_summary {
                Text(overall)
                    .font(DSFont.body)
            }
            if let ps = s.pipeline_summaries {
                ForEach(ps.sorted(by: { $0.key < $1.key }), id: \.key) { key, value in
                    HStack(alignment: .top) {
                        Text("**\(key)**:")
                            .font(DSFont.caption)
                        Text(value)
                            .font(DSFont.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            if let gen = s.generated_at, let model = s.model {
                Text("Generated at \(gen) using \(model)")
                    .font(.system(size: 10))
                    .foregroundStyle(.quaternary)
            }
        }
        .padding(16)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(DSColor.accent.opacity(0.06))
        )
    }

    // MARK: - Footer

    private var footerButtons: some View {
        HStack {
            Spacer()
            Button("Close") { onClose() }
                .keyboardShortcut(.cancelAction)
        }
    }

    // MARK: - Helpers

    private var displayDate: String {
        // Try parsing ISO date for a nicer display
        let raw = data.workspace.created_at
        let df = ISO8601DateFormatter()
        df.formatOptions = [.withFullDate, .withTime, .withColonSeparatorInTime]
        if let date = df.date(from: raw) {
            let pretty = DateFormatter()
            pretty.dateStyle = .full
            return pretty.string(from: date)
        }
        return raw
    }

    private func shortTime(_ ts: String) -> String {
        if let t = ts.split(separator: "T").last {
            return String(t.prefix(8))
        }
        return ts
    }

    private func typeIcon(_ type: String) -> String {
        switch type {
        case "image": return "photo"
        case "url": return "link"
        case "text": return "doc.text"
        default: return "questionmark"
        }
    }

    private let pipelineColors: [Color] = [
        DSColor.accent, .orange, .purple, .green, .pink, .cyan
    ]

    private func pipelineColor(_ name: String) -> Color {
        let idx = abs(name.hashValue) % pipelineColors.count
        return pipelineColors[idx]
    }
}

// MARK: - FlowLayout (simple horizontal wrap) ----------------------------

/// A simple flow layout that wraps children horizontally.
private struct FlowLayout: Layout {
    var spacing: CGFloat = 4

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let maxWidth = proposal.width ?? .infinity
        var x: CGFloat = 0
        var y: CGFloat = 0
        var rowHeight: CGFloat = 0

        for sub in subviews {
            let size = sub.sizeThatFits(.unspecified)
            if x + size.width > maxWidth && x > 0 {
                x = 0
                y += rowHeight + spacing
                rowHeight = 0
            }
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
        return CGSize(width: maxWidth, height: y + rowHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX
        var y = bounds.minY
        var rowHeight: CGFloat = 0

        for sub in subviews {
            let size = sub.sizeThatFits(.unspecified)
            if x + size.width > bounds.maxX && x > bounds.minX {
                x = bounds.minX
                y += rowHeight + spacing
                rowHeight = 0
            }
            sub.place(at: CGPoint(x: x, y: y), proposal: .unspecified)
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
    }
}

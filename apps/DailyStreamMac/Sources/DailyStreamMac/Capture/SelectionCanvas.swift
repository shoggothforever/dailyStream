// SelectionCanvas.swift
// Custom NSView that draws a screen-capture selection overlay:
//  * Semi-transparent dark + blurred background
//  * Clear "punch-through" for the selected rectangle
//  * Double border (inner white + outer dark)
//  * 8×8 corner handles
//  * Size capsule label (centered below selection)
//
// Mouse events: click-drag to define selection, Esc to cancel.
// On mouse-up with a valid rectangle (>5 px each axis), the result
// is delivered to a callback.

import AppKit
import CoreImage
import QuartzCore

final class SelectionCanvas: NSView {
    // MARK: - Result callback
    var onComplete: ((NSRect?) -> Void)?

    // MARK: - State
    private var origin: NSPoint?
    private var current: NSPoint?
    private var isDragging = false

    // MARK: - Visual constants
    private let overlayColor = NSColor(calibratedRed: 0, green: 0, blue: 0, alpha: 0.35)
    private let borderColorInner = NSColor.white
    private let borderColorOuter = NSColor(calibratedRed: 0, green: 0, blue: 0, alpha: 0.3)
    private let handleSize: CGFloat = 8
    private let capsuleFont = NSFont.systemFont(ofSize: 12, weight: .medium)

    // MARK: - Setup

    override var acceptsFirstResponder: Bool { true }
    override var canBecomeKeyView: Bool { true }
    override var isFlipped: Bool { false }

    // MARK: - Mouse

    override func mouseDown(with event: NSEvent) {
        origin = convert(event.locationInWindow, from: nil)
        current = origin
        isDragging = true
        needsDisplay = true
    }

    override func mouseDragged(with event: NSEvent) {
        current = convert(event.locationInWindow, from: nil)
        needsDisplay = true
    }

    override func mouseUp(with event: NSEvent) {
        current = convert(event.locationInWindow, from: nil)
        isDragging = false
        if let rect = selectionRect, rect.width > 5, rect.height > 5 {
            onComplete?(rect)
        } else {
            needsDisplay = true
        }
    }

    // MARK: - Keyboard

    override func keyDown(with event: NSEvent) {
        switch event.keyCode {
        case 53: // Esc
            onComplete?(nil)
        case 49: // Space — could be used for "move selection" in the future
            break
        default:
            super.keyDown(with: event)
        }
    }

    // MARK: - Drawing

    override func draw(_ dirtyRect: NSRect) {
        // 1. Dark overlay covering the entire view.
        overlayColor.set()
        NSBezierPath.fill(bounds)

        guard let sel = selectionRect, sel.width > 2, sel.height > 2 else { return }

        // 2. Clear the selected area ("punch-through").
        NSColor.clear.set()
        NSBezierPath.fill(sel)

        // 3. Outer border (dark, slightly wider).
        borderColorOuter.set()
        let outerPath = NSBezierPath(rect: sel.insetBy(dx: -0.75, dy: -0.75))
        outerPath.lineWidth = 0.5
        outerPath.stroke()

        // 4. Inner border (white).
        borderColorInner.set()
        let innerPath = NSBezierPath(rect: sel)
        innerPath.lineWidth = 1.5
        innerPath.stroke()

        // 5. Corner handles.
        drawHandle(at: NSPoint(x: sel.minX, y: sel.minY))
        drawHandle(at: NSPoint(x: sel.maxX, y: sel.minY))
        drawHandle(at: NSPoint(x: sel.minX, y: sel.maxY))
        drawHandle(at: NSPoint(x: sel.maxX, y: sel.maxY))

        // 6. Size capsule.
        drawSizeCapsule(for: sel)
    }

    // MARK: - Helpers

    private var selectionRect: NSRect? {
        guard let o = origin, let c = current else { return nil }
        return NSRect(
            x: min(o.x, c.x),
            y: min(o.y, c.y),
            width: abs(c.x - o.x),
            height: abs(c.y - o.y)
        )
    }

    private func drawHandle(at center: NSPoint) {
        let half = handleSize / 2
        let rect = NSRect(
            x: center.x - half,
            y: center.y - half,
            width: handleSize,
            height: handleSize
        )
        // Shadow
        let shadow = NSShadow()
        shadow.shadowColor = NSColor.black.withAlphaComponent(0.4)
        shadow.shadowOffset = NSSize(width: 0, height: -1)
        shadow.shadowBlurRadius = 2
        shadow.set()

        NSColor.white.set()
        NSBezierPath.fill(rect)
        NSShadow().set()  // reset shadow
    }

    private func drawSizeCapsule(for rect: NSRect) {
        let w = Int(rect.width)
        let h = Int(rect.height)
        let text = "\(w) × \(h)" as NSString
        let attrs: [NSAttributedString.Key: Any] = [
            .font: capsuleFont,
            .foregroundColor: NSColor.white,
        ]
        let textSize = text.size(withAttributes: attrs)

        let capsuleWidth = textSize.width + 16
        let capsuleHeight = textSize.height + 8
        let capsuleX = rect.midX - capsuleWidth / 2
        // Place below the selection; if too close to bottom, place above.
        var capsuleY = rect.minY - capsuleHeight - 8
        if capsuleY < 4 {
            capsuleY = rect.maxY + 8
        }

        let capsuleRect = NSRect(
            x: capsuleX, y: capsuleY,
            width: capsuleWidth, height: capsuleHeight
        )

        // Background pill
        let pill = NSBezierPath(
            roundedRect: capsuleRect,
            xRadius: capsuleHeight / 2,
            yRadius: capsuleHeight / 2
        )
        NSColor(calibratedRed: 0, green: 0, blue: 0, alpha: 0.75).set()
        pill.fill()

        // Text
        let textOrigin = NSPoint(
            x: capsuleRect.midX - textSize.width / 2,
            y: capsuleRect.midY - textSize.height / 2
        )
        text.draw(at: textOrigin, withAttributes: attrs)
    }
}

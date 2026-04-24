// MagnifierView.swift
// A 64pt circular magnifier that follows the mouse cursor during
// screenshot selection.  Shows a 4× zoomed view of the screen under
// the cursor with a crosshair overlay.
//
// Implementation
// --------------
// We use CGWindowListCreateImage to grab a small region of the screen
// around the mouse and render it at 4× inside a circular CALayer.
// The magnifier is repositioned on every mouseMoved / mouseDragged
// event forwarded by `SelectionCanvas`.

import AppKit
import Quartz

final class MagnifierView: NSView {
    private let magnification: CGFloat = 4
    private let diameter: CGFloat = 64
    private let sourceRadius: CGFloat = 8  // pixels around cursor to capture

    override init(frame: NSRect) {
        super.init(frame: NSRect(x: 0, y: 0, width: diameter, height: diameter))
        wantsLayer = true
        layer?.cornerRadius = diameter / 2
        layer?.masksToBounds = true
        layer?.borderWidth = 2
        layer?.borderColor = NSColor.white.withAlphaComponent(0.9).cgColor
        layer?.shadowColor = NSColor.black.cgColor
        layer?.shadowOpacity = 0.4
        layer?.shadowRadius = 6
        layer?.shadowOffset = NSSize(width: 0, height: -2)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError() }

    /// Call on every mouse event to update position + content.
    func track(screenPoint: NSPoint, screen: NSScreen) {
        // Position: offset 20pt up-right from cursor
        let offset: CGFloat = 20
        var pos = NSPoint(x: screenPoint.x + offset,
                          y: screenPoint.y + offset)
        // Flip if too close to right/top edge
        let frame = screen.frame
        if pos.x + diameter > frame.maxX { pos.x = screenPoint.x - offset - diameter }
        if pos.y + diameter > frame.maxY { pos.y = screenPoint.y - offset - diameter }

        self.frame.origin = convert(pos, from: nil)

        // Grab a small region of the screen around the cursor.
        let captureRect = CGRect(
            x: screenPoint.x - sourceRadius,
            y: screenPoint.y - sourceRadius,
            width: sourceRadius * 2,
            height: sourceRadius * 2
        )
        // Convert to global display coords (y-flipped).
        let displayRect = CGRect(
            x: captureRect.origin.x,
            y: screen.frame.height - captureRect.maxY + screen.frame.origin.y,
            width: captureRect.width,
            height: captureRect.height
        )
        if let cgImage = CGWindowListCreateImage(
            displayRect,
            .optionOnScreenOnly,
            kCGNullWindowID,
            [.bestResolution]
        ) {
            layer?.contents = cgImage
            layer?.contentsGravity = .resizeAspectFill
        }

        isHidden = false
    }

    /// Draw crosshair overlay.
    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        let ctx = NSGraphicsContext.current?.cgContext
        ctx?.setStrokeColor(NSColor.white.withAlphaComponent(0.6).cgColor)
        ctx?.setLineWidth(0.5)
        // Horizontal
        ctx?.move(to: CGPoint(x: 0, y: bounds.midY))
        ctx?.addLine(to: CGPoint(x: bounds.width, y: bounds.midY))
        // Vertical
        ctx?.move(to: CGPoint(x: bounds.midX, y: 0))
        ctx?.addLine(to: CGPoint(x: bounds.midX, y: bounds.height))
        ctx?.strokePath()
    }
}

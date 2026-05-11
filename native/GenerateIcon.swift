import AppKit
import Foundation

struct IconImage {
    let name: String
    let pixels: Int
}

let images = [
    IconImage(name: "icon_16x16.png", pixels: 16),
    IconImage(name: "icon_16x16@2x.png", pixels: 32),
    IconImage(name: "icon_32x32.png", pixels: 32),
    IconImage(name: "icon_32x32@2x.png", pixels: 64),
    IconImage(name: "icon_128x128.png", pixels: 128),
    IconImage(name: "icon_128x128@2x.png", pixels: 256),
    IconImage(name: "icon_256x256.png", pixels: 256),
    IconImage(name: "icon_256x256@2x.png", pixels: 512),
    IconImage(name: "icon_512x512.png", pixels: 512),
    IconImage(name: "icon_512x512@2x.png", pixels: 1024),
]

guard CommandLine.arguments.count == 2 else {
    fputs("usage: GenerateIcon.swift <iconset-dir>\n", stderr)
    exit(2)
}

let outputDir = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: outputDir, withIntermediateDirectories: true)

let paragraph = NSMutableParagraphStyle()
paragraph.alignment = .center

for image in images {
    let size = NSSize(width: image.pixels, height: image.pixels)
    let nsImage = NSImage(size: size)
    nsImage.lockFocus()

    let bounds = NSRect(origin: .zero, size: size)
    NSColor.white.setFill()
    bounds.fill()

    let symbolSize = size.width * 0.64
    let symbolAttributes: [NSAttributedString.Key: Any] = [
        .font: NSFont.systemFont(ofSize: symbolSize, weight: .regular),
        .paragraphStyle: paragraph,
    ]
    let symbolRect = NSRect(
        x: 0,
        y: size.height * 0.18,
        width: size.width,
        height: size.height * 0.68
    )
    ("👾" as NSString).draw(in: symbolRect, withAttributes: symbolAttributes)

    nsImage.unlockFocus()

    guard
        let tiff = nsImage.tiffRepresentation,
        let bitmap = NSBitmapImageRep(data: tiff),
        let png = bitmap.representation(using: .png, properties: [:])
    else {
        fputs("failed to render \(image.name)\n", stderr)
        exit(1)
    }

    try png.write(to: outputDir.appendingPathComponent(image.name))
}

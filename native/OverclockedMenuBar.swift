import AppKit
import Foundation

private struct StyledMenuLine {
    let text: String
    let indentationLevel: Int
    let attributes: [NSAttributedString.Key: Any]
    let image: NSImage?
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private var timer: Timer?
    private var lastError: String?

    private func log(_ message: String) {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let logURL = home.appendingPathComponent(".overclocked/native-menubar.log")
        let line = "[\(ISO8601DateFormatter().string(from: Date()))] \(message)\n"

        do {
            try FileManager.default.createDirectory(
                at: logURL.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
            if FileManager.default.fileExists(atPath: logURL.path) {
                let handle = try FileHandle(forWritingTo: logURL)
                defer { try? handle.close() }
                try handle.seekToEnd()
                handle.write(Data(line.utf8))
            } else {
                try line.write(to: logURL, atomically: true, encoding: .utf8)
            }
        } catch {
            // Logging must never break the menu bar app.
        }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        installBaseMenu()
        refresh(nil)

        timer = Timer.scheduledTimer(
            timeInterval: 5.0,
            target: self,
            selector: #selector(refresh(_:)),
            userInfo: nil,
            repeats: true
        )
    }

    private func installBaseMenu() {
        let menu = NSMenu()
        menu.autoenablesItems = false
        menu.addItem(NSMenuItem(title: "Refresh", action: #selector(refresh(_:)), keyEquivalent: "r"))
        menu.addItem(NSMenuItem.separator())
        menu.addItem(NSMenuItem(title: "Quit Overclocked", action: #selector(quit(_:)), keyEquivalent: "q"))
        statusItem.menu = menu
    }

    private func overclockedExecutable() -> URL? {
        if let override = ProcessInfo.processInfo.environment["OVERCLOCKED_BIN"], !override.isEmpty {
            log("Using OVERCLOCKED_BIN override: \(override)")
            return URL(fileURLWithPath: override)
        }

        if let bundledRoot = Bundle.main.object(forInfoDictionaryKey: "OverclockedRepoRoot") as? String,
           !bundledRoot.isEmpty {
            let candidate = URL(fileURLWithPath: bundledRoot).appendingPathComponent(".venv/bin/overclocked")
            if FileManager.default.isExecutableFile(atPath: candidate.path) {
                log("Using bundled repo root: \(candidate.path)")
                return candidate
            }
            log("Bundled repo root did not resolve executable: \(candidate.path)")
        }

        let execURL = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath()
        var searchRoot = execURL.deletingLastPathComponent()
        for _ in 0..<6 {
            let candidate = searchRoot.appendingPathComponent(".venv/bin/overclocked")
            if FileManager.default.isExecutableFile(atPath: candidate.path) {
                log("Found executable by upward search: \(candidate.path)")
                return candidate
            }
            searchRoot.deleteLastPathComponent()
        }

        log("Failed to resolve overclocked executable from bundle")
        return nil
    }

    @objc private func refresh(_ sender: Any?) {
        guard let output = renderOnce() else {
            statusItem.button?.title = "👾 !"
            rebuildMenu(lines: ["---", lastError ?? "Unable to run overclocked --once"])
            return
        }

        let lines = output.split(separator: "\n", omittingEmptySubsequences: false).map(String.init)
        statusItem.button?.title = lines.first?.isEmpty == false ? lines[0] : "👾 ?"
        rebuildMenu(lines: Array(lines.dropFirst()))
    }

    private func renderOnce() -> String? {
        if let executable = overclockedExecutable() {
            return run(command: executable, arguments: ["--once"])
        }

        let env = URL(fileURLWithPath: "/usr/bin/env")
        return run(command: env, arguments: ["overclocked", "--once"])
    }

    private func run(command: URL, arguments: [String]) -> String? {
        let process = Process()
        process.executableURL = command
        process.arguments = arguments

        let stdout = Pipe()
        let stderr = Pipe()
        process.standardOutput = stdout
        process.standardError = stderr

        do {
            try process.run()
            process.waitUntilExit()
        } catch {
            lastError = error.localizedDescription
            log("Process launch failed: \(command.path) \(arguments.joined(separator: " ")) :: \(error.localizedDescription)")
            return nil
        }

        let stderrData = stderr.fileHandleForReading.readDataToEndOfFile()
        let stderrText = String(data: stderrData, encoding: .utf8)?
            .trimmingCharacters(in: .whitespacesAndNewlines)

        guard process.terminationStatus == 0 else {
            lastError = stderrText?.isEmpty == false
                ? stderrText
                : "overclocked exited with status \(process.terminationStatus)"
            log("Process exited non-zero: \(command.path) \(arguments.joined(separator: " ")) :: \(lastError ?? "unknown error")")
            return nil
        }

        let data = stdout.fileHandleForReading.readDataToEndOfFile()
        lastError = nil
        log("Process succeeded: \(command.path) \(arguments.joined(separator: " "))")
        return String(data: data, encoding: .utf8)
    }

    private func rebuildMenu(lines: [String]) {
        guard let menu = statusItem.menu else { return }

        while menu.items.count > 2 {
            menu.removeItem(at: 1)
        }

        let insertIndex = 1
        for rawLine in lines.prefix(40).reversed() {
            if rawLine == "---" {
                menu.insertItem(.separator(), at: insertIndex)
                continue
            }

            guard let styledLine = styledMenuLine(for: rawLine) else {
                continue
            }

            let item = NSMenuItem(title: styledLine.text, action: nil, keyEquivalent: "")
            item.isEnabled = false
            item.indentationLevel = styledLine.indentationLevel
            item.attributedTitle = NSAttributedString(
                string: styledLine.text,
                attributes: styledLine.attributes
            )
            item.image = styledLine.image
            menu.insertItem(item, at: insertIndex)
        }
    }

    private func styledMenuLine(for swiftBarLine: String) -> StyledMenuLine? {
        let parts = swiftBarLine.split(separator: "|", maxSplits: 1, omittingEmptySubsequences: false)
        let rawText = parts.first.map(String.init) ?? ""
        let indentationLevel = min(15, rawText.prefix { $0 == " " }.count / 2)
        let text = rawText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else {
            return nil
        }

        let params = parts.count > 1 ? parseParams(String(parts[1])) : [:]
        let font = menuFont(from: params)
        let color = menuColor(from: params) ?? NSColor.labelColor

        var attributes: [NSAttributedString.Key: Any] = [
            .font: font,
            .foregroundColor: color,
        ]

        let paragraph = NSMutableParagraphStyle()
        paragraph.lineBreakMode = .byTruncatingTail
        attributes[.paragraphStyle] = paragraph

        return StyledMenuLine(
            text: text,
            indentationLevel: indentationLevel,
            attributes: attributes,
            image: menuImage(from: params)
        )
    }

    private func parseParams(_ raw: String) -> [String: String] {
        raw
            .split(separator: " ", omittingEmptySubsequences: true)
            .reduce(into: [String: String]()) { result, token in
                let parts = token.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
                guard parts.count == 2 else { return }
                result[String(parts[0])] = String(parts[1])
            }
    }

    private func menuFont(from params: [String: String]) -> NSFont {
        let size = CGFloat(Double(params["size"] ?? "") ?? 13.0)

        if let fontName = params["font"], let font = NSFont(name: fontName, size: size) {
            return font
        }

        if params["font"] == "Menlo", let font = NSFont(name: "Menlo", size: size) {
            return font
        }

        return NSFont.systemFont(ofSize: size)
    }

    private func menuColor(from params: [String: String]) -> NSColor? {
        guard let hex = params["color"] else {
            return nil
        }

        let normalized = hex.trimmingCharacters(in: CharacterSet(charactersIn: "#"))
        guard normalized.count == 6, let value = Int(normalized, radix: 16) else {
            return nil
        }

        return NSColor(
            calibratedRed: CGFloat((value >> 16) & 0xFF) / 255.0,
            green: CGFloat((value >> 8) & 0xFF) / 255.0,
            blue: CGFloat(value & 0xFF) / 255.0,
            alpha: 1.0
        )
    }

    private func menuImage(from params: [String: String]) -> NSImage? {
        guard let symbol = params["sfimage"] else {
            return nil
        }

        let image = NSImage(systemSymbolName: symbol, accessibilityDescription: nil)
        image?.isTemplate = false
        return image
    }

    @objc private func quit(_ sender: Any?) {
        NSApp.terminate(nil)
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()

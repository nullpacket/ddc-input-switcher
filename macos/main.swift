// Monitor Input Switcher — macOS menu bar app.
//
// Switches the DDC/CI input source of external displays on Apple Silicon Macs by
// shelling out to m1ddc (https://github.com/waydabber/m1ddc), the same way the
// Linux version of this tool shells out to ddcutil.
//
// Build with ./build.sh — see README.md.

import AppKit
import Foundation

// MARK: - Configuration

/// VCP feature 0x60 values, in the decimal form m1ddc expects.
/// 15 = 0x0f DisplayPort, 17 = 0x11 HDMI, 27 = 0x1b USB-C (Dell's vendor code).
struct InputSource {
    let code: Int
    let name: String
    let role: String?

    /// "DisplayPort (Linux)" — what appears in the menu.
    var label: String {
        if let role = role { return "\(name) (\(role))" }
        return name
    }
}

let inputSources = [
    InputSource(code: 15, name: "DisplayPort", role: "Linux"),
    InputSource(code: 27, name: "USB-C", role: "this Mac"),
    InputSource(code: 17, name: "HDMI", role: nil),
]

/// Where Homebrew puts m1ddc on Apple Silicon and Intel, plus a manual build.
let m1ddcCandidates = [
    "/opt/homebrew/bin/m1ddc",
    "/usr/local/bin/m1ddc",
    "\(NSHomeDirectory())/bin/m1ddc",
]

// MARK: - Model

final class DisplayInfo {
    let number: Int
    let productName: String
    var serial: String = ""
    var currentInput: Int?

    init(number: Int, productName: String) {
        self.number = number
        self.productName = productName
    }

    /// "DELL P2723QE (D5GPRS3)" — serial matters because both panels are the
    /// same model and the product name alone cannot tell them apart.
    var title: String {
        if serial.isEmpty { return productName }
        return "\(productName) (\(serial))"
    }

    var currentInputLabel: String {
        guard let current = currentInput else { return "unknown" }
        for source in inputSources where source.code == current {
            return source.name
        }
        return "input \(current)"
    }
}

/// Carried on each NSMenuItem so the action knows what to switch.
final class SwitchRequest: NSObject {
    let displays: [DisplayInfo]
    let code: Int

    init(displays: [DisplayInfo], code: Int) {
        self.displays = displays
        self.code = code
    }
}

// MARK: - m1ddc plumbing

func locateM1ddc() -> String? {
    let fileManager = FileManager.default
    for path in m1ddcCandidates where fileManager.isExecutableFile(atPath: path) {
        return path
    }
    // Fall back to PATH, which covers unusual install locations.
    let which = Process()
    which.executableURL = URL(fileURLWithPath: "/usr/bin/env")
    which.arguments = ["which", "m1ddc"]
    let pipe = Pipe()
    which.standardOutput = pipe
    which.standardError = Pipe()
    do {
        try which.run()
    } catch {
        return nil
    }
    let data = pipe.fileHandleForReading.readDataToEndOfFile()
    which.waitUntilExit()
    let found = String(data: data, encoding: .utf8)?
        .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    return found.isEmpty ? nil : found
}

struct CommandResult {
    let output: String
    let succeeded: Bool
}

func runM1ddc(_ binary: String, _ arguments: [String]) -> CommandResult {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: binary)
    process.arguments = arguments

    let pipe = Pipe()
    process.standardOutput = pipe
    process.standardError = pipe

    do {
        try process.run()
    } catch {
        return CommandResult(output: "failed to launch m1ddc: \(error)", succeeded: false)
    }

    // Read before waiting: a full pipe buffer would otherwise deadlock the child.
    let data = pipe.fileHandleForReading.readDataToEndOfFile()
    process.waitUntilExit()

    let text = String(data: data, encoding: .utf8) ?? ""
    // m1ddc reports "No external display found" on stdout with a zero exit code,
    // so the exit status alone is not enough to call it a success.
    let complained = text.localizedCaseInsensitiveContains("No external display found")
    return CommandResult(output: text, succeeded: process.terminationStatus == 0 && !complained)
}

/// Parses `m1ddc display list detailed`, whose format is:
///
///     [1] DELL P2723QE (2E8B0000-...)
///      - Product name:  DELL P2723QE
///      - AN Serial:     D5GPRS3
///      ...
func parseDisplayList(_ text: String) -> [DisplayInfo] {
    var displays: [DisplayInfo] = []

    for rawLine in text.components(separatedBy: .newlines) {
        let line = rawLine.trimmingCharacters(in: .whitespaces)
        if line.isEmpty { continue }

        if line.hasPrefix("[") {
            guard let closeBracket = line.firstIndex(of: "]") else { continue }
            let numberText = String(line[line.index(after: line.startIndex)..<closeBracket])
            guard let number = Int(numberText) else { continue }

            var remainder = String(line[line.index(after: closeBracket)...])
                .trimmingCharacters(in: .whitespaces)
            // Strip the trailing "(system-uuid)" to leave just the product name.
            if remainder.hasSuffix(")"), let parenStart = remainder.range(of: " (", options: .backwards) {
                remainder = String(remainder[remainder.startIndex..<parenStart.lowerBound])
            }
            let name = remainder.isEmpty ? "Display \(number)" : remainder
            displays.append(DisplayInfo(number: number, productName: name))
            continue
        }

        // Detail lines look like "- AN Serial:     D5GPRS3" once trimmed.
        if line.hasPrefix("-"), let current = displays.last {
            let body = String(line.dropFirst()).trimmingCharacters(in: .whitespaces)
            guard let colon = body.firstIndex(of: ":") else { continue }
            let key = String(body[body.startIndex..<colon]).trimmingCharacters(in: .whitespaces)
            let value = String(body[body.index(after: colon)...]).trimmingCharacters(in: .whitespaces)
            if key.caseInsensitiveCompare("AN Serial") == .orderedSame && !value.isEmpty {
                current.serial = value
            }
        }
    }

    return displays
}

// MARK: - App

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var statusItem: NSStatusItem!
    private let menu = NSMenu()
    private var displays: [DisplayInfo] = []
    private var m1ddcPath: String?
    private var statusLine = "Loading…"
    private var busy = false

    private let work = DispatchQueue(label: "ddc.switcher.m1ddc")

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem.button {
            button.image = NSImage(systemSymbolName: "display",
                                   accessibilityDescription: "Monitor Input Switcher")
            button.image?.isTemplate = true
            if button.image == nil { button.title = "⧉" }
        }

        menu.delegate = self
        statusItem.menu = menu

        m1ddcPath = locateM1ddc()
        rebuildMenu()
        refresh()
    }

    // Refresh once the menu closes, so the next open shows fresh state without
    // mutating a menu that is currently on screen.
    func menuDidClose(_ menu: NSMenu) {
        guard !busy else { return }
        refresh()
    }

    // MARK: Actions

    private func refresh() {
        guard let binary = m1ddcPath else {
            statusLine = "m1ddc not found"
            rebuildMenu()
            return
        }
        guard !busy else { return }
        busy = true

        work.async { [weak self] in
            let listing = runM1ddc(binary, ["display", "list", "detailed"])
            var found: [DisplayInfo] = []
            if listing.succeeded {
                found = parseDisplayList(listing.output)
                for display in found {
                    let reading = runM1ddc(binary, ["display", "\(display.number)", "get", "input"])
                    if reading.succeeded {
                        let trimmed = reading.output.trimmingCharacters(in: .whitespacesAndNewlines)
                        display.currentInput = Int(trimmed)
                    }
                }
            }

            let summary: String
            if !listing.succeeded {
                summary = listing.output
                    .trimmingCharacters(in: .whitespacesAndNewlines)
                    .components(separatedBy: .newlines).first ?? "m1ddc failed"
            } else if found.isEmpty {
                summary = "No external displays found"
            } else {
                summary = "\(found.count) display(s)"
            }

            DispatchQueue.main.async {
                guard let self = self else { return }
                self.displays = found
                self.statusLine = summary
                self.busy = false
                self.rebuildMenu()
            }
        }
    }

    @objc private func switchInput(_ sender: NSMenuItem) {
        guard let binary = m1ddcPath,
              let request = sender.representedObject as? SwitchRequest else { return }
        guard !request.displays.isEmpty else { return }

        busy = true
        statusLine = "Switching…"

        work.async { [weak self] in
            var problems: [String] = []
            for display in request.displays {
                let result = runM1ddc(binary, ["display", "\(display.number)",
                                               "set", "input", "\(request.code)"])
                if result.succeeded {
                    display.currentInput = request.code
                } else {
                    let detail = result.output
                        .trimmingCharacters(in: .whitespacesAndNewlines)
                        .components(separatedBy: .newlines).first ?? "failed"
                    problems.append("\(display.title): \(detail)")
                }
            }

            DispatchQueue.main.async {
                guard let self = self else { return }
                self.busy = false
                self.statusLine = problems.first ?? "Switched"
                self.rebuildMenu()
            }
        }
    }

    @objc private func refreshClicked(_ sender: NSMenuItem) {
        refresh()
    }

    @objc private func quitClicked(_ sender: NSMenuItem) {
        NSApp.terminate(nil)
    }

    // MARK: Menu

    private func rebuildMenu() {
        menu.removeAllItems()

        guard m1ddcPath != nil else {
            let item = NSMenuItem(title: "m1ddc not installed", action: nil, keyEquivalent: "")
            item.isEnabled = false
            menu.addItem(item)
            let hint = NSMenuItem(title: "Install with: brew install m1ddc",
                                  action: nil, keyEquivalent: "")
            hint.isEnabled = false
            menu.addItem(hint)
            menu.addItem(NSMenuItem.separator())
            addQuitItem()
            return
        }

        if displays.isEmpty {
            let item = NSMenuItem(title: statusLine, action: nil, keyEquivalent: "")
            item.isEnabled = false
            menu.addItem(item)
        } else {
            let header = NSMenuItem(title: "Switch all displays to", action: nil, keyEquivalent: "")
            header.isEnabled = false
            menu.addItem(header)

            for source in inputSources {
                let item = NSMenuItem(title: "    \(source.label)",
                                      action: #selector(switchInput(_:)),
                                      keyEquivalent: "")
                item.target = self
                item.representedObject = SwitchRequest(displays: displays, code: source.code)
                menu.addItem(item)
            }

            if displays.count > 1 {
                menu.addItem(NSMenuItem.separator())
                for display in displays {
                    let parent = NSMenuItem(
                        title: "\(display.title) — \(display.currentInputLabel)",
                        action: nil, keyEquivalent: "")
                    let submenu = NSMenu()
                    for source in inputSources {
                        let item = NSMenuItem(title: source.label,
                                              action: #selector(switchInput(_:)),
                                              keyEquivalent: "")
                        item.target = self
                        item.representedObject = SwitchRequest(displays: [display], code: source.code)
                        item.state = (display.currentInput == source.code) ? .on : .off
                        submenu.addItem(item)
                    }
                    parent.submenu = submenu
                    menu.addItem(parent)
                }
            }

            menu.addItem(NSMenuItem.separator())
            let status = NSMenuItem(title: statusLine, action: nil, keyEquivalent: "")
            status.isEnabled = false
            menu.addItem(status)
        }

        menu.addItem(NSMenuItem.separator())
        let refreshItem = NSMenuItem(title: "Refresh displays",
                                     action: #selector(refreshClicked(_:)), keyEquivalent: "r")
        refreshItem.target = self
        menu.addItem(refreshItem)
        addQuitItem()
    }

    private func addQuitItem() {
        let quit = NSMenuItem(title: "Quit", action: #selector(quitClicked(_:)), keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)
    }
}

// MARK: - Entry point

let application = NSApplication.shared
// Held strongly here because NSApplication.delegate is a weak reference.
let appDelegate = AppDelegate()
application.delegate = appDelegate
application.setActivationPolicy(.accessory)  // menu bar only, no Dock icon
application.run()

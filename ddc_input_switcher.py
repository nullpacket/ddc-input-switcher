#!/usr/bin/env python3
"""Switch the active input of DDC/CI-capable monitors, together or individually.

GUI (PyQt6) with a system tray icon, plus a small CLI for hotkey bindings:

    ddc_input_switcher.py                 # open the window
    ddc_input_switcher.py --list          # print each monitor's current input
    ddc_input_switcher.py --set usb-c     # switch every monitor to USB-C
    ddc_input_switcher.py --set hdmi --display D5GPRS3
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

DDCUTIL = shutil.which("ddcutil") or "ddcutil"
CALL_TIMEOUT = 20  # seconds; a wedged i2c bus should not hang the app forever

# VCP feature 0x60 (Input Source). 0x01-0x12 are MCCS-standard; 0x1b/0x1c are the
# vendor codes Dell uses for USB-C (DP alt mode) and are what ddcutil reports as
# "Unrecognized value".
INPUT_NAMES = {
    0x01: "VGA-1",
    0x02: "VGA-2",
    0x03: "DVI-1",
    0x04: "DVI-2",
    0x05: "Composite-1",
    0x06: "Composite-2",
    0x07: "S-Video-1",
    0x08: "S-Video-2",
    0x09: "Tuner-1",
    0x0A: "Tuner-2",
    0x0B: "Tuner-3",
    0x0C: "Component-1",
    0x0D: "Component-2",
    0x0E: "Component-3",
    0x0F: "DisplayPort",
    0x10: "DisplayPort-2",
    0x11: "HDMI",
    0x12: "HDMI-2",
    0x1B: "USB-C",
    0x1C: "USB-C-2",
}

# What the user may type after --set, normalised.
CLI_ALIASES = {
    "dp": 0x0F,
    "displayport": 0x0F,
    "dp1": 0x0F,
    "dp2": 0x10,
    "hdmi": 0x11,
    "hdmi1": 0x11,
    "hdmi2": 0x12,
    "usbc": 0x1B,
    "usb-c": 0x1B,
    "typec": 0x1B,
    "vga": 0x01,
    "dvi": 0x03,
}


class DdcError(RuntimeError):
    pass


def run_ddcutil(args: list[str]) -> str:
    """Run ddcutil and return stdout, raising DdcError on any failure."""
    try:
        proc = subprocess.run(
            [DDCUTIL, *args],
            capture_output=True,
            text=True,
            timeout=CALL_TIMEOUT,
        )
    except FileNotFoundError:
        raise DdcError("ddcutil is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        raise DdcError(f"ddcutil timed out after {CALL_TIMEOUT}s: {' '.join(args)}")
    if proc.returncode != 0:
        detail = (proc.stderr.strip() or proc.stdout.strip() or "no output").splitlines()
        raise DdcError(f"ddcutil {' '.join(args)} failed: {detail[0]}")
    return proc.stdout


def input_label(code: int, ddcutil_name: str = "") -> str:
    """Human name for an input code, preferring our table over ddcutil's."""
    if code in INPUT_NAMES:
        return INPUT_NAMES[code]
    if ddcutil_name and "unrecognized" not in ddcutil_name.lower():
        return ddcutil_name
    return f"Input 0x{code:02x}"


@dataclass
class Display:
    bus: int
    model: str
    serial: str
    connector: str
    inputs: list[tuple[int, str]] = field(default_factory=list)  # (code, label)
    current: int | None = None
    error: str = ""

    @property
    def name(self) -> str:
        return f"{self.model} ({self.serial})" if self.serial else self.model

    @property
    def current_label(self) -> str:
        if self.error:
            return "unavailable"
        if self.current is None:
            return "unknown"
        known = dict(self.inputs)
        return known.get(self.current, input_label(self.current))


def parse_detect(text: str) -> list[Display]:
    """Parse `ddcutil detect --terse` into Display records."""
    displays: list[Display] = []
    bus = connector = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Display "):
            bus, connector = None, None
        elif stripped.startswith("I2C bus:"):
            m = re.search(r"i2c-(\d+)", stripped)
            bus = int(m.group(1)) if m else None
        elif stripped.startswith("DRM connector:"):
            connector = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("Monitor:") and bus is not None:
            # "Monitor:          DEL:DELL P2723QE:D5GPRS3"
            parts = stripped.split(":", 1)[1].strip().split(":")
            model = parts[1].strip() if len(parts) > 1 else "Unknown monitor"
            serial = parts[2].strip() if len(parts) > 2 else ""
            displays.append(
                Display(bus=bus, model=model, serial=serial, connector=connector or "")
            )
            bus = connector = None
    return displays


def parse_input_capabilities(text: str) -> list[tuple[int, str]]:
    """Pull the value list for VCP feature 60 out of `ddcutil capabilities`."""
    inputs: list[tuple[int, str]] = []
    in_feature = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Feature:"):
            in_feature = stripped.startswith("Feature: 60")
            continue
        if not in_feature or stripped == "Values:":
            continue
        m = re.match(r"^([0-9A-Fa-f]{2}):\s*(.*)$", stripped)
        if m:
            code = int(m.group(1), 16)
            inputs.append((code, input_label(code, m.group(2).strip())))
    return inputs


def read_current_input(bus: int) -> int | None:
    """Current value of VCP 60, or None if the monitor did not answer."""
    try:
        out = run_ddcutil(["--bus", str(bus), "getvcp", "60", "--terse"])
    except DdcError:
        return None
    m = re.search(r"x([0-9a-fA-F]{2})\b", out)
    return int(m.group(1), 16) if m else None


def scan_displays() -> list[Display]:
    """Detect monitors and fill in their input capabilities and current input."""
    displays = parse_detect(run_ddcutil(["detect", "--terse"]))
    for d in displays:
        try:
            d.inputs = parse_input_capabilities(
                run_ddcutil(["--bus", str(d.bus), "capabilities"])
            )
        except DdcError as exc:
            d.error = str(exc)
            continue
        d.current = read_current_input(d.bus)
    return displays


def set_input(bus: int, code: int) -> None:
    run_ddcutil(["--bus", str(bus), "setvcp", "60", f"x{code:02x}"])


# --------------------------------------------------------------------------- GUI


class TaskSignals(QObject):
    done = pyqtSignal(object)
    failed = pyqtSignal(str)


class Task(QRunnable):
    """Run a callable off the GUI thread; results come back as signals."""

    def __init__(self, fn, *args):
        super().__init__()
        self._fn, self._args = fn, args
        self.signals = TaskSignals()

    @pyqtSlot()
    def run(self) -> None:
        try:
            self.signals.done.emit(self._fn(*self._args))
        except Exception as exc:  # surfaced in the status line, never fatal
            self.signals.failed.emit(str(exc))


def monitor_icon() -> QIcon:
    icon = QIcon.fromTheme("video-display")
    if not icon.isNull():
        return icon
    pix = QPixmap(64, 64)
    pix.fill(QColor(0, 0, 0, 0))
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QColor("#dcdcdc"))
    p.setBrush(QColor("#3c78d8"))
    p.drawRoundedRect(6, 10, 52, 36, 4, 4)
    p.setBrush(QColor("#dcdcdc"))
    p.drawRect(26, 46, 12, 6)
    p.drawRect(18, 52, 28, 4)
    p.end()
    return QIcon(pix)


class DisplayRow(QFrame):
    """One monitor: its name, its current input, and a button per input."""

    def __init__(self, display: Display, on_switch):
        super().__init__()
        self.display = display
        self.on_switch = on_switch
        self.setFrameShape(QFrame.Shape.StyledPanel)

        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel(f"<b>{display.name}</b>")
        header.addWidget(title)
        header.addStretch()
        self.status = QLabel()
        self.status.setStyleSheet("color: palette(mid);")
        header.addWidget(self.status)
        layout.addLayout(header)

        sub = QLabel(display.connector or f"i2c-{display.bus}")
        sub.setStyleSheet("color: palette(mid); font-size: 11px;")
        layout.addWidget(sub)

        self.buttons: dict[int, QPushButton] = {}
        row = QHBoxLayout()
        row.setSpacing(6)
        if display.error:
            err = QLabel(display.error)
            err.setWordWrap(True)
            err.setStyleSheet("color: #d05050;")
            row.addWidget(err)
        for code, label in display.inputs:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _, c=code: self.on_switch([self.display], c))
            self.buttons[code] = btn
            row.addWidget(btn)
        row.addStretch()
        layout.addLayout(row)

        self.refresh()

    def refresh(self) -> None:
        self.status.setText(f"now: {self.display.current_label}")
        for code, btn in self.buttons.items():
            btn.setChecked(code == self.display.current)


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Monitor Input Switcher")
        self.setWindowIcon(monitor_icon())
        self.displays: list[Display] = []
        self.rows: list[DisplayRow] = []
        self._tasks: list[Task] = []

        # One worker thread: ddcutil calls are serialised so they never contend
        # on the same i2c bus, and they stay in the order the user clicked them.
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(1)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        self.both_label = QLabel("<b>Switch all monitors to</b>")
        root.addWidget(self.both_label)
        self.both_row = QHBoxLayout()
        self.both_row.setSpacing(6)
        root.addLayout(self.both_row)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        root.addWidget(line)

        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(8)
        root.addLayout(self.rows_box)

        root.addStretch()

        footer = QHBoxLayout()
        self.rescan_btn = QPushButton("Rescan")
        self.rescan_btn.clicked.connect(self.rescan)
        footer.addWidget(self.rescan_btn)
        self.refresh_btn = QPushButton("Refresh status")
        self.refresh_btn.clicked.connect(self.refresh_status)
        footer.addWidget(self.refresh_btn)
        footer.addStretch()
        self.status_label = QLabel()
        self.status_label.setStyleSheet("color: palette(mid);")
        footer.addWidget(self.status_label)
        root.addLayout(footer)

        self.tray = self._build_tray()
        self.rescan()

    # -- plumbing ----------------------------------------------------------

    def submit(self, fn, *args, on_done=None) -> None:
        task = Task(fn, *args)
        self._tasks.append(task)

        def finished(result=None):
            if task in self._tasks:
                self._tasks.remove(task)
            if on_done is not None and result is not None:
                on_done(result)
            self._set_busy(bool(self._tasks))

        task.signals.done.connect(finished)
        task.signals.failed.connect(lambda msg: (self.set_status(msg, error=True), finished()))
        self._set_busy(True)
        self.pool.start(task)

    def _set_busy(self, busy: bool) -> None:
        self.rescan_btn.setEnabled(not busy)
        self.refresh_btn.setEnabled(not busy)

    def set_status(self, text: str, error: bool = False) -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet(
            "color: #d05050;" if error else "color: palette(mid);"
        )

    # -- actions -----------------------------------------------------------

    def rescan(self) -> None:
        self.set_status("Scanning…")
        self.submit(scan_displays, on_done=self._apply_scan)

    def refresh_status(self) -> None:
        self.set_status("Reading inputs…")

        def read_all():
            for d in self.displays:
                if not d.error:
                    d.current = read_current_input(d.bus)
            return list(self.displays)

        self.submit(read_all, on_done=lambda _: (self._refresh_rows(), self.set_status("")))

    def switch(self, displays: list[Display], code: int) -> None:
        label = input_label(code)
        targets = list(displays)
        self.set_status(f"Switching to {label}…")

        def do_switch():
            problems = []
            for d in targets:
                try:
                    set_input(d.bus, code)
                    d.current = code
                except DdcError as exc:
                    problems.append(f"{d.name}: {exc}")
            return problems

        def done(problems):
            self._refresh_rows()
            if problems:
                self.set_status(problems[0], error=True)
            else:
                self.set_status(f"Switched to {label}")

        self.submit(do_switch, on_done=done)

    # -- view --------------------------------------------------------------

    def _apply_scan(self, displays: list[Display]) -> None:
        self.displays = displays
        for row in self.rows:
            row.setParent(None)
        self.rows.clear()
        while self.both_row.count():
            item = self.both_row.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

        if not displays:
            self.set_status("No DDC/CI monitors found", error=True)
            self.both_label.setText("<b>No monitors detected</b>")
            self._rebuild_tray_menu()
            return

        self.both_label.setText("<b>Switch all monitors to</b>")
        for code, label in self._shared_inputs():
            btn = QPushButton(label)
            btn.clicked.connect(lambda _, c=code: self.switch(self.displays, c))
            self.both_row.addWidget(btn)
        self.both_row.addStretch()

        for d in displays:
            row = DisplayRow(d, self.switch)
            self.rows.append(row)
            self.rows_box.addWidget(row)

        self.set_status(f"{len(displays)} monitor(s)")
        self._rebuild_tray_menu()
        self.adjustSize()

    def _shared_inputs(self) -> list[tuple[int, str]]:
        """Inputs every detected monitor supports, in first-monitor order."""
        usable = [d for d in self.displays if d.inputs]
        if not usable:
            return []
        common = set.intersection(*({c for c, _ in d.inputs} for d in usable))
        return [(c, lbl) for c, lbl in usable[0].inputs if c in common]

    def _refresh_rows(self) -> None:
        for row in self.rows:
            row.refresh()
        self._rebuild_tray_menu()

    # -- tray --------------------------------------------------------------

    def _build_tray(self) -> QSystemTrayIcon | None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        tray = QSystemTrayIcon(monitor_icon(), self)
        tray.setToolTip("Monitor Input Switcher")
        tray.activated.connect(self._tray_activated)
        tray.setContextMenu(QMenu())
        tray.show()
        return tray

    def _tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_window()

    def _rebuild_tray_menu(self) -> None:
        if not self.tray:
            return
        menu = QMenu()
        for code, label in self._shared_inputs():
            act = QAction(f"All monitors → {label}", menu)
            act.triggered.connect(lambda _, c=code: self.switch(self.displays, c))
            menu.addAction(act)

        if len(self.displays) > 1:
            menu.addSeparator()
            for d in self.displays:
                sub = menu.addMenu(f"{d.name} — {d.current_label}")
                for code, label in d.inputs:
                    act = QAction(label, sub)
                    act.setCheckable(True)
                    act.setChecked(code == d.current)
                    act.triggered.connect(lambda _, dd=d, c=code: self.switch([dd], c))
                    sub.addAction(act)

        menu.addSeparator()
        show = QAction("Show window", menu)
        show.triggered.connect(self.show_window)
        menu.addAction(show)
        rescan = QAction("Rescan monitors", menu)
        rescan.triggered.connect(self.rescan)
        menu.addAction(rescan)
        menu.addSeparator()
        quit_act = QAction("Quit", menu)
        quit_act.triggered.connect(QApplication.instance().quit)
        menu.addAction(quit_act)

        self.tray.setContextMenu(menu)  # replaces the old menu; keeps it alive
        self._tray_menu = menu

    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:
        """Closing hides to the tray; Quit in the tray menu really exits."""
        if self.tray and self.tray.isVisible():
            event.ignore()
            self.hide()
        else:
            event.accept()


# --------------------------------------------------------------------------- CLI


def resolve_input(token: str) -> int:
    key = token.strip().lower().replace("_", "-")
    if key in CLI_ALIASES:
        return CLI_ALIASES[key]
    if key.replace("-", "") in CLI_ALIASES:
        return CLI_ALIASES[key.replace("-", "")]
    try:
        return int(key, 16) if key.startswith("0x") else int(key)
    except ValueError:
        raise SystemExit(
            f"Unknown input '{token}'. Try one of: "
            + ", ".join(sorted(CLI_ALIASES)) + ", or a raw code like 0x1b"
        )


def cli_list() -> int:
    displays = scan_displays()
    if not displays:
        print("No DDC/CI monitors found")
        return 1
    for d in displays:
        supported = ", ".join(f"{lbl} (0x{c:02x})" for c, lbl in d.inputs) or "unknown"
        print(f"{d.name}  [i2c-{d.bus} {d.connector}]")
        print(f"    current:   {d.current_label}")
        print(f"    supported: {supported}")
    return 0


def cli_set(token: str, serial: str | None) -> int:
    code = resolve_input(token)
    displays = parse_detect(run_ddcutil(["detect", "--terse"]))
    if serial:
        displays = [d for d in displays if d.serial.lower() == serial.lower()]
        if not displays:
            print(f"No monitor with serial {serial}", file=sys.stderr)
            return 1
    if not displays:
        print("No DDC/CI monitors found", file=sys.stderr)
        return 1
    failed = False
    for d in displays:
        try:
            set_input(d.bus, code)
            print(f"{d.name} → {input_label(code)}")
        except DdcError as exc:
            print(f"{d.name}: {exc}", file=sys.stderr)
            failed = True
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list", action="store_true", help="print current inputs and exit")
    parser.add_argument("--set", metavar="INPUT", help="switch to INPUT (dp, hdmi, usb-c, 0x1b…) and exit")
    parser.add_argument("--display", metavar="SERIAL", help="limit --set to one monitor's serial number")
    args = parser.parse_args()

    if args.list:
        return cli_list()
    if args.set:
        return cli_set(args.set, args.display)

    app = QApplication(sys.argv)
    app.setApplicationName("Monitor Input Switcher")
    app.setDesktopFileName("ddc-input-switcher")
    app.setQuitOnLastWindowClosed(False)  # tray keeps it running

    if not shutil.which("ddcutil"):
        QMessageBox.critical(None, "Monitor Input Switcher", "ddcutil is not installed or not on PATH.")
        return 1

    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except DdcError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)

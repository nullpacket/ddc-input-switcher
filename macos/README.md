# Monitor Input Switcher — macOS

The macOS counterpart to the Linux app in the parent directory. Same job: flip the
DDC/CI input source on both Dell P2723QE panels, so you can hand the monitors back
to the Linux box without reaching for the OSD joystick.

**Input mapping for this desk:** USB-C = this Mac, DisplayPort = the Linux box.

## Status

Working. The Swift menu bar app was written on the Linux box without a Swift
toolchain or any Apple hardware to test against, then built on an Apple Silicon Mac
on 2026-08-17 — it compiled clean with no source changes.

`switch-input.sh` remains the quickest way to sanity-check a new machine: it needs
no compiler and exercises the same m1ddc commands the app uses.

## Requirements

- Apple Silicon Mac. m1ddc is Apple Silicon only; on Intel use
  [BetterDisplay](https://github.com/waydabber/BetterDisplay) or `ddcctl` instead.
- `brew install m1ddc`
- For the app only: Xcode Command Line Tools (`xcode-select --install`)

## Quick start — no build required

```
chmod +x switch-input.sh
./switch-input.sh list      # confirm m1ddc sees both panels
./switch-input.sh linux     # both displays -> DisplayPort, back to the Linux box
```

`list` is the safe one to run first: it only reads.

## Menu bar app

```
./build.sh
open MonitorInputSwitcher.app
```

`build.sh` compiles `main.swift`, wraps it in an `.app` bundle marked `LSUIElement`
(menu bar only, no Dock icon), and ad-hoc signs it. Copy it to `/Applications` and
add it to **System Settings → General → Login Items** to have it always present.

The menu offers "Switch all displays to → DisplayPort (Linux) / USB-C (this Mac) /
HDMI", plus a per-display submenu when more than one panel is attached, with the
current input check-marked.

## How it works

Everything goes through [m1ddc](https://github.com/waydabber/m1ddc), exactly as the
Linux version goes through `ddcutil`:

| Action | Command |
|---|---|
| List displays | `m1ddc display list detailed` |
| Read current input | `m1ddc display 1 get input` |
| Set input | `m1ddc display 1 set input 15` |

Input codes are the same VCP 0x60 values the Linux tool uses, in decimal:

| Input | m1ddc (decimal) | VCP (hex) |
|---|---|---|
| DisplayPort — Linux | 15 | `0x0f` |
| HDMI | 17 | `0x11` |
| USB-C — this Mac | 27 | `0x1b` |

Displays are addressed by their `[N]` list number. `display list detailed` also
prints an **AN Serial** line (`D5GPRS3`, `D5MQRS3`) which the app shows in the menu,
since both panels are the same model and the product name cannot tell them apart.

Note that m1ddc's `edid:` identification method is unreliable for identical
monitors — vendors ship batches sharing one EDID UUID
([m1ddc#41](https://github.com/waydabber/m1ddc/issues/41)). The default `uuid:`
method uses the system UUID and does not have that problem.

## Caveats

- **The switch is one-way.** Sending both panels to DisplayPort drops this Mac's
  video, so you come back from the Linux side (`ddc_input_switcher.py --set usb-c`)
  or via the monitors' OSD joystick. That is the intended workflow, not a bug.
- **DDC may not survive a dock.** m1ddc talks DDC over the Mac's own video link;
  some USB-C hubs and docks do not pass it through. A direct USB-C cable from the
  Mac to the monitor is the reliable path.

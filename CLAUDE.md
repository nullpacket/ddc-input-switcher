# ddc-input-switcher

Two implementations of one small utility: switch the DDC/CI input source on the
desk's monitors, so a single pair of panels can be handed between a Linux box and
a Mac without using the monitors' OSD joystick.

- `ddc_input_switcher.py` — Linux, PyQt6 window + system tray, drives `ddcutil`.
- `macos/` — macOS menu bar app in Swift, plus a bash fallback, drives `m1ddc`.

## The hardware this was written against

Two **identical** Dell P2723QE panels. Identical model names mean the serial number
is the only reliable way to tell them apart — do not identify displays by model.

| | Linux (`ddcutil`) | macOS (`m1ddc`) |
|---|---|---|
| Serials | `D5GPRS3` (card1-DP-1, i2c-2), `D5MQRS3` (card1-DP-2, i2c-3) | shown as `AN Serial` in `display list detailed` |
| DisplayPort — the Linux box | `x0f` | `15` |
| HDMI | `x11` | `17` |
| USB-C — the Mac | `x1b` | `27` |

`0x1b` is Dell's vendor code for USB-C (DP alt mode). `ddcutil` prints it as
"Unrecognized value"; that is expected, not an error.

Desk wiring: **USB-C = the Mac, DisplayPort = the Linux box.**

## Verification status

Read this before assuming anything works.

**Linux side — verified on the target machine.** Detection, capability parsing,
input decoding, the CLI, and the GUI all run. The user confirmed a real input
switch works.

**macOS side — verified on an Apple Silicon Mac (2026-08-17).** `main.swift` was
authored on the Linux box with no Swift toolchain and no Apple hardware, so it went
to the Mac untested — but it compiled clean through `./build.sh` with no source
changes, and the menu bar app works. Treat it as working code now, not a draft.

The m1ddc command syntax and output format were taken from its source, not guessed:

```
[1] DELL P2723QE (2E8B0000-...)      <- "[%i] %@ (%@)\n" from printDisplayInfos()
 - AN Serial:     D5GPRS3            <- only present with `detailed`
```

`m1ddc display N get input` prints a bare decimal. `set` prints `Writing N`.
Note that m1ddc prints "No external display found, aborting" while still exiting
zero, so exit status alone is not a sufficient success check — both
implementations test for that string.

## Testing gotcha that matters

Switching **both** panels away from the machine you are sitting at kills your own
display. There is no active video link left for DDC to travel over, so you cannot
switch back from that machine — recovery is from the other computer or the OSD
joystick.

When testing, switch **one** display at a time. That leaves the other panel live
and is fully reversible. Never test with a "switch all" as the first action.

## Conventions

- Both implementations shell out to a CLI rather than talking I2C directly.
- All DDC calls are serialised onto a single background thread; they must never
  run on the UI thread, and never two at once on the same bus.
- Commit messages: one-line subject, no body.

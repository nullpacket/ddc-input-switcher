# Monitor Input Switcher

A small PyQt6 tray utility that switches monitor inputs over DDC/CI (`ddcutil`),
either all monitors at once or one at a time.

Two computers share these monitors: **DisplayPort = the Linux box, USB-C = the Mac**.
The macOS counterpart lives in [`macos/`](macos/) — a menu bar app plus a bash
fallback, built on `m1ddc` instead of `ddcutil`. Between the two you can hand the
panels back and forth without touching the OSD joystick.

Detected on this machine:

| Monitor | Connector | Inputs |
|---|---|---|
| DELL P2723QE (D5GPRS3) | card1-DP-1 | DisplayPort `0x0f`, HDMI `0x11`, USB-C `0x1b` |
| DELL P2723QE (D5MQRS3) | card1-DP-2 | DisplayPort `0x0f`, HDMI `0x11`, USB-C `0x1b` |

`ddcutil` reports `0x1b` as "Unrecognized value" — it is Dell's vendor code for
the USB-C (DisplayPort alt mode) input, and the app labels it accordingly.

## Requirements

- `ddcutil` (already installed, 2.2.7)
- `python-pyqt6`
- Read/write access to `/dev/i2c-*`. The udev rules shipped with ddcutil already
  grant this to the logged-in user via ACL, which is why no `sudo` is needed.

## Running

```
./ddc_input_switcher.py
```

Closing the window hides it to the tray; **Quit** in the tray menu exits.
Left-click the tray icon to reopen the window, right-click for the switch menu.

To add it to the application launcher:

```
cp ddc-input-switcher.desktop ~/.local/share/applications/
```

## CLI

The same script works headlessly, which is what to bind a hotkey to:

```
./ddc_input_switcher.py --list                       # current + supported inputs
./ddc_input_switcher.py --set usb-c                  # all monitors to USB-C
./ddc_input_switcher.py --set dp                     # all monitors to DisplayPort
./ddc_input_switcher.py --set hdmi --display D5MQRS3 # just the right-hand panel
```

Accepted names: `dp`, `hdmi`, `usb-c`, `vga`, `dvi`, the numbered variants
(`dp2`, `hdmi2`), or a raw code such as `0x1b`.

### KDE hotkey

System Settings → Shortcuts → Add → Command/URL, with the command set to
`/home/nullpacket/git/ddc-input-switcher/ddc_input_switcher.py --set usb-c`,
and a second entry for `--set dp` to come back.

## Notes on behaviour

- Monitors are identified by **serial number** at scan time, then driven by i2c
  bus number for speed. Both panels are the same model, so the serial is the only
  thing that reliably tells them apart. Hit **Rescan** if bus numbers shuffle
  after a reboot or cable change.
- All `ddcutil` calls run on a single background thread, so the UI never blocks
  and two calls never contend on the same bus.
- **Test with one monitor first.** Switching *both* panels away from DisplayPort
  drops this machine's DP links: KDE will see the displays disconnect and shuffle
  windows onto whatever remains, and with no active link the monitors may stop
  answering DDC/CI from this box — meaning you would switch back from the other
  machine or the monitors' own OSD joystick, not from this app. Switching a single
  panel away leaves the other one live and is fully reversible from here.

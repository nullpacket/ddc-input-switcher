#!/usr/bin/env bash
# Switch every external display to a given input, with no GUI and nothing to build.
# Bind this to a hotkey via Raycast, Alfred, or Shortcuts.
#
#   ./switch-input.sh linux     # DisplayPort  -> hands the monitors to the Linux box
#   ./switch-input.sh mac       # USB-C        -> hands the monitors to this Mac
#   ./switch-input.sh hdmi
#   ./switch-input.sh list      # show displays and their current input
#
# Requires: brew install m1ddc   (Apple Silicon only)
set -euo pipefail

M1DDC="$(command -v m1ddc || true)"
for candidate in /opt/homebrew/bin/m1ddc /usr/local/bin/m1ddc; do
    [ -n "$M1DDC" ] && break
    [ -x "$candidate" ] && M1DDC="$candidate"
done

if [ -z "$M1DDC" ]; then
    echo "error: m1ddc not found. Install it with: brew install m1ddc" >&2
    exit 1
fi

# VCP 0x60 values in the decimal form m1ddc expects.
case "${1:-}" in
    linux|dp|displayport) CODE=15 ; LABEL="DisplayPort (Linux)" ;;
    mac|usbc|usb-c|typec) CODE=27 ; LABEL="USB-C (this Mac)" ;;
    hdmi)                 CODE=17 ; LABEL="HDMI" ;;
    list)                 CODE=""  ; LABEL="" ;;
    ''|-h|--help)
        echo "usage: $(basename "$0") linux|mac|hdmi|list|<vcp-code>" >&2
        exit 1 ;;
    *)
        if [[ "$1" =~ ^[0-9]+$ ]]; then CODE="$1"; LABEL="input $1"
        else echo "error: unknown input '$1'" >&2; exit 1; fi ;;
esac

# Display numbers are the "[N]" prefixes from m1ddc's listing.
LISTING="$("$M1DDC" display list 2>&1)"
if grep -qi 'No external display found' <<<"$LISTING"; then
    echo "error: no external displays found" >&2
    exit 1
fi
NUMBERS="$(grep -oE '^\[[0-9]+\]' <<<"$LISTING" | tr -d '[]')"

if [ -z "$NUMBERS" ]; then
    echo "error: could not parse display list:" >&2
    echo "$LISTING" >&2
    exit 1
fi

if [ "${1:-}" = "list" ]; then
    while read -r n; do
        current="$("$M1DDC" display "$n" get input 2>&1 | tr -d '[:space:]')"
        case "$current" in
            15) name="DisplayPort (Linux)" ;;
            17) name="HDMI" ;;
            27) name="USB-C (this Mac)" ;;
            *)  name="input ${current:-unknown}" ;;
        esac
        echo "[$n] $(sed -n "s/^\[$n\] //p" <<<"$LISTING") -> $name"
    done <<<"$NUMBERS"
    exit 0
fi

echo "Switching all displays to ${LABEL}…"
while read -r n; do
    "$M1DDC" display "$n" set input "$CODE" >/dev/null && echo "  display $n -> ${LABEL}"
done <<<"$NUMBERS"

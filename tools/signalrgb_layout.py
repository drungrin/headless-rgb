"""Write SignalRGB device layouts into the registry.

SignalRGB keeps everything in the registry as Qt QSettings, under
``HKCU\\Software\\WhirlwindFX\\SignalRgb``. A layout is one subkey of ``layouts``
holding one binary value per device: a QSettings ``@Variant(...)`` blob wrapping
a UTF-16 JSON object with the device's position, scale and rotation.

Arranging twenty devices by hand in the UI is tedious and easy to get subtly
wrong, and two of the arrangements this project wants are *computed* rather than
aesthetic -- "spectrum" reproduces, on the canvas, the per-device palette offsets
the Linux renderer uses. So they are generated here.

    python tools/signalrgb_layout.py --list
    python tools/signalrgb_layout.py spectrum --dry-run
    python tools/signalrgb_layout.py spectrum desk

SignalRGB must be closed: it rewrites its settings on exit and would discard
anything written underneath it. Existing layouts are never modified; this only
creates new ones. Back the branch up first if you want a way back:

    reg export HKCU\Software\WhirlwindFX backup.reg

Keep that file outside the repository -- it carries account state.
"""

from __future__ import annotations

import argparse
import json
import sys

try:
    import winreg
except ImportError:  # pragma: no cover - the tool is Windows-only
    winreg = None


ROOT = r"Software\WhirlwindFX\SignalRgb"
LAYOUTS = rf"{ROOT}\layouts"

# The SignalRGB canvas, in the same units device positions use. Every effect
# that ships with the app draws onto a 320x200 canvas.
CANVAS_WIDTH = 320
CANVAS_HEIGHT = 200

# QSettings writes a non-string QVariant as the UTF-16 string
# "@Variant(" + <4 code units: type> + <4 code units: length> + <payload> + ")".
# Type 143 is what SignalRGB uses for these; it is copied verbatim.
VARIANT_TYPE = 143


def encode_variant(payload: str) -> bytes:
    """Encode a JSON string the way QSettings stores it in the registry."""
    def word(value: int) -> str:
        return "".join(chr((value >> shift) & 0xFF) for shift in (24, 16, 8, 0))

    text = "@Variant(" + word(VARIANT_TYPE) + word(len(payload)) + payload + ")"
    return text.encode("utf-16-le")


def decode_variant(raw: bytes) -> dict:
    """Inverse of :func:`encode_variant`, used to read an existing layout."""
    text = raw.decode("utf-16-le")
    if not text.startswith("@Variant(") or not text.endswith(")"):
        raise ValueError("not a QSettings variant blob")
    body = text[len("@Variant(") : -1]
    length = 0
    for char in body[4:8]:
        length = (length << 8) | ord(char)
    payload = body[8 : 8 + length]
    return json.loads(payload)


def entry(x: float, y: float, scale: float, *, brightness: int = 100) -> str:
    """Build the JSON SignalRGB stores for one device in a layout."""
    return json.dumps(
        {
            "brightness": brightness,
            "flipped": False,
            "flippedV": False,
            "rotation": 0,
            "scale": {"x": scale, "y": scale},
            "x": round(x),
            "y": round(y),
        },
        separators=(",", ":"),
    )


# --- the devices on this desk ----------------------------------------------
#
# `size` is the plugin's Size(), which the stored `scale` multiplies to give the
# device's real footprint on the canvas.

FAN_PREFIX = "1b1c:0c3f:17D1813FD1AD525F81F49A5ED4A63071"
BEELIGHT = "2e3c:5740:05864F464466:COM3"
ASIAHORSE = "7210494f-3276-4eaf-85c2-6885f9cfd728"
AURA = "0b05:19af:9876543210"

SIZES = {
    "signalrgb-mac-bridge-k70": (22, 9),
    "signalrgb-mac-bridge-mm700": (9, 3),
    "signalrgb-mac-bridge-g560": (5, 3),
    "signalrgb-mac-bridge-scimitar": (3, 5),
    BEELIGHT: (33, 1),
    ASIAHORSE: (26, 1),
    "I2CBUS:25": (8, 2),
    "I2CBUS:27": (8, 2),
}
FAN_SIZE = (7, 9)


def fan_uids(existing: dict[str, dict]) -> list[str]:
    return sorted(
        uid
        for uid in existing
        if uid.startswith(FAN_PREFIX + ":") and uid != FAN_PREFIX
    )


def size_of(uid: str) -> tuple[int, int] | None:
    if uid in SIZES:
        return SIZES[uid]
    if uid.startswith(FAN_PREFIX + ":"):
        return FAN_SIZE
    return None


# --- layout: spectrum -------------------------------------------------------
#
# Every device is placed where its colours already come from. effects.py gives
# each one an offset and a span into the palette -- the Beelight strip starts at
# 0.67 and runs 1.24, the K70 at 0.12 running 1.05 -- and that is what makes the
# devices show different parts of the spectrum at the same instant instead of
# all turning the same colour together. Mapping those numbers straight onto the
# canvas reproduces the Linux look under a single SignalRGB effect.

SPECTRUM_SPANS = {
    "signalrgb-mac-bridge-k70": (0.12, 1.05),
    "signalrgb-mac-bridge-mm700": (0.54, 0.34 * 3),
    "signalrgb-mac-bridge-g560": (0.28, 0.29 * 4),
    "signalrgb-mac-bridge-scimitar": (0.49, 0.24 * 3),
    BEELIGHT: (0.67, 1.24),
    ASIAHORSE: (0.38, 1.08),
    "I2CBUS:25": (0.14, 0.56),
    "I2CBUS:27": (0.14 + 0.31, 0.56),
}
FAN_OFFSET_STEP = 0.23
FAN_SPAN = 0.72


def spectrum_spans(existing: dict[str, dict]) -> dict[str, tuple[float, float]]:
    spans = dict(SPECTRUM_SPANS)
    for index, uid in enumerate(fan_uids(existing)):
        spans[uid] = (index * FAN_OFFSET_STEP, FAN_SPAN)
    return spans


def build_spectrum(existing: dict[str, dict]) -> tuple[dict[str, str], dict]:
    spans = spectrum_spans(existing)
    total = max(offset + span for offset, span in spans.values())

    # Vertical order is for legibility only. The effect reads position from x,
    # so overlap costs nothing -- but a layout nobody can read is still a bad
    # layout, so devices are laid out in rows and tall ones are allowed to run
    # over their neighbours rather than being squashed.
    order = [uid for uid in sorted(spans, key=lambda uid: (spans[uid][0], uid))
             if size_of(uid) is not None]
    placement: dict[str, str] = {}
    row = (CANVAS_HEIGHT - 16) / max(1, len(order))
    for index, uid in enumerate(order):
        size = size_of(uid)
        offset, span = spans[uid]
        scale = (span / total) * CANVAS_WIDTH / size[0]
        placement[uid] = entry(
            (offset / total) * CANVAS_WIDTH, 8 + index * row, scale
        )

    carry_over(placement, existing)
    return placement, {
        "spread": round(total * 10),
        "tilt": 0,
        "note": "tilt 0: with devices offset horizontally, y must not shift the palette",
    }


# --- layout: desk -----------------------------------------------------------
#
# Where the hardware actually is. The Beelight runs along the wall behind the
# desk; the Lian Li case stands to the right with the Asiahorse around the top
# of the motherboard; the Mac's peripherals sit on the desk in front.

CASE_LEFT = 196.0
CASE_RIGHT = 318.0


def build_desk(existing: dict[str, dict]) -> tuple[dict[str, str], dict]:
    case_width = CASE_RIGHT - CASE_LEFT
    placement: dict[str, str] = {}

    # The wall: full width, at the very back.
    placement[BEELIGHT] = entry(0, 2, CANVAS_WIDTH / SIZES[BEELIGHT][0])

    # The case, right hand side. The Asiahorse runs along the top of the
    # motherboard and turns down the right side; a component is a straight line,
    # so only the top run is represented.
    placement[ASIAHORSE] = entry(CASE_LEFT, 26, case_width / SIZES[ASIAHORSE][0])

    fans = fan_uids(existing)
    fan_scale = case_width / (4 * FAN_SIZE[0])
    for index, uid in enumerate(fans[:8]):
        column, row = index % 4, index // 4
        placement[uid] = entry(
            CASE_LEFT + column * FAN_SIZE[0] * fan_scale,
            38 + row * FAN_SIZE[1] * fan_scale,
            fan_scale,
        )

    ram_scale = (case_width / 2) / SIZES["I2CBUS:25"][0]
    for index, uid in enumerate(("I2CBUS:25", "I2CBUS:27")):
        if uid in existing:
            placement[uid] = entry(
                CASE_LEFT + index * (case_width / 2), 130, ram_scale
            )

    # The desk. The MM700 is an extended pad: the keyboard and the mouse sit on
    # it, so they overlap it on purpose.
    placement["signalrgb-mac-bridge-mm700"] = entry(6, 130, 20.0)
    placement["signalrgb-mac-bridge-k70"] = entry(10, 136, 6.0)
    placement["signalrgb-mac-bridge-scimitar"] = entry(150, 142, 8.0)
    placement["signalrgb-mac-bridge-g560"] = entry(64, 92, 10.0)

    carry_over(placement, existing)
    # One palette cycle across the whole room, so the colour is spatially
    # coherent: the wall strip and the keyboard below it show the same part of
    # the spectrum. A device therefore shows as much of the palette as it
    # physically occupies -- the keyboard covers 41% of the desk and gets 41% of
    # a cycle. That is the opposite of the spectrum layout's goal, and correct
    # here.
    return placement, {
        "spread": 11,
        "tilt": 26,
        "note": "one cycle across the room; each device shows the share it occupies",
    }


def carry_over(placement: dict[str, str], existing: dict[str, dict]) -> None:
    """Keep devices this layout has no opinion about where they already were.

    A layout key holds only what it lists, so anything omitted would be handed
    to SignalRGB's auto-placement and jump somewhere unrelated. The motherboard
    and its 12V headers have no Size() here, so they are copied across rather
    than positioned.
    """
    for uid, data in existing.items():
        if uid in placement:
            continue
        placement[uid] = entry(
            data["x"], data["y"], data["scale"]["x"],
            brightness=data.get("brightness", 100),
        )


BUILDERS = {"spectrum": build_spectrum, "desk": build_desk}
# SignalRGB's own layout is called "Stacked"; match that capitalisation
# so the three read as a set in the layout picker.
KEY_NAMES = {"spectrum": "Spectrum", "desk": "Desk"}


# --- registry ---------------------------------------------------------------


def read_layout(name: str) -> dict[str, dict]:
    values: dict[str, dict] = {}
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{LAYOUTS}\{name}") as key:
        index = 0
        while True:
            try:
                value_name, data, kind = winreg.EnumValue(key, index)
            except OSError:
                break
            index += 1
            if kind == winreg.REG_BINARY:
                try:
                    values[value_name] = decode_variant(data)
                except (ValueError, json.JSONDecodeError):
                    continue
    return values


def write_layout(name: str, placement: dict[str, str]) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{LAYOUTS}\{name}") as key:
        for uid, payload in placement.items():
            winreg.SetValueEx(key, uid, 0, winreg.REG_BINARY, encode_variant(payload))


def signalrgb_is_running() -> bool:
    import subprocess

    completed = subprocess.run(
        ("tasklist", "/FI", "IMAGENAME eq SignalRgb.exe", "/NH"),
        capture_output=True,
        text=True,
    )
    return "SignalRgb.exe" in completed.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="signalrgb_layout")
    parser.add_argument("layouts", nargs="*", choices=[*BUILDERS, []], default=[])
    parser.add_argument("--source", default="Stacked", help="layout to read devices from")
    parser.add_argument("--list", action="store_true", help="show the current layouts")
    parser.add_argument("--dry-run", action="store_true", help="print, do not write")
    args = parser.parse_args(argv)

    if winreg is None:
        print("this tool only runs on Windows", file=sys.stderr)
        return 2

    existing = read_layout(args.source)
    if not existing:
        print(f"layout {args.source!r} has no devices to copy", file=sys.stderr)
        return 2

    if args.list:
        for uid, data in sorted(existing.items()):
            size = size_of(uid)
            footprint = (
                f"{size[0] * data['scale']['x']:.0f}x{size[1] * data['scale']['y']:.0f}"
                if size
                else "unknown"
            )
            print(f"{uid:<55} x={data['x']:<4} y={data['y']:<4} {footprint}")
        return 0

    if not args.layouts:
        parser.error("name at least one layout, or pass --list")

    if not args.dry_run and signalrgb_is_running():
        print(
            "SignalRGB is running; it rewrites its settings on exit and would\n"
            "discard this. Close it first.",
            file=sys.stderr,
        )
        return 1

    for name in args.layouts:
        placement, advice = BUILDERS[name](existing)
        print(f"\n{name}: {len(placement)} devices")
        for uid, payload in sorted(placement.items()):
            data = json.loads(payload)
            print(f"  {uid:<55} x={data['x']:<4} y={data['y']:<4} scale={data['scale']['x']:.2f}")
        print(
            f"  watercolor settings: Spread {advice['spread']}, Tilt {advice['tilt']}"
            f"\n  ({advice['note']})"
        )
        if not args.dry_run:
            write_layout(KEY_NAMES[name], placement)
            print(f"  written to HKCU\\{LAYOUTS}\\{KEY_NAMES[name]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

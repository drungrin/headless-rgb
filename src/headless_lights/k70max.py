"""Read the K70 MAX channel geometry out of mac-agent/k70max_layout.h.

The C++ header is the single source of truth: 142 hardware channels, 116 of
which drive a physical LED, each with a normalized x/y. Both the SignalRGB
layout generator (tools/gen_k70_layout.py) and the streaming probe read it from
here so the two never drift apart.
"""

from __future__ import annotations

from pathlib import Path
import re


WIRE_SLOTS = 142

HEADER_PATH = Path(__file__).resolve().parents[2] / "mac-agent" / "k70max_layout.h"

_ENTRY = re.compile(
    r"\{\s*([0-9]*\.?[0-9]+)\s*,\s*([0-9]*\.?[0-9]+)\s*,\s*(true|false)\s*\}"
)


class LayoutError(RuntimeError):
    pass


def parse_header(text: str) -> list[tuple[float, float, bool]]:
    """Return one (x, y, mapped) triple per hardware channel, in wire order."""
    try:
        body = text.split("kK70LedCoordinates{{", 1)[1].rsplit("}};", 1)[0]
    except IndexError as error:
        raise LayoutError("could not find kK70LedCoordinates in the header") from error

    entries = [
        (float(x), float(y), flag == "true") for x, y, flag in _ENTRY.findall(body)
    ]
    if len(entries) != WIRE_SLOTS:
        raise LayoutError(
            f"expected {WIRE_SLOTS} coordinates in the header, parsed {len(entries)}"
        )
    return entries


def load_coordinates(path: Path | None = None) -> list[tuple[float, float, bool]]:
    header = path or HEADER_PATH
    try:
        return parse_header(header.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise LayoutError(f"missing {header}") from error

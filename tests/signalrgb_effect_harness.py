"""Shared plumbing for the SignalRGB effect cross-checks.

Both effect tests run the real HTML under Node against a fake canvas and compare
the gradients it builds against this project's own renderer. Only the scenarios
and the expected colours differ, so the running and parsing live here.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess


REPO_ROOT = Path(__file__).resolve().parents[1]
DUMPER = REPO_ROOT / "signalrgb" / "tests" / "dump_effect_stops.mjs"
EFFECTS_DIR = REPO_ROOT / "signalrgb" / "effects"

# An arbitrary but fixed Unix instant. The effects read the wall clock, so the
# tests pin it rather than letting the result depend on when they run.
T0 = 1758000000


def node() -> str | None:
    found = shutil.which("node")
    if found:
        return found
    # winget installs Node outside the shell's default PATH.
    fallback = Path("C:/Program Files/nodejs/node.exe")
    return str(fallback) if fallback.exists() else None


NODE = node()


def run_effect(effect: str, scenarios: dict) -> dict:
    """Render `effect` under Node and return everything it drew."""
    completed = subprocess.run(
        (NODE, str(DUMPER), str(EFFECTS_DIR / effect)),
        cwd=REPO_ROOT,
        input=json.dumps({"scenarios": scenarios}),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != 0:
        raise AssertionError(f"{effect} dumper failed:\n{completed.stderr.strip()}")
    return json.loads(completed.stdout)


def rgb(value: str) -> tuple[int, int, int]:
    """Parse the "rgb(r, g, b)" strings the effects write into gradients."""
    inner = value[value.index("(") + 1 : value.index(")")]
    parts = tuple(int(component) for component in inner.split(","))
    if len(parts) != 3:
        raise AssertionError(f"not an RGB triple: {value!r}")
    return parts


def spans(globals_: dict) -> tuple[float, float]:
    """Reproduce spanX()/spanY(), which both effects define identically."""
    span_x = globals_["spread"] / 10.0
    return span_x, span_x * globals_["tilt"] / 100.0


def axis_is_parallel(gradient: dict, span_x: float, span_y: float, canvas: dict) -> float:
    """Cross product of the gradient axis with the expected direction.

    Zero when the axis is parallel to (spanX / width, spanY / height), which is
    the direction position(x, y) actually increases in.
    """
    return gradient["x1"] * (span_y / canvas["height"]) - gradient["y1"] * (
        span_x / canvas["width"]
    )

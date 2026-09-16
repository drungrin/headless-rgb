"""Cross-check the SignalRGB watercolor effect against this project's renderer.

signalrgb/effects/watercolor.html reimplements
headless_lights.effects.watercolor_color in JavaScript. The two run on different
machines painting the same desk, so a drift between them is a visible seam
rather than a crash. This runs the real effect under Node against a fake canvas
and compares every gradient stop it produces with the Python implementation.

Skipped when Node is not installed.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import unittest

from headless_lights import effects


REPO_ROOT = Path(__file__).resolve().parents[1]
DUMPER = REPO_ROOT / "signalrgb" / "tests" / "dump_watercolor_stops.mjs"
EFFECT = REPO_ROOT / "signalrgb" / "effects" / "watercolor.html"


def _node() -> str | None:
    found = shutil.which("node")
    if found:
        return found
    # winget installs Node outside the shell's default PATH.
    fallback = Path("C:/Program Files/nodejs/node.exe")
    return str(fallback) if fallback.exists() else None


NODE = _node()


def _rgb(value: str) -> tuple[int, int, int]:
    """Parse the "rgb(r, g, b)" strings the effect writes into the gradient."""
    inner = value[value.index("(") + 1 : value.index(")")]
    parts = tuple(int(component) for component in inner.split(","))
    if len(parts) != 3:
        raise AssertionError(f"not an RGB triple: {value!r}")
    return parts


@unittest.skipIf(NODE is None, "Node.js is not installed")
class WatercolorEffectTests(unittest.TestCase):
    dump: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        completed = subprocess.run(
            (NODE, str(DUMPER)),
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if completed.returncode != 0:
            raise AssertionError(
                f"the watercolor dumper failed:\n{completed.stderr.strip()}"
            )
        cls.dump = json.loads(completed.stdout)

    # -- helpers ------------------------------------------------------------

    def _spans(self, scenario: dict) -> tuple[float, float]:
        """Reproduce spanX()/spanY() from the effect."""
        span_x = scenario["spread"] / 10.0
        return span_x, span_x * scenario["tilt"] / 100.0

    # -- the contract -------------------------------------------------------

    def test_every_stop_matches_the_python_renderer(self) -> None:
        """The whole point: the same position and clock give the same colour."""
        for name in ("defaults", "flat", "wide", "narrow"):
            scenario = self.dump[name]
            span_x, span_y = self._spans(scenario)
            total = span_x + span_y
            for frame in scenario["frames"]:
                for stop in frame["gradient"]["stops"]:
                    expected = effects.watercolor_color(
                        stop["offset"] * total, frame["elapsed"]
                    )
                    self.assertEqual(
                        _rgb(stop["color"]),
                        expected,
                        f"{name} @ offset {stop['offset']}, t={frame['elapsed']}",
                    )

    def test_the_effect_is_phase_locked_to_the_unix_clock(self) -> None:
        """Two frames 12 s apart must differ, and each must match Python at
        that same wall-clock instant. This is what keeps the Mac from jumping
        when it falls back to its own renderer."""
        first, second = self.dump["defaults"]["frames"]
        self.assertEqual(second["elapsed"] - first["elapsed"], 12)
        self.assertNotEqual(
            [stop["color"] for stop in first["gradient"]["stops"]],
            [stop["color"] for stop in second["gradient"]["stops"]],
            "a 12 s gap is the effect's own drift period; it must move",
        )

    def test_the_gradient_axis_follows_the_span(self) -> None:
        """The axis must be parallel to (spanX / width, spanY / height), or the
        tilt runs the wrong way across the canvas."""
        canvas = self.dump["canvas"]
        for name in ("defaults", "wide", "narrow"):
            scenario = self.dump[name]
            span_x, span_y = self._spans(scenario)
            gradient = scenario["frames"][0]["gradient"]
            self.assertEqual((gradient["x0"], gradient["y0"]), (0, 0))
            # Cross product of the axis and the expected direction is zero when
            # they are parallel.
            self.assertAlmostEqual(
                gradient["x1"] * (span_y / canvas["height"])
                - gradient["y1"] * (span_x / canvas["width"]),
                0.0,
                places=9,
                msg=name,
            )

    def test_the_axis_end_is_the_canvas_corner(self) -> None:
        """Stop 1.0 must land on position spanX + spanY, the far corner, or the
        palette is compressed or clipped relative to the other renderers."""
        canvas = self.dump["canvas"]
        for name in ("defaults", "wide", "narrow"):
            scenario = self.dump[name]
            span_x, span_y = self._spans(scenario)
            gradient = scenario["frames"][0]["gradient"]
            # Gradient parameter of the canvas corner, projected onto the axis.
            length = gradient["x1"] ** 2 + gradient["y1"] ** 2
            corner = (
                canvas["width"] * gradient["x1"] + canvas["height"] * gradient["y1"]
            ) / length
            self.assertAlmostEqual(corner, 1.0, places=9, msg=name)

    def test_no_tilt_gives_a_horizontal_gradient(self) -> None:
        gradient = self.dump["flat"]["frames"][0]["gradient"]
        self.assertEqual(gradient["y1"], 0)
        self.assertGreater(gradient["x1"], 0)

    def test_spread_changes_how_much_palette_fits_on_the_canvas(self) -> None:
        narrow_x, _ = self._spans(self.dump["narrow"])
        wide_x, _ = self._spans(self.dump["wide"])
        self.assertLess(narrow_x, wide_x)
        colors = {
            name: {
                _rgb(stop["color"])
                for stop in self.dump[name]["frames"][0]["gradient"]["stops"]
            }
            for name in ("narrow", "wide")
        }
        self.assertLess(
            len(colors["narrow"]),
            len(colors["wide"]),
            "a wider spread must sweep more of the palette",
        )

    # -- the shape SignalRGB requires ---------------------------------------

    def test_one_gradient_and_one_fill_per_frame(self) -> None:
        """Ultralight is slow at repeated fills; Rainbow.html says so in its own
        source. One gradient and one full-canvas fill is the budget."""
        canvas = self.dump["canvas"]
        for frame in self.dump["defaults"]["frames"]:
            self.assertEqual(frame["newGradients"], 1)
            self.assertEqual(
                frame["fill"],
                {
                    "x": 0,
                    "y": 0,
                    "width": canvas["width"],
                    "height": canvas["height"],
                    "gradient": frame["fill"]["gradient"],
                    "solid": None,
                },
            )
            self.assertIsNotNone(
                frame["fill"]["gradient"], "the fill must use the gradient"
            )

    def test_the_canvas_is_the_size_signalrgb_expects(self) -> None:
        self.assertEqual(self.dump["canvas"], {"width": 320, "height": 200})

    def test_the_effect_is_named_and_attributed(self) -> None:
        self.assertEqual(self.dump["title"], "Watercolor Spectrum")
        self.assertIn('publisher="headless-lights"', EFFECT.read_text(encoding="utf-8"))

    def test_every_declared_setting_is_actually_read(self) -> None:
        """A <meta property> the script never reads is a dead control in the UI."""
        declared = {entry["property"] for entry in self.dump["meta"]}
        self.assertEqual(declared, {"spread", "tilt"})
        read = set(self.dump["defaults"]["reads"])
        self.assertTrue(declared <= read, f"never read: {declared - read}")

    def test_settings_declare_a_usable_range(self) -> None:
        for entry in self.dump["meta"]:
            self.assertEqual(entry["type"], "number", entry["property"])
            low, high = int(entry["min"]), int(entry["max"])
            default = int(entry["default"])
            self.assertLess(low, high, entry["property"])
            self.assertTrue(low <= default <= high, entry["property"])

    def test_the_default_spread_reproduces_the_k70_mapping(self) -> None:
        """macstream.py renders the K70 as x * 1.05 + y * 0.18. The defaults are
        chosen so a keyboard-sized device sees the same shape."""
        scenario = self.dump["defaults"]
        span_x, span_y = self._spans(scenario)
        self.assertAlmostEqual(span_y / span_x, 0.18 / 1.05, places=2)


if __name__ == "__main__":
    unittest.main()

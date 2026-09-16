"""Cross-check the SignalRGB watercolor effect against this project's renderer.

signalrgb/effects/watercolor.html reimplements
headless_lights.effects.watercolor_color in JavaScript. The two run on different
machines painting the same desk, so a drift between them is a visible seam
rather than a crash. This runs the real effect under Node against a fake canvas
and compares every gradient stop it produces with the Python implementation.

Skipped when Node is not installed.
"""

from __future__ import annotations

import unittest

from headless_lights import effects
from signalrgb_effect_harness import (
    EFFECTS_DIR,
    NODE,
    T0,
    axis_is_parallel,
    rgb,
    run_effect,
    spans,
)


EFFECT = "watercolor.html"


@unittest.skipIf(NODE is None, "Node.js is not installed")
class WatercolorEffectTests(unittest.TestCase):
    dump: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        cls.dump = run_effect(
            EFFECT,
            {
                # 12 s is the effect's own drift period, so the two frames must
                # differ.
                "defaults": {
                    "globals": {"spread": 25, "tilt": 17},
                    "times": [T0, T0 + 12],
                },
                "flat": {"globals": {"spread": 25, "tilt": 0}, "times": [T0]},
                "wide": {"globals": {"spread": 60, "tilt": 17}, "times": [T0]},
                "narrow": {"globals": {"spread": 5, "tilt": 40}, "times": [T0]},
            },
        )

    def scenario(self, name: str) -> dict:
        return self.dump["scenarios"][name]

    # -- the contract -------------------------------------------------------

    def test_every_stop_matches_the_python_renderer(self) -> None:
        """The whole point: the same position and clock give the same colour."""
        for name in ("defaults", "flat", "wide", "narrow"):
            scenario = self.scenario(name)
            span_x, span_y = spans(scenario["globals"])
            total = span_x + span_y
            for frame in scenario["frames"]:
                for stop in frame["gradients"][0]["stops"]:
                    expected = effects.watercolor_color(
                        stop["offset"] * total, frame["elapsed"]
                    )
                    self.assertEqual(
                        rgb(stop["color"]),
                        expected,
                        f"{name} @ offset {stop['offset']}, t={frame['elapsed']}",
                    )

    def test_the_effect_is_phase_locked_to_the_unix_clock(self) -> None:
        """Two frames 12 s apart must differ, and each must match Python at that
        same wall-clock instant. This is what keeps the Mac from jumping when it
        falls back to its own renderer."""
        first, second = self.scenario("defaults")["frames"]
        self.assertEqual(second["elapsed"] - first["elapsed"], 12)
        self.assertNotEqual(
            [stop["color"] for stop in first["gradients"][0]["stops"]],
            [stop["color"] for stop in second["gradients"][0]["stops"]],
            "a 12 s gap is the effect's own drift period; it must move",
        )

    def test_the_gradient_axis_follows_the_span(self) -> None:
        """The axis must be parallel to (spanX / width, spanY / height), or the
        tilt runs the wrong way across the canvas."""
        canvas = self.dump["canvas"]
        for name in ("defaults", "wide", "narrow"):
            scenario = self.scenario(name)
            span_x, span_y = spans(scenario["globals"])
            gradient = scenario["frames"][0]["gradients"][0]
            self.assertEqual((gradient["x0"], gradient["y0"]), (0, 0))
            self.assertAlmostEqual(
                axis_is_parallel(gradient, span_x, span_y, canvas),
                0.0,
                places=9,
                msg=name,
            )

    def test_the_axis_end_is_the_canvas_corner(self) -> None:
        """Stop 1.0 must land on position spanX + spanY, the far corner, or the
        palette is compressed or clipped relative to the other renderers."""
        canvas = self.dump["canvas"]
        for name in ("defaults", "wide", "narrow"):
            gradient = self.scenario(name)["frames"][0]["gradients"][0]
            length = gradient["x1"] ** 2 + gradient["y1"] ** 2
            corner = (
                canvas["width"] * gradient["x1"] + canvas["height"] * gradient["y1"]
            ) / length
            self.assertAlmostEqual(corner, 1.0, places=9, msg=name)

    def test_no_tilt_gives_a_horizontal_gradient(self) -> None:
        gradient = self.scenario("flat")["frames"][0]["gradients"][0]
        self.assertEqual(gradient["y1"], 0)
        self.assertGreater(gradient["x1"], 0)

    def test_spread_changes_how_much_palette_fits_on_the_canvas(self) -> None:
        narrow_x, _ = spans(self.scenario("narrow")["globals"])
        wide_x, _ = spans(self.scenario("wide")["globals"])
        self.assertLess(narrow_x, wide_x)
        colors = {
            name: {
                rgb(stop["color"])
                for stop in self.scenario(name)["frames"][0]["gradients"][0]["stops"]
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
        for frame in self.scenario("defaults")["frames"]:
            self.assertEqual(len(frame["gradients"]), 1)
            self.assertEqual(len(frame["fills"]), 1)
            fill = frame["fills"][0]
            self.assertEqual(
                (fill["x"], fill["y"], fill["width"], fill["height"]),
                (0, 0, canvas["width"], canvas["height"]),
            )
            self.assertEqual(fill["gradient"], 0, "the fill must use the gradient")
            self.assertIsNone(fill["solid"])

    def test_the_canvas_is_the_size_signalrgb_expects(self) -> None:
        self.assertEqual(self.dump["canvas"], {"width": 320, "height": 200})

    def test_the_effect_is_named_and_attributed(self) -> None:
        self.assertEqual(self.dump["title"], "Watercolor Spectrum")
        source = (EFFECTS_DIR / EFFECT).read_text(encoding="utf-8")
        self.assertIn('publisher="headless-lights"', source)

    def test_every_declared_setting_is_actually_read(self) -> None:
        """A <meta property> the script never reads is a dead control in the UI."""
        declared = {entry["property"] for entry in self.dump["meta"]}
        self.assertEqual(declared, {"spread", "tilt"})
        read = set(self.scenario("defaults")["reads"])
        self.assertTrue(declared <= read, f"never read: {declared - read}")

    def test_settings_declare_a_usable_range(self) -> None:
        for entry in self.dump["meta"]:
            self.assertEqual(entry["type"], "number", entry["property"])
            low, high = int(entry["min"]), int(entry["max"])
            self.assertLess(low, high, entry["property"])
            self.assertTrue(low <= int(entry["default"]) <= high, entry["property"])

    def test_the_default_spread_reproduces_the_k70_mapping(self) -> None:
        """macstream.py renders the K70 as x * 1.05 + y * 0.18. The defaults are
        chosen so a keyboard-sized device sees the same shape."""
        span_x, span_y = spans(self.scenario("defaults")["globals"])
        self.assertAlmostEqual(span_y / span_x, 0.18 / 1.05, places=2)


if __name__ == "__main__":
    unittest.main()

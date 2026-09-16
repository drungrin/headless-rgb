"""Cross-check the SignalRGB Borderlands 4 effect against this project's renderer.

signalrgb/effects/borderlands4.html reimplements
headless_lights.effects.borderlands4_color in JavaScript. Unlike Watercolor it
takes a `lane` -- a small integer that shifts the pulse and both waves so the
zones do not beat in unison -- which a canvas effect cannot look up. It derives
the lane from vertical position the way the K70 does in macstream.py, and paints
one gradient band per lane.

This runs the real HTML under Node and checks every stop of every band.

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


EFFECT = "borderlands4.html"


@unittest.skipIf(NODE is None, "Node.js is not installed")
class BorderlandsEffectTests(unittest.TestCase):
    dump: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        cls.dump = run_effect(
            EFFECT,
            {
                "defaults": {
                    "globals": {"spread": 25, "tilt": 17, "lanes": 6},
                    "times": [T0, T0 + 3],
                },
                "single_lane": {
                    "globals": {"spread": 25, "tilt": 17, "lanes": 1},
                    "times": [T0],
                },
                "many_lanes": {
                    "globals": {"spread": 40, "tilt": 0, "lanes": 12},
                    "times": [T0],
                },
            },
        )

    def scenario(self, name: str) -> dict:
        return self.dump["scenarios"][name]

    # -- the contract -------------------------------------------------------

    def test_every_stop_of_every_band_matches_the_python_renderer(self) -> None:
        """The whole point: same position, same clock, same lane, same colour."""
        for name in ("defaults", "single_lane", "many_lanes"):
            scenario = self.scenario(name)
            span_x, span_y = spans(scenario["globals"])
            total = span_x + span_y
            for frame in scenario["frames"]:
                for lane, gradient in enumerate(frame["gradients"]):
                    for stop in gradient["stops"]:
                        expected = effects.borderlands4_color(
                            stop["offset"] * total, frame["elapsed"], lane=lane
                        )
                        self.assertEqual(
                            rgb(stop["color"]),
                            expected,
                            f"{name} lane {lane} @ {stop['offset']}, t={frame['elapsed']}",
                        )

    def test_one_band_per_lane(self) -> None:
        for name, expected in (("defaults", 6), ("single_lane", 1), ("many_lanes", 12)):
            for frame in self.scenario(name)["frames"]:
                self.assertEqual(len(frame["gradients"]), expected, name)
                self.assertEqual(len(frame["fills"]), expected, name)

    def test_lanes_actually_differ(self) -> None:
        """A lane that changed nothing would make the whole exercise pointless."""
        frame = self.scenario("defaults")["frames"][0]
        signatures = {
            tuple(stop["color"] for stop in gradient["stops"])
            for gradient in frame["gradients"]
        }
        self.assertEqual(len(signatures), len(frame["gradients"]))

    # -- the bands ----------------------------------------------------------

    def test_the_bands_tile_the_canvas_without_gaps_or_overlap(self) -> None:
        canvas = self.dump["canvas"]
        for name in ("defaults", "single_lane", "many_lanes"):
            frame = self.scenario(name)["frames"][0]
            edges = sorted((fill["y"], fill["y"] + fill["height"]) for fill in frame["fills"])
            self.assertAlmostEqual(edges[0][0], 0, places=6, msg=name)
            self.assertAlmostEqual(edges[-1][1], canvas["height"], places=6, msg=name)
            for (_, end), (start, _) in zip(edges, edges[1:]):
                self.assertAlmostEqual(end, start, places=6, msg=name)
            for fill in frame["fills"]:
                self.assertEqual(fill["x"], 0, name)
                self.assertEqual(fill["width"], canvas["width"], name)

    def test_band_edges_reproduce_the_k70_lane_rule(self) -> None:
        """macstream.py uses lane = round(y * 5) over a normalised height, so the
        first and last bands are half width. Dividing evenly would shift every
        lane boundary."""
        canvas = self.dump["canvas"]
        frame = self.scenario("defaults")["frames"][0]
        heights = [fill["height"] / canvas["height"] for fill in frame["fills"]]
        self.assertAlmostEqual(heights[0], 0.1, places=6)
        self.assertAlmostEqual(heights[-1], 0.1, places=6)
        for height in heights[1:-1]:
            self.assertAlmostEqual(height, 0.2, places=6)

    def test_every_band_is_painted_with_its_own_gradient(self) -> None:
        for name in ("defaults", "many_lanes"):
            frame = self.scenario(name)["frames"][0]
            used = [fill["gradient"] for fill in frame["fills"]]
            self.assertEqual(used, list(range(len(frame["gradients"]))), name)

    def test_a_single_lane_covers_the_whole_canvas(self) -> None:
        canvas = self.dump["canvas"]
        fill = self.scenario("single_lane")["frames"][0]["fills"][0]
        self.assertEqual((fill["y"], fill["height"]), (0, canvas["height"]))

    # -- geometry and timing -------------------------------------------------

    def test_the_gradient_axis_follows_the_span(self) -> None:
        canvas = self.dump["canvas"]
        for name in ("defaults", "many_lanes"):
            scenario = self.scenario(name)
            span_x, span_y = spans(scenario["globals"])
            for gradient in scenario["frames"][0]["gradients"]:
                self.assertEqual((gradient["x0"], gradient["y0"]), (0, 0))
                self.assertAlmostEqual(
                    axis_is_parallel(gradient, span_x, span_y, canvas),
                    0.0,
                    places=9,
                    msg=name,
                )

    def test_the_effect_is_phase_locked_to_the_unix_clock(self) -> None:
        """Three seconds is the red pulse's own period, so the two frames must
        differ, and each must match Python at that same wall-clock instant."""
        first, second = self.scenario("defaults")["frames"]
        self.assertEqual(second["elapsed"] - first["elapsed"], 3)
        self.assertNotEqual(
            [stop["color"] for stop in first["gradients"][0]["stops"]],
            [stop["color"] for stop in second["gradients"][0]["stops"]],
        )

    def test_no_tilt_gives_horizontal_gradients(self) -> None:
        for gradient in self.scenario("many_lanes")["frames"][0]["gradients"]:
            self.assertEqual(gradient["y1"], 0)
            self.assertGreater(gradient["x1"], 0)

    # -- the shape SignalRGB requires ---------------------------------------

    def test_the_canvas_is_the_size_signalrgb_expects(self) -> None:
        self.assertEqual(self.dump["canvas"], {"width": 320, "height": 200})

    def test_the_effect_is_named_and_attributed(self) -> None:
        self.assertEqual(self.dump["title"], "Borderlands 4")
        source = (EFFECTS_DIR / EFFECT).read_text(encoding="utf-8")
        self.assertIn('publisher="drungrin"', source)

    def test_every_declared_setting_is_actually_read(self) -> None:
        declared = {entry["property"] for entry in self.dump["meta"]}
        self.assertEqual(declared, {"spread", "tilt", "lanes"})
        read = set(self.scenario("defaults")["reads"])
        self.assertTrue(declared <= read, f"never read: {declared - read}")

    def test_settings_declare_a_usable_range(self) -> None:
        for entry in self.dump["meta"]:
            self.assertEqual(entry["type"], "number", entry["property"])
            low, high = int(entry["min"]), int(entry["max"])
            self.assertLess(low, high, entry["property"])
            self.assertTrue(low <= int(entry["default"]) <= high, entry["property"])


if __name__ == "__main__":
    unittest.main()

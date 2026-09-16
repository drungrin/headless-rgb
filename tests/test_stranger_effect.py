"""Cross-check the SignalRGB Stranger Things effect against this project's renderer.

signalrgb/effects/stranger-things.html reimplements
headless_lights.effects.stranger_things_color in JavaScript. It is the hardest
of the three to carry onto a canvas: besides the per-lane banding Borderlands
needs, it has two rain layers running at 7x and 11x the position frequency, and
a flash driven by a keyframe table over a seven second cycle.

This runs the real HTML under Node and checks every stop of every band, at
instants chosen to fall before the flash window, inside it and after it.

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


EFFECT = "stranger-things.html"

# The flash runs over a 7 s cycle, between phase 0.627 and 0.756.
FLASH_OPENS = 0.6270491803278688
FLASH_CLOSES = 0.7561475409836066


def _at_phase(phase: float) -> float:
    """A Unix time whose 7 s flash phase is approximately `phase`.

    Only approximately: at Unix-time magnitudes a double's fractional part
    resolves to about 3e-8 of a cycle, so a time computed to land exactly on a
    keyframe can fall on either side of it. That is fine for the animation and
    both renderers agree regardless -- they do the same arithmetic -- but it
    means the tests probe inside the window rather than on its edges.
    """
    base = T0 - (T0 % 7.0)
    return base + phase * 7.0


@unittest.skipIf(NODE is None, "Node.js is not installed")
class StrangerEffectTests(unittest.TestCase):
    dump: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        cls.dump = run_effect(
            EFFECT,
            {
                "defaults": {
                    "globals": {"spread": 25, "tilt": 17, "lanes": 6, "detail": 320},
                    "times": [T0, T0 + 4],
                },
                "single_lane": {
                    "globals": {"spread": 25, "tilt": 17, "lanes": 1, "detail": 320},
                    "times": [T0],
                },
                "coarse": {
                    "globals": {"spread": 11, "tilt": 0, "lanes": 3, "detail": 64},
                    "times": [T0],
                },
                # Straddles the flash: before it, just after it opens, in its
                # dim middle, just before it closes, and after it.
                "flash": {
                    "globals": {"spread": 25, "tilt": 17, "lanes": 2, "detail": 128},
                    "times": [
                        _at_phase(0.30),
                        _at_phase(FLASH_OPENS + 0.003),
                        _at_phase(0.69),
                        _at_phase(FLASH_CLOSES - 0.003),
                        _at_phase(0.95),
                    ],
                },
            },
        )

    def scenario(self, name: str) -> dict:
        return self.dump["scenarios"][name]

    # -- the contract -------------------------------------------------------

    def test_every_stop_of_every_band_matches_the_python_renderer(self) -> None:
        """Same position, same clock, same lane, same colour -- including the
        rain, the waves and whatever the flash is doing at that instant."""
        for name in ("defaults", "single_lane", "coarse", "flash"):
            scenario = self.scenario(name)
            span_x, span_y = spans(scenario["globals"])
            total = span_x + span_y
            for frame in scenario["frames"]:
                for lane, gradient in enumerate(frame["gradients"]):
                    for stop in gradient["stops"]:
                        expected = effects.stranger_things_color(
                            stop["offset"] * total, frame["elapsed"], lane=lane
                        )
                        self.assertEqual(
                            rgb(stop["color"]),
                            expected,
                            f"{name} lane {lane} @ {stop['offset']}, t={frame['elapsed']}",
                        )

    def test_the_flash_fires_and_stops(self) -> None:
        """The flash is the one layer that is purely a function of the clock, so
        it either follows the seven second cycle or it does not."""
        frames = self.scenario("flash")["frames"]
        before, opening, middle, closing, after = [
            effects._stranger_flash(frame["elapsed"])[1] for frame in frames
        ]
        self.assertEqual(before, 0.0, "silent before the window")
        self.assertEqual(after, 0.0, "silent after it")
        for name, opacity in (
            ("opening", opening), ("middle", middle), ("closing", closing)
        ):
            self.assertGreater(opacity, 0.0, f"the flash must fire at {name}")
        self.assertGreater(opening, middle, "it opens bright and decays")

    def test_the_flash_changes_what_is_drawn(self) -> None:
        """Matching Python is necessary but not sufficient: if the flash were
        dropped from the JS side, the frames inside and outside the window would
        be identical and every other assertion would still pass."""
        frames = self.scenario("flash")["frames"]
        outside = [stop["color"] for stop in frames[0]["gradients"][0]["stops"]]
        inside = [stop["color"] for stop in frames[2]["gradients"][0]["stops"]]
        self.assertNotEqual(outside, inside)

    def test_lanes_actually_differ(self) -> None:
        frame = self.scenario("defaults")["frames"][0]
        signatures = {
            tuple(stop["color"] for stop in gradient["stops"])
            for gradient in frame["gradients"]
        }
        self.assertEqual(len(signatures), len(frame["gradients"]))

    def test_the_rain_survives_at_full_detail(self) -> None:
        """The rain is the layer the canvas nearly cannot carry. At full detail
        the stops must still show the bright spikes it makes, or it has been
        smoothed into the background and the effect is just the slow layers."""
        stops = self.scenario("defaults")["frames"][0]["gradients"][0]["stops"]
        reds = [rgb(stop["color"])[0] for stop in stops]
        self.assertGreater(max(reds) - min(reds), 40, "no spikes: the rain is gone")

    # -- the bands ----------------------------------------------------------

    def test_one_band_per_lane(self) -> None:
        for name, expected in (
            ("defaults", 6), ("single_lane", 1), ("coarse", 3), ("flash", 2)
        ):
            for frame in self.scenario(name)["frames"]:
                self.assertEqual(len(frame["gradients"]), expected, name)
                self.assertEqual(len(frame["fills"]), expected, name)

    def test_the_bands_tile_the_canvas_without_gaps_or_overlap(self) -> None:
        canvas = self.dump["canvas"]
        for name in ("defaults", "single_lane", "coarse"):
            frame = self.scenario(name)["frames"][0]
            edges = sorted(
                (fill["y"], fill["y"] + fill["height"]) for fill in frame["fills"]
            )
            self.assertAlmostEqual(edges[0][0], 0, places=6, msg=name)
            self.assertAlmostEqual(edges[-1][1], canvas["height"], places=6, msg=name)
            for (_, end), (start, _) in zip(edges, edges[1:]):
                self.assertAlmostEqual(end, start, places=6, msg=name)

    def test_band_edges_reproduce_the_k70_lane_rule(self) -> None:
        canvas = self.dump["canvas"]
        frame = self.scenario("defaults")["frames"][0]
        heights = [fill["height"] / canvas["height"] for fill in frame["fills"]]
        self.assertAlmostEqual(heights[0], 0.1, places=6)
        self.assertAlmostEqual(heights[-1], 0.1, places=6)
        for height in heights[1:-1]:
            self.assertAlmostEqual(height, 0.2, places=6)

    # -- resolution ----------------------------------------------------------

    def test_detail_controls_the_stop_count(self) -> None:
        for name, expected in (("defaults", 320), ("coarse", 64), ("flash", 128)):
            gradient = self.scenario(name)["frames"][0]["gradients"][0]
            self.assertEqual(len(gradient["stops"]), expected + 1, name)

    def test_full_detail_is_one_stop_per_canvas_pixel(self) -> None:
        """320 stops across a 320 wide canvas: the finest a canvas can carry, and
        the reason the default is that and not more."""
        self.assertEqual(
            int(self.scenario("defaults")["globals"]["detail"]),
            self.dump["canvas"]["width"],
        )

    # -- geometry and timing -------------------------------------------------

    def test_the_gradient_axis_follows_the_span(self) -> None:
        canvas = self.dump["canvas"]
        for name in ("defaults", "flash"):
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
        first, second = self.scenario("defaults")["frames"]
        self.assertEqual(second["elapsed"] - first["elapsed"], 4)
        self.assertNotEqual(
            [stop["color"] for stop in first["gradients"][0]["stops"]],
            [stop["color"] for stop in second["gradients"][0]["stops"]],
        )

    def test_no_tilt_gives_horizontal_gradients(self) -> None:
        for gradient in self.scenario("coarse")["frames"][0]["gradients"]:
            self.assertEqual(gradient["y1"], 0)
            self.assertGreater(gradient["x1"], 0)

    # -- the shape SignalRGB requires ---------------------------------------

    def test_the_canvas_is_the_size_signalrgb_expects(self) -> None:
        self.assertEqual(self.dump["canvas"], {"width": 320, "height": 200})

    def test_the_effect_is_named_and_attributed(self) -> None:
        self.assertEqual(self.dump["title"], "Stranger Things")
        source = (EFFECTS_DIR / EFFECT).read_text(encoding="utf-8")
        self.assertIn('publisher="headless-lights"', source)

    def test_every_declared_setting_is_actually_read(self) -> None:
        declared = {entry["property"] for entry in self.dump["meta"]}
        self.assertEqual(declared, {"spread", "tilt", "lanes", "detail"})
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

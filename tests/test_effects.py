from __future__ import annotations

import unittest

from headless_lights.effects import (
    borderlands4_color,
    render_borderlands4_fans,
    render_borderlands4_line,
    render_watercolor_fans,
    render_watercolor_line,
    render_stranger_fans,
    stranger_things_color,
    watercolor_color,
)


class WatercolorTests(unittest.TestCase):
    def test_color_is_deterministic_and_in_range(self) -> None:
        color = watercolor_color(0.37, 4.2)

        self.assertEqual(color, watercolor_color(0.37, 4.2))
        self.assertTrue(all(0 <= component <= 255 for component in color))

    def test_frame_matches_lx_topology(self) -> None:
        frame = render_watercolor_fans(6, elapsed=1.5)

        self.assertEqual(len(frame), 6)
        self.assertTrue(all(len(fan) == 18 for fan in frame))

    def test_effect_varies_across_space_and_time(self) -> None:
        first = render_watercolor_fans(6, elapsed=0.0)
        later = render_watercolor_fans(6, elapsed=3.0)

        self.assertNotEqual(first[0][0], first[5][0])
        self.assertNotEqual(first[2][7], later[2][7])

    def test_neighboring_leds_change_smoothly(self) -> None:
        fan = render_watercolor_fans(1, elapsed=2.0)[0]
        largest_step = max(
            sum(abs(left - right) for left, right in zip(fan[index], fan[index + 1]))
            for index in range(len(fan) - 1)
        )

        self.assertLess(largest_step, 80)

    def test_linear_devices_share_time_but_accept_offsets(self) -> None:
        first = render_watercolor_line(12, elapsed=2.0, offset=0.1, span=0.5)
        second = render_watercolor_line(12, elapsed=2.0, offset=0.4, span=0.5)

        self.assertEqual(len(first), 12)
        self.assertNotEqual(first, second)

    def test_stranger_frame_matches_lx_topology(self) -> None:
        frame = render_stranger_fans(6, elapsed=1.5)

        self.assertEqual(len(frame), 6)
        self.assertTrue(all(len(fan) == 18 for fan in frame))

    def test_stranger_is_dark_between_red_flashes(self) -> None:
        quiet = stranger_things_color(0.2, 1.0)
        flash = stranger_things_color(0.2, 7.0 * 0.7110655737704918)

        self.assertGreater(flash[0], quiet[0])
        self.assertGreater(flash[0], flash[2])

    def test_borderlands4_frame_matches_lx_topology(self) -> None:
        frame = render_borderlands4_fans(6, elapsed=1.5)

        self.assertEqual(len(frame), 6)
        self.assertTrue(all(len(fan) == 18 for fan in frame))

    def test_borderlands4_has_moving_warm_layers(self) -> None:
        first = borderlands4_color(0.37, 1.5, lane=2)
        later = borderlands4_color(0.37, 4.0, lane=2)
        line = render_borderlands4_line(
            12, elapsed=1.5, offset=0.1, span=0.8, lane=4
        )

        self.assertTrue(all(0 <= component <= 255 for component in first))
        self.assertGreater(first[0], first[2])
        self.assertNotEqual(first, later)
        self.assertNotEqual(line[0], line[-1])


if __name__ == "__main__":
    unittest.main()

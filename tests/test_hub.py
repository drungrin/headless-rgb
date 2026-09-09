from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from headless_lights.hub import build_hub_server_command


def hub_listing(*, fans: int, leds: int) -> str:
    zones = " ".join("'iCUE LINK LX RGB'" for _ in range(fans))
    led_names = " ".join(f"'LED {index + 1}'" for index in range(leds))
    return f"""2: Corsair iCUE Link System Hub
  Type:           Cooler
  Description:    iCUE Link Device
  Location:       HID: /dev/hidraw8
  Zones: {zones}
  LEDs: {led_names}
"""


class HubTests(unittest.TestCase):
    @patch("headless_lights.hub.subprocess.run")
    def test_builds_one_color_update_per_detected_fan(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            (), 0, hub_listing(fans=6, leds=108), ""
        )

        command, fan_count = build_hub_server_command((0, 0, 255))

        self.assertEqual(fan_count, 6)
        self.assertEqual(command.count("--device"), 6)
        self.assertEqual(command.count("--zone"), 6)
        self.assertEqual(
            [command[index + 1] for index, value in enumerate(command) if value == "--zone"],
            ["0", "1", "2", "3", "4", "5"],
        )
        self.assertIn("127.0.0.1", command)
        self.assertEqual(command.count("0000FF"), 6)

    @patch("headless_lights.hub.subprocess.run")
    def test_accepts_future_eight_fan_topology(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            (), 0, hub_listing(fans=8, leds=144), ""
        )

        _, fan_count = build_hub_server_command((0, 0, 255))

        self.assertEqual(fan_count, 8)

    @patch("headless_lights.hub.subprocess.run")
    def test_rejects_incomplete_led_topology(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            (), 0, hub_listing(fans=6, leds=102), ""
        )

        with self.assertRaisesRegex(RuntimeError, "invalid iCUE LINK topology"):
            build_hub_server_command((0, 0, 255))


if __name__ == "__main__":
    unittest.main()

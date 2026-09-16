from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from headless_lights.aura import set_asiahorse_color


DEVICE_LIST = """0: Corsair Dominator Platinum
  Type:           DRAM
  Location:       I2C: /dev/i2c-3, address 0x19

2: ASUS PRIME Z690-P
  Type:           Motherboard
  Description:    ASUS Aura USB Mainboard Device
  Location:       HID: /dev/hidraw7
"""


class AuraTests(unittest.TestCase):
    @patch("headless_lights.aura.subprocess.run")
    def test_configures_then_colors_only_the_confirmed_zone(self, run) -> None:
        run.side_effect = [
            subprocess.CompletedProcess((), 0, DEVICE_LIST, ""),
            subprocess.CompletedProcess((), 0, "", ""),
            subprocess.CompletedProcess((), 0, "", ""),
        ]

        device = set_asiahorse_color((0, 0, 255))

        self.assertEqual(device.index, 2)
        resize_command = run.call_args_list[1].args[0]
        color_command = run.call_args_list[2].args[0]
        self.assertEqual(resize_command[resize_command.index("--zone") + 1], "2")
        self.assertEqual(resize_command[resize_command.index("--size") + 1], "26")
        self.assertNotIn("--size", color_command)
        self.assertIn("0000FF", color_command)

    @patch("headless_lights.aura.subprocess.run")
    def test_fails_closed_for_an_unexpected_board(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            (), 0, "0: ASUS Other\n  Type:           Motherboard\n", ""
        )

        with self.assertRaisesRegex(RuntimeError, "expected one"):
            set_asiahorse_color((0, 0, 255))


if __name__ == "__main__":
    unittest.main()

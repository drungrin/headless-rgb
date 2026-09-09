from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from headless_lights.ram import parse_device_list, set_corsair_memory_color


DEVICE_LIST = """0: Corsair Dominator Platinum
  Type:           DRAM
  Description:    Corsair RAM RGB Device
  Location:       I2C: /dev/i2c-3, address 0x19
  Modes: [Direct]
  Zones: 'Corsair RAM Zone'

1: Corsair Dominator Platinum
  Type:           DRAM
  Description:    Corsair RAM RGB Device
  Location:       I2C: /dev/i2c-3, address 0x1B

2: ASUS Aura
  Type:           Mainboard
  Location:       HID: /dev/hidraw4
"""


class RamTests(unittest.TestCase):
    def test_parses_openrgb_device_blocks(self) -> None:
        devices = parse_device_list(DEVICE_LIST)

        self.assertEqual(len(devices), 3)
        self.assertEqual(devices[0].index, 0)
        self.assertEqual(devices[1].location, "I2C: /dev/i2c-3, address 0x1B")
        self.assertEqual(devices[2].device_type, "Mainboard")
        self.assertEqual(devices[0].zones, ("Corsair RAM Zone",))

    @patch("headless_lights.ram.subprocess.run")
    def test_updates_only_detected_corsair_dram(self, run) -> None:
        run.side_effect = [
            subprocess.CompletedProcess((), 0, DEVICE_LIST, ""),
            subprocess.CompletedProcess((), 0, "", ""),
        ]

        devices = set_corsair_memory_color((0, 0, 255))

        self.assertEqual([device.index for device in devices], [0, 1])
        color_command = run.call_args_list[1].args[0]
        self.assertEqual(color_command.count("--device"), 2)
        self.assertIn("0000FF", color_command)
        self.assertNotIn("2", color_command)

    @patch("headless_lights.ram.subprocess.run")
    def test_fails_closed_without_corsair_dram(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            (), 0, "0: ASUS Aura\n  Type:           Mainboard\n", ""
        )

        with self.assertRaisesRegex(RuntimeError, "no Corsair RGB memory"):
            set_corsair_memory_color((0, 0, 255))


if __name__ == "__main__":
    unittest.main()

"""Check that the Bragi fork turns side-button bits into the right key presses.

The fork (signalrgb/corsair-bragi-scimitar.js) exists because SignalRGB puts the
Scimitar in software mode, where the twelve side buttons stop being ordinary
input and arrive as a vendor bitmask instead. The bit window, the keymap and the
virtual keys are three places the mapping can silently go wrong, and the only
other way to notice is to press a button and get the wrong character.

So this runs the real plugin under Node against scripted reports, shaped like
the ones tools/scimitar_input_probe.py captured from the hardware, and asserts
on the keys it sent.

Skipped when Node is not installed.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
DUMPER = REPO_ROOT / "signalrgb" / "tests" / "dump_side_buttons.mjs"
LOADER = REPO_ROOT / "signalrgb" / "tests" / "signalrgb-loader.mjs"

# Windows virtual keys for "1" through "0", then VK_OEM_MINUS and VK_OEM_PLUS:
# what the macOS agent already injects, so the mouse matches on both machines.
DEFAULT_VIRTUAL_KEYS = [0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39,
                        0x30, 0xBD, 0xBB]
DEFAULT_LABELS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "-", "="]
SLIPSTREAM_PID = 0x2B00


def _node() -> str | None:
    found = shutil.which("node")
    if found:
        return found
    # winget installs Node outside the shell's default PATH.
    fallback = Path("C:/Program Files/nodejs/node.exe")
    return str(fallback) if fallback.exists() else None


NODE = _node()


@unittest.skipIf(NODE is None, "Node.js is not installed")
class SideButtonTests(unittest.TestCase):
    dumped: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        completed = subprocess.run(
            (NODE, "--experimental-loader", LOADER.as_uri(), str(DUMPER)),
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if completed.returncode != 0:
            raise AssertionError(
                f"the side-button dumper failed:\n{completed.stderr.strip()}"
            )
        cls.dumped = json.loads(completed.stdout)

    def test_each_side_button_sends_its_configured_key(self) -> None:
        keys = self.dumped["eachButton"]["sentKeys"]
        self.assertEqual(
            [key["vkCode"] for key in keys if not key["released"]],
            DEFAULT_VIRTUAL_KEYS,
        )

    def test_every_press_is_matched_by_a_release(self) -> None:
        keys = self.dumped["eachButton"]["sentKeys"]
        self.assertEqual(
            [key["released"] for key in keys],
            [False, True] * len(DEFAULT_VIRTUAL_KEYS),
        )

    def test_the_default_mapping_never_reaches_the_macro_engine(self) -> None:
        # Where the presses used to go, and be dropped: SignalRGB 2.5.74 cannot
        # load the tab that would bind them.
        self.assertEqual(self.dumped["eachButton"]["macroEventCount"], 0)

    def test_only_changed_bits_produce_events(self) -> None:
        # Held together, then released one at a time, with one mask repeated:
        # the repeat must produce nothing, or a held button would stutter.
        self.assertEqual(
            [(key["vkCode"], key["released"])
             for key in self.dumped["overlapping"]["sentKeys"]],
            [(0x31, False), (0x33, False), (0x31, True), (0x33, True)],
        )

    def test_a_button_can_be_remapped(self) -> None:
        self.assertEqual(
            [(key["vkCode"], key["released"])
             for key in self.dumped["remapped"]["sentKeys"]],
            [(0x7C, False), (0x7C, True)],  # F13, and nothing from the other two
        )

    def test_none_swallows_the_button(self) -> None:
        events = self.dumped["remapped"]["macroEvents"]
        self.assertNotIn("Keypad 2", [event["name"] for event in events])

    def test_signalrgb_macro_falls_through_to_upstream(self) -> None:
        events = self.dumped["remapped"]["macroEvents"]
        self.assertEqual(
            [(event["name"], event["released"], event["type"]) for event in events],
            [("Keypad 3", False, "Button Press"), ("Keypad 3", True, "Button Press")],
        )

    def test_bits_outside_the_side_button_window_send_nothing(self) -> None:
        # Bit 3 is the DPI cycle and bit 17 is past the last side button.
        self.assertEqual(self.dumped["outsideWindow"]["sentKeys"], [])

    def test_defaults_match_the_macos_agent(self) -> None:
        defaults = self.dumped["defaults"]
        self.assertEqual(len(defaults), 12)
        self.assertEqual([entry["default"] for entry in defaults], DEFAULT_LABELS)
        self.assertEqual(
            [entry["property"] for entry in defaults],
            [f"sideButton{number}" for number in range(1, 13)],
        )

    def test_every_offered_option_is_selectable(self) -> None:
        for entry in self.dumped["defaults"]:
            self.assertIn(entry["default"], entry["values"])
            self.assertIn("None", entry["values"])
            self.assertIn("SignalRGB macro", entry["values"])

    def test_the_fork_claims_the_slipstream_dongle_alone(self) -> None:
        # Narrow on purpose: the K70 MAX and the other Corsair devices have to
        # stay on the stock plugin, which SignalRGB keys by VID:PID.
        self.assertEqual(self.dumped["productId"], [SLIPSTREAM_PID])


class VendoredForkTests(unittest.TestCase):
    def test_the_committed_fork_matches_upstream_plus_the_patch(self) -> None:
        """Catch a hand-edited fork, or a SignalRGB update that moved an anchor."""
        completed = subprocess.run(
            (sys.executable, "tools/vendor_bragi.py", "--check"),
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if completed.returncode == 2:
            self.skipTest("SignalRGB is not installed on this machine")
        self.assertEqual(
            completed.returncode,
            0,
            f"run python tools/vendor_bragi.py:\n{completed.stderr.strip()}",
        )


if __name__ == "__main__":
    unittest.main()

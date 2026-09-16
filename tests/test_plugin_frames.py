"""Cross-check the SignalRGB plugin against the agent's frame protocol.

The plugin (signalrgb/headless-lights-mac.js) and the agent
(mac-agent/stream_protocol.h) are written in different languages and can drift
apart silently. This runs the real plugin under Node against a fake canvas,
then decodes the bytes it produced with the same parser the repo ships, so a
mismatch fails here instead of showing up as wrong colours on the hardware.

Skipped when Node is not installed.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import unittest

from headless_lights import macstream
from headless_lights.k70max import load_coordinates
from headless_lights.macstream import Device


REPO_ROOT = Path(__file__).resolve().parents[1]
DUMPER = REPO_ROOT / "signalrgb" / "tests" / "dump_frames.mjs"


def _node() -> str | None:
    found = shutil.which("node")
    if found:
        return found
    # winget installs Node outside the shell's default PATH.
    fallback = Path("C:/Program Files/nodejs/node.exe")
    return str(fallback) if fallback.exists() else None


NODE = _node()


def _canvas(x: int, y: int) -> tuple[int, int, int]:
    """Mirror of the fake canvas in dump_frames.mjs."""
    return ((x * 11 + 3) & 0xFF, (y * 29 + 7) & 0xFF, (x * 7 + y * 13 + 1) & 0xFF)


@unittest.skipIf(NODE is None, "Node.js is not installed")
class PluginFrameTests(unittest.TestCase):
    frames: dict[str, dict] = {}

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
                f"the plugin dumper failed:\n{completed.stderr.strip()}"
            )
        cls.frames = json.loads(completed.stdout)

    def _decode(self, model: str) -> tuple[Device, list[tuple[int, int, int]]]:
        return macstream.decode_frame(bytes.fromhex(self.frames[model]["frame"]))

    def test_every_device_produces_a_frame_the_parser_accepts(self) -> None:
        for model in ("k70", "mm700", "g560", "scimitar"):
            device, colors = self._decode(model)
            self.assertEqual(len(colors), macstream.led_count(device))

    def test_device_ids_match_the_protocol(self) -> None:
        expected = {
            "k70": Device.K70,
            "mm700": Device.MM700,
            "g560": Device.G560,
            "scimitar": Device.SCIMITAR,
        }
        for model, device in expected.items():
            self.assertEqual(self._decode(model)[0], device)

    def test_k70_sends_every_wire_slot(self) -> None:
        _, colors = self._decode("k70")
        self.assertEqual(len(colors), 142)

    def test_k70_unmapped_channels_are_black(self) -> None:
        _, colors = self._decode("k70")
        for index, (_, _, mapped) in enumerate(load_coordinates()):
            if not mapped:
                self.assertEqual(
                    colors[index], (0, 0, 0), f"channel {index} should be dark"
                )

    def test_k70_lit_channels_carry_their_canvas_cell(self) -> None:
        """The wire index must route each canvas cell to the right channel."""
        _, colors = self._decode("k70")
        positions = self.frames["layout"]
        wire_index = positions["wireIndex"]
        led_positions = positions["ledPositions"]
        for led, slot in enumerate(wire_index):
            column, row = led_positions[led]
            self.assertEqual(
                colors[slot],
                _canvas(column, row),
                f"LED {led} (cell {column},{row}) landed wrong on slot {slot}",
            )

    def test_zone_devices_carry_their_canvas_cells(self) -> None:
        positions = self.frames["layout"]
        for model in ("mm700", "g560", "scimitar"):
            _, colors = self._decode(model)
            for index, (column, row) in enumerate(positions[model]):
                self.assertEqual(colors[index], _canvas(column, row), f"{model}[{index}]")

    def test_forced_mode_paints_one_colour(self) -> None:
        _, colors = self._decode("k70_forced")
        lit = {color for color in colors if color != (0, 0, 0)}
        self.assertEqual(lit, {(0xFF, 0x66, 0x00)})

    def test_k70_layout_matches_the_generated_tables(self) -> None:
        """The plugin's own view of the layout must match the C++ header."""
        positions = self.frames["layout"]
        expected = [
            index for index, (_, _, mapped) in enumerate(load_coordinates()) if mapped
        ]
        self.assertEqual(positions["wireIndex"], expected)


if __name__ == "__main__":
    unittest.main()

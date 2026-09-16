"""Pin the QSettings blob format the layout tool writes into the registry.

tools/signalrgb_layout.py creates SignalRGB layouts by writing binary values
that Qt's QSettings has to read back. Get the framing wrong and SignalRGB either
ignores the layout or shows devices in nonsense positions, with nothing in its
log to say why -- so the encoder is checked against a blob captured from
SignalRGB itself.

These tests need no registry and run on any platform.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_tool():
    # Build tooling, not part of the installed package, so it is loaded by path.
    path = REPO_ROOT / "tools" / "signalrgb_layout.py"
    spec = importlib.util.spec_from_file_location("signalrgb_layout", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


layout = _load_tool()

# The MM700's entry in this machine's "Stacked" layout, exactly as SignalRGB
# wrote it. 300 bytes: "@Variant(" + type + length + JSON + ")", all UTF-16LE.
CAPTURED = bytes.fromhex(
    "4000560061007200690061006e00740028000000000000008f0000000000000084007b002200"
    "6200720069006700680074006e0065007300730022003a003100300030002c00220066006c00"
    "6900700070006500640022003a00660061006c00730065002c00220066006c00690070007000"
    "6500640056002200 3a00660061006c00730065002c00220072006f0074006100740069006f00"
    "6e0022003a0030002c0022007300630061006c00650022003a007b002200780022003a003300"
    "30002e00320032003200320032003100330037003400350031003100370032002c0022007900"
    "22003a00330030002e003200320032003200320031003300370034003500310031003700320"
    "07d002c002200780022003a00320034002c002200790022003a00350035007d002900".replace(" ", "")
)


class VariantFormatTests(unittest.TestCase):
    def test_decodes_a_blob_signalrgb_actually_wrote(self) -> None:
        data = layout.decode_variant(CAPTURED)
        self.assertEqual(data["x"], 24)
        self.assertEqual(data["y"], 55)
        self.assertEqual(data["brightness"], 100)
        self.assertAlmostEqual(data["scale"]["x"], 30.222221374511720)
        self.assertFalse(data["flipped"])
        self.assertEqual(data["rotation"], 0)

    def test_re_encoding_that_blob_reproduces_its_framing(self) -> None:
        """Byte-identical is too strong -- SignalRGB's JSON spacing is its own --
        but the header, length field and total size must match exactly."""
        text = CAPTURED.decode("utf-16-le")
        payload = text[text.index("{") : text.rindex("}") + 1]
        rebuilt = layout.encode_variant(payload)
        self.assertEqual(rebuilt, CAPTURED)

    def test_round_trip_through_encode_and_decode(self) -> None:
        payload = layout.entry(12, 34, 5.5, brightness=80)
        data = layout.decode_variant(layout.encode_variant(payload))
        self.assertEqual(data["x"], 12)
        self.assertEqual(data["y"], 34)
        self.assertEqual(data["scale"], {"x": 5.5, "y": 5.5})
        self.assertEqual(data["brightness"], 80)

    def test_the_length_field_counts_utf16_code_units(self) -> None:
        payload = layout.entry(1, 2, 3.0)
        raw = layout.encode_variant(payload)
        text = raw.decode("utf-16-le")
        declared = 0
        for char in text[13:17]:
            declared = (declared << 8) | ord(char)
        self.assertEqual(declared, len(payload))
        self.assertEqual(len(text), 9 + 8 + len(payload) + 1)

    def test_a_blob_that_is_not_a_variant_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            layout.decode_variant("not a variant".encode("utf-16-le"))

    def test_entry_carries_every_field_signalrgb_writes(self) -> None:
        captured = set(layout.decode_variant(CAPTURED))
        self.assertEqual(set(json.loads(layout.entry(0, 0, 1.0))), captured)


class SpectrumLayoutTests(unittest.TestCase):
    """The spectrum layout is a computation, not a taste: each device lands at
    the fraction of the canvas its palette offset in effects.py corresponds to."""

    def setUp(self) -> None:
        self.existing = {
            f"{layout.FAN_PREFIX}:000{index}": {
                "x": 0, "y": 0, "scale": {"x": 1.0, "y": 1.0}, "brightness": 100,
            }
            for index in range(8)
        }
        self.existing["0b05:19af:9876543210"] = {
            "x": 140, "y": 80, "scale": {"x": 40.0, "y": 40.0}, "brightness": 100,
        }
        self.placement, self.advice = layout.build_spectrum(self.existing)

    def test_devices_land_on_their_palette_offset(self) -> None:
        spans = layout.spectrum_spans(self.existing)
        total = max(offset + span for offset, span in spans.values())
        for uid, (offset, _) in spans.items():
            data = json.loads(self.placement[uid])
            expected = round(offset / total * layout.CANVAS_WIDTH)
            self.assertEqual(data["x"], expected, uid)

    def test_device_width_matches_its_palette_span(self) -> None:
        spans = layout.spectrum_spans(self.existing)
        total = max(offset + span for offset, span in spans.values())
        for uid, (_, span) in spans.items():
            data = json.loads(self.placement[uid])
            width = layout.size_of(uid)[0] * data["scale"]["x"]
            self.assertAlmostEqual(
                width, span / total * layout.CANVAS_WIDTH, places=6, msg=uid
            )

    def test_nothing_runs_off_the_canvas(self) -> None:
        spans = layout.spectrum_spans(self.existing)
        for uid in spans:
            data = json.loads(self.placement[uid])
            width = layout.size_of(uid)[0] * data["scale"]["x"]
            self.assertLessEqual(data["x"] + width, layout.CANVAS_WIDTH + 1, uid)

    def test_tilt_is_zero_so_vertical_position_cannot_shift_the_palette(self) -> None:
        self.assertEqual(self.advice["tilt"], 0)

    def test_devices_the_layout_has_no_opinion_about_keep_their_place(self) -> None:
        """Anything omitted from a layout key gets auto-placed by SignalRGB and
        jumps somewhere unrelated, so it has to be carried across."""
        carried = json.loads(self.placement["0b05:19af:9876543210"])
        self.assertEqual((carried["x"], carried["y"]), (140, 80))
        self.assertEqual(carried["scale"]["x"], 40.0)


class DeskLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.existing = {
            f"{layout.FAN_PREFIX}:000{index}": {
                "x": 0, "y": 0, "scale": {"x": 1.0, "y": 1.0}, "brightness": 100,
            }
            for index in range(8)
        }
        self.existing["I2CBUS:25"] = {
            "x": 0, "y": 0, "scale": {"x": 1.0, "y": 1.0}, "brightness": 100,
        }
        self.placement, self.advice = layout.build_desk(self.existing)

    def test_the_strip_spans_the_whole_wall(self) -> None:
        data = json.loads(self.placement[layout.BEELIGHT])
        width = layout.SIZES[layout.BEELIGHT][0] * data["scale"]["x"]
        self.assertEqual(data["x"], 0)
        self.assertAlmostEqual(width, layout.CANVAS_WIDTH, places=6)
        self.assertLess(data["y"], 10, "the wall strip sits at the back")

    def test_everything_in_the_case_is_on_the_right(self) -> None:
        for uid in [layout.ASIAHORSE, "I2CBUS:25"] + layout.fan_uids(self.existing):
            data = json.loads(self.placement[uid])
            self.assertGreaterEqual(data["x"], layout.CASE_LEFT, uid)

    def test_the_peripherals_are_on_the_desk_not_in_the_case(self) -> None:
        for uid in (
            "signalrgb-mac-bridge-k70",
            "signalrgb-mac-bridge-mm700",
            "signalrgb-mac-bridge-scimitar",
            "signalrgb-mac-bridge-g560",
        ):
            data = json.loads(self.placement[uid])
            self.assertLess(data["x"], layout.CASE_LEFT, uid)

    def test_the_keyboard_and_mouse_sit_on_the_mousepad(self) -> None:
        pad = json.loads(self.placement["signalrgb-mac-bridge-mm700"])
        pad_box = (
            pad["x"],
            pad["y"],
            pad["x"] + layout.SIZES["signalrgb-mac-bridge-mm700"][0] * pad["scale"]["x"],
            pad["y"] + layout.SIZES["signalrgb-mac-bridge-mm700"][1] * pad["scale"]["y"],
        )
        for uid in ("signalrgb-mac-bridge-k70", "signalrgb-mac-bridge-scimitar"):
            data = json.loads(self.placement[uid])
            self.assertTrue(
                pad_box[0] <= data["x"] <= pad_box[2]
                and pad_box[1] <= data["y"] <= pad_box[3],
                f"{uid} should rest on the pad, not beside it",
            )

    def test_the_colour_is_spatially_coherent(self) -> None:
        """The physical layout's goal is the opposite of the spectrum layout's:
        a device should show the share of the palette it physically occupies, so
        the wall strip and the keyboard under it agree on the colour. One cycle
        across the room is what produces that."""
        span_x = self.advice["spread"] / 10
        self.assertAlmostEqual(span_x, 1.0, delta=0.3)

        def share(uid: str) -> float:
            data = json.loads(self.placement[uid])
            return layout.SIZES[uid][0] * data["scale"]["x"] / layout.CANVAS_WIDTH

        wall = share(layout.BEELIGHT)
        keyboard = share("signalrgb-mac-bridge-k70")
        self.assertAlmostEqual(wall, 1.0, delta=0.02, msg="the wall spans the room")
        self.assertLess(keyboard, wall, "the keyboard is a part of the room")
        self.assertGreater(keyboard, 0.3)

    def test_the_tilt_stays_gentle(self) -> None:
        """A physical wash tilts a little across the room; a steep tilt would
        make devices at different heights disagree about the colour."""
        self.assertGreater(self.advice["tilt"], 0)
        self.assertLessEqual(self.advice["tilt"], 40)


if __name__ == "__main__":
    unittest.main()

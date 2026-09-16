"""Cross-check the Beelight SignalRGB plugin against this project's parser.

signalrgb/beelight.js reimplements src/headless_lights/beelight/protocol.py in
JavaScript, so the two can drift apart silently. This runs the real plugin under
Node against a fake serial port, feeds it acknowledgements encoded by the Python
implementation, and decodes everything it writes back with the Python parser.

Skipped when Node is not installed.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import unittest

from headless_lights.beelight import protocol


REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = REPO_ROOT / "signalrgb" / "tests"
DUMPER = TESTS_DIR / "dump_beelight_frames.mjs"
LOADER = TESTS_DIR / "signalrgb-loader.mjs"

# The strip reports 33 pixels across 2 channels. Same blob as the protocol tests.
SYNC_CONFIG_BLOB = bytes.fromhex("21000221000000000000000000000000000000")
FIRMWARE_BLOB = bytes.fromhex("0100000001000000") + b"diy-d6lfwphynoh3"


def _node() -> str | None:
    found = shutil.which("node")
    if found:
        return found
    # winget installs Node outside the shell's default PATH.
    fallback = Path("C:/Program Files/nodejs/node.exe")
    return str(fallback) if fallback.exists() else None


NODE = _node()


def _canvas(x: int, y: int) -> tuple[int, int, int]:
    """Mirror of the fake canvas in dump_beelight_frames.mjs."""
    return ((x * 11 + 3) & 0xFF, (y * 29 + 7) & 0xFF, (x * 7 + y * 13 + 1) & 0xFF)


def _decoy_frame() -> bytes:
    """A checksum-valid frame carrying no command, as the idle strip emits."""
    key = bytes.fromhex("0102030405")
    body = bytes((0x30 + (len(key) ^ 1),)) + key
    payload = bytes((sum(body) & 0xFF,)) + body
    return protocol.HEADER + len(payload).to_bytes(2, "little") + payload


@unittest.skipIf(NODE is None, "Node.js is not installed")
class BeelightPluginTests(unittest.TestCase):
    dump: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        ack = protocol.ATTRIBUTE_ACKNOWLEDGEMENT
        scripted = {
            "ackFirmware": protocol.encode_frame(
                protocol.COMMAND_FIRMWARE, FIRMWARE_BLOB, attribute=ack
            ).hex(),
            "ackSyncConfig": protocol.encode_frame(
                protocol.COMMAND_SYNC_CONFIG, SYNC_CONFIG_BLOB, attribute=ack
            ).hex(),
            "ackControl": protocol.encode_frame(
                protocol.COMMAND_CONTROL, b"", attribute=ack
            ).hex(),
            "heartbeatRequest": protocol.encode_frame(
                protocol.COMMAND_HEARTBEAT, b""
            ).hex(),
            "decoy": _decoy_frame().hex(),
        }
        completed = subprocess.run(
            (NODE, "--experimental-loader", LOADER.as_uri(), str(DUMPER)),
            cwd=REPO_ROOT,
            input=json.dumps(scripted),
            capture_output=True,
            text=True,
            timeout=180,
        )
        if completed.returncode != 0:
            raise AssertionError(
                f"the Beelight dumper failed:\n{completed.stderr.strip()}"
            )
        cls.dump = json.loads(completed.stdout)

    # -- helpers ------------------------------------------------------------

    def _frames(self, scenario: str) -> list[protocol.Frame]:
        return [
            protocol.decode_frame(bytes.fromhex(write))
            for write in self.dump[scenario]["writes"]
        ]

    def _control(self, frame: protocol.Frame) -> tuple[int, int, bytes]:
        """Split a control frame into (control, channel, inner payload)."""
        self.assertEqual(frame.command, protocol.COMMAND_CONTROL)
        control, channel = frame.data[0], frame.data[1]
        length = int.from_bytes(frame.data[2:4], "little")
        payload = frame.data[4:]
        self.assertEqual(len(payload), length, "inner length must match")
        return control, channel, payload

    def _pixels(self, frame: protocol.Frame) -> list[tuple[int, int, int]]:
        control, _, payload = self._control(frame)
        self.assertEqual(control, protocol.CONTROL_RGB_TRANSFER)
        count = int.from_bytes(payload[:2], "little")
        rgb = payload[2:]
        self.assertEqual(len(rgb), count * 3, "pixel count must match the payload")
        return [
            (rgb[index * 3], rgb[index * 3 + 1], rgb[index * 3 + 2])
            for index in range(count)
        ]

    # -- protocol vectors ---------------------------------------------------

    def test_js_decodes_the_vendor_firmware_frame(self) -> None:
        decoded = self.dump["vectors"]["decodedFirmware"]
        expected = protocol.decode_frame(bytes.fromhex("55aa5a0900ff3441af2ef8c641ae"))
        self.assertEqual(decoded["command"], expected.command)
        self.assertEqual(decoded["attribute"], expected.attribute)
        self.assertEqual(bytes(decoded["data"]), expected.data)
        self.assertEqual(decoded["keySize"], expected.key_size)

    def test_js_encoding_matches_the_vendor_vector(self) -> None:
        expected = protocol.encode_frame(
            5, b"\x00" * 4, key=bytes.fromhex("a64361aada41e7"), key_size=7
        )
        self.assertEqual(self.dump["vectors"]["encodedControl"], expected.hex())
        self.assertEqual(
            self.dump["vectors"]["encodedControl"],
            "55aa5a0f003e36a64361aada41e7a64661aada41",
        )

    def test_js_control_data_matches_python(self) -> None:
        vectors = self.dump["vectors"]["controlData"]
        self.assertEqual(
            vectors["switchOn"],
            protocol.control_data(protocol.CONTROL_SWITCH, b"\x01").hex(),
        )
        self.assertEqual(
            vectors["brightness"],
            protocol.control_data(
                protocol.CONTROL_BRIGHTNESS, (100).to_bytes(2, "little")
            ).hex(),
        )
        self.assertEqual(
            vectors["color"],
            protocol.control_data(protocol.CONTROL_COLOR, b"\xff\x00\x00").hex(),
        )

    def test_js_sync_config_matches_python(self) -> None:
        expected = protocol.parse_sync_config(SYNC_CONFIG_BLOB)
        parsed = self.dump["vectors"]["syncConfig"]
        self.assertEqual(parsed["totalPixels"], expected.total_pixels)
        self.assertEqual(parsed["channelCount"], expected.channel_count)

    def test_js_rejects_unusable_sync_config(self) -> None:
        """A short, empty or absurd pixel count must not reach setControllableLeds."""
        vectors = self.dump["vectors"]
        self.assertIsNone(vectors["syncConfigTruncated"])
        self.assertIsNone(vectors["syncConfigZero"])
        self.assertIsNone(vectors["syncConfigHuge"])

    def test_js_decoder_returns_null_instead_of_throwing(self) -> None:
        """Python raises here; the plugin must not, or the engine quarantines it."""
        with self.assertRaises(protocol.ProtocolError):
            protocol.decode_frame(bytes.fromhex("55aa5a0300010203"))
        self.assertIsNone(self.dump["vectors"]["decodedGarbage"])

    def test_js_frame_stream_resyncs_across_reads(self) -> None:
        first, second = self.dump["vectors"]["splitStream"]
        self.assertEqual(first, [], "an incomplete frame must not be emitted")
        self.assertEqual(second, ["55aa5a0900ff3441af2ef8c641ae"])

    # -- handshake ----------------------------------------------------------

    def test_handshake_succeeds_and_opens_the_port_at_115200_8n1(self) -> None:
        handshake = self.dump["handshake"]
        self.assertTrue(handshake["initialized"], handshake["logs"])
        self.assertEqual(
            handshake["connectOptions"],
            {"baudRate": 115200, "dataBits": 8, "parity": "None", "stopBits": "One"},
        )

    def test_handshake_sends_the_commands_in_order(self) -> None:
        frames = self._frames("handshake")
        self.assertEqual(
            [frame.command for frame in frames],
            [
                protocol.COMMAND_FIRMWARE,
                protocol.COMMAND_SYNC_CONFIG,
                protocol.COMMAND_CONTROL,
                protocol.COMMAND_CONTROL,
                protocol.COMMAND_CONTROL,
            ],
        )
        self.assertEqual(
            [self._control(frame)[0] for frame in frames[2:]],
            [
                protocol.CONTROL_WORK_MODE,
                protocol.CONTROL_SWITCH,
                protocol.CONTROL_BRIGHTNESS,
            ],
            "PC mode must be set before the strip is switched on",
        )

    def test_handshake_control_payloads_match_python(self) -> None:
        frames = self._frames("handshake")
        work_mode, switch, brightness = frames[2], frames[3], frames[4]
        self.assertEqual(self._control(work_mode)[2], b"\x00\x00\x00")
        self.assertEqual(self._control(switch)[2], b"\x01")
        self.assertEqual(self._control(brightness)[2], (100).to_bytes(2, "little"))
        for frame in frames[2:]:
            self.assertEqual(self._control(frame)[1], 0xFF, "channel must be broadcast")

    def test_led_count_comes_from_the_device_not_a_constant(self) -> None:
        expected = protocol.parse_sync_config(SYNC_CONFIG_BLOB).total_pixels
        self.assertEqual(self.dump["handshake"]["ledCount"], expected)
        self.assertEqual(self.dump["handshake"]["size"], [expected, 1])

    def test_the_engine_is_asked_for_thirty_frames_per_second(self) -> None:
        self.assertIn(30, self.dump["handshake"]["frameRateTargets"])

    # -- rendering ----------------------------------------------------------

    def test_render_writes_one_pixel_frame_carrying_the_canvas(self) -> None:
        render = self.dump["render"]
        frames = self._frames("render")[render["handshakeWrites"] :]
        self.assertEqual(len(frames), 1)
        pixels = self._pixels(frames[0])
        self.assertEqual(len(pixels), render["ledCount"])
        self.assertEqual(pixels, [_canvas(x, 0) for x in range(render["ledCount"])])

    def test_render_frame_matches_the_python_builder_byte_for_byte(self) -> None:
        """The whole frame, not just its payload — framing included."""
        render = self.dump["render"]
        written = bytes.fromhex(render["writes"][render["handshakeWrites"]])
        colors = [_canvas(x, 0) for x in range(render["ledCount"])]
        expected = protocol.pixels_request(colors)
        # The obfuscation key is random per frame, so compare the decoded frames
        # and the framing, not the raw ciphertext.
        self.assertEqual(written[:3], expected[:3])
        self.assertEqual(
            protocol.decode_frame(written).data, protocol.decode_frame(expected).data
        )

    def test_forced_mode_paints_one_colour(self) -> None:
        forced = self.dump["forced"]
        frames = self._frames("forced")[forced["handshakeWrites"] :]
        pixels = self._pixels(frames[0])
        self.assertEqual(set(pixels), {(0xFF, 0x66, 0x00)})
        self.assertEqual(len(pixels), forced["ledCount"])

    def test_a_second_render_inside_the_interval_writes_nothing(self) -> None:
        pacing = self.dump["pacing"]
        self.assertEqual(pacing["afterFirst"], pacing["handshakeWrites"] + 1)
        self.assertEqual(pacing["afterSecond"], pacing["afterFirst"])
        self.assertEqual(pacing["afterInterval"], pacing["afterFirst"] + 1)

    # -- inbound traffic ----------------------------------------------------

    def test_a_heartbeat_request_is_acknowledged(self) -> None:
        heartbeat = self.dump["heartbeat"]
        frames = self._frames("heartbeat")[heartbeat["handshakeWrites"] :]
        commands = [(frame.attribute, frame.command) for frame in frames]
        self.assertIn(
            (protocol.ATTRIBUTE_ACKNOWLEDGEMENT, protocol.COMMAND_HEARTBEAT), commands
        )
        acknowledgement = frames[commands.index(
            (protocol.ATTRIBUTE_ACKNOWLEDGEMENT, protocol.COMMAND_HEARTBEAT)
        )]
        self.assertEqual(acknowledgement.data, b"")

    def test_a_decoy_frame_is_ignored_rather_than_answered(self) -> None:
        decoy = self.dump["decoy"]
        frames = self._frames("decoy")[decoy["handshakeWrites"] :]
        self.assertEqual(
            [frame.command for frame in frames],
            [protocol.COMMAND_CONTROL, protocol.COMMAND_CONTROL],
            "only the two pixel frames; the decoy must produce no reply",
        )

    # -- shutdown and failure paths -----------------------------------------

    def test_shutdown_paints_the_shutdown_colour_and_releases_the_port(self) -> None:
        shutdown = self.dump["shutdown"]
        frames = self._frames("shutdown")[shutdown["beforeShutdown"] :]
        self.assertTrue(frames, "shutdown must write something")
        for frame in frames:
            self.assertEqual(set(self._pixels(frame)), {(0x12, 0x34, 0x56)})
        self.assertFalse(shutdown["isOpen"], "COM3 must not stay held")

    def test_a_silent_device_fails_initialization_but_still_publishes_leds(self) -> None:
        silent = self.dump["silentDevice"]
        self.assertFalse(silent["initialized"])
        self.assertEqual(silent["ledCount"], 33, "fall back to the known layout")
        self.assertTrue(any("firmware" in line for line in silent["logs"]), silent["logs"])

    def test_a_port_that_will_not_open_never_writes(self) -> None:
        failure = self.dump["connectFailure"]
        self.assertFalse(failure["initialized"])
        self.assertEqual(failure["writes"], [])
        self.assertTrue(any("connect" in line for line in failure["logs"]))

    def test_an_unplugged_port_stops_streaming_instead_of_writing_blind(self) -> None:
        unplugged = self.dump["unplugged"]
        self.assertEqual(len(unplugged["writes"]), unplugged["beforeUnplug"])
        self.assertFalse(unplugged["isOpen"])


if __name__ == "__main__":
    unittest.main()

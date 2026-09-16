from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock


REPO_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_PATH = REPO_ROOT / "windows" / "signalrgb-mac-bridge.py"


def _load_bridge():
    spec = importlib.util.spec_from_file_location("signalrgb_mac_bridge", BRIDGE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bridge = _load_bridge()


def frame(device: int, fill: int = 0x42) -> bytes:
    count = bridge.LED_COUNTS[device]
    payload = bytes([fill]) * (count * 3)
    return bridge.MAGIC + bytes((bridge.VERSION, device)) + len(payload).to_bytes(2, "little") + payload


class FrameValidationTests(unittest.TestCase):
    def test_accepts_exact_frame_for_each_device(self) -> None:
        for device in range(len(bridge.LED_COUNTS)):
            self.assertTrue(bridge.valid_frame(frame(device)))

    def test_rejects_bad_magic(self) -> None:
        self.assertFalse(bridge.valid_frame(b"XX" + frame(1)[2:]))

    def test_rejects_unknown_version(self) -> None:
        data = bytearray(frame(1))
        data[2] = 9
        self.assertFalse(bridge.valid_frame(bytes(data)))

    def test_rejects_unknown_device(self) -> None:
        data = bytearray(frame(1))
        data[3] = 9
        self.assertFalse(bridge.valid_frame(bytes(data)))

    def test_rejects_length_that_disagrees_with_device(self) -> None:
        data = bytearray(frame(1))
        data[4:6] = (12).to_bytes(2, "little")
        self.assertFalse(bridge.valid_frame(bytes(data)))

    def test_rejects_truncated_or_extended_frame(self) -> None:
        data = frame(0)
        self.assertFalse(bridge.valid_frame(data[:-1]))
        self.assertFalse(bridge.valid_frame(data + b"\x00"))


class ForwardingTests(unittest.TestCase):
    def test_forwards_valid_frame_unchanged(self) -> None:
        connection = Mock()
        instance = bridge.Bridge()
        instance.upstream = connection
        data = frame(2, 0x7F)

        self.assertTrue(instance.forward(data))
        connection.sendall.assert_called_once_with(data)
        self.assertEqual(instance.frames, 1)
        self.assertEqual(instance.device_frames, [0, 0, 1, 0])
        self.assertEqual(instance.rejected, 0)

    def test_never_forwards_invalid_frame(self) -> None:
        connection = Mock()
        instance = bridge.Bridge()
        instance.upstream = connection

        self.assertFalse(instance.forward(b"not a frame"))
        connection.sendall.assert_not_called()
        self.assertEqual(instance.rejected, 1)

    def test_disconnects_after_tcp_write_failure(self) -> None:
        connection = Mock()
        connection.sendall.side_effect = OSError("gone")
        instance = bridge.Bridge(reconnect_delay=0)
        instance.upstream = connection

        self.assertFalse(instance.forward(frame(3)))
        self.assertIsNone(instance.upstream)
        connection.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from unittest.mock import Mock

from headless_lights.beelight import protocol
from headless_lights.beelight.device import BeelightDevice


class ProtocolTests(unittest.TestCase):
    def test_vendor_firmware_request_vector(self) -> None:
        packet = bytes.fromhex("55AA5A0900FF3441AF2EF8C641AE")
        frame = protocol.decode_frame(packet, attribute=protocol.ATTRIBUTE_REQUEST)
        self.assertEqual(frame.command, protocol.COMMAND_FIRMWARE)
        self.assertEqual(frame.data, b"")
        self.assertEqual(frame.key_size, 5)

    def test_encode_matches_chosen_vendor_vector(self) -> None:
        packet = protocol.encode_frame(
            5,
            b"\x00\x00\x00\x00",
            key=bytes.fromhex("A64361AADA41E7"),
            key_size=7,
        )
        self.assertEqual(
            packet.hex(),
            "55aa5a0f003e36a64361aada41e7a64661aada41",
        )

    def test_acknowledgement_flips_transport_attribute(self) -> None:
        key = bytes.fromhex("A64361AADA41E7")
        request = protocol.encode_frame(5, b"\x00" * 4, key=key, key_size=7)
        acknowledgement = protocol.encode_frame(
            5,
            b"\x00" * 4,
            attribute=protocol.ATTRIBUTE_ACKNOWLEDGEMENT,
            key=key,
            key_size=7,
        )
        self.assertEqual(request[14] ^ acknowledgement[14], 1)
        decoded = protocol.decode_frame(
            acknowledgement,
            attribute=protocol.ATTRIBUTE_ACKNOWLEDGEMENT,
        )
        self.assertEqual((decoded.command, decoded.data), (5, b"\x00" * 4))

    def test_checksum_rejects_corruption(self) -> None:
        packet = bytearray(protocol.encode_frame(1, key=b"\x01\x02\x03", key_size=3))
        packet[-1] ^= 1
        with self.assertRaises(protocol.ProtocolError):
            protocol.decode_frame(bytes(packet), attribute=protocol.ATTRIBUTE_REQUEST)

    def test_stream_handles_noise_and_partial_reads(self) -> None:
        packet = protocol.encode_frame(1, key=b"\x01\x02\x03", key_size=3)
        stream = protocol.FrameStream()
        self.assertEqual(stream.feed(b"noise" + packet[:4]), [])
        self.assertEqual(stream.feed(packet[4:]), [packet])

    def test_control_payloads_match_vendor_plaintext(self) -> None:
        self.assertEqual(
            protocol.control_data(protocol.CONTROL_SWITCH, b"\x01"),
            bytes.fromhex("01ff010001"),
        )
        self.assertEqual(
            protocol.control_data(protocol.CONTROL_BRIGHTNESS, (100).to_bytes(2, "little")),
            bytes.fromhex("02ff02006400"),
        )
        self.assertEqual(
            protocol.control_data(protocol.CONTROL_COLOR, bytes((255, 0, 0))),
            bytes.fromhex("04ff0300ff0000"),
        )

    def test_parse_color(self) -> None:
        self.assertEqual(protocol.parse_color("#12abEF"), (0x12, 0xAB, 0xEF))
        with self.assertRaises(ValueError):
            protocol.parse_color("red")

    def test_parse_real_device_info(self) -> None:
        firmware = protocol.parse_firmware_info(bytes.fromhex(
            "01000101010001016469792d64366c66777068796e6f6833000035a80d000080"
            "664411a77805dc111864737a2d676c6f776d776f726d2e636f6d202d2d627920"
            "6665696765006874747068747470733a2f2f696c79746d692e636f6d0000e8f7"
            "ff1f2de9f04104460e46"
        ))
        self.assertEqual(firmware.product_id, "diy-d6lfwphynoh3")
        self.assertEqual(firmware.bsp_version, (1, 0, 1, 1))
        self.assertEqual(firmware.app_version, (1, 0, 1, 1))
        self.assertEqual(firmware.manufacturer, "dsz-glowmworm.com --by feige")
        self.assertEqual(firmware.market, "httphttps://ilytmi.com")

        config = protocol.parse_sync_config(
            bytes.fromhex("21000221000000000000000000000000000000")
        )
        self.assertEqual(config.total_pixels, 33)
        self.assertEqual(config.channel_count, 2)
        self.assertEqual(config.channel_pixels, (33, 0))

    def test_acknowledged_control_retries_transient_timeouts(self) -> None:
        device = BeelightDevice("/dev/unused")
        device.send = Mock(side_effect=(TimeoutError(), TimeoutError(), None))
        factory = Mock(return_value=b"packet")

        device._send_acknowledged_control(factory)

        self.assertEqual(factory.call_count, 3)
        self.assertEqual(device.send.call_count, 3)

    def test_initialization_query_retries_transient_timeouts(self) -> None:
        device = BeelightDevice("/dev/unused")
        response = type("Response", (), {"data": b"ok"})()
        device.query = Mock(side_effect=(TimeoutError(), response))

        self.assertEqual(device._query_with_retry(protocol.COMMAND_FIRMWARE), b"ok")
        self.assertEqual(device.query.call_count, 2)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from headless_lights import macstream
from headless_lights.macstream import Device, StreamError


class EncodeFrameTests(unittest.TestCase):
    def test_header_layout(self) -> None:
        frame = macstream.encode_frame(Device.MM700, [(1, 2, 3)] * 3)
        self.assertEqual(frame[0:2], b"SG")
        self.assertEqual(frame[2], macstream.VERSION)
        self.assertEqual(frame[3], int(Device.MM700))
        self.assertEqual(frame[4:6], (9).to_bytes(2, "little"))
        self.assertEqual(frame[6:], bytes([1, 2, 3] * 3))

    def test_length_is_little_endian(self) -> None:
        frame = macstream.encode_frame(Device.K70, [(0, 0, 0)] * 142)
        # 426 == 0x01AA, so the two bytes differ and byte order is observable.
        self.assertEqual(frame[4], 0xAA)
        self.assertEqual(frame[5], 0x01)

    def test_exact_size_per_device(self) -> None:
        expected = {
            Device.K70: 6 + 142 * 3,
            Device.MM700: 6 + 3 * 3,
            Device.G560: 6 + 4 * 3,
            Device.SCIMITAR: 6 + 3 * 3,
        }
        for device, size in expected.items():
            colors = [(0, 0, 0)] * macstream.led_count(device)
            self.assertEqual(len(macstream.encode_frame(device, colors)), size)

    def test_rejects_wrong_led_count(self) -> None:
        with self.assertRaisesRegex(StreamError, "needs 3 colors"):
            macstream.encode_frame(Device.MM700, [(0, 0, 0)] * 2)

    def test_rejects_out_of_range_component(self) -> None:
        with self.assertRaises(StreamError):
            macstream.encode_frame(Device.MM700, [(0, 0, 256)] * 3)

    def test_rejects_short_tuple(self) -> None:
        with self.assertRaises(StreamError):
            macstream.encode_frame(Device.MM700, [(0, 0)] * 3)


class DecodeFrameTests(unittest.TestCase):
    def test_round_trips_every_device(self) -> None:
        for device in Device:
            count = macstream.led_count(device)
            colors = [(index % 256, 7, 9) for index in range(count)]
            decoded_device, decoded = macstream.decode_frame(
                macstream.encode_frame(device, colors)
            )
            self.assertEqual(decoded_device, device)
            self.assertEqual(decoded, colors)

    def test_rejects_bad_magic(self) -> None:
        frame = bytearray(macstream.encode_frame(Device.MM700, [(0, 0, 0)] * 3))
        frame[0] = ord("X")
        with self.assertRaisesRegex(StreamError, "magic"):
            macstream.decode_frame(bytes(frame))

    def test_rejects_unknown_version(self) -> None:
        frame = bytearray(macstream.encode_frame(Device.MM700, [(0, 0, 0)] * 3))
        frame[2] = 9
        with self.assertRaisesRegex(StreamError, "version"):
            macstream.decode_frame(bytes(frame))

    def test_rejects_unknown_device(self) -> None:
        frame = bytearray(macstream.encode_frame(Device.MM700, [(0, 0, 0)] * 3))
        frame[3] = 7
        with self.assertRaisesRegex(StreamError, "device"):
            macstream.decode_frame(bytes(frame))

    def test_rejects_length_that_does_not_match_the_device(self) -> None:
        frame = bytearray(macstream.encode_frame(Device.MM700, [(0, 0, 0)] * 3))
        frame[4] = 12
        with self.assertRaisesRegex(StreamError, "payload bytes"):
            macstream.decode_frame(bytes(frame))

    def test_rejects_truncated_frame(self) -> None:
        frame = macstream.encode_frame(Device.MM700, [(0, 0, 0)] * 3)
        with self.assertRaises(StreamError):
            macstream.decode_frame(frame[:-1])


class RenderTests(unittest.TestCase):
    def test_k70_fills_every_wire_slot(self) -> None:
        colors = macstream.render_k70("watercolor", 12.0)
        self.assertEqual(len(colors), macstream.led_count(Device.K70))

    def test_k70_leaves_unmapped_channels_black(self) -> None:
        colors = macstream.render_k70("watercolor", 12.0)
        # Channels 0-3 have no physical LED in k70max_layout.h.
        for index in range(4):
            self.assertEqual(colors[index], (0, 0, 0))

    def test_k70_lights_mapped_channels(self) -> None:
        colors = macstream.render_k70("watercolor", 12.0)
        self.assertNotEqual(colors[4], (0, 0, 0))

    def test_zone_counts_match_the_devices(self) -> None:
        for device in (Device.MM700, Device.G560, Device.SCIMITAR):
            colors = macstream.render_zones(device, "borderlands-4", 3.0)
            self.assertEqual(len(colors), macstream.led_count(device))

    def test_zones_differ_from_each_other(self) -> None:
        colors = macstream.render_zones(Device.G560, "watercolor", 5.0)
        self.assertGreater(len(set(colors)), 1)

    def test_render_is_deterministic_for_a_given_phase(self) -> None:
        first = macstream.render_zones(Device.MM700, "stranger-things", 41.5)
        second = macstream.render_zones(Device.MM700, "stranger-things", 41.5)
        self.assertEqual(first, second)

    def test_every_component_is_a_byte(self) -> None:
        for effect in macstream.EFFECTS:
            for device in Device:
                for color in macstream.render_zones(device, effect, 2.5):
                    for component in color:
                        self.assertIsInstance(component, int)
                        self.assertGreaterEqual(component, 0)
                        self.assertLessEqual(component, 255)

    def test_rejects_unknown_effect(self) -> None:
        with self.assertRaises(StreamError):
            macstream.render_zones(Device.MM700, "rainbow", 1.0)

    def test_render_color_repeats_one_value(self) -> None:
        colors = macstream.render_color(Device.SCIMITAR, (10, 20, 30))
        self.assertEqual(colors, [(10, 20, 30)] * 3)


class FrameStreamTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stream = macstream.FrameStream()
        self.mm700 = macstream.encode_frame(Device.MM700, [(1, 2, 3)] * 3)
        self.k70 = macstream.encode_frame(
            Device.K70, [(9, 9, 9)] * macstream.led_count(Device.K70)
        )

    def test_single_whole_frame(self) -> None:
        frames = self.stream.feed(self.mm700)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0][0], Device.MM700)

    def test_two_frames_in_one_read(self) -> None:
        frames = self.stream.feed(self.mm700 + self.k70)
        self.assertEqual([device for device, _ in frames], [Device.MM700, Device.K70])

    def test_frame_split_across_reads(self) -> None:
        self.assertEqual(self.stream.feed(self.k70[:10]), [])
        self.assertEqual(self.stream.feed(self.k70[10:200]), [])
        frames = self.stream.feed(self.k70[200:])
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0][0], Device.K70)

    def test_byte_at_a_time(self) -> None:
        collected = []
        for index in range(len(self.mm700)):
            collected.extend(self.stream.feed(self.mm700[index : index + 1]))
        self.assertEqual(len(collected), 1)

    def test_split_exactly_between_the_two_magic_bytes(self) -> None:
        self.assertEqual(self.stream.feed(self.mm700[:1]), [])
        frames = self.stream.feed(self.mm700[1:])
        self.assertEqual(len(frames), 1)

    def test_leading_garbage_is_discarded(self) -> None:
        frames = self.stream.feed(b"\x00\xff garbage " + self.mm700)
        self.assertEqual(len(frames), 1)

    def test_garbage_that_starts_like_a_frame(self) -> None:
        # Valid magic, bad version: the parser must drop one byte and recover.
        frames = self.stream.feed(b"SG\x99\x00\x00\x00" + self.mm700)
        self.assertEqual(len(frames), 1)

    def test_magic_appearing_inside_a_payload_does_not_desync(self) -> None:
        colors = [(0x53, 0x47, 0x01), (0x00, 0x09, 0x00), (1, 2, 3)]
        frame = macstream.encode_frame(Device.MM700, colors)
        frames = self.stream.feed(frame + self.mm700)
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0][1], colors)

    def test_nothing_is_retained_after_whole_frames(self) -> None:
        self.stream.feed(self.mm700 + self.k70 + self.mm700)
        self.assertEqual(len(self.stream.buffer), 0)

    def test_only_a_partial_magic_is_retained(self) -> None:
        self.stream.feed(self.mm700 + b"\x53")
        self.assertEqual(bytes(self.stream.buffer), b"\x53")

    def test_truncated_tail_is_kept_for_the_next_read(self) -> None:
        self.stream.feed(self.mm700 + self.k70[:5])
        frames = self.stream.feed(self.k70[5:])
        self.assertEqual(len(frames), 1)

    def test_buffer_does_not_grow_without_bound(self) -> None:
        for _ in range(50):
            self.stream.feed(b"\x00" * 1024)
        self.assertLessEqual(len(self.stream.buffer), self.stream.max_buffer)

    def test_recovers_after_a_flood_of_garbage(self) -> None:
        self.stream.feed(b"\x11" * 5000)
        frames = self.stream.feed(self.mm700)
        self.assertEqual(len(frames), 1)

    def test_real_capture_from_the_probe(self) -> None:
        # The segmentation the dump tool actually observed: one 432-byte read,
        # then the remaining three device frames split across two reads.
        payload = b"".join(
            macstream.encode_frame(
                device, [(index, index, index)] * macstream.led_count(device)
            )
            for index, device in enumerate(Device)
        )
        collected = []
        for chunk in (payload[:432], payload[432:465], payload[465:]):
            collected.extend(self.stream.feed(chunk))
        self.assertEqual([device for device, _ in collected], list(Device))


class StreamClientTests(unittest.TestCase):
    @patch("headless_lights.macstream.socket.create_connection")
    def test_connect_uses_the_configured_endpoint(self, create) -> None:
        create.return_value = Mock()
        client = macstream.StreamClient(host="127.0.0.1", port=7532, timeout=2.0)
        client.connect()
        self.assertEqual(create.call_args.args[0], ("127.0.0.1", 7532))
        self.assertEqual(create.call_args.kwargs["timeout"], 2.0)

    @patch("headless_lights.macstream.socket.create_connection")
    def test_send_frame_writes_the_encoded_bytes(self, create) -> None:
        connection = Mock()
        create.return_value = connection
        with macstream.StreamClient() as client:
            client.send_frame(Device.MM700, [(4, 5, 6)] * 3)
        connection.sendall.assert_called_once_with(
            macstream.encode_frame(Device.MM700, [(4, 5, 6)] * 3)
        )

    def test_send_frame_without_a_connection_raises(self) -> None:
        client = macstream.StreamClient()
        with self.assertRaisesRegex(StreamError, "not connected"):
            client.send_frame(Device.MM700, [(0, 0, 0)] * 3)

    def test_rejects_non_positive_timeout(self) -> None:
        with self.assertRaises(StreamError):
            macstream.StreamClient(timeout=0)


class PacingTests(unittest.TestCase):
    def test_frame_period_matches_fps(self) -> None:
        self.assertAlmostEqual(macstream.frame_period(20), 0.05)

    def test_rejects_out_of_range_fps(self) -> None:
        for fps in (0, 61):
            with self.assertRaises(StreamError):
                macstream.clamp_fps(fps)


if __name__ == "__main__":
    unittest.main()

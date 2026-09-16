"""Frame protocol spoken to the Mac agent's streaming port.

The agent normally renders its own effects. When SignalRGB is driving the PC it
instead receives per-LED frames on 127.0.0.1:7532, reached through an SSH
tunnel. This module is the Python side of that wire format: it lets the repo
test the agent without SignalRGB in the loop, and it lets the PC paint the Mac
with the project's own effects.

The C++ definition lives in mac-agent/stream_protocol.h; the two must agree.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import IntEnum
import functools
import socket
import time

from headless_lights.effects import (
    borderlands4_color,
    stranger_things_color,
    watercolor_color,
)
from headless_lights.k70max import WIRE_SLOTS, load_coordinates


MAGIC = b"SG"
VERSION = 1
HEADER_SIZE = 6

DEFAULT_STREAM_HOST = "127.0.0.1"
DEFAULT_STREAM_PORT = 7532


class Device(IntEnum):
    K70 = 0
    MM700 = 1
    G560 = 2
    SCIMITAR = 3


LED_COUNTS: dict[Device, int] = {
    Device.K70: WIRE_SLOTS,
    Device.MM700: 3,
    Device.G560: 4,
    Device.SCIMITAR: 3,
}

EFFECTS = ("watercolor", "stranger-things", "borderlands-4")

Color = tuple[int, int, int]


class StreamError(RuntimeError):
    pass


def led_count(device: Device) -> int:
    return LED_COUNTS[Device(device)]


def encode_frame(device: Device, colors: Sequence[Color]) -> bytes:
    """Serialise one frame for one device."""
    device = Device(device)
    expected = led_count(device)
    if len(colors) != expected:
        raise StreamError(
            f"{device.name} needs {expected} colors, got {len(colors)}"
        )

    payload = bytearray()
    for color in colors:
        if len(color) != 3:
            raise StreamError("each color must be an RGB triple")
        for component in color:
            if not isinstance(component, int) or not 0 <= component <= 255:
                raise StreamError("RGB components must be integers between 0 and 255")
            payload.append(component)

    header = bytearray(MAGIC)
    header.append(VERSION)
    header.append(int(device))
    header.extend(len(payload).to_bytes(2, "little"))
    return bytes(header + payload)


def decode_frame(data: bytes) -> tuple[Device, list[Color]]:
    """Inverse of encode_frame, for tests and for inspecting captures."""
    if len(data) < HEADER_SIZE:
        raise StreamError("frame is shorter than the header")
    if data[0:2] != MAGIC:
        raise StreamError("bad magic")
    if data[2] != VERSION:
        raise StreamError(f"unsupported version {data[2]}")
    if data[3] >= len(Device):
        raise StreamError(f"unknown device id {data[3]}")

    device = Device(data[3])
    length = int.from_bytes(data[4:6], "little")
    if length != led_count(device) * 3:
        raise StreamError(
            f"{device.name} frame declares {length} payload bytes, "
            f"expected {led_count(device) * 3}"
        )
    if len(data) != HEADER_SIZE + length:
        raise StreamError("frame length does not match the declared payload")

    payload = data[HEADER_SIZE:]
    colors = [
        (payload[offset], payload[offset + 1], payload[offset + 2])
        for offset in range(0, length, 3)
    ]
    return device, colors


@functools.lru_cache(maxsize=1)
def _coordinates() -> tuple[tuple[float, float, bool], ...]:
    return tuple(load_coordinates())


class FrameStream:
    """Reassemble frames from a TCP byte stream.

    Reference implementation of the resync loop in mac-agent/stream_protocol.h:
    scan for the magic, drop the garbage in front of it, stop on a short buffer,
    and drop exactly one byte on a bad header so a hostile peer cannot wedge the
    parser. Same shape as FrameStream in headless_lights.beelight.protocol.
    """

    def __init__(self, *, max_buffer: int = (HEADER_SIZE + WIRE_SLOTS * 3) * 2) -> None:
        self.buffer = bytearray()
        self.max_buffer = max_buffer

    def _header_is_valid(self, offset: int) -> bool:
        if self.buffer[offset + 2] != VERSION:
            return False
        device_id = self.buffer[offset + 3]
        if device_id >= len(Device):
            return False
        length = int.from_bytes(self.buffer[offset + 4 : offset + 6], "little")
        return length == led_count(Device(device_id)) * 3

    def feed(self, data: bytes) -> list[tuple[Device, list[Color]]]:
        self.buffer.extend(data)
        frames: list[tuple[Device, list[Color]]] = []
        cursor = 0

        while True:
            start = cursor
            while start + 1 < len(self.buffer) and not (
                self.buffer[start] == MAGIC[0] and self.buffer[start + 1] == MAGIC[1]
            ):
                start += 1
            if start + 1 >= len(self.buffer):
                # A trailing lone byte may be the first half of a magic, so keep
                # it. Never move the cursor back over bytes already consumed.
                cursor = max(cursor, len(self.buffer) - 1)
                break

            cursor = start
            if len(self.buffer) - cursor < HEADER_SIZE:
                break
            if not self._header_is_valid(cursor):
                cursor += 1
                continue

            length = int.from_bytes(self.buffer[cursor + 4 : cursor + 6], "little")
            if len(self.buffer) - cursor < HEADER_SIZE + length:
                break

            frame = bytes(self.buffer[cursor : cursor + HEADER_SIZE + length])
            frames.append(decode_frame(frame))
            cursor += HEADER_SIZE + length

        if cursor:
            del self.buffer[:cursor]
        if len(self.buffer) > self.max_buffer:
            del self.buffer[: len(self.buffer) - self.max_buffer]
        return frames


def _sample(effect: str, position: float, elapsed: float, *, lane: int) -> Color:
    if effect == "watercolor":
        return watercolor_color(position, elapsed)
    if effect == "stranger-things":
        return stranger_things_color(position, elapsed, lane=lane)
    if effect == "borderlands-4":
        return borderlands4_color(position, elapsed, lane=lane)
    raise StreamError(f"unknown effect {effect!r}")


def render_k70(effect: str, elapsed: float) -> list[Color]:
    """Reproduce the agent's own K70 rendering, slot for slot.

    Sampling matches K70Backend::set_watercolor / set_stranger /
    set_borderlands4 in mac-agent/agent.cpp so a streamed frame and a locally
    rendered one are indistinguishable. Unmapped channels stay black, as they
    do there.
    """
    span, lane_scale = {
        "watercolor": (1.05, 0.18),
        "stranger-things": (1.28, 0.2),
        "borderlands-4": (1.16, 0.2),
    }[effect]
    offset = 0.11 if effect == "stranger-things" else 0.12

    colors: list[Color] = []
    for x, y, mapped in _coordinates():
        if not mapped:
            colors.append((0, 0, 0))
            continue
        colors.append(
            _sample(
                effect,
                offset + x * span + y * lane_scale,
                elapsed,
                lane=round(y * 5.0),
            )
        )
    return colors


def render_zones(device: Device, effect: str, elapsed: float) -> list[Color]:
    """Reproduce the agent's zone sampling for the three non-keyboard devices."""
    device = Device(device)
    if device is Device.K70:
        return render_k70(effect, elapsed)

    watercolor_span, effect_span, lane_base = {
        Device.MM700: ((0.54, 0.34), (0.57, 0.41), 8),
        Device.G560: ((0.28, 0.29), (0.31, 0.33), 20),
        Device.SCIMITAR: ((0.49, 0.24), (0.53, 0.27), 24),
    }[device]

    start, step = watercolor_span if effect == "watercolor" else effect_span
    return [
        _sample(effect, start + zone * step, elapsed, lane=lane_base + zone)
        for zone in range(led_count(device))
    ]


def render_color(device: Device, color: Color) -> list[Color]:
    return [color] * led_count(device)


class StreamClient:
    """Persistent TCP connection to the agent's streaming port."""

    def __init__(
        self,
        *,
        host: str = DEFAULT_STREAM_HOST,
        port: int = DEFAULT_STREAM_PORT,
        timeout: float = 5.0,
    ) -> None:
        if timeout <= 0:
            raise StreamError("stream timeout must be positive")
        self.host = host
        self.port = port
        self.timeout = timeout
        self.socket: socket.socket | None = None

    def __enter__(self) -> "StreamClient":
        self.connect()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def connect(self) -> None:
        self.close()
        self.socket = socket.create_connection(
            (self.host, self.port), timeout=self.timeout
        )
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def close(self) -> None:
        if self.socket is not None:
            try:
                self.socket.close()
            finally:
                self.socket = None

    def send_frame(self, device: Device, colors: Sequence[Color]) -> None:
        if self.socket is None:
            raise StreamError("stream is not connected")
        self.socket.sendall(encode_frame(device, colors))


def phase_seconds() -> float:
    """Wall-clock phase, matching the agent's effect_seconds()."""
    return time.time()


def stream_hold(
    *,
    host: str = DEFAULT_STREAM_HOST,
    port: int = DEFAULT_STREAM_PORT,
    fps: int = 20,
    seconds: float | None = None,
    color: Color | None = None,
    effect: str | None = None,
    devices: Sequence[Device] | None = None,
    reconnect_delay: float = 2.0,
) -> None:
    """Drive the Mac agent's streaming port until interrupted.

    Exists so the agent can be exercised without SignalRGB: same wire format,
    same per-device frames, and — when an effect is selected — the same colors
    the agent would have rendered for itself.
    """
    if color is None and effect is None:
        raise StreamError("stream_hold needs either a color or an effect")
    if color is not None and effect is not None:
        raise StreamError("stream_hold takes a color or an effect, not both")
    if effect is not None and effect not in EFFECTS:
        raise StreamError(f"unknown effect {effect!r}")
    if reconnect_delay < 0:
        raise StreamError("reconnect delay cannot be negative")

    period = frame_period(fps)
    targets = list(devices) if devices else list(Device)
    start = time.monotonic()

    while True:
        try:
            with StreamClient(host=host, port=port) as client:
                print(
                    f"streaming {len(targets)} device(s) to {host}:{port} at {fps} FPS",
                    flush=True,
                )
                next_frame = time.monotonic()
                while True:
                    if seconds is not None and time.monotonic() - start >= seconds:
                        return
                    elapsed = phase_seconds()
                    for device in targets:
                        if color is not None:
                            frame = render_color(device, color)
                        else:
                            frame = render_zones(device, effect, elapsed)
                        client.send_frame(device, frame)

                    next_frame += period
                    delay = next_frame - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
                    elif delay < -period:
                        next_frame = time.monotonic()
        except KeyboardInterrupt:
            return
        except OSError as error:
            if seconds is not None and time.monotonic() - start >= seconds:
                return
            print(
                f"stream connection lost: {error}; retrying in {reconnect_delay:g}s",
                flush=True,
            )
            time.sleep(reconnect_delay)


def clamp_fps(fps: int) -> int:
    if not 1 <= fps <= 60:
        raise StreamError("stream FPS must be between 1 and 60")
    return fps


def frame_period(fps: int) -> float:
    return 1.0 / clamp_fps(fps)


__all__ = [
    "Color",
    "DEFAULT_STREAM_HOST",
    "DEFAULT_STREAM_PORT",
    "Device",
    "EFFECTS",
    "FrameStream",
    "HEADER_SIZE",
    "LED_COUNTS",
    "MAGIC",
    "StreamClient",
    "StreamError",
    "VERSION",
    "clamp_fps",
    "decode_frame",
    "encode_frame",
    "frame_period",
    "led_count",
    "phase_seconds",
    "render_color",
    "render_k70",
    "render_zones",
    "stream_hold",
]

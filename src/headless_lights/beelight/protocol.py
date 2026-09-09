"""Beelight V3's reverse-engineered 55 AA 5A serial protocol."""

from __future__ import annotations

from dataclasses import dataclass
import secrets
from typing import Iterable, Sequence


HEADER = b"\x55\xaa\x5a"
MIN_KEY_SIZE = 3
MAX_KEY_SIZE = 10
DEFAULT_KEY_SIZE = 5

COMMAND_HEARTBEAT = 0
COMMAND_FIRMWARE = 1
COMMAND_SYNC_STATUS = 2
COMMAND_SYNC_CONFIG = 3
COMMAND_CONTROL = 5

CONTROL_SWITCH = 1
CONTROL_BRIGHTNESS = 2
CONTROL_COLOR = 4
CONTROL_RGB_TRANSFER = 5
CONTROL_WORK_MODE = 6

ATTRIBUTE_REQUEST = 0
ATTRIBUTE_ACKNOWLEDGEMENT = 1


class ProtocolError(ValueError):
    """Raised when a serial frame is malformed."""


@dataclass(frozen=True)
class Frame:
    attribute: int
    command: int
    data: bytes
    key_size: int


@dataclass(frozen=True)
class FirmwareInfo:
    bsp_version: tuple[int, int, int, int]
    app_version: tuple[int, int, int, int]
    product_id: str
    device_id: bytes
    manufacturer: str
    market: str


@dataclass(frozen=True)
class SyncConfig:
    total_pixels: int
    channel_count: int
    channel_pixels: tuple[int, ...]


def _validate_byte(value: int, label: str) -> None:
    if not 0 <= value <= 0xFF:
        raise ValueError(f"{label} must be between 0 and 255")


def encode_frame(
    command: int,
    data: bytes = b"",
    *,
    attribute: int = ATTRIBUTE_REQUEST,
    key: bytes | None = None,
    key_size: int = DEFAULT_KEY_SIZE,
) -> bytes:
    """Encode a transport frame.

    Supplying ``key`` is useful for tests; normal callers get cryptographically
    random bytes. The marker encodes the key length as ``length XOR 1``.
    """

    _validate_byte(command, "command")
    if attribute not in (ATTRIBUTE_REQUEST, ATTRIBUTE_ACKNOWLEDGEMENT):
        raise ValueError("attribute must be request (0) or acknowledgement (1)")
    if not MIN_KEY_SIZE <= key_size <= MAX_KEY_SIZE:
        raise ValueError(
            f"key_size must be between {MIN_KEY_SIZE} and {MAX_KEY_SIZE}"
        )

    clear_key = key if key is not None else secrets.token_bytes(key_size)
    if len(clear_key) != key_size:
        raise ValueError("key has the wrong length")

    plaintext = bytes((attribute, command)) + data
    ciphertext = bytes(
        value ^ clear_key[index % len(clear_key)]
        for index, value in enumerate(plaintext)
    )

    payload_without_checksum = (
        bytes((0x30 + (key_size ^ 1),)) + clear_key + ciphertext
    )
    payload = bytes((sum(payload_without_checksum) & 0xFF,)) + payload_without_checksum
    return HEADER + len(payload).to_bytes(2, "little") + payload


def decode_frame(raw: bytes, *, attribute: int | None = None) -> Frame:
    if len(raw) < 5 or raw[:3] != HEADER:
        raise ProtocolError("invalid frame header")
    payload_size = int.from_bytes(raw[3:5], "little")
    if len(raw) != 5 + payload_size:
        raise ProtocolError(
            f"frame declares {payload_size} payload bytes but contains {len(raw) - 5}"
        )

    payload = raw[5:]
    if len(payload) < 7:
        raise ProtocolError("frame is too short")
    if payload[0] != sum(payload[1:]) & 0xFF:
        raise ProtocolError("checksum mismatch")

    key_size_marker = payload[1] - 0x30
    key_size = key_size_marker ^ 1
    if not MIN_KEY_SIZE <= key_size <= MAX_KEY_SIZE:
        raise ProtocolError("invalid key size")
    ciphertext_offset = 2 + key_size
    if len(payload) < ciphertext_offset + 2:
        # The controller emits checksum-valid decoy frames while idle.
        raise ProtocolError("frame does not contain a command")

    clear_key = payload[2:ciphertext_offset]
    ciphertext = payload[ciphertext_offset:]
    plaintext = bytes(
        value ^ clear_key[index % len(clear_key)]
        for index, value in enumerate(ciphertext)
    )
    if len(plaintext) < 2 or plaintext[0] not in (
        ATTRIBUTE_REQUEST,
        ATTRIBUTE_ACKNOWLEDGEMENT,
    ):
        raise ProtocolError("invalid encrypted-frame attribute")
    if attribute is not None and plaintext[0] != attribute:
        raise ProtocolError("unexpected encrypted-frame attribute")

    return Frame(
        attribute=plaintext[0],
        command=plaintext[1],
        data=plaintext[2:],
        key_size=key_size,
    )


class FrameStream:
    """Incrementally split arbitrary serial reads into complete frames."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self._buffer.extend(data)
        frames: list[bytes] = []
        while True:
            header_at = self._buffer.find(HEADER)
            if header_at < 0:
                if len(self._buffer) > len(HEADER) - 1:
                    del self._buffer[: -(len(HEADER) - 1)]
                break
            if header_at:
                del self._buffer[:header_at]
            if len(self._buffer) < 5:
                break

            payload_size = int.from_bytes(self._buffer[3:5], "little")
            total_size = 5 + payload_size
            if payload_size > 65_535:
                del self._buffer[0]
                continue
            if len(self._buffer) < total_size:
                break
            frames.append(bytes(self._buffer[:total_size]))
            del self._buffer[:total_size]
        return frames


def control_data(control: int, payload: bytes, *, channel: int = 0xFF) -> bytes:
    _validate_byte(control, "control")
    _validate_byte(channel, "channel")
    return bytes((control, channel)) + len(payload).to_bytes(2, "little") + payload


def switch_request(enabled: bool, *, channel: int = 0xFF) -> bytes:
    return encode_frame(
        COMMAND_CONTROL,
        control_data(CONTROL_SWITCH, bytes((int(enabled),)), channel=channel),
    )


def brightness_request(brightness: int, *, channel: int = 0xFF) -> bytes:
    if not 0 <= brightness <= 100:
        raise ValueError("brightness must be between 0 and 100")
    return encode_frame(
        COMMAND_CONTROL,
        control_data(CONTROL_BRIGHTNESS, brightness.to_bytes(2, "little"), channel=channel),
    )


def color_request(red: int, green: int, blue: int, *, channel: int = 0xFF) -> bytes:
    for value, label in ((red, "red"), (green, "green"), (blue, "blue")):
        _validate_byte(value, label)
    return encode_frame(
        COMMAND_CONTROL,
        control_data(CONTROL_COLOR, bytes((red, green, blue)), channel=channel),
    )


def pixels_request(
    colors: Sequence[tuple[int, int, int]], *, channel: int = 0xFF
) -> bytes:
    if len(colors) > 0xFFFF:
        raise ValueError("too many pixels")
    rgb = bytearray()
    for index, color in enumerate(colors):
        if len(color) != 3:
            raise ValueError(f"pixel {index} is not an RGB triple")
        for value in color:
            _validate_byte(value, f"pixel {index} component")
        rgb.extend(color)
    payload = len(colors).to_bytes(2, "little") + rgb
    return encode_frame(
        COMMAND_CONTROL,
        control_data(CONTROL_RGB_TRANSFER, bytes(payload), channel=channel),
    )


def pc_mode_request(*, channel: int = 0xFF) -> bytes:
    return encode_frame(
        COMMAND_CONTROL,
        control_data(CONTROL_WORK_MODE, b"\x00\x00\x00", channel=channel),
    )


def parse_color(value: str) -> tuple[int, int, int]:
    normalized = value.strip().removeprefix("#")
    if len(normalized) != 6:
        raise ValueError("color must be RRGGBB or #RRGGBB")
    try:
        return tuple(bytes.fromhex(normalized))  # type: ignore[return-value]
    except ValueError as error:
        raise ValueError("color must be RRGGBB or #RRGGBB") from error


def parse_colors(values: Iterable[str]) -> list[tuple[int, int, int]]:
    return [parse_color(value) for value in values]


def parse_firmware_info(data: bytes) -> FirmwareInfo:
    if len(data) < 41:
        raise ProtocolError("firmware response is too short")
    product_id = data[8:25].split(b"\0", 1)[0].decode("ascii", errors="replace")
    strings = data[41:].split(b"\0")
    manufacturer = strings[0].decode("ascii", errors="replace") if strings else ""
    market = strings[1].decode("ascii", errors="replace") if len(strings) > 1 else ""
    return FirmwareInfo(
        bsp_version=tuple(data[:4]),  # type: ignore[arg-type]
        app_version=tuple(data[4:8]),  # type: ignore[arg-type]
        product_id=product_id,
        device_id=data[25:41],
        manufacturer=manufacturer,
        market=market,
    )


def parse_sync_config(data: bytes) -> SyncConfig:
    if len(data) < 3:
        raise ProtocolError("sync-config response is too short")
    total_pixels = int.from_bytes(data[:2], "little")
    channel_count = data[2]
    expected_size = 3 + channel_count * 2
    if len(data) < expected_size:
        raise ProtocolError("sync-config response omits channel sizes")
    channel_pixels = tuple(
        int.from_bytes(data[offset : offset + 2], "little")
        for offset in range(3, expected_size, 2)
    )
    return SyncConfig(total_pixels, channel_count, channel_pixels)

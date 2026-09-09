from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import time

from headless_lights.beelight import protocol
from headless_lights.beelight.serial import SerialPort


@dataclass(frozen=True)
class Response:
    command: int
    data: bytes


def discover_port() -> Path:
    by_id = Path("/dev/serial/by-id")
    if by_id.is_dir():
        candidates = sorted(
            path for path in by_id.iterdir() if "Artery_AT32_Virtual_Com_Port" in path.name
        )
        if candidates:
            return candidates[0]

    for sys_tty in sorted(Path("/sys/class/tty").glob("ttyACM*")):
        current = sys_tty.resolve()
        for parent in (current, *current.parents):
            vendor_file = parent / "idVendor"
            product_file = parent / "idProduct"
            if vendor_file.exists() and product_file.exists():
                if (
                    vendor_file.read_text().strip().lower() == "2e3c"
                    and product_file.read_text().strip().lower() == "5740"
                ):
                    return Path("/dev") / sys_tty.name
                break
    raise FileNotFoundError("Beelight/AT32 serial device was not found")


class BeelightDevice:
    def __init__(self, port: str | Path | None = None, *, timeout: float = 1.5) -> None:
        self.port = Path(port) if port is not None else discover_port()
        self.timeout = timeout
        self._serial: SerialPort | None = None
        self._stream = protocol.FrameStream()
        self._firmware_data: bytes | None = None
        self._config_data: bytes | None = None

    def __enter__(self) -> "BeelightDevice":
        if self._serial is not None:
            raise RuntimeError("Beelight session is already open")
        serial = SerialPort(self.port)
        serial.__enter__()
        serial.drain()
        self._serial = serial
        self._stream = protocol.FrameStream()
        self._firmware_data = None
        self._config_data = None
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._serial is not None:
            self._serial.__exit__(*exc_info)
            self._serial = None
            self._firmware_data = None
            self._config_data = None

    def _exchange(
        self,
        serial: SerialPort,
        packet: bytes,
        expected_command: int | None,
    ) -> Response | None:
        serial.write(packet)
        if expected_command is None:
            return None

        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            chunk = serial.read(min(0.1, deadline - time.monotonic()))
            if not chunk:
                continue
            for raw_frame in self._stream.feed(chunk):
                try:
                    frame = protocol.decode_frame(raw_frame)
                except protocol.ProtocolError:
                    continue
                if (
                    frame.attribute == protocol.ATTRIBUTE_REQUEST
                    and frame.command == protocol.COMMAND_HEARTBEAT
                ):
                    serial.write(
                        protocol.encode_frame(
                            protocol.COMMAND_HEARTBEAT,
                            attribute=protocol.ATTRIBUTE_ACKNOWLEDGEMENT,
                        )
                    )
                    continue
                if (
                    frame.attribute == protocol.ATTRIBUTE_ACKNOWLEDGEMENT
                    and frame.command == expected_command
                ):
                    return Response(frame.command, frame.data)
        raise TimeoutError(
            f"Beelight did not acknowledge command {expected_command} within {self.timeout:g}s"
        )

    def send(self, packet: bytes, *, expected_command: int | None = None) -> Response | None:
        if self._serial is not None:
            return self._exchange(self._serial, packet, expected_command)

        with SerialPort(self.port) as serial:
            serial.drain()
            self._stream = protocol.FrameStream()
            return self._exchange(serial, packet, expected_command)

    def process_incoming(self, timeout: float = 0.0) -> list[protocol.Frame]:
        """Drain inbound frames and acknowledge controller heartbeats."""

        if self._serial is None:
            raise RuntimeError("process_incoming requires an open Beelight session")
        frames: list[protocol.Frame] = []
        chunk = self._serial.read(timeout)
        while chunk:
            for raw_frame in self._stream.feed(chunk):
                try:
                    frame = protocol.decode_frame(raw_frame)
                except protocol.ProtocolError:
                    continue
                if (
                    frame.attribute == protocol.ATTRIBUTE_REQUEST
                    and frame.command == protocol.COMMAND_HEARTBEAT
                ):
                    self._serial.write(
                        protocol.encode_frame(
                            protocol.COMMAND_HEARTBEAT,
                            attribute=protocol.ATTRIBUTE_ACKNOWLEDGEMENT,
                        )
                    )
                else:
                    frames.append(frame)
            chunk = self._serial.read(0.0)
        return frames

    def query(self, command: int) -> Response:
        response = self.send(protocol.encode_frame(command), expected_command=command)
        if response is None:
            raise RuntimeError("missing response")
        return response

    def initialize(self) -> tuple[bytes, bytes]:
        """Perform the discovery handshake required before control commands."""

        if self._serial is None:
            with self:
                return self.initialize()
        if self._firmware_data is None:
            self._firmware_data = self._query_with_retry(protocol.COMMAND_FIRMWARE)
        if self._config_data is None:
            self._config_data = self._query_with_retry(protocol.COMMAND_SYNC_CONFIG)
        return self._firmware_data, self._config_data

    def _query_with_retry(self, command: int, *, attempts: int = 3) -> bytes:
        last_error: TimeoutError | None = None
        for _ in range(attempts):
            try:
                return self.query(command).data
            except TimeoutError as error:
                last_error = error
        if last_error is not None:
            raise last_error
        raise ValueError("attempts must be at least one")

    def _send_acknowledged_control(
        self,
        packet_factory: Callable[[], bytes],
        *,
        attempts: int = 3,
    ) -> None:
        last_error: TimeoutError | None = None
        for _ in range(attempts):
            try:
                self.send(
                    packet_factory(),
                    expected_command=protocol.COMMAND_CONTROL,
                )
            except TimeoutError as error:
                last_error = error
                continue
            return
        if last_error is not None:
            raise last_error
        raise ValueError("attempts must be at least one")

    def set_pc_mode(self) -> None:
        if self._serial is None:
            with self:
                self.set_pc_mode()
            return
        self.initialize()
        self._send_acknowledged_control(protocol.pc_mode_request)

    def switch(self, enabled: bool) -> None:
        if self._serial is None:
            with self:
                self.switch(enabled)
            return
        self.initialize()
        self._send_acknowledged_control(
            lambda: protocol.switch_request(enabled),
        )

    def set_brightness(self, brightness: int) -> None:
        if self._serial is None:
            with self:
                self.set_brightness(brightness)
            return
        self.initialize()
        self._send_acknowledged_control(
            lambda: protocol.brightness_request(brightness),
        )

    def set_color(self, red: int, green: int, blue: int) -> None:
        if self._serial is None:
            with self:
                self.set_color(red, green, blue)
            return
        self.initialize()
        self._send_acknowledged_control(
            lambda: protocol.color_request(red, green, blue),
        )

    def set_pixels(self, colors: list[tuple[int, int, int]]) -> None:
        if self._serial is None:
            with self:
                self.set_pixels(colors)
            return
        self.initialize()
        # RGB_TRANSFER is the streaming path and deliberately has no ACK.
        self.send(protocol.pixels_request(colors))

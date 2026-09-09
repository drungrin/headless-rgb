"""Small dependency-free POSIX serial transport."""

from __future__ import annotations

import os
from pathlib import Path
import select
import termios
import time


class SerialPort:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self.fd: int | None = None

    def __enter__(self) -> "SerialPort":
        try:
            self.fd = os.open(
                self.path,
                os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK,
            )
        except PermissionError as error:
            raise PermissionError(
                f"permission denied opening {self.path}; add your user to the dialout group"
            ) from error
        self._configure()
        # Opening the CDC ACM port toggles its control lines. The controller
        # needs a short interval before it reliably accepts the first frame.
        time.sleep(0.25)
        return self

    def __exit__(self, *_: object) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def _configure(self) -> None:
        if self.fd is None:
            raise RuntimeError("serial port is closed")
        attributes = termios.tcgetattr(self.fd)
        attributes[0] = 0
        attributes[1] = 0
        attributes[2] = (
            attributes[2]
            & ~(termios.CSIZE | termios.PARENB | termios.CSTOPB | termios.CRTSCTS)
        ) | termios.CS8 | termios.CREAD | termios.CLOCAL
        attributes[3] = 0
        attributes[4] = termios.B115200
        attributes[5] = termios.B115200
        attributes[6][termios.VMIN] = 0
        attributes[6][termios.VTIME] = 1
        termios.tcsetattr(self.fd, termios.TCSANOW, attributes)
        termios.tcflush(self.fd, termios.TCIOFLUSH)

    def write(self, data: bytes) -> None:
        if self.fd is None:
            raise RuntimeError("serial port is closed")
        view = memoryview(data)
        while view:
            _, writable, _ = select.select([], [self.fd], [], 1.0)
            if not writable:
                raise TimeoutError("timed out writing to the serial port")
            written = os.write(self.fd, view)
            view = view[written:]

    def read(self, timeout: float) -> bytes:
        if self.fd is None:
            raise RuntimeError("serial port is closed")
        readable, _, _ = select.select([self.fd], [], [], timeout)
        if not readable:
            return b""
        return os.read(self.fd, 65_536)

    def drain(self, duration: float = 0.1) -> None:
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            if not self.read(min(0.02, deadline - time.monotonic())):
                break

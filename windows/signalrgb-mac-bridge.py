"""Forward SignalRGB frames from UDP loopback into the Mac's TCP tunnel.

SignalRGB 2.5.74 exposes UDP, but not TCP, to device plugins. The add-on sends
the agent's native binary frames to UDP 127.0.0.1:7532; this process validates
them and forwards the bytes unchanged to the local end of the SSH TCP tunnel.

Both protocols can use port 7532 simultaneously because TCP and UDP have
separate port spaces::

    SignalRGB --UDP 7532--> this bridge --TCP 7532--> SSH --> Mac agent

Run after windows/start-mac-tunnel.ps1, or install both as logon tasks.
"""

from __future__ import annotations

import argparse
import socket
import sys
import time


MAGIC = b"SG"
VERSION = 1
HEADER_SIZE = 6
LED_COUNTS = (142, 3, 4, 3)
MAX_DATAGRAM = HEADER_SIZE + max(LED_COUNTS) * 3


def valid_frame(data: bytes) -> bool:
    if len(data) < HEADER_SIZE or data[:2] != MAGIC or data[2] != VERSION:
        return False
    device = data[3]
    if device >= len(LED_COUNTS):
        return False
    length = int.from_bytes(data[4:6], "little")
    expected = LED_COUNTS[device] * 3
    return length == expected and len(data) == HEADER_SIZE + expected


class Bridge:
    def __init__(
        self,
        *,
        listen_host: str = "127.0.0.1",
        listen_port: int = 7532,
        upstream_host: str = "127.0.0.1",
        upstream_port: int = 7532,
        reconnect_delay: float = 1.0,
    ) -> None:
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port
        self.reconnect_delay = reconnect_delay
        self.upstream: socket.socket | None = None
        self.next_connect = 0.0
        self.frames = 0
        self.rejected = 0
        self.device_frames = [0] * len(LED_COUNTS)
        self.last_report = time.monotonic()

    def connect(self) -> bool:
        now = time.monotonic()
        if self.upstream is not None:
            return True
        if now < self.next_connect:
            return False
        self.next_connect = now + self.reconnect_delay
        try:
            upstream = socket.create_connection(
                (self.upstream_host, self.upstream_port), timeout=3.0
            )
            upstream.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.upstream = upstream
            print(
                f"connected to TCP {self.upstream_host}:{self.upstream_port}",
                flush=True,
            )
            return True
        except OSError as error:
            print(f"TCP tunnel unavailable: {error}", file=sys.stderr, flush=True)
            return False

    def disconnect(self) -> None:
        if self.upstream is not None:
            try:
                self.upstream.close()
            finally:
                self.upstream = None
        self.next_connect = time.monotonic() + self.reconnect_delay

    def forward(self, frame: bytes) -> bool:
        if not valid_frame(frame):
            self.rejected += 1
            return False
        if not self.connect():
            return False
        try:
            self.upstream.sendall(frame)
            self.frames += 1
            self.device_frames[frame[3]] += 1
            now = time.monotonic()
            if now - self.last_report >= 10.0:
                print(
                    "forwarded "
                    + " ".join(
                        f"device{index}={count}"
                        for index, count in enumerate(self.device_frames)
                    )
                    + f" rejected={self.rejected}",
                    flush=True,
                )
                self.last_report = now
            return True
        except OSError as error:
            print(f"TCP connection lost: {error}", file=sys.stderr, flush=True)
            self.disconnect()
            return False

    def run(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        listener.bind((self.listen_host, self.listen_port))
        print(
            f"listening on UDP {self.listen_host}:{self.listen_port}; "
            f"forwarding to TCP {self.upstream_host}:{self.upstream_port}",
            flush=True,
        )
        try:
            while True:
                frame, _ = listener.recvfrom(MAX_DATAGRAM + 1)
                self.forward(frame)
        except KeyboardInterrupt:
            return
        finally:
            listener.close()
            self.disconnect()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="signalrgb-mac-bridge")
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=7532)
    parser.add_argument("--upstream-host", default="127.0.0.1")
    parser.add_argument("--upstream-port", type=int, default=7532)
    args = parser.parse_args(argv)
    Bridge(
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        upstream_host=args.upstream_host,
        upstream_port=args.upstream_port,
    ).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

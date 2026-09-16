"""Hex-dump whatever a client sends to the Mac agent stream port.

Used to verify, from the Windows side, that the SignalRGB plugin puts the bytes
it intends on the wire. Run it in place of the SSH tunnel: the plugin connects
to 127.0.0.1:7532 either way.

    python tools/stream_dump.py
    python tools/stream_dump.py --port 7532 --max-bytes 4096
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading


def hex_dump(data: bytes, *, prefix: str) -> str:
    lines = []
    for offset in range(0, len(data), 16):
        chunk = data[offset : offset + 16]
        hexadecimal = " ".join(f"{byte:02x}" for byte in chunk)
        printable = "".join(
            chr(byte) if 0x20 <= byte < 0x7F else "." for byte in chunk
        )
        lines.append(f"{prefix}{offset:06x}  {hexadecimal:<47}  {printable}")
    return "\n".join(lines)


def serve_client(connection: socket.socket, address, *, max_bytes: int) -> None:
    label = f"{address[0]}:{address[1]}"
    print(f"[{label}] connected", flush=True)
    total = 0
    try:
        while True:
            data = connection.recv(65536)
            if not data:
                break
            remaining = max(0, max_bytes - total)
            total += len(data)
            print(f"[{label}] {len(data)} bytes (total {total})", flush=True)
            if remaining:
                print(hex_dump(data[:remaining], prefix=f"[{label}] "), flush=True)
            elif remaining == 0:
                print(f"[{label}] ...dump limit reached, counting only", flush=True)
    except OSError as error:
        print(f"[{label}] {error}", flush=True)
    finally:
        connection.close()
        print(f"[{label}] closed after {total} bytes", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stream_dump")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7532)
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=2048,
        help="stop printing hex after this many bytes per connection",
    )
    args = parser.parse_args(argv)

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((args.host, args.port))
    listener.listen(8)
    print(f"listening on {args.host}:{args.port}", flush=True)

    try:
        while True:
            connection, address = listener.accept()
            thread = threading.Thread(
                target=serve_client,
                args=(connection, address),
                kwargs={"max_bytes": args.max_bytes},
                daemon=True,
            )
            thread.start()
    except KeyboardInterrupt:
        return 0
    finally:
        listener.close()


if __name__ == "__main__":
    sys.exit(main())

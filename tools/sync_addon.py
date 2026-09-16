"""Copy a canonical SignalRGB plugin into its add-on checkout.

SignalRGB add-ons are git repositories it clones itself, so each plugin lives
twice: the canonical source here, where the tests and the layout generator run
against it, and a published repository that SignalRGB actually reads.

Copying by hand has gone wrong the same way every time: the working tree on
Windows is CRLF, the add-on repositories are LF, and the difference only shows
up later as a whole-file diff. This always writes LF.

    python tools/sync_addon.py mac ../signalrgb-mac-bridge
    python tools/sync_addon.py beelight ../signalrgb-beelight --check
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]

# add-on name -> {published file name: canonical path, relative to the repo}
ADDONS: dict[str, dict[str, str]] = {
    "mac": {
        "headless-lights-mac.js": "signalrgb/headless-lights-mac.js",
        "headless-lights-mac.qml": "signalrgb/headless-lights-mac.qml",
    },
    "beelight": {
        "beelight.js": "signalrgb/beelight.js",
    },
}


def normalise(path: Path) -> bytes:
    """Read a text file and return it with LF endings and a trailing newline."""
    raw = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    if raw and not raw.endswith(b"\n"):
        raw += b"\n"
    return raw


def sync(addon: str, destination: Path, *, check: bool) -> int:
    files = ADDONS[addon]
    if not destination.is_dir():
        print(f"{destination} is not a directory", file=sys.stderr)
        return 2

    differences = 0
    for published, canonical in sorted(files.items()):
        source = REPO_ROOT / canonical
        if not source.is_file():
            print(f"missing canonical source: {canonical}", file=sys.stderr)
            return 2

        wanted = normalise(source)
        target = destination / published
        current = target.read_bytes() if target.is_file() else None

        if current == wanted:
            print(f"unchanged  {published}")
            continue

        differences += 1
        if check:
            state = "differs" if current is not None else "missing"
            print(f"{state:>9}  {published}", file=sys.stderr)
            continue

        target.write_bytes(wanted)
        print(f"{'written' if current is not None else 'created':>9}  {published}")

    if check and differences:
        print(
            f"{differences} file(s) out of sync; run without --check to update",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sync_addon")
    parser.add_argument("addon", choices=sorted(ADDONS))
    parser.add_argument("destination", type=Path, help="the add-on checkout")
    parser.add_argument(
        "--check",
        action="store_true",
        help="report differences without writing anything",
    )
    args = parser.parse_args(argv)
    return sync(args.addon, args.destination, check=args.check)


if __name__ == "__main__":
    sys.exit(main())

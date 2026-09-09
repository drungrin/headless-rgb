from __future__ import annotations

from dataclasses import dataclass
import re
import subprocess


@dataclass(frozen=True)
class OpenRgbDevice:
    index: int
    name: str
    device_type: str
    location: str
    zones: tuple[str, ...]
    leds: tuple[str, ...]


_DEVICE_HEADER = re.compile(r"^(\d+): (.+)$")
_FIELD = re.compile(r"^\s{2}([^:]+):\s*(.*)$")


def parse_device_list(output: str) -> list[OpenRgbDevice]:
    devices: list[OpenRgbDevice] = []
    index: int | None = None
    name = ""
    fields: dict[str, str] = {}

    def finish_device() -> None:
        if index is None:
            return
        devices.append(
            OpenRgbDevice(
                index=index,
                name=name,
                device_type=fields.get("Type", ""),
                location=fields.get("Location", ""),
                zones=tuple(re.findall(r"'([^']+)'", fields.get("Zones", ""))),
                leds=tuple(re.findall(r"'([^']+)'", fields.get("LEDs", ""))),
            )
        )

    for line in output.splitlines():
        header = _DEVICE_HEADER.fullmatch(line)
        if header is not None:
            finish_device()
            index = int(header.group(1))
            name = header.group(2)
            fields = {}
            continue
        field = _FIELD.fullmatch(line)
        if index is not None and field is not None:
            fields[field.group(1)] = field.group(2)
    finish_device()
    return devices


def corsair_memory_devices(*, timeout: float = 20.0) -> list[OpenRgbDevice]:
    completed = subprocess.run(
        ("openrgb", "--list-devices", "--noautoconnect"),
        text=True,
        capture_output=True,
        check=True,
        timeout=timeout,
    )
    return [
        device
        for device in parse_device_list(completed.stdout)
        if device.device_type == "DRAM" and "corsair" in device.name.lower()
    ]


def set_corsair_memory_color(
    color: tuple[int, int, int],
    *,
    timeout: float = 30.0,
) -> list[OpenRgbDevice]:
    if len(color) != 3 or any(not 0 <= component <= 255 for component in color):
        raise ValueError("RGB components must be between 0 and 255")
    devices = corsair_memory_devices(timeout=timeout)
    if not devices:
        raise RuntimeError("no Corsair RGB memory was detected by OpenRGB")

    normalized = "".join(f"{component:02X}" for component in color)
    arguments = ["openrgb", "--noautoconnect"]
    for device in devices:
        arguments.extend(
            (
                "--device",
                str(device.index),
                "--mode",
                "Direct",
                "--color",
                normalized,
            )
        )
    subprocess.run(
        arguments,
        text=True,
        capture_output=True,
        check=True,
        timeout=timeout,
    )
    return devices

from __future__ import annotations

import subprocess

from headless_lights.ram import OpenRgbDevice, parse_device_list


AURA_DEVICE_NAME = "ASUS PRIME Z690-P"
ASIAHORSE_ZONE = 2
ASIAHORSE_LED_COUNT = 24


def aura_devices(*, timeout: float = 20.0) -> list[OpenRgbDevice]:
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
        if device.name == AURA_DEVICE_NAME
        and device.device_type == "Motherboard"
        and device.location.startswith("HID: /dev/hidraw")
    ]


def set_asiahorse_color(
    color: tuple[int, int, int],
    *,
    timeout: float = 30.0,
) -> OpenRgbDevice:
    if len(color) != 3 or any(not 0 <= component <= 255 for component in color):
        raise ValueError("RGB components must be between 0 and 255")
    devices = aura_devices(timeout=timeout)
    if len(devices) != 1:
        raise RuntimeError(
            f"expected one {AURA_DEVICE_NAME} Aura controller, found {len(devices)}"
        )

    device = devices[0]
    normalized = "".join(f"{component:02X}" for component in color)
    base = [
        "openrgb",
        "--noautoconnect",
        "--device",
        str(device.index),
        "--zone",
        str(ASIAHORSE_ZONE),
    ]

    # OpenRGB applies a zone resize after the color buffer in the same command,
    # leaving this controller black. A second color-only command is required.
    subprocess.run(
        base
        + [
            "--size",
            str(ASIAHORSE_LED_COUNT),
            "--mode",
            "Static",
            "--color",
            normalized,
            "--brightness",
            "100",
        ],
        text=True,
        capture_output=True,
        check=True,
        timeout=timeout,
    )
    subprocess.run(
        base
        + [
            "--mode",
            "Static",
            "--color",
            normalized,
            "--brightness",
            "100",
        ],
        text=True,
        capture_output=True,
        check=True,
        timeout=timeout,
    )
    return device

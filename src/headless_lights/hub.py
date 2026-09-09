from __future__ import annotations

import os
import subprocess

from headless_lights.ram import OpenRgbDevice, parse_device_list


HUB_DEVICE_NAME = "Corsair iCUE Link System Hub"
LX_ZONE_NAME = "iCUE LINK LX RGB"
LX_LED_COUNT = 18
OPENRGB_SERVER_PORT = 6742


def system_hubs(*, timeout: float = 20.0) -> list[OpenRgbDevice]:
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
        if device.name == HUB_DEVICE_NAME
        and device.device_type == "Cooler"
        and device.location.startswith("HID: /dev/hidraw")
    ]


def build_hub_server_command(
    color: tuple[int, int, int],
    *,
    timeout: float = 20.0,
) -> tuple[list[str], int]:
    if len(color) != 3 or any(not 0 <= component <= 255 for component in color):
        raise ValueError("RGB components must be between 0 and 255")
    hubs = system_hubs(timeout=timeout)
    if len(hubs) != 1:
        raise RuntimeError(f"expected one iCUE LINK System Hub, found {len(hubs)}")

    hub = hubs[0]
    fan_zones = [
        index for index, zone_name in enumerate(hub.zones) if zone_name == LX_ZONE_NAME
    ]
    if not fan_zones:
        raise RuntimeError("the iCUE LINK hub reported no LX RGB fan zones")
    if len(fan_zones) != len(hub.zones):
        raise RuntimeError("the iCUE LINK hub contains an unsupported mixed topology")
    if len(hub.leds) != len(fan_zones) * LX_LED_COUNT:
        raise RuntimeError(
            "invalid iCUE LINK topology: "
            f"{len(fan_zones)} fan zones reported {len(hub.leds)} LEDs"
        )

    normalized = "".join(f"{component:02X}" for component in color)
    arguments = [
        "openrgb",
        "--server",
        "--server-host",
        "127.0.0.1",
        "--server-port",
        str(OPENRGB_SERVER_PORT),
        "--noautoconnect",
    ]
    for zone in fan_zones:
        arguments.extend(
            (
                "--device",
                str(hub.index),
                "--zone",
                str(zone),
                "--mode",
                "Direct",
                "--color",
                normalized,
            )
        )
    return arguments, len(fan_zones)


def hold_hub_color(color: tuple[int, int, int]) -> None:
    arguments, fan_count = build_hub_server_command(color)
    print(
        f"holding {fan_count} iCUE LINK LX fan(s), "
        f"{LX_LED_COUNT} LEDs each, on 127.0.0.1:{OPENRGB_SERVER_PORT}",
        flush=True,
    )
    os.execvp(arguments[0], arguments)

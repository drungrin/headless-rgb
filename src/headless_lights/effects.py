from __future__ import annotations

import math
import time

from headless_lights.beelight import protocol
from headless_lights.beelight.device import BeelightDevice
from headless_lights.hub import HUB_DEVICE_NAME, LX_LED_COUNT, LX_ZONE_NAME


AURA_DEVICE_NAME = "ASUS PRIME Z690-P"
AURA_ZONE_INDEX = 2
AURA_LED_COUNT = 24
CORSAIR_RAM_LED_COUNT = 12
BEELIGHT_LED_COUNT = 33


WATERCOLOR_PALETTE = (
    (20, 222, 255),
    (65, 137, 255),
    (172, 91, 255),
    (255, 48, 194),
    (255, 153, 202),
    (255, 244, 151),
    (164, 255, 241),
)


def _smooth_mix(left: int, right: int, amount: float) -> int:
    weight = (1.0 - math.cos(math.pi * amount)) / 2.0
    return round(left * (1.0 - weight) + right * weight)


def watercolor_color(position: float, elapsed: float) -> tuple[int, int, int]:
    """Return one slowly drifting, softly blended spectrum sample."""
    phase = position * 0.92 + elapsed / 12.0
    phase += 0.055 * math.sin(2.0 * math.pi * (position * 0.43 - elapsed / 17.0))
    phase += 0.018 * math.sin(2.0 * math.pi * (position * 1.71 + elapsed / 9.0))
    palette_position = (phase % 1.0) * len(WATERCOLOR_PALETTE)
    left_index = math.floor(palette_position)
    amount = palette_position - left_index
    left = WATERCOLOR_PALETTE[left_index]
    right = WATERCOLOR_PALETTE[(left_index + 1) % len(WATERCOLOR_PALETTE)]

    wash = 0.035 + 0.035 * (
        1.0 + math.sin(2.0 * math.pi * (position * 0.73 + elapsed / 13.0))
    ) / 2.0
    return tuple(
        round(_smooth_mix(left[channel], right[channel], amount) * (1.0 - wash)
              + 255 * wash)
        for channel in range(3)
    )


def render_watercolor_fans(
    fan_count: int,
    *,
    elapsed: float,
) -> list[list[tuple[int, int, int]]]:
    if fan_count <= 0:
        raise ValueError("fan count must be positive")
    frame: list[list[tuple[int, int, int]]] = []
    for fan in range(fan_count):
        colors = []
        for led in range(LX_LED_COUNT):
            ring_position = led / LX_LED_COUNT
            position = fan * 0.23 + ring_position * 0.72
            colors.append(watercolor_color(position, elapsed))
        frame.append(colors)
    return frame


def render_watercolor_line(
    led_count: int,
    *,
    elapsed: float,
    offset: float,
    span: float,
) -> list[tuple[int, int, int]]:
    if led_count <= 0:
        raise ValueError("LED count must be positive")
    denominator = max(1, led_count - 1)
    return [
        watercolor_color(offset + span * led / denominator, elapsed)
        for led in range(led_count)
    ]


def run_watercolor(*, scope: str, seconds: float | None, fps: int) -> None:
    from openrgb import OpenRGBClient
    from openrgb.utils import DeviceType, RGBColor

    if scope not in {"hub", "local", "pc"}:
        raise ValueError("effect scope must be hub, local or pc")
    if not 1 <= fps <= 30:
        raise ValueError("effect FPS must be between 1 and 30")
    if seconds is not None and not 1 <= seconds <= 300:
        raise ValueError("preview duration must be between 1 and 300 seconds")

    client = OpenRGBClient(
        "127.0.0.1",
        6742,
        name="headless-lights-watercolor-preview",
    )
    hubs = [device for device in client.devices if device.name == HUB_DEVICE_NAME]
    if len(hubs) != 1:
        raise RuntimeError(f"expected one iCUE LINK System Hub, found {len(hubs)}")
    zones = [zone for zone in hubs[0].zones if zone.name == LX_ZONE_NAME]
    if not zones or any(len(zone.leds) != LX_LED_COUNT for zone in zones):
        raise RuntimeError("invalid iCUE LINK LX zone topology")

    ram_zones = []
    aura_zone = None
    if scope in {"local", "pc"}:
        ram_devices = [
            device
            for device in client.devices
            if device.type == DeviceType.DRAM and "corsair" in device.name.lower()
        ]
        if len(ram_devices) != 2:
            raise RuntimeError(
                f"expected two Corsair RGB memory modules, found {len(ram_devices)}"
            )
        ram_zones = [device.zones[0] for device in ram_devices]
        if any(len(zone.leds) != CORSAIR_RAM_LED_COUNT for zone in ram_zones):
            raise RuntimeError("invalid Corsair RGB memory topology")

        aura_devices = [
            device for device in client.devices if device.name == AURA_DEVICE_NAME
        ]
        if len(aura_devices) != 1:
            raise RuntimeError(
                f"expected one {AURA_DEVICE_NAME}, found {len(aura_devices)}"
            )
        aura_zone = aura_devices[0].zones[AURA_ZONE_INDEX]
        if len(aura_zone.leds) != AURA_LED_COUNT:
            raise RuntimeError("invalid Asiahorse zone topology")

    beelight = None
    if scope == "pc":
        beelight = BeelightDevice(timeout=1.5)
        beelight.__enter__()
        try:
            _, config_data = beelight.initialize()
            config = protocol.parse_sync_config(config_data)
            if config.total_pixels != BEELIGHT_LED_COUNT:
                raise RuntimeError(
                    f"expected {BEELIGHT_LED_COUNT} Beelight pixels, "
                    f"found {config.total_pixels}"
                )
            beelight.set_pc_mode()
            beelight.switch(True)
        except BaseException:
            beelight.__exit__(None, None, None)
            raise

    duration = "continuously" if seconds is None else f"for {seconds:g}s"
    print(
        f"rendering Watercolor Spectrum scope={scope} on {len(zones)} fans "
        f"at {fps} FPS {duration}",
        flush=True,
    )
    try:
        start = time.monotonic()
        next_frame = start
        while True:
            now = time.monotonic()
            runtime = now - start
            if seconds is not None and runtime >= seconds:
                return
            elapsed = time.time()
            frame = render_watercolor_fans(len(zones), elapsed=elapsed)
            for zone, colors in zip(zones, frame, strict=True):
                zone.set_colors([RGBColor(*color) for color in colors], fast=True)
            for index, zone in enumerate(ram_zones):
                colors = render_watercolor_line(
                    CORSAIR_RAM_LED_COUNT,
                    elapsed=elapsed,
                    offset=0.14 + index * 0.31,
                    span=0.56,
                )
                zone.set_colors([RGBColor(*color) for color in colors], fast=True)
            if aura_zone is not None:
                colors = render_watercolor_line(
                    AURA_LED_COUNT,
                    elapsed=elapsed,
                    offset=0.38,
                    span=1.08,
                )
                aura_zone.set_colors([RGBColor(*color) for color in colors], fast=True)
            if beelight is not None:
                colors = render_watercolor_line(
                    BEELIGHT_LED_COUNT,
                    elapsed=elapsed,
                    offset=0.67,
                    span=1.24,
                )
                beelight.set_pixels(colors)
                beelight.process_incoming()
            next_frame += 1.0 / fps
            delay = next_frame - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_frame = time.monotonic()
    finally:
        if beelight is not None:
            beelight.__exit__(None, None, None)


def preview_watercolor(*, scope: str, seconds: float, fps: int) -> None:
    run_watercolor(scope=scope, seconds=seconds, fps=fps)


def hold_watercolor(*, scope: str, fps: int) -> None:
    run_watercolor(scope=scope, seconds=None, fps=fps)

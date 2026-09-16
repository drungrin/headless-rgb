from __future__ import annotations

import math
import time

from headless_lights.beelight import protocol
from headless_lights.beelight.device import BeelightDevice
from headless_lights.hub import HUB_DEVICE_NAME, LX_LED_COUNT, LX_ZONE_NAME


AURA_DEVICE_NAME = "ASUS PRIME Z690-P"
AURA_ZONE_INDEX = 2
AURA_LED_COUNT = 26
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

STRANGER_FLASH_STOPS = (
    (0.6270491803278688, (128, 43, 36), 1.0),
    (0.6516393442622951, (128, 0, 0), 0.0),
    (0.6536885245901639, (128, 0, 0), 1.0),
    (0.6762295081967213, (128, 0, 0), 0.27451),
    (0.7028688524590164, (128, 0, 0), 0.290196),
    (0.7110655737704918, (128, 43, 36), 1.0),
    (0.7356557377049180, (128, 0, 0), 1.0),
    (0.7561475409836066, (128, 0, 0), 0.0),
)


def borderlands4_color(
    position: float,
    elapsed: float,
    *,
    lane: int = 0,
) -> tuple[int, int, int]:
    """Render the continuous red, orange and gold layers from Borderlands 4."""
    red_pulse = 0.18 + 0.12 * (
        1.0 + math.sin(2.0 * math.pi * (elapsed / 3.0 + lane * 0.11))
    ) / 2.0
    color = _mix_color((192, 0, 2), (255, 18, 0), red_pulse)

    orange_wave = _circular_peak(
        (position - elapsed / 5.0 + lane * 0.071) % 1.0,
        0.36,
        0.25,
    )
    gold_wave = _circular_peak(
        (position - elapsed / 7.3 - lane * 0.043) % 1.0,
        0.74,
        0.17,
    )
    color = _screen_color(color, (255, 125, 0), orange_wave * 0.92)
    return _screen_color(color, (250, 180, 0), gold_wave * 0.76)


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


def _mix_color(
    left: tuple[int, int, int],
    right: tuple[int, int, int],
    amount: float,
) -> tuple[int, int, int]:
    return tuple(
        round(left[channel] * (1.0 - amount) + right[channel] * amount)
        for channel in range(3)
    )


def _screen_color(
    base: tuple[int, int, int],
    layer: tuple[int, int, int],
    opacity: float,
) -> tuple[int, int, int]:
    opacity = max(0.0, min(1.0, opacity))
    return tuple(
        round(
            255
            - (255 - base[channel])
            * (255 - layer[channel] * opacity)
            / 255
        )
        for channel in range(3)
    )


def _circular_peak(phase: float, center: float, width: float) -> float:
    distance = abs((phase - center + 0.5) % 1.0 - 0.5)
    if distance >= width:
        return 0.0
    normalized = 1.0 - distance / width
    return normalized * normalized * (3.0 - 2.0 * normalized)


def _stranger_flash(elapsed: float) -> tuple[tuple[int, int, int], float]:
    phase = (elapsed % 7.0) / 7.0
    if phase < STRANGER_FLASH_STOPS[0][0] or phase > STRANGER_FLASH_STOPS[-1][0]:
        return (0, 0, 0), 0.0
    for left, right in zip(
        STRANGER_FLASH_STOPS,
        STRANGER_FLASH_STOPS[1:],
        strict=True,
    ):
        if left[0] <= phase <= right[0]:
            amount = (phase - left[0]) / (right[0] - left[0])
            return (
                _mix_color(left[1], right[1], amount),
                left[2] * (1.0 - amount) + right[2] * amount,
            )
    return (0, 0, 0), 0.0


def stranger_things_color(
    position: float,
    elapsed: float,
    *,
    lane: int = 0,
) -> tuple[int, int, int]:
    """Approximate the layered ambient portion of the iCUE profile."""
    purple_amount = 0.5 + 0.5 * math.sin(
        2.0 * math.pi * (position * 0.73 + elapsed / 8.5)
    )
    color = _mix_color((6, 4, 23), (10, 4, 46), purple_amount)

    red_wave_fast = _circular_peak(
        (position - elapsed / 4.8) % 1.0,
        0.18 + (lane % 3) * 0.19,
        0.24,
    )
    red_wave_slow = _circular_peak(
        (position - elapsed / 9.5) % 1.0,
        0.71 - (lane % 2) * 0.17,
        0.18,
    )
    color = _screen_color(color, (125, 0, 0), red_wave_fast * 0.92)
    color = _screen_color(color, (125, 0, 1), red_wave_slow * 0.72)

    rain_phase_1 = (position * 7.0 + elapsed * 0.82 + lane * 0.37) % 1.0
    rain_phase_2 = (position * 11.0 + elapsed * 0.57 + lane * 0.61) % 1.0
    rain_1 = _circular_peak(rain_phase_1, 0.08, 0.065)
    rain_2 = _circular_peak(rain_phase_2, 0.56, 0.045)
    color = _screen_color(color, (128, 0, 0), rain_1 * 0.9)
    color = _screen_color(color, (64, 0, 0), rain_2 * 0.85)

    flash_color, flash_opacity = _stranger_flash(elapsed)
    return _screen_color(color, flash_color, flash_opacity)


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


def render_stranger_fans(
    fan_count: int,
    *,
    elapsed: float,
) -> list[list[tuple[int, int, int]]]:
    if fan_count <= 0:
        raise ValueError("fan count must be positive")
    return [
        [
            stranger_things_color(
                fan * 0.21 + led / LX_LED_COUNT * 0.86,
                elapsed,
                lane=fan,
            )
            for led in range(LX_LED_COUNT)
        ]
        for fan in range(fan_count)
    ]


def render_stranger_line(
    led_count: int,
    *,
    elapsed: float,
    offset: float,
    span: float,
    lane: int,
) -> list[tuple[int, int, int]]:
    if led_count <= 0:
        raise ValueError("LED count must be positive")
    denominator = max(1, led_count - 1)
    return [
        stranger_things_color(
            offset + span * led / denominator,
            elapsed,
            lane=lane,
        )
        for led in range(led_count)
    ]


def render_borderlands4_fans(
    fan_count: int,
    *,
    elapsed: float,
) -> list[list[tuple[int, int, int]]]:
    if fan_count <= 0:
        raise ValueError("fan count must be positive")
    return [
        [
            borderlands4_color(
                fan * 0.19 + led / LX_LED_COUNT * 0.88,
                elapsed,
                lane=fan,
            )
            for led in range(LX_LED_COUNT)
        ]
        for fan in range(fan_count)
    ]


def render_borderlands4_line(
    led_count: int,
    *,
    elapsed: float,
    offset: float,
    span: float,
    lane: int,
) -> list[tuple[int, int, int]]:
    if led_count <= 0:
        raise ValueError("LED count must be positive")
    denominator = max(1, led_count - 1)
    return [
        borderlands4_color(
            offset + span * led / denominator,
            elapsed,
            lane=lane,
        )
        for led in range(led_count)
    ]


def run_effect(
    effect: str,
    *,
    scope: str,
    seconds: float | None,
    fps: int,
) -> None:
    from openrgb import OpenRGBClient
    from openrgb.utils import DeviceType, RGBColor

    if scope not in {"hub", "local", "pc"}:
        raise ValueError("effect scope must be hub, local or pc")
    if effect not in {"watercolor", "stranger-things", "borderlands-4"}:
        raise ValueError("unknown effect")
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
        f"rendering {effect} scope={scope} on {len(zones)} fans "
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
            if effect == "watercolor":
                frame = render_watercolor_fans(len(zones), elapsed=elapsed)
            elif effect == "stranger-things":
                frame = render_stranger_fans(len(zones), elapsed=elapsed)
            else:
                frame = render_borderlands4_fans(len(zones), elapsed=elapsed)
            for zone, colors in zip(zones, frame, strict=True):
                zone.set_colors([RGBColor(*color) for color in colors], fast=True)
            for index, zone in enumerate(ram_zones):
                if effect == "watercolor":
                    colors = render_watercolor_line(
                        CORSAIR_RAM_LED_COUNT,
                        elapsed=elapsed,
                        offset=0.14 + index * 0.31,
                        span=0.56,
                    )
                elif effect == "stranger-things":
                    colors = render_stranger_line(
                        CORSAIR_RAM_LED_COUNT,
                        elapsed=elapsed,
                        offset=0.14 + index * 0.31,
                        span=0.78,
                        lane=10 + index,
                    )
                else:
                    colors = render_borderlands4_line(
                        CORSAIR_RAM_LED_COUNT,
                        elapsed=elapsed,
                        offset=0.14 + index * 0.31,
                        span=0.78,
                        lane=10 + index,
                    )
                zone.set_colors([RGBColor(*color) for color in colors], fast=True)
            if aura_zone is not None:
                if effect == "watercolor":
                    colors = render_watercolor_line(
                        AURA_LED_COUNT,
                        elapsed=elapsed,
                        offset=0.38,
                        span=1.08,
                    )
                elif effect == "stranger-things":
                    colors = render_stranger_line(
                        AURA_LED_COUNT,
                        elapsed=elapsed,
                        offset=0.38,
                        span=1.2,
                        lane=12,
                    )
                else:
                    colors = render_borderlands4_line(
                        AURA_LED_COUNT,
                        elapsed=elapsed,
                        offset=0.38,
                        span=1.2,
                        lane=12,
                    )
                aura_zone.set_colors([RGBColor(*color) for color in colors], fast=True)
            if beelight is not None:
                if effect == "watercolor":
                    colors = render_watercolor_line(
                        BEELIGHT_LED_COUNT,
                        elapsed=elapsed,
                        offset=0.67,
                        span=1.24,
                    )
                elif effect == "stranger-things":
                    colors = render_stranger_line(
                        BEELIGHT_LED_COUNT,
                        elapsed=elapsed,
                        offset=0.67,
                        span=1.45,
                        lane=13,
                    )
                else:
                    colors = render_borderlands4_line(
                        BEELIGHT_LED_COUNT,
                        elapsed=elapsed,
                        offset=0.67,
                        span=1.45,
                        lane=13,
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


def preview_effect(effect: str, *, scope: str, seconds: float, fps: int) -> None:
    run_effect(effect, scope=scope, seconds=seconds, fps=fps)


def hold_effect(effect: str, *, scope: str, fps: int) -> None:
    run_effect(effect, scope=scope, seconds=None, fps=fps)

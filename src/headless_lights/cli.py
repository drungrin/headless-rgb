from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import subprocess
import sys

from headless_lights.aura import (
    ASIAHORSE_LED_COUNT,
    ASIAHORSE_ZONE,
    set_asiahorse_color,
)
from headless_lights.beelight.device import BeelightDevice, discover_port
from headless_lights.beelight import protocol
from headless_lights.effects import hold_watercolor, preview_watercolor
from headless_lights.hub import hold_hub_color
from headless_lights.mac import DEFAULT_MAC_HOST, send_agent_command
from headless_lights.ram import set_corsair_memory_color
from headless_lights.service import (
    hold_color,
    install_aura_user_service,
    install_hub_user_service,
    install_ram_user_service,
    install_user_service,
    install_watercolor_user_service,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="headless-lights")
    parser.add_argument("--port", type=Path, help="serial device (auto-detected by default)")
    parser.add_argument("--timeout", type=float, default=1.5)
    subparsers = parser.add_subparsers(dest="action", required=True)

    subparsers.add_parser("detect", help="print the detected Beelight serial device")
    subparsers.add_parser("info", help="query firmware and LED layout")
    subparsers.add_parser("on", help="turn the strip on")
    subparsers.add_parser("off", help="turn the strip off")

    brightness = subparsers.add_parser("brightness", help="set brightness from 0 to 100")
    brightness.add_argument("value", type=int)

    color = subparsers.add_parser("color", help="set one static RGB color")
    color.add_argument("value", help="RRGGBB or #RRGGBB")

    pixels = subparsers.add_parser("pixels", help="send one RGB value per LED")
    pixels.add_argument("values", nargs="+", help="RRGGBB values, one per LED")

    hold = subparsers.add_parser("hold", help="continuously stream one fixed color")
    hold.add_argument("value", help="RRGGBB or #RRGGBB")
    hold.add_argument("--fps", type=int, default=20)
    hold.add_argument("--brightness", type=int)
    hold.add_argument("--reconnect-delay", type=float, default=2.0)

    install = subparsers.add_parser(
        "service-install",
        help="install and start a user service for a fixed color",
    )
    install.add_argument("value", help="RRGGBB or #RRGGBB")
    install.add_argument("--fps", type=int, default=20)
    install.add_argument("--brightness", type=int)

    mac_color = subparsers.add_parser(
        "mac-color",
        help="set K70 MAX, Scimitar and G560 through the Mac agent",
    )
    mac_color.add_argument("value", help="RRGGBB or #RRGGBB")
    mac_color.add_argument("--host", default=DEFAULT_MAC_HOST)
    mac_color.add_argument("--ssh-timeout", type=float, default=10.0)

    mac_status = subparsers.add_parser(
        "mac-status",
        help="query the Mac lighting agent",
    )
    mac_status.add_argument("--host", default=DEFAULT_MAC_HOST)
    mac_status.add_argument("--ssh-timeout", type=float, default=10.0)

    mac_effect = subparsers.add_parser(
        "mac-effect",
        help="start an animated effect on the Mac agent",
    )
    mac_effect.add_argument("effect", choices=("watercolor",))
    mac_effect.add_argument("--host", default=DEFAULT_MAC_HOST)
    mac_effect.add_argument("--ssh-timeout", type=float, default=10.0)

    ram_color = subparsers.add_parser(
        "ram-color",
        help="set all locally detected Corsair RGB memory modules",
    )
    ram_color.add_argument("value", help="RRGGBB or #RRGGBB")

    ram_install = subparsers.add_parser(
        "ram-service-install",
        help="apply a Corsair memory color automatically at login",
    )
    ram_install.add_argument("value", help="RRGGBB or #RRGGBB")

    aura_color = subparsers.add_parser(
        "aura-color",
        help="set the local Asiahorse strip through ASUS Aura",
    )
    aura_color.add_argument("value", help="RRGGBB or #RRGGBB")

    aura_install = subparsers.add_parser(
        "aura-service-install",
        help="apply the Asiahorse color automatically at login",
    )
    aura_install.add_argument("value", help="RRGGBB or #RRGGBB")

    hub_hold = subparsers.add_parser(
        "hub-hold",
        help="continuously hold one color on all detected iCUE LINK LX fans",
    )
    hub_hold.add_argument("value", help="RRGGBB or #RRGGBB")

    hub_install = subparsers.add_parser(
        "hub-service-install",
        help="install continuous iCUE LINK fan lighting at login",
    )
    hub_install.add_argument("value", help="RRGGBB or #RRGGBB")

    effect_preview = subparsers.add_parser(
        "effect-preview",
        help="run a temporary animated effect preview",
    )
    effect_preview.add_argument("effect", choices=("watercolor",))
    effect_preview.add_argument(
        "--scope", choices=("hub", "local", "pc"), default="hub"
    )
    effect_preview.add_argument("--seconds", type=float, default=20.0)
    effect_preview.add_argument("--fps", type=int, default=12)

    effect_hold = subparsers.add_parser(
        "effect-hold",
        help="continuously render an animated effect",
    )
    effect_hold.add_argument("effect", choices=("watercolor",))
    effect_hold.add_argument(
        "--scope", choices=("hub", "local", "pc"), default="hub"
    )
    effect_hold.add_argument("--fps", type=int, default=12)

    effect_install = subparsers.add_parser(
        "effect-service-install",
        help="install and start a persistent animated effect",
    )
    effect_install.add_argument("effect", choices=("watercolor",))
    effect_install.add_argument(
        "--scope", choices=("hub", "local", "pc"), default="hub"
    )
    effect_install.add_argument("--fps", type=int, default=12)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        if args.action == "detect":
            print(args.port or discover_port())
            return
        if args.action == "hold":
            hold_color(
                protocol.parse_color(args.value),
                port=args.port,
                fps=args.fps,
                brightness=args.brightness,
                reconnect_delay=args.reconnect_delay,
                timeout=args.timeout,
            )
            return
        if args.action == "service-install":
            color = protocol.parse_color(args.value)
            normalized_color = "".join(f"{component:02x}" for component in color)
            unit_path = install_user_service(
                normalized_color,
                fps=args.fps,
                brightness=args.brightness,
                port=args.port,
            )
            print(f"installed and started: {unit_path}")
            return
        if args.action == "mac-color":
            color = protocol.parse_color(args.value)
            normalized_color = "".join(f"{component:02x}" for component in color)
            print(
                send_agent_command(
                    f"COLOR {normalized_color}",
                    host=args.host,
                    timeout=args.ssh_timeout,
                )
            )
            return
        if args.action == "mac-status":
            print(
                send_agent_command(
                    "STATUS",
                    host=args.host,
                    timeout=args.ssh_timeout,
                )
            )
            return
        if args.action == "mac-effect":
            print(
                send_agent_command(
                    "EFFECT WATERCOLOR",
                    host=args.host,
                    timeout=args.ssh_timeout,
                )
            )
            return
        if args.action == "ram-color":
            devices = set_corsair_memory_color(protocol.parse_color(args.value))
            summary = ", ".join(
                f"{device.index}:{device.name} ({device.location})"
                for device in devices
            )
            print(f"updated {len(devices)} Corsair DRAM device(s): {summary}")
            return
        if args.action == "ram-service-install":
            color = protocol.parse_color(args.value)
            normalized_color = "".join(f"{component:02x}" for component in color)
            unit_path = install_ram_user_service(normalized_color)
            print(f"installed and started: {unit_path}")
            return
        if args.action == "aura-color":
            device = set_asiahorse_color(protocol.parse_color(args.value))
            print(
                f"updated {device.name} zone {ASIAHORSE_ZONE} "
                f"with {ASIAHORSE_LED_COUNT} Asiahorse LEDs"
            )
            return
        if args.action == "aura-service-install":
            color = protocol.parse_color(args.value)
            normalized_color = "".join(f"{component:02x}" for component in color)
            unit_path = install_aura_user_service(normalized_color)
            print(f"installed and started: {unit_path}")
            return
        if args.action == "hub-hold":
            hold_hub_color(protocol.parse_color(args.value))
            return
        if args.action == "hub-service-install":
            color = protocol.parse_color(args.value)
            normalized_color = "".join(f"{component:02x}" for component in color)
            unit_path = install_hub_user_service(normalized_color)
            print(f"installed and started: {unit_path}")
            return
        if args.action == "effect-preview":
            preview_watercolor(scope=args.scope, seconds=args.seconds, fps=args.fps)
            return
        if args.action == "effect-hold":
            hold_watercolor(scope=args.scope, fps=args.fps)
            return
        if args.action == "effect-service-install":
            unit_path = install_watercolor_user_service(scope=args.scope, fps=args.fps)
            print(f"installed and started: {unit_path}")
            return

        with BeelightDevice(args.port, timeout=args.timeout) as device:
            if args.action == "info":
                firmware_data, config_data = device.initialize()
                firmware = protocol.parse_firmware_info(firmware_data)
                config = protocol.parse_sync_config(config_data)
                print(f"port: {device.port}")
                print(f"product: {firmware.product_id}")
                print(f"BSP: {'.'.join(map(str, firmware.bsp_version))}")
                print(f"app: {'.'.join(map(str, firmware.app_version))}")
                print(f"device ID: {firmware.device_id.hex()}")
                print(f"manufacturer: {firmware.manufacturer}")
                print(f"market: {firmware.market}")
                print(f"pixels: {config.total_pixels}")
                print(
                    f"channels: {config.channel_count} "
                    f"({', '.join(map(str, config.channel_pixels))})"
                )
            elif args.action == "on":
                device.switch(True)
            elif args.action == "off":
                device.switch(False)
            elif args.action == "brightness":
                device.set_brightness(args.value)
            elif args.action == "color":
                device.set_pc_mode()
                device.switch(True)
                device.set_color(*protocol.parse_color(args.value))
            elif args.action == "pixels":
                colors = protocol.parse_colors(args.values)
                _, config_data = device.initialize()
                config = protocol.parse_sync_config(config_data)
                if len(colors) != config.total_pixels:
                    raise ValueError(
                        f"this strip reports {config.total_pixels} pixels, "
                        f"but {len(colors)} colors were supplied"
                    )
                device.set_pc_mode()
                device.switch(True)
                device.set_pixels(colors)
    except (
        FileNotFoundError,
        PermissionError,
        subprocess.SubprocessError,
        TimeoutError,
        ValueError,
        RuntimeError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error

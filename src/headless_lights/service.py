from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import time

from headless_lights.beelight.device import BeelightDevice
from headless_lights.beelight import protocol


SERVICE_NAME = "headless-lights.service"
RAM_SERVICE_NAME = "headless-lights-ram.service"
AURA_SERVICE_NAME = "headless-lights-aura.service"
HUB_SERVICE_NAME = "headless-lights-hub.service"
WATERCOLOR_SERVICE_NAME = "headless-lights-watercolor.service"


def _validate_stream_options(
    *, fps: int, brightness: int | None, reconnect_delay: float
) -> None:
    if not 1 <= fps <= 60:
        raise ValueError("fps must be between 1 and 60")
    if brightness is not None and not 0 <= brightness <= 100:
        raise ValueError("brightness must be between 0 and 100")
    if reconnect_delay < 0:
        raise ValueError("reconnect delay cannot be negative")


def hold_color(
    color: tuple[int, int, int],
    *,
    port: Path | None,
    fps: int,
    brightness: int | None,
    reconnect_delay: float,
    timeout: float,
) -> None:
    _validate_stream_options(
        fps=fps,
        brightness=brightness,
        reconnect_delay=reconnect_delay,
    )

    period = 1.0 / fps
    while True:
        try:
            with BeelightDevice(port, timeout=timeout) as device:
                _, config_data = device.initialize()
                config = protocol.parse_sync_config(config_data)
                pixels = [color] * config.total_pixels
                device.set_pc_mode()
                device.switch(True)
                if brightness is not None:
                    device.set_brightness(brightness)

                print(
                    f"streaming {color!r} to {config.total_pixels} pixels "
                    f"at {fps} FPS on {device.port}",
                    file=sys.stderr,
                    flush=True,
                )
                next_frame = time.monotonic()
                while True:
                    device.set_pixels(pixels)
                    device.process_incoming()
                    next_frame += period
                    delay = next_frame - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
                    elif delay < -period:
                        next_frame = time.monotonic()
        except KeyboardInterrupt:
            return
        except (FileNotFoundError, OSError, TimeoutError, protocol.ProtocolError) as error:
            print(
                f"Beelight connection lost: {error}; retrying in {reconnect_delay:g}s",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(reconnect_delay)


def _systemd_quote(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise ValueError("systemd argument cannot contain newlines")
    escaped = value.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def build_user_unit(
    color: str,
    *,
    fps: int,
    brightness: int | None,
    port: Path | None,
) -> str:
    _validate_stream_options(fps=fps, brightness=brightness, reconnect_delay=2.0)
    source_root = Path(__file__).resolve().parents[1]
    arguments = [
        sys.executable,
        "-m",
        "headless_lights",
    ]
    if port is not None:
        arguments.extend(("--port", str(port)))
    arguments.extend(("hold", color, "--fps", str(fps)))
    if brightness is not None:
        arguments.extend(("--brightness", str(brightness)))
    exec_start = " ".join(_systemd_quote(argument) for argument in arguments)
    return f"""[Unit]
Description=Headless Lights RGB stream

[Service]
Type=simple
Environment={_systemd_quote(f"PYTHONPATH={source_root}")}
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart={exec_start}
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
"""


def install_user_service(
    color: str,
    *,
    fps: int,
    brightness: int | None,
    port: Path | None,
) -> Path:
    unit = build_user_unit(color, fps=fps, brightness=brightness, port=port)
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_path = unit_dir / SERVICE_NAME
    unit_path.write_text(unit, encoding="utf-8")
    subprocess.run(
        ("systemctl", "--user", "daemon-reload"),
        check=True,
        timeout=10,
    )
    subprocess.run(
        ("systemctl", "--user", "enable", SERVICE_NAME),
        check=True,
        timeout=15,
    )
    subprocess.run(
        ("systemctl", "--user", "restart", SERVICE_NAME),
        check=True,
        timeout=15,
    )
    return unit_path


def build_ram_user_unit(color: str) -> str:
    source_root = Path(__file__).resolve().parents[1]
    arguments = [sys.executable, "-m", "headless_lights", "ram-color", color]
    exec_start = " ".join(_systemd_quote(argument) for argument in arguments)
    return f"""[Unit]
Description=Headless Lights Corsair RGB memory

[Service]
Type=oneshot
Environment={_systemd_quote(f"PYTHONPATH={source_root}")}
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart={exec_start}
RemainAfterExit=yes

[Install]
WantedBy=default.target
"""


def install_ram_user_service(color: str) -> Path:
    unit = build_ram_user_unit(color)
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_path = unit_dir / RAM_SERVICE_NAME
    unit_path.write_text(unit, encoding="utf-8")
    subprocess.run(
        ("systemctl", "--user", "daemon-reload"),
        check=True,
        timeout=10,
    )
    subprocess.run(
        ("systemctl", "--user", "enable", RAM_SERVICE_NAME),
        check=True,
        timeout=15,
    )
    subprocess.run(
        ("systemctl", "--user", "restart", RAM_SERVICE_NAME),
        check=True,
        timeout=45,
    )
    return unit_path


def build_aura_user_unit(color: str) -> str:
    source_root = Path(__file__).resolve().parents[1]
    arguments = [sys.executable, "-m", "headless_lights", "aura-color", color]
    exec_start = " ".join(_systemd_quote(argument) for argument in arguments)
    return f"""[Unit]
Description=Headless Lights Asiahorse strip through ASUS Aura
After={RAM_SERVICE_NAME}

[Service]
Type=oneshot
Environment={_systemd_quote(f"PYTHONPATH={source_root}")}
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart={exec_start}
RemainAfterExit=yes
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
"""


def install_aura_user_service(color: str) -> Path:
    unit = build_aura_user_unit(color)
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_path = unit_dir / AURA_SERVICE_NAME
    unit_path.write_text(unit, encoding="utf-8")
    subprocess.run(
        ("systemctl", "--user", "daemon-reload"),
        check=True,
        timeout=10,
    )
    subprocess.run(
        ("systemctl", "--user", "enable", AURA_SERVICE_NAME),
        check=True,
        timeout=15,
    )
    subprocess.run(
        ("systemctl", "--user", "restart", AURA_SERVICE_NAME),
        check=True,
        timeout=60,
    )
    return unit_path


def build_hub_user_unit(color: str) -> str:
    source_root = Path(__file__).resolve().parents[1]
    arguments = [sys.executable, "-m", "headless_lights", "hub-hold", color]
    exec_start = " ".join(_systemd_quote(argument) for argument in arguments)
    return f"""[Unit]
Description=Headless Lights Corsair iCUE LINK fans
After={RAM_SERVICE_NAME} {AURA_SERVICE_NAME}

[Service]
Type=simple
Environment={_systemd_quote(f"PYTHONPATH={source_root}")}
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart={exec_start}
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
"""


def install_hub_user_service(color: str) -> Path:
    unit = build_hub_user_unit(color)
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_path = unit_dir / HUB_SERVICE_NAME
    unit_path.write_text(unit, encoding="utf-8")
    subprocess.run(
        ("systemctl", "--user", "daemon-reload"),
        check=True,
        timeout=10,
    )
    subprocess.run(
        ("systemctl", "--user", "enable", HUB_SERVICE_NAME),
        check=True,
        timeout=15,
    )
    subprocess.run(
        ("systemctl", "--user", "restart", HUB_SERVICE_NAME),
        check=True,
        timeout=30,
    )
    return unit_path


def build_watercolor_user_unit(*, scope: str, fps: int) -> str:
    if not 1 <= fps <= 30:
        raise ValueError("effect FPS must be between 1 and 30")
    if scope not in {"hub", "local", "pc"}:
        raise ValueError("effect scope must be hub, local or pc")
    source_root = Path(__file__).resolve().parents[1]
    arguments = [
        sys.executable,
        "-m",
        "headless_lights",
        "effect-hold",
        "watercolor",
        "--scope",
        scope,
        "--fps",
        str(fps),
    ]
    exec_start = " ".join(_systemd_quote(argument) for argument in arguments)
    return f"""[Unit]
Description=Headless Lights Watercolor Spectrum renderer
After={HUB_SERVICE_NAME}
Requires={HUB_SERVICE_NAME}

[Service]
Type=simple
Environment={_systemd_quote(f"PYTHONPATH={source_root}")}
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart={exec_start}
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
"""


def install_watercolor_user_service(*, scope: str, fps: int) -> Path:
    unit = build_watercolor_user_unit(scope=scope, fps=fps)
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_path = unit_dir / WATERCOLOR_SERVICE_NAME
    unit_path.write_text(unit, encoding="utf-8")
    subprocess.run(
        ("systemctl", "--user", "daemon-reload"),
        check=True,
        timeout=10,
    )
    subprocess.run(
        ("systemctl", "--user", "enable", WATERCOLOR_SERVICE_NAME),
        check=True,
        timeout=15,
    )
    subprocess.run(
        ("systemctl", "--user", "restart", WATERCOLOR_SERVICE_NAME),
        check=True,
        timeout=15,
    )
    return unit_path

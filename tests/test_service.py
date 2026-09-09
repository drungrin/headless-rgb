from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from headless_lights.service import (
    AURA_SERVICE_NAME,
    HUB_SERVICE_NAME,
    RAM_SERVICE_NAME,
    SERVICE_NAME,
    EFFECT_SERVICE_NAME,
    LEGACY_WATERCOLOR_SERVICE_NAME,
    _systemd_quote,
    build_aura_user_unit,
    build_hub_user_unit,
    build_ram_user_unit,
    build_user_unit,
    build_effect_user_unit,
    install_aura_user_service,
    install_hub_user_service,
    install_ram_user_service,
    install_user_service,
    install_effect_user_service,
)


class ServiceTests(unittest.TestCase):
    def test_user_unit_runs_published_module(self) -> None:
        unit = build_user_unit(
            "0000ff",
            fps=20,
            brightness=60,
            port=None,
        )

        self.assertIn('"-m" "headless_lights" "hold" "0000ff"', unit)
        self.assertIn('"--fps" "20" "--brightness" "60"', unit)
        self.assertIn("Environment=\"PYTHONPATH=", unit)
        self.assertIn("Restart=on-failure", unit)
        self.assertNotIn("After=default.target", unit)

    def test_user_unit_rejects_invalid_fps(self) -> None:
        with self.assertRaises(ValueError):
            build_user_unit("0000ff", fps=0, brightness=None, port=None)

    def test_systemd_quote_rejects_newlines(self) -> None:
        with self.assertRaises(ValueError):
            _systemd_quote("bad\nvalue")

    def test_install_writes_unit_and_restarts_service(self) -> None:
        with TemporaryDirectory() as temporary_home:
            with (
                patch(
                    "headless_lights.service.Path.home",
                    return_value=Path(temporary_home),
                ),
                patch("headless_lights.service.subprocess.run") as run,
            ):
                unit_path = install_user_service(
                    "0000ff",
                    fps=20,
                    brightness=None,
                    port=None,
                )

            self.assertTrue(unit_path.is_file())
            self.assertIn('"hold" "0000ff"', unit_path.read_text())
            self.assertEqual(
                [call.args[0] for call in run.call_args_list],
                [
                    ("systemctl", "--user", "daemon-reload"),
                    ("systemctl", "--user", "enable", SERVICE_NAME),
                    ("systemctl", "--user", "restart", SERVICE_NAME),
                ],
            )

    def test_ram_unit_runs_scoped_memory_command(self) -> None:
        unit = build_ram_user_unit("0000ff")

        self.assertIn('"ram-color" "0000ff"', unit)
        self.assertIn("Type=oneshot", unit)
        self.assertIn("RemainAfterExit=yes", unit)

    def test_install_ram_service_writes_and_starts_unit(self) -> None:
        with TemporaryDirectory() as temporary_home:
            with (
                patch(
                    "headless_lights.service.Path.home",
                    return_value=Path(temporary_home),
                ),
                patch("headless_lights.service.subprocess.run") as run,
            ):
                unit_path = install_ram_user_service("0000ff")

            self.assertEqual(unit_path.name, RAM_SERVICE_NAME)
            self.assertIn('"ram-color" "0000ff"', unit_path.read_text())
            self.assertEqual(
                [call.args[0] for call in run.call_args_list],
                [
                    ("systemctl", "--user", "daemon-reload"),
                    ("systemctl", "--user", "enable", RAM_SERVICE_NAME),
                    ("systemctl", "--user", "restart", RAM_SERVICE_NAME),
                ],
            )

    def test_aura_unit_is_ordered_after_memory(self) -> None:
        unit = build_aura_user_unit("0000ff")

        self.assertIn('"aura-color" "0000ff"', unit)
        self.assertIn(f"After={RAM_SERVICE_NAME}", unit)
        self.assertIn("Restart=on-failure", unit)

    def test_install_aura_service_writes_and_starts_unit(self) -> None:
        with TemporaryDirectory() as temporary_home:
            with (
                patch(
                    "headless_lights.service.Path.home",
                    return_value=Path(temporary_home),
                ),
                patch("headless_lights.service.subprocess.run") as run,
            ):
                unit_path = install_aura_user_service("0000ff")

            self.assertEqual(unit_path.name, AURA_SERVICE_NAME)
            self.assertIn('"aura-color" "0000ff"', unit_path.read_text())
            self.assertEqual(
                [call.args[0] for call in run.call_args_list],
                [
                    ("systemctl", "--user", "daemon-reload"),
                    ("systemctl", "--user", "enable", AURA_SERVICE_NAME),
                    ("systemctl", "--user", "restart", AURA_SERVICE_NAME),
                ],
            )

    def test_hub_unit_starts_after_other_openrgb_users(self) -> None:
        unit = build_hub_user_unit("0000ff")

        self.assertIn('"hub-hold" "0000ff"', unit)
        self.assertIn(f"After={RAM_SERVICE_NAME} {AURA_SERVICE_NAME}", unit)
        self.assertIn("Type=simple", unit)

    def test_install_hub_service_writes_and_starts_unit(self) -> None:
        with TemporaryDirectory() as temporary_home:
            with (
                patch(
                    "headless_lights.service.Path.home",
                    return_value=Path(temporary_home),
                ),
                patch("headless_lights.service.subprocess.run") as run,
            ):
                unit_path = install_hub_user_service("0000ff")

            self.assertEqual(unit_path.name, HUB_SERVICE_NAME)
            self.assertIn('"hub-hold" "0000ff"', unit_path.read_text())
            self.assertEqual(
                [call.args[0] for call in run.call_args_list],
                [
                    ("systemctl", "--user", "daemon-reload"),
                    ("systemctl", "--user", "enable", HUB_SERVICE_NAME),
                    ("systemctl", "--user", "restart", HUB_SERVICE_NAME),
                ],
            )

    def test_effect_unit_requires_hub_server(self) -> None:
        unit = build_effect_user_unit("stranger-things", scope="local", fps=12)

        self.assertIn('"effect-hold" "stranger-things"', unit)
        self.assertIn(f"After={HUB_SERVICE_NAME}", unit)
        self.assertIn(f"Requires={HUB_SERVICE_NAME}", unit)
        self.assertIn('"--scope" "local"', unit)
        self.assertIn('"--fps" "12"', unit)

    def test_install_effect_service_migrates_legacy_unit(self) -> None:
        with TemporaryDirectory() as temporary_home:
            with (
                patch(
                    "headless_lights.service.Path.home",
                    return_value=Path(temporary_home),
                ),
                patch("headless_lights.service.subprocess.run") as run,
            ):
                unit_path = install_effect_user_service(
                    "stranger-things", scope="local", fps=12
                )

            self.assertEqual(unit_path.name, EFFECT_SERVICE_NAME)
            self.assertIn('"effect-hold" "stranger-things"', unit_path.read_text())
            self.assertEqual(
                [call.args[0] for call in run.call_args_list],
                [
                    (
                        "systemctl",
                        "--user",
                        "disable",
                        "--now",
                        LEGACY_WATERCOLOR_SERVICE_NAME,
                    ),
                    ("systemctl", "--user", "daemon-reload"),
                    ("systemctl", "--user", "enable", EFFECT_SERVICE_NAME),
                    ("systemctl", "--user", "restart", EFFECT_SERVICE_NAME),
                ],
            )


if __name__ == "__main__":
    unittest.main()

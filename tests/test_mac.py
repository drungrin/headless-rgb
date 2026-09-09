from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from headless_lights.mac import send_agent_command


class MacAgentTests(unittest.TestCase):
    @patch("headless_lights.mac.subprocess.run")
    def test_sends_validated_command_over_ssh(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=(),
            returncode=0,
            stdout="OK color=0000ff icue=ok g560=ok scimitar=ok\n",
            stderr="",
        )

        response = send_agent_command(
            "COLOR 0000ff",
            host="michel@172.16.0.104",
            timeout=7,
        )

        self.assertEqual(
            response,
            "OK color=0000ff icue=ok g560=ok scimitar=ok",
        )
        self.assertEqual(run.call_args.kwargs["input"], "COLOR 0000ff\n")
        self.assertEqual(run.call_args.kwargs["timeout"], 7)
        self.assertEqual(
            run.call_args.args[0][-3:],
            ("/usr/bin/nc", "127.0.0.1", "7531"),
        )

    @patch("headless_lights.mac.subprocess.run")
    def test_rejects_malformed_agent_response(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=(), returncode=0, stdout="unexpected\n", stderr=""
        )

        with self.assertRaisesRegex(RuntimeError, "invalid response"):
            send_agent_command("STATUS")

    def test_rejects_command_injection(self) -> None:
        with self.assertRaises(ValueError):
            send_agent_command("COLOR 0000ff\nSTATUS")

    @patch("headless_lights.mac.subprocess.run")
    def test_accepts_watercolor_effect_response(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=(),
            returncode=0,
            stdout="OK effect=watercolor icue=ok g560=ok scimitar=ok\n",
            stderr="",
        )

        response = send_agent_command("EFFECT WATERCOLOR")

        self.assertIn("effect=watercolor", response)
        self.assertEqual(run.call_args.kwargs["input"], "EFFECT WATERCOLOR\n")

    def test_rejects_option_like_host(self) -> None:
        with self.assertRaises(ValueError):
            send_agent_command("STATUS", host="-oProxyCommand=bad")


if __name__ == "__main__":
    unittest.main()

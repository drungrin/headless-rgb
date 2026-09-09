from __future__ import annotations

import re
import subprocess


DEFAULT_MAC_HOST = "michel@172.16.0.104"
_RESPONSE = re.compile(
    r"^(?:OK|PARTIAL) (?:color=[0-9a-f]{6}|effect=watercolor) "
    r"icue=(?:ok|error) g560=(?:ok|error) scimitar=(?:ok|error)$"
)


def send_agent_command(
    command: str,
    *,
    host: str = DEFAULT_MAC_HOST,
    timeout: float = 10.0,
) -> str:
    if (
        command not in {"STATUS", "EFFECT WATERCOLOR"}
        and not re.fullmatch(r"COLOR [0-9a-f]{6}", command)
    ):
        raise ValueError("invalid Mac agent command")
    if not host or host.startswith("-") or any(character.isspace() for character in host):
        raise ValueError("invalid SSH host")
    if timeout <= 0:
        raise ValueError("SSH timeout must be positive")

    completed = subprocess.run(
        (
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={max(1, min(60, round(timeout)))}",
            host,
            "/usr/bin/nc",
            "127.0.0.1",
            "7531",
        ),
        input=f"{command}\n",
        text=True,
        capture_output=True,
        check=True,
        timeout=timeout,
    )
    response = completed.stdout.strip()
    if not _RESPONSE.fullmatch(response):
        detail = completed.stderr.strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"invalid response from Mac agent{suffix}")
    return response

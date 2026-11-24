"""PowerShell — boshqa tool to'g'ri kelmaganda. Har doim tasdiq so'raydi."""

from __future__ import annotations

import subprocess
from typing import Annotated

from .base import Risk, registry

CREATE_NO_WINDOW = 0x08000000


@registry.tool(risk=Risk.CONFIRM, timeout=130,
               confirm=lambda a: f"PowerShell buyrug'ini bajarish:\n    {a.get('command')}")
def run_powershell(
    command: Annotated[str, "PowerShell command"],
    timeout_seconds: int = 60,
) -> dict:
    """Run a PowerShell command and return its output. Use ONLY when no other tool can do the job.
    The user must approve every command."""
    ps = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $ProgressPreference='SilentlyContinue'; " + command
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, timeout=max(5, min(timeout_seconds, 120)),
                           creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timed out"}
    out = r.stdout.decode("utf-8", "replace")
    err = r.stderr.decode("utf-8", "replace")
    return {"ok": r.returncode == 0, "exit_code": r.returncode,
            "stdout": out[-3000:], "stderr": err[-1500:], "truncated": len(out) > 3000}

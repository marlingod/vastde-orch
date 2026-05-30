"""Shared subprocess runner with structured error capture.

All shell-outs in this project go through `run()` so we get uniform
logging, env handling, and a single ShellError type to catch.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ShellResult:
    cmd: list[str]
    returncode: int
    stdout: str
    stderr: str


class ShellError(RuntimeError):
    def __init__(self, result: ShellResult, message: str | None = None) -> None:
        self.result = result
        cmd_str = " ".join(result.cmd)
        msg = message or f"command failed (exit {result.returncode}): {cmd_str}\nstderr: {result.stderr.strip()}"
        super().__init__(msg)


def which_or_raise(binary: str) -> str:
    """Return absolute path to `binary` or raise ShellError if not on PATH."""
    found = shutil.which(binary)
    if not found:
        raise ShellError(
            ShellResult(cmd=[binary], returncode=127, stdout="", stderr=""),
            message=f"required binary {binary!r} not found on PATH",
        )
    return found


def run(
    cmd: list[str],
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
    check: bool = True,
    input_text: str | None = None,
) -> ShellResult:
    """Run a command, capture stdout/stderr, raise ShellError on non-zero (when check)."""
    proc = subprocess.run(  # noqa: S603 — we control cmd construction at call sites
        cmd,
        cwd=cwd,
        env=env,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    result = ShellResult(cmd=cmd, returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
    if check and proc.returncode != 0:
        raise ShellError(result)
    return result


def run_json(cmd: list[str], **kwargs: Any) -> Any:
    """Run a command expected to print JSON on stdout; return parsed JSON."""
    result = run(cmd, **kwargs)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ShellError(result, message=f"expected JSON on stdout but got: {exc}") from exc

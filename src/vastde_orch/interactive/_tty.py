"""TTY guard + color detection.

Single point of policy: every interactive entry point must call `require_tty(ctx)`
first. Behavior:
  - If stdin is a TTY → return; interactive mode is allowed.
  - If stdin is NOT a TTY and `--non-interactive` was passed OR
    `VASTDE_NO_INTERACTIVE` env is set → return (caller will error on missing
    required values later, not here).
  - Otherwise → print actionable help to stderr and exit 2.

Color: `color_enabled(stream)` returns False if `NO_COLOR` env is set non-empty,
`TERM=dumb`, or the stream is not a TTY.
"""

from __future__ import annotations

import os
import sys
from typing import IO

import click

ENV_NO_INTERACTIVE = "VASTDE_NO_INTERACTIVE"


def is_non_interactive_env() -> bool:
    """True if the user has opted into non-interactive mode via env var."""
    return bool(os.environ.get(ENV_NO_INTERACTIVE, "").strip())


def require_tty(
    ctx: click.Context | None,
    *,
    command: str,
    ci_hint: str,
    non_interactive_flag: bool = False,
) -> None:
    """Exit 2 with a helpful message if interactivity is impossible.

    Args:
        ctx: click context (used for exit). May be None for tests.
        command: name of the command requiring interactivity (for the error text).
        ci_hint: a concrete suggested replacement command for CI.
        non_interactive_flag: True if --non-interactive was passed on the CLI.
    """
    if sys.stdin.isatty() and not non_interactive_flag and not is_non_interactive_env():
        return
    if non_interactive_flag or is_non_interactive_env():
        # User explicitly opted out of interactivity; callers handle missing fields.
        return
    msg = (
        f"Error: {command!r} requires an interactive terminal.\n"
        f"  - In CI:  {ci_hint}\n"
        f"  - Or set: {ENV_NO_INTERACTIVE}=1\n"
    )
    click.echo(msg, err=True)
    if ctx is not None:
        ctx.exit(2)
    sys.exit(2)


def color_enabled(stream: IO[str] | None = None) -> bool:
    """True only if writing color codes to `stream` (default stdout) is safe.

    Honors the NO_COLOR convention (https://no-color.org) and TERM=dumb.
    """
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR", "").strip():
        return False
    if os.environ.get("TERM", "") == "dumb":
        return False
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False

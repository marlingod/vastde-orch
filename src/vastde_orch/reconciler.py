"""Aggregation + pretty-printing of reconciliation outcomes.

The actual diff/apply logic for VMS resources lives in `clients/vms.py:ensure()`
(`EnsureOutcome` with a `DiffResult`). This module collects those outcomes
across many ensure_* calls and renders a Terraform-style plan summary.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import IO

from vastde_orch.clients.vms import DiffResult, EnsureOutcome


@dataclass
class Plan:
    """Accumulator for EnsureOutcomes across a reconciliation run."""

    outcomes: list[EnsureOutcome] = field(default_factory=list)

    def record(self, outcome: EnsureOutcome) -> EnsureOutcome:
        self.outcomes.append(outcome)
        return outcome

    def extend(self, other: Plan) -> None:
        self.outcomes.extend(other.outcomes)

    # ── filters & counts ────────────────────────────────────────────────

    def by_result(self, result: DiffResult) -> list[EnsureOutcome]:
        return [o for o in self.outcomes if o.result is result]

    def changed(self) -> list[EnsureOutcome]:
        """Outcomes representing an actual or planned mutation."""
        mutated = {
            DiffResult.CREATED, DiffResult.UPDATED, DiffResult.DELETED,
            DiffResult.WOULD_CREATE, DiffResult.WOULD_UPDATE, DiffResult.WOULD_DELETE,
        }
        return [o for o in self.outcomes if o.result in mutated]

    def has_failures(self) -> bool:
        # Failures are raised as exceptions; if we have outcomes, none failed.
        return False

    def summary(self) -> dict[DiffResult, int]:
        out: dict[DiffResult, int] = {}
        for o in self.outcomes:
            out[o.result] = out.get(o.result, 0) + 1
        return out

    # ── rendering ───────────────────────────────────────────────────────

    def render(self, *, stream: IO[str] | None = None, color: bool | None = None) -> None:
        """Print a Terraform-style summary. `color` defaults to TTY auto-detect."""
        stream = stream or sys.stdout
        use_color = stream.isatty() if color is None else color

        for line in self._lines(use_color):
            stream.write(line + "\n")

        counts = self.summary()
        total_changed = sum(
            counts.get(r, 0) for r in (
                DiffResult.CREATED, DiffResult.UPDATED, DiffResult.DELETED,
                DiffResult.WOULD_CREATE, DiffResult.WOULD_UPDATE, DiffResult.WOULD_DELETE,
            )
        )
        unchanged = counts.get(DiffResult.UNCHANGED, 0)
        stream.write(f"\nSummary: {total_changed} change(s), {unchanged} unchanged.\n")

    def _lines(self, color: bool) -> Iterator[str]:
        # ANSI codes; no-op when color=False.
        def c(code: str, text: str) -> str:
            return f"\x1b[{code}m{text}\x1b[0m" if color else text

        glyphs: dict[DiffResult, tuple[str, str]] = {
            DiffResult.CREATED:      ("+", "32"),  # green
            DiffResult.WOULD_CREATE: ("+", "32"),
            DiffResult.UPDATED:      ("~", "33"),  # yellow
            DiffResult.WOULD_UPDATE: ("~", "33"),
            DiffResult.DELETED:      ("-", "31"),  # red
            DiffResult.WOULD_DELETE: ("-", "31"),
            DiffResult.UNCHANGED:    ("=", "90"),  # grey
        }
        for o in self.outcomes:
            glyph, color_code = glyphs.get(o.result, ("?", "0"))
            verb = o.result.value
            label = f"{o.resource}/{o.name}"
            line = c(color_code, f"  {glyph} {label:<50} {verb}")
            if o.drift:
                drift_str = ", ".join(f"{k}={v!r}" for k, v in o.drift.items())
                line += f"  ({drift_str})"
            yield line

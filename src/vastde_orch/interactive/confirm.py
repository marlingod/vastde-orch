"""Per-resource-type confirmation for `--interactive` mode on enable/apply.

Bulk-by-type (not per-item) — see docs/research-interactive-ux.md §4.
Three options + sticky `continue`:
  yes      → apply changes for this resource type
  no       → abort the run; partial state preserved (idempotent on retry)
  details  → show per-resource diff, then re-prompt for the same type
  continue → "yes to all remaining types" — sets a session flag
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import IO

import click
import questionary

from vastde_orch.clients.vms import DiffResult, EnsureOutcome
from vastde_orch.reconciler import Plan


class ConfirmDecision(str, Enum):
    YES = "yes"
    NO = "no"
    CONTINUE = "continue"


@dataclass
class InteractiveSession:
    """State for one `--interactive` run.

    Holds the sticky `--continue` flag so once the user picks 'continue', all
    remaining prompts auto-yes for the rest of the run.
    """

    sticky_yes: bool = False
    yes_all: bool = False  # set by --yes-all flag; supersedes interactive
    # Test hook: when set, every prompt returns this without calling questionary.
    test_response_queue: list[str] = field(default_factory=list)

    def confirm_type(
        self,
        resource_type: str,
        outcomes: list[EnsureOutcome],
        *,
        stream: IO[str] | None = None,
    ) -> ConfirmDecision:
        if self.yes_all or self.sticky_yes:
            return ConfirmDecision.YES
        if not outcomes:
            return ConfirmDecision.YES

        # Print the per-type summary (counts grouped by DiffResult).
        counts = self._count_by_result(outcomes)
        summary = ", ".join(f"{n} to {r.value}" for r, n in counts.items() if n)
        names = ", ".join(o.name for o in outcomes[:5])
        ellipsis = "..." if len(outcomes) > 5 else ""

        click.echo(f"\n{resource_type}: {summary} ({names}{ellipsis})")

        while True:
            answer = self._ask_choice()
            if answer == "details":
                self._render_details(outcomes, stream=stream)
                continue
            if answer == "yes":
                return ConfirmDecision.YES
            if answer == "no":
                return ConfirmDecision.NO
            if answer == "continue":
                self.sticky_yes = True
                return ConfirmDecision.CONTINUE

    def _ask_choice(self) -> str:
        if self.test_response_queue:
            return self.test_response_queue.pop(0)
        result = questionary.select(
            "Apply these changes?",
            choices=["yes", "no", "details", "continue (yes to all remaining)"],
            default="yes",
        ).ask()
        if result is None:
            return "no"  # treat Ctrl-C as abort
        if result.startswith("continue"):
            return "continue"
        return result

    @staticmethod
    def _count_by_result(outcomes: list[EnsureOutcome]) -> dict[DiffResult, int]:
        counts: dict[DiffResult, int] = {}
        for o in outcomes:
            counts[o.result] = counts.get(o.result, 0) + 1
        return counts

    @staticmethod
    def _render_details(outcomes: list[EnsureOutcome], *, stream: IO[str] | None = None) -> None:
        plan = Plan(outcomes=list(outcomes))
        plan.render(stream=stream)

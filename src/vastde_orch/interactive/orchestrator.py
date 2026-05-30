"""Wraps the dry-run/real-run flow with per-type interactive confirmation.

The CLI layer calls `run_interactive(plan_fn, apply_fn, session)`:
  1. plan_fn() is invoked (dry_run=True) → returns Plan.
  2. Plan.outcomes are grouped by `resource`.
  3. For each resource type, session.confirm_type(...) prompts the user.
  4. If every type approved (or 'continue' was picked): apply_fn() runs for real.
  5. If any type is rejected: abort cleanly without applying any.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from vastde_orch.clients.vms import DiffResult, EnsureOutcome
from vastde_orch.interactive.confirm import ConfirmDecision, InteractiveSession
from vastde_orch.reconciler import Plan


@dataclass
class InteractiveResult:
    plan: Plan
    applied: bool
    rejected_types: list[str]


def _is_changed(outcome: EnsureOutcome) -> bool:
    return outcome.result in (
        DiffResult.WOULD_CREATE,
        DiffResult.WOULD_UPDATE,
        DiffResult.WOULD_DELETE,
        DiffResult.CREATED,
        DiffResult.UPDATED,
        DiffResult.DELETED,
    )


def group_by_type(plan: Plan) -> dict[str, list[EnsureOutcome]]:
    """Group outcomes by resource type, keeping only changed ones."""
    groups: dict[str, list[EnsureOutcome]] = {}
    for o in plan.outcomes:
        if not _is_changed(o):
            continue
        groups.setdefault(o.resource, []).append(o)
    return groups


def run_interactive(
    plan_fn: Callable[[], Plan],
    apply_fn: Callable[[], Plan],
    session: InteractiveSession,
) -> InteractiveResult:
    """Run dry-run, prompt per type, then real-run if approved.

    Returns InteractiveResult; the caller decides exit code based on .applied
    and .rejected_types.
    """
    plan = plan_fn()
    groups = group_by_type(plan)

    if not groups:
        # Nothing to change; render the plan (which will say "0 changes") and exit clean.
        plan.render()
        return InteractiveResult(plan=plan, applied=False, rejected_types=[])

    plan.render()  # Show the upfront summary.

    rejected: list[str] = []
    for resource_type in sorted(groups):
        decision = session.confirm_type(resource_type, groups[resource_type])
        if decision is ConfirmDecision.NO:
            rejected.append(resource_type)
            break  # Abort on first rejection — partial state is risky to reason about.

    if rejected:
        return InteractiveResult(plan=plan, applied=False, rejected_types=rejected)

    real_plan = apply_fn()
    return InteractiveResult(plan=real_plan, applied=True, rejected_types=[])

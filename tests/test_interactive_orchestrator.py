"""Tests for src/vastde_orch/interactive/orchestrator.py."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from vastde_orch.clients.vms import DiffResult, EnsureOutcome
from vastde_orch.interactive.confirm import InteractiveSession
from vastde_orch.interactive.orchestrator import group_by_type, run_interactive
from vastde_orch.reconciler import Plan


def _oc(result: DiffResult, resource: str, name: str = "x") -> EnsureOutcome:
    return EnsureOutcome(result=result, resource=resource, name=name, id=1, drift={})


class TestGroupByType:
    def test_groups_changed_outcomes(self) -> None:
        plan = Plan(outcomes=[
            _oc(DiffResult.WOULD_CREATE, "users", "alice"),
            _oc(DiffResult.WOULD_CREATE, "users", "bob"),
            _oc(DiffResult.WOULD_UPDATE, "views", "logs"),
            _oc(DiffResult.UNCHANGED, "views", "metrics"),  # filtered out
        ])
        groups = group_by_type(plan)
        assert set(groups) == {"users", "views"}
        assert len(groups["users"]) == 2
        assert len(groups["views"]) == 1

    def test_skips_unchanged(self) -> None:
        plan = Plan(outcomes=[_oc(DiffResult.UNCHANGED, "users", "x")])
        assert group_by_type(plan) == {}


class TestRunInteractive:
    def test_empty_plan_no_apply(self) -> None:
        plan_fn = MagicMock(return_value=Plan())
        apply_fn = MagicMock()
        session = InteractiveSession(yes_all=True)
        result = run_interactive(plan_fn, apply_fn, session)
        assert result.applied is False
        assert result.rejected_types == []
        apply_fn.assert_not_called()

    def test_yes_all_applies(self) -> None:
        plan_fn = MagicMock(return_value=Plan(outcomes=[
            _oc(DiffResult.WOULD_CREATE, "users", "alice"),
        ]))
        applied_plan = Plan(outcomes=[_oc(DiffResult.CREATED, "users", "alice")])
        apply_fn = MagicMock(return_value=applied_plan)
        session = InteractiveSession(yes_all=True)

        result = run_interactive(plan_fn, apply_fn, session)

        assert result.applied is True
        apply_fn.assert_called_once()

    def test_no_at_first_type_aborts(self) -> None:
        plan_fn = MagicMock(return_value=Plan(outcomes=[
            _oc(DiffResult.WOULD_CREATE, "users", "alice"),
            _oc(DiffResult.WOULD_CREATE, "views", "logs"),
        ]))
        apply_fn = MagicMock()
        session = InteractiveSession(test_response_queue=["no"])

        result = run_interactive(plan_fn, apply_fn, session)

        assert result.applied is False
        # Sorted alphabetically: users first, then views — but we stop at first 'no'.
        assert result.rejected_types == ["users"]
        apply_fn.assert_not_called()

    def test_yes_per_type_then_apply(self) -> None:
        plan_fn = MagicMock(return_value=Plan(outcomes=[
            _oc(DiffResult.WOULD_CREATE, "users", "alice"),
            _oc(DiffResult.WOULD_UPDATE, "views", "logs"),
        ]))
        apply_fn = MagicMock(return_value=Plan(outcomes=[
            _oc(DiffResult.CREATED, "users", "alice"),
            _oc(DiffResult.UPDATED, "views", "logs"),
        ]))
        session = InteractiveSession(test_response_queue=["yes", "yes"])

        result = run_interactive(plan_fn, apply_fn, session)

        assert result.applied is True
        assert result.rejected_types == []

    def test_continue_short_circuits_remaining_prompts(self) -> None:
        plan_fn = MagicMock(return_value=Plan(outcomes=[
            _oc(DiffResult.WOULD_CREATE, "users", "alice"),
            _oc(DiffResult.WOULD_CREATE, "views", "logs"),
            _oc(DiffResult.WOULD_CREATE, "policies", "p1"),
        ]))
        apply_fn = MagicMock(return_value=Plan())
        # Only 'continue' is queued; once consumed, sticky_yes auto-yeses the rest.
        session = InteractiveSession(test_response_queue=["continue"])

        result = run_interactive(plan_fn, apply_fn, session)

        assert result.applied is True
        assert session.sticky_yes is True

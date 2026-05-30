"""Tests for src/vastde_orch/interactive/confirm.py."""

from __future__ import annotations

import io

import pytest

from vastde_orch.clients.vms import DiffResult, EnsureOutcome
from vastde_orch.interactive.confirm import ConfirmDecision, InteractiveSession


def _oc(result: DiffResult, name: str = "x") -> EnsureOutcome:
    return EnsureOutcome(result=result, resource="users", name=name, id=1, drift={})


class TestInteractiveSession:
    def test_yes_all_short_circuits(self) -> None:
        session = InteractiveSession(yes_all=True)
        outcomes = [_oc(DiffResult.WOULD_CREATE, "alice")]
        assert session.confirm_type("users", outcomes) is ConfirmDecision.YES

    def test_empty_outcomes_returns_yes_noop(self) -> None:
        session = InteractiveSession()
        assert session.confirm_type("users", []) is ConfirmDecision.YES

    def test_yes(self) -> None:
        session = InteractiveSession(test_response_queue=["yes"])
        out = session.confirm_type("users", [_oc(DiffResult.WOULD_CREATE)])
        assert out is ConfirmDecision.YES

    def test_no(self) -> None:
        session = InteractiveSession(test_response_queue=["no"])
        out = session.confirm_type("users", [_oc(DiffResult.WOULD_CREATE)])
        assert out is ConfirmDecision.NO

    def test_continue_sets_sticky_yes(self) -> None:
        session = InteractiveSession(test_response_queue=["continue"])
        out = session.confirm_type("users", [_oc(DiffResult.WOULD_CREATE)])
        assert out is ConfirmDecision.CONTINUE
        assert session.sticky_yes is True
        # Subsequent calls auto-yes without consuming the queue.
        out2 = session.confirm_type("views", [_oc(DiffResult.WOULD_UPDATE)])
        assert out2 is ConfirmDecision.YES

    def test_details_then_yes(self) -> None:
        # First ask returns 'details' (which re-prompts), second returns 'yes'.
        session = InteractiveSession(test_response_queue=["details", "yes"])
        stream = io.StringIO()
        out = session.confirm_type("users", [_oc(DiffResult.WOULD_UPDATE, "alice")], stream=stream)
        assert out is ConfirmDecision.YES
        # The details render should have written something to the stream.
        assert "users/alice" in stream.getvalue() or "alice" in stream.getvalue()

    def test_details_can_repeat_then_no(self) -> None:
        session = InteractiveSession(test_response_queue=["details", "details", "no"])
        stream = io.StringIO()
        out = session.confirm_type("users", [_oc(DiffResult.WOULD_CREATE)], stream=stream)
        assert out is ConfirmDecision.NO

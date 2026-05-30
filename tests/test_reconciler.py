"""Tests for src/vastde_orch/reconciler.py — pure logic, no I/O mocks needed."""

from __future__ import annotations

import io

import pytest

from vastde_orch.clients.vms import DiffResult, EnsureOutcome
from vastde_orch.reconciler import Plan


def _o(result: DiffResult, name: str, drift: dict | None = None) -> EnsureOutcome:
    return EnsureOutcome(
        result=result, resource="views", name=name, id=1, drift=drift or {}
    )


class TestPlan:
    def test_record_and_count(self) -> None:
        p = Plan()
        p.record(_o(DiffResult.CREATED, "a"))
        p.record(_o(DiffResult.UNCHANGED, "b"))
        p.record(_o(DiffResult.UPDATED, "c", {"x": 1}))
        s = p.summary()
        assert s[DiffResult.CREATED] == 1
        assert s[DiffResult.UNCHANGED] == 1
        assert s[DiffResult.UPDATED] == 1

    def test_changed_filter_excludes_unchanged(self) -> None:
        p = Plan()
        p.record(_o(DiffResult.CREATED, "a"))
        p.record(_o(DiffResult.UNCHANGED, "b"))
        p.record(_o(DiffResult.WOULD_UPDATE, "c", {"y": 2}))
        names = [o.name for o in p.changed()]
        assert names == ["a", "c"]

    def test_extend_merges_two_plans(self) -> None:
        p1 = Plan()
        p1.record(_o(DiffResult.CREATED, "a"))
        p2 = Plan()
        p2.record(_o(DiffResult.UPDATED, "b"))
        p1.extend(p2)
        assert len(p1.outcomes) == 2

    def test_by_result(self) -> None:
        p = Plan()
        p.record(_o(DiffResult.CREATED, "a"))
        p.record(_o(DiffResult.CREATED, "b"))
        p.record(_o(DiffResult.UNCHANGED, "c"))
        assert {o.name for o in p.by_result(DiffResult.CREATED)} == {"a", "b"}


class TestRender:
    def test_renders_count_summary(self) -> None:
        p = Plan()
        p.record(_o(DiffResult.WOULD_CREATE, "a"))
        p.record(_o(DiffResult.WOULD_UPDATE, "b", {"x": 1}))
        p.record(_o(DiffResult.UNCHANGED, "c"))

        buf = io.StringIO()
        p.render(stream=buf, color=False)
        out = buf.getvalue()
        assert "+ views/a" in out
        assert "~ views/b" in out
        assert "x=1" in out
        assert "= views/c" in out
        assert "2 change(s), 1 unchanged" in out

    def test_empty_plan_renders_zero(self) -> None:
        buf = io.StringIO()
        Plan().render(stream=buf, color=False)
        assert "0 change(s), 0 unchanged" in buf.getvalue()

    def test_color_emits_ansi_when_enabled(self) -> None:
        p = Plan()
        p.record(_o(DiffResult.CREATED, "a"))
        buf = io.StringIO()
        p.render(stream=buf, color=True)
        assert "\x1b[32m" in buf.getvalue()  # green
        assert "\x1b[0m" in buf.getvalue()   # reset

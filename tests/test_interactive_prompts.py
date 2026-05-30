"""Tests for src/vastde_orch/interactive/_prompts.py and _vms_probe.py.

We rely on the inject-answers pattern: a Prompter constructed with answers={...}
never invokes questionary, so these tests are fast and deterministic.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from vastde_orch.interactive._prompts import Prompter, PromptCancelled, _walk
from vastde_orch.interactive._vms_probe import VmsProbe


class TestWalk:
    def test_simple_key(self) -> None:
        assert _walk({"a": 1}, "a") == 1

    def test_nested_dot(self) -> None:
        assert _walk({"a": {"b": {"c": 7}}}, "a.b.c") == 7

    def test_list_index(self) -> None:
        assert _walk({"xs": [{"n": "alice"}, {"n": "bob"}]}, "xs.1.n") == "bob"

    def test_missing_raises(self) -> None:
        with pytest.raises(KeyError):
            _walk({"a": 1}, "b")


class TestPrompterScripted:
    def test_text_returns_answer(self) -> None:
        p = Prompter(answers={"vms": {"address": "vms.test"}})
        assert p.text("vms.address", "?") == "vms.test"

    def test_text_falls_back_to_default(self) -> None:
        p = Prompter(answers={})
        assert p.text("missing.key", "?", default="d") == "d"

    def test_missing_with_no_default_raises(self) -> None:
        p = Prompter(answers={})
        with pytest.raises(KeyError, match="missing key"):
            p.text("nope", "?")

    def test_confirm(self) -> None:
        p = Prompter(answers={"do_it": True})
        assert p.confirm("do_it", "?") is True

    def test_choice_validates_against_allowed(self) -> None:
        p = Prompter(answers={"mode": "fast"})
        assert p.choice("mode", "?", choices=["fast", "slow"]) == "fast"
        with pytest.raises(ValueError, match="not one of"):
            p.choice("mode", "?", choices=["a", "b"])

    def test_integer_parses(self) -> None:
        p = Prompter(answers={"uid": 10001})
        assert p.integer("uid", "?") == 10001

    def test_password_returns_value(self) -> None:
        p = Prompter(answers={"token": "secret"})
        assert p.password("token", "?") == "secret"


class TestPrompterLoop:
    def test_loop_yields_one_item_per_scripted_element(self) -> None:
        p = Prompter(answers={"users": [
            {"name": "alice", "uid": 10001},
            {"name": "bob", "uid": 10002},
        ]})

        def build_user(i: int, sub: Prompter) -> dict:
            return {
                "name": sub.text("name", "Name:"),
                "uid": sub.integer("uid", "UID:"),
            }

        items = p.loop("users", build_user)
        assert items == [
            {"name": "alice", "uid": 10001},
            {"name": "bob", "uid": 10002},
        ]

    def test_loop_empty_list_yields_no_items(self) -> None:
        p = Prompter(answers={"users": []})
        result = p.loop("users", lambda i, sub: {"x": 1})
        assert result == []

    def test_loop_non_list_value_raises(self) -> None:
        p = Prompter(answers={"users": "not a list"})
        with pytest.raises(TypeError, match="must be a list"):
            p.loop("users", lambda i, sub: {})


class TestVmsProbe:
    def test_unavailable_returns_empty(self) -> None:
        probe = VmsProbe(vms=None)
        assert probe.tenants() == []
        assert probe.users() == []

    def test_lists_tenants(self) -> None:
        vms = MagicMock()
        vms.raw.tenants.get.return_value = [
            {"name": "data-platform"}, {"name": "default"}
        ]
        probe = VmsProbe(vms=vms)
        assert probe.tenants() == ["data-platform", "default"]

    def test_cached_across_calls(self) -> None:
        vms = MagicMock()
        vms.raw.tenants.get.return_value = [{"name": "t1"}]
        probe = VmsProbe(vms=vms)
        probe.tenants()
        probe.tenants()
        vms.raw.tenants.get.assert_called_once()

    def test_vms_error_falls_back_gracefully(self, capsys: pytest.CaptureFixture) -> None:
        vms = MagicMock()
        vms.raw.tenants.get.side_effect = ConnectionError("refused")
        probe = VmsProbe(vms=vms)
        assert probe.tenants() == []
        assert "could not list" in capsys.readouterr().err

    def test_views_uses_path_field(self) -> None:
        vms = MagicMock()
        vms.raw.views.get.return_value = [{"path": "/raw/docs"}, {"path": "/raw/logs"}]
        probe = VmsProbe(vms=vms)
        assert probe.views() == ["/raw/docs", "/raw/logs"]

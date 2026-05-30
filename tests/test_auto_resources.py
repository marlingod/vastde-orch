"""Tests for src/vastde_orch/enablement/auto_resources.py."""

from __future__ import annotations

import pytest

from vastde_orch.enablement.auto_resources import (
    filter_user_views,
    filter_user_viewpolicies,
    is_auto_view,
    is_auto_viewpolicy,
)


class TestIsAutoView:
    @pytest.mark.parametrize("path", [
        "/dataengine",
        "/dataengine-telemetries-f1d5d9d3-3523-4654-bfd5-4d5170d0f13c",
        "/dataengine-telemetries-abcdef01-2345-6789-abcd-ef0123456789",
    ])
    def test_matches_auto_paths(self, path: str) -> None:
        assert is_auto_view(path) is True

    @pytest.mark.parametrize("path", [
        "/raw/docs",
        "/wi-de-broker",
        "/dataengineish",                 # not the exact prefix
        "/dataengine-telemetries",        # missing UUID suffix
        "/dataengine-other",              # different suffix
        "/dataengine/sub",                # different structure
        "",
    ])
    def test_does_not_match_user_paths(self, path: str) -> None:
        assert is_auto_view(path) is False


class TestIsAutoViewpolicy:
    @pytest.mark.parametrize("name", [
        "dataengine-policy",
        "vast-data-engine-telemetries-policy",
        "wi-tenant__default_policy",
        "wi-tenant__s3_default_policy",
        "demo-tenant__default_policy",
    ])
    def test_matches_auto(self, name: str) -> None:
        assert is_auto_viewpolicy(name) is True

    @pytest.mark.parametrize("name", [
        "wi-s3-policy",
        "dataengine-default",           # operator-named, not auto
        "my-policy",
        "default_policy",                # missing tenant prefix
    ])
    def test_does_not_match_operator_policies(self, name: str) -> None:
        assert is_auto_viewpolicy(name) is False


class TestFilters:
    def test_filter_user_views_drops_auto(self) -> None:
        views = [
            {"path": "/raw/docs"},
            {"path": "/dataengine"},
            {"path": "/dataengine-telemetries-f1d5d9d3-3523-4654-bfd5-4d5170d0f13c"},
            {"path": "/wi-de-broker"},
        ]
        kept = filter_user_views(views)
        assert [v["path"] for v in kept] == ["/raw/docs", "/wi-de-broker"]

    def test_filter_user_viewpolicies_drops_auto(self) -> None:
        pols = [
            {"name": "wi-s3-policy"},
            {"name": "wi-tenant__default_policy"},
            {"name": "dataengine-policy"},
            {"name": "vast-data-engine-telemetries-policy"},
            {"name": "demo-de-policy"},
        ]
        kept = filter_user_viewpolicies(pols)
        assert sorted(p["name"] for p in kept) == ["demo-de-policy", "wi-s3-policy"]

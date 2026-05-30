"""Tests for src/vastde_orch/clients/_shell.py, vastde_cli.py, kube.py, docker.py.

We mock `subprocess.run` so no real binaries are invoked.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from vastde_orch.clients import docker, kube
from vastde_orch.clients._shell import ShellError, run, run_json, which_or_raise
from vastde_orch.clients.vastde_cli import VastdeCli, VastdeContext


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    m = MagicMock(spec=subprocess.CompletedProcess)
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


@pytest.fixture
def mock_subprocess(mocker: MockerFixture) -> MagicMock:
    return mocker.patch("vastde_orch.clients._shell.subprocess.run")


@pytest.fixture
def mock_which(mocker: MockerFixture) -> MagicMock:
    return mocker.patch("vastde_orch.clients._shell.shutil.which", return_value="/usr/bin/fake")


# ── _shell.py ───────────────────────────────────────────────────────────────

class TestShell:
    def test_run_success(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value = _completed(0, "hello\n", "")
        result = run(["echo", "hello"])
        assert result.returncode == 0
        assert result.stdout == "hello\n"

    def test_run_raises_on_failure(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value = _completed(1, "", "boom")
        with pytest.raises(ShellError, match="exit 1"):
            run(["false"])

    def test_run_no_check_returns_failure(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value = _completed(2, "", "x")
        result = run(["false"], check=False)
        assert result.returncode == 2

    def test_run_json_parses(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value = _completed(0, json.dumps([{"a": 1}]), "")
        assert run_json(["fake"]) == [{"a": 1}]

    def test_run_json_raises_on_invalid(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value = _completed(0, "not json", "")
        with pytest.raises(ShellError, match="JSON"):
            run_json(["fake"])

    def test_which_or_raise_found(self, mock_which: MagicMock) -> None:
        assert which_or_raise("anything") == "/usr/bin/fake"

    def test_which_or_raise_missing(self, mocker: MockerFixture) -> None:
        mocker.patch("vastde_orch.clients._shell.shutil.which", return_value=None)
        with pytest.raises(ShellError, match="not found on PATH"):
            which_or_raise("nonexistent")


# ── vastde_cli.py ───────────────────────────────────────────────────────────

@pytest.fixture
def vastde_ctx() -> VastdeContext:
    return VastdeContext(
        vms_url="https://vms.test",
        tenant="default",
        username="u",
        password="p",
        builder_image_url="img/builder:v1",
    )


class TestVastdeCli:
    def test_configure_runs_init(
        self, mock_subprocess: MagicMock, mock_which: MagicMock, vastde_ctx: VastdeContext
    ) -> None:
        mock_subprocess.return_value = _completed()
        cli = VastdeCli(vastde_ctx)
        cli.configure()
        assert mock_subprocess.call_count == 1
        args = mock_subprocess.call_args.args[0]
        assert args[:3] == ["vastde", "config", "init"]
        assert "--vms-url" in args
        assert "https://vms.test" in args

    def test_configure_runs_only_once(
        self, mock_subprocess: MagicMock, mock_which: MagicMock, vastde_ctx: VastdeContext
    ) -> None:
        mock_subprocess.return_value = _completed()
        cli = VastdeCli(vastde_ctx)
        cli.configure()
        cli.configure()
        assert mock_subprocess.call_count == 1

    def test_triggers_get_returns_match(
        self, mock_subprocess: MagicMock, mock_which: MagicMock, vastde_ctx: VastdeContext
    ) -> None:
        # First call is configure(), then triggers list.
        mock_subprocess.side_effect = [
            _completed(),  # configure
            _completed(0, json.dumps([{"name": "t1", "id": 1}, {"name": "t2", "id": 2}]), ""),
        ]
        cli = VastdeCli(vastde_ctx)
        assert cli.triggers_get("t2") == {"name": "t2", "id": 2}

    def test_dry_run_skips_mutation(
        self, mock_subprocess: MagicMock, mock_which: MagicMock, vastde_ctx: VastdeContext
    ) -> None:
        mock_subprocess.return_value = _completed()
        cli = VastdeCli(vastde_ctx, dry_run=True)
        result = cli.triggers_create("t", {"foo": "bar"})
        assert result == {"name": "t", "dry_run": True}
        # No subprocess call for create
        assert mock_subprocess.call_count == 0


# ── kube.py ─────────────────────────────────────────────────────────────────

class TestKube:
    def test_sysctl_get_parses_int(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value = _completed(0, "8192\n", "")
        assert kube.sysctl_get("fs.inotify.max_user_instances") == 8192

    def test_ensure_inotify_no_change_when_already_above(
        self, mock_subprocess: MagicMock
    ) -> None:
        # Both sysctl_get calls return huge values; no sets occur.
        mock_subprocess.side_effect = [
            _completed(0, "99999\n", ""),  # instances
            _completed(0, "999999\n", ""),  # watches
        ]
        changes = kube.ensure_inotify_limits(8192, 524288)
        assert changes == {}
        assert mock_subprocess.call_count == 2

    def test_ensure_inotify_sets_when_below(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.side_effect = [
            _completed(0, "100\n", ""),  # get instances → low
            _completed(),  # set instances
            _completed(0, "200\n", ""),  # get watches → low
            _completed(),  # set watches
        ]
        changes = kube.ensure_inotify_limits(8192, 524288)
        assert "fs.inotify.max_user_instances" in changes
        assert changes["fs.inotify.max_user_instances"] == (100, 8192)

    def test_namespace_exists_true(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value = _completed(0, "namespace/vast-dataengine\n", "")
        assert kube.kubectl_namespace_exists("vast-dataengine") is True

    def test_namespace_exists_false(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value = _completed(0, "", "")
        assert kube.kubectl_namespace_exists("nope") is False

    def test_ensure_namespaces_creates_and_labels(
        self, mock_subprocess: MagicMock, mock_which: MagicMock
    ) -> None:
        # Per namespace: 1) exists-check returns empty, 2) create, 3) label
        # Three namespaces × 3 calls = 9 calls
        mock_subprocess.side_effect = [_completed(0, "", "")] * 9
        created = kube.ensure_vast_namespaces()
        assert set(created) == {"vast-dataengine", "knative-eventing", "knative-serving"}
        assert mock_subprocess.call_count == 9

    def test_zarf_init_passes_args(self, mock_subprocess: MagicMock, mock_which: MagicMock) -> None:
        mock_subprocess.return_value = _completed()
        kube.zarf_init(Path("/pkg/init.tar.zst"), storage_class="local-path")
        args = mock_subprocess.call_args.args[0]
        assert args[0] == "zarf"
        assert "init" in args
        assert "--storage-class" in args
        assert "local-path" in args


# ── docker.py ───────────────────────────────────────────────────────────────

class TestDocker:
    def test_login_passes_password_via_stdin(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value = _completed()
        docker.docker_login("registry.example.com", username="u", password="secret")
        kwargs = mock_subprocess.call_args.kwargs
        assert kwargs["input"] == "secret"
        args = mock_subprocess.call_args.args[0]
        assert "--password-stdin" in args

    def test_manifest_exists_true(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value = _completed(0, "{}", "")
        assert docker.docker_manifest_exists("r/x:1") is True

    def test_manifest_exists_false_on_unknown(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value = _completed(1, "", "manifest unknown for r/x:1")
        assert docker.docker_manifest_exists("r/x:1") is False

    def test_manifest_other_error_reraises(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.return_value = _completed(1, "", "auth required")
        with pytest.raises(ShellError):
            docker.docker_manifest_exists("r/x:1")

"""Tests for the CLI's schema-aware dispatch (load_any_config + _require_full)."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from vastde_orch.cli import main


MINIMAL_YAML = """
vms:
  address: vms.example.com
  tenant_name: my-tenant
  auth:
    user_env: TENANT_ADMIN_USER
    password_env: TENANT_ADMIN_PASSWORD
vip_pool_name: my-de-vips
k8s:
  kube_api_url: https://10.143.2.242:6443
  mtls:
    ca_cert_file: ./certs/ca.pem
    client_cert_file: ./certs/client.pem
    client_key_file: ./certs/client.key
registry:
  url: docker.io
"""

FULL_YAML = """
vms:
  address: vms.example.com
  token: t0k
  tenant: default
enablement:
  tenant: { name: default }
  event_broker:
    kind: vast
    view_path: /sys/de
    bucket_name: de
    bucket_owner: owner
    vip_pool:
      name: pool
      cidr: '10.0.0.0/24'
      ip_range: [10.0.0.1, 10.0.0.10]
    default_topic: { name: t-default }
    deadletter_topic: { name: t-dlq }
  container_registry:
    name: r
    base_url: r.example.com
    auth: { method: none }
  kubernetes:
    name: k8s
    api_server: https://k8s:6443
  identity:
    group: { name: g, gid: 100 }
"""


@pytest.fixture
def minimal_cfg(tmp_path: Path) -> Path:
    p = tmp_path / "vastde.yaml"
    p.write_text(MINIMAL_YAML)
    return p


@pytest.fixture
def full_cfg(tmp_path: Path) -> Path:
    p = tmp_path / "vastde.yaml"
    p.write_text(FULL_YAML)
    return p


class TestValidateDispatch:
    def test_validate_minimal_schema(self, minimal_cfg: Path) -> None:
        result = CliRunner().invoke(main, ["validate", "-c", str(minimal_cfg)])
        assert result.exit_code == 0, result.output
        assert "schema: minimal" in result.output
        assert "tenant: my-tenant" in result.output
        assert "vip pool: my-de-vips" in result.output
        assert "k8s cluster: 10-143-2-242" in result.output
        assert "registry: docker" in result.output
        assert "broker view: /de-broker" in result.output
        assert "pipelines: 0" in result.output

    def test_validate_full_schema(self, full_cfg: Path) -> None:
        result = CliRunner().invoke(main, ["validate", "-c", str(full_cfg)])
        assert result.exit_code == 0, result.output
        assert "schema: full" in result.output
        assert "tenant: default" in result.output
        assert "enablement: present" in result.output
        assert "pipelines: 0" in result.output


class TestMinimalSchemaGatedCommands:
    """Commands not yet wired for minimal should exit 2 with a helpful message."""

    @pytest.mark.parametrize("cmd_args", [
        ["enable", "--plan"],
        ["apply", "--plan"],
        ["status"],
        ["destroy", "--yes"],
    ])
    def test_gated(self, minimal_cfg: Path, cmd_args: list[str]) -> None:
        args = [cmd_args[0], "-c", str(minimal_cfg), *cmd_args[1:]]
        result = CliRunner().invoke(main, args)
        assert result.exit_code == 2, result.output
        assert "does not yet support the minimal schema" in result.output
        assert "vastde-orch wizard" in result.output

    def test_function_build_gated(self, minimal_cfg: Path) -> None:
        result = CliRunner().invoke(main, [
            "function", "build", "some-fn", "-c", str(minimal_cfg),
        ])
        assert result.exit_code == 2, result.output
        assert "does not yet support the minimal schema" in result.output

    def test_function_tag_gated(self, minimal_cfg: Path) -> None:
        result = CliRunner().invoke(main, [
            "function", "tag", "some-fn", "-c", str(minimal_cfg),
        ])
        assert result.exit_code == 2, result.output
        assert "does not yet support the minimal schema" in result.output


class TestLoadFailure:
    def test_missing_file_exits_2(self, tmp_path: Path) -> None:
        # Click's exists=True check fires first (exit 2 from Click usage error).
        result = CliRunner().invoke(main, ["validate", "-c", str(tmp_path / "nope.yaml")])
        assert result.exit_code == 2
        assert "does not exist" in result.output.lower() or "no such" in result.output.lower()

    def test_malformed_yaml_exits_2(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.yaml"
        p.write_text("vms: {bad: [unterminated\n")
        result = CliRunner().invoke(main, ["validate", "-c", str(p)])
        assert result.exit_code == 2
        assert "config error" in result.output.lower()

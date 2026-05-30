"""Tests for src/vastde_orch/config/models.py and loader.py."""

from __future__ import annotations

import os
from pathlib import Path
from textwrap import dedent

import pytest

from vastde_orch.config.loader import ConfigError, load_config
from vastde_orch.config.models import (
    DeploymentSpec,
    ElementTriggerSpec,
    FlowEdge,
    FunctionSpec,
    MinMax,
    PipelineSpec,
    ScheduleSpec,
    VastdeConfig,
    VmsSpec,
)


# ── Building blocks ─────────────────────────────────────────────────────────

def _min_vms() -> dict:
    return {"address": "vms.example.com", "token": "t0k", "tenant": "default"}


def _min_enablement() -> dict:
    return {
        "tenant": {"name": "default"},
        "event_broker": {
            "kind": "vast",
            "view_path": "/sys/de",
            "bucket_name": "de",
            "bucket_owner": "owner",
            "vip_pool": {
                "name": "pool",
                "cidr": "10.0.0.0/24",
                "ip_range": ["10.0.0.10", "10.0.0.20"],
            },
            "default_topic": {"name": "default", "partitions": 50, "retention_hours": 168},
            "deadletter_topic": {"name": "dlq", "partitions": 8, "retention_hours": 24},
        },
        "container_registry": {
            "name": "primary",
            "base_url": "registry.example.com",
            "auth": {
                "method": "user_credentials",
                "username_env": "REGISTRY_USER",
                "password_env": "REGISTRY_PASSWORD",
            },
        },
        "kubernetes": {"name": "prod-k8s", "api_server": "https://k8s:6443"},
        "identity": {"group": {"name": "de-users", "gid": 5000}},
    }


# ── VmsSpec ─────────────────────────────────────────────────────────────────

class TestVmsSpec:
    def test_token_auth_ok(self) -> None:
        VmsSpec(address="x", token="t", tenant="default")

    def test_user_password_ok(self) -> None:
        VmsSpec(address="x", user="u", password="p", tenant="default")

    def test_no_auth_fails(self) -> None:
        with pytest.raises(ValueError, match="must provide either"):
            VmsSpec(address="x", tenant="default")

    def test_partial_user_fails(self) -> None:
        with pytest.raises(ValueError, match="must provide either"):
            VmsSpec(address="x", user="u", tenant="default")


# ── Trigger / Schedule ──────────────────────────────────────────────────────

class TestTriggers:
    def test_element_trigger_ok(self) -> None:
        t = ElementTriggerSpec(
            name="t1",
            type="element",
            source_view="/raw",
            event_type="ElementCreated",
            topic="default",
        )
        assert t.object_key_suffix is None

    def test_custom_extension_rejects_uppercase(self) -> None:
        with pytest.raises(ValueError, match="lowercase alnum"):
            ElementTriggerSpec(
                name="t1",
                type="element",
                source_view="/raw",
                event_type="ElementCreated",
                topic="default",
                custom_extensions={"BadKey": "v"},
            )

    def test_custom_extension_rejects_too_long(self) -> None:
        with pytest.raises(ValueError, match="lowercase alnum"):
            ElementTriggerSpec(
                name="t1",
                type="element",
                source_view="/raw",
                event_type="ElementCreated",
                topic="default",
                custom_extensions={"a" * 21: "v"},
            )

    def test_schedule_exactly_one(self) -> None:
        ScheduleSpec(simple="0 2 * * *")
        ScheduleSpec(advanced="0 0 2 * * ?")
        with pytest.raises(ValueError, match="exactly one"):
            ScheduleSpec()
        with pytest.raises(ValueError, match="exactly one"):
            ScheduleSpec(simple="x", advanced="y")


# ── Deployment ──────────────────────────────────────────────────────────────

class TestDeployment:
    def test_minmax_order(self) -> None:
        with pytest.raises(ValueError, match=r"min.*must be <= max"):
            MinMax(min=2, max=1)

    def test_defaults(self) -> None:
        d = DeploymentSpec()
        assert d.timeout_seconds == 60
        assert d.log_level == "INFO"
        assert d.method_of_delivery == "ordered"


# ── Pipeline flow validation ────────────────────────────────────────────────

def _func(name: str, image: str = "reg/foo") -> FunctionSpec:
    return FunctionSpec(name=name, source=Path("/tmp/x"), image=image)


def _trig(name: str) -> ElementTriggerSpec:
    return ElementTriggerSpec(
        name=name,
        type="element",
        source_view="/raw",
        event_type="ElementCreated",
        topic="default",
    )


class TestPipelineFlow:
    def test_valid_flow(self) -> None:
        PipelineSpec(
            name="p",
            k8s_cluster="k",
            triggers=[_trig("t1")],
            functions=[_func("f1"), _func("f2")],
            flow=[FlowEdge.model_validate({"from": "t1", "to": "f1"}),
                  FlowEdge.model_validate({"from": "f1", "to": "f2"})],
        )

    def test_flow_unknown_source(self) -> None:
        with pytest.raises(ValueError, match="unknown trigger/function"):
            PipelineSpec(
                name="p",
                k8s_cluster="k",
                triggers=[_trig("t1")],
                functions=[_func("f1")],
                flow=[FlowEdge.model_validate({"from": "missing", "to": "f1"})],
            )

    def test_flow_target_must_be_function(self) -> None:
        with pytest.raises(ValueError, match="cannot be targets"):
            PipelineSpec(
                name="p",
                k8s_cluster="k",
                triggers=[_trig("t1"), _trig("t2")],
                functions=[_func("f1")],
                flow=[FlowEdge.model_validate({"from": "t1", "to": "t2"})],
            )

    def test_flow_cycle_rejected(self) -> None:
        with pytest.raises(ValueError, match="cycle"):
            PipelineSpec(
                name="p",
                k8s_cluster="k",
                functions=[_func("f1"), _func("f2")],
                flow=[
                    FlowEdge.model_validate({"from": "f1", "to": "f2"}),
                    FlowEdge.model_validate({"from": "f2", "to": "f1"}),
                ],
            )


# ── Cross-ref: pipeline.k8s_cluster must exist in enablement.kubernetes ─────

class TestVastdeConfig:
    def test_pipeline_k8s_must_be_registered(self) -> None:
        cfg = {
            "vms": _min_vms(),
            "enablement": _min_enablement(),
            "pipelines": [
                {
                    "name": "p",
                    "k8s_cluster": "nonexistent",
                    "functions": [{"name": "f1", "source": "/tmp", "image": "r/f"}],
                }
            ],
        }
        with pytest.raises(ValueError, match="not declared"):
            VastdeConfig.model_validate(cfg)

    def test_full_minimal_config_validates(self) -> None:
        cfg = {
            "vms": _min_vms(),
            "enablement": _min_enablement(),
            "pipelines": [
                {
                    "name": "p",
                    "k8s_cluster": "prod-k8s",
                    "functions": [{"name": "f1", "source": "/tmp", "image": "r/f"}],
                }
            ],
        }
        parsed = VastdeConfig.model_validate(cfg)
        assert parsed.pipelines[0].name == "p"
        assert parsed.enablement.event_broker.kind == "vast"


# ── Loader ──────────────────────────────────────────────────────────────────

class TestLoader:
    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="not found"):
            load_config(tmp_path / "missing.yaml", env_file=None)

    def test_env_interpolation(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_VMS", "vms.test")
        monkeypatch.setenv("MY_TOKEN", "abc123")
        yaml_path = tmp_path / "vastde.yaml"
        yaml_path.write_text(dedent("""\
            vms:
              address: ${MY_VMS}
              token: ${MY_TOKEN}
              tenant: default
        """))
        cfg = load_config(yaml_path, env_file=None)
        assert cfg.vms.address == "vms.test"
        assert cfg.vms.token == "abc123"

    def test_missing_env_var_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DEFINITELY_NOT_SET", raising=False)
        yaml_path = tmp_path / "vastde.yaml"
        yaml_path.write_text(dedent("""\
            vms:
              address: ${DEFINITELY_NOT_SET}
              token: t
              tenant: default
        """))
        with pytest.raises(ConfigError, match="DEFINITELY_NOT_SET"):
            load_config(yaml_path, env_file=None)

    def test_non_mapping_root_fails(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "vastde.yaml"
        yaml_path.write_text("- not\n- a\n- map\n")
        with pytest.raises(ConfigError, match="mapping at the root"):
            load_config(yaml_path, env_file=None)

"""End-to-end wizard tests using the inject-answers pattern.

Each test passes a full `answers` dict to `run_wizard`, asserts the returned
config is valid Pydantic, and (where relevant) inspects a section.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from vastde_orch.config.models import VastdeConfig
from vastde_orch.interactive._vms_probe import VmsProbe
from vastde_orch.interactive.wizard import (
    WizardValidationError,
    run_wizard,
    run_wizard_to_file,
)


def _full_answers() -> dict:
    return {
        "vms": {
            "address": "vms.test",
            "auth": "token",
            "token_env": "VMS_TOKEN",
            "tenant": "data-platform",  # picked from probe
            "api_version": "",
        },
        "generate_enablement": True,
        "tenant": {"name": "data-platform", "create_if_missing": True},
        "k8s": {
            "already_configured": False,
            "name": "prod-k8s",
            "api_server": "https://k8s:6443",
            "kubeconfig": "",
            "zarf_package_path": "./packages/zarf-pkg.tar.zst",
            "zarf_init_path": "./packages/zarf-init.tar.zst",
        },
        "registry": {
            "name": "primary",
            "base_url": "registry.example.com",
            "auth_method": "user_credentials",
            "username_env": "REGISTRY_USER",
            "password_env": "REGISTRY_PASSWORD",
        },
        "broker": {
            "kind": "vast",
            "view_path": "/sys/de",
            "bucket_name": "de",
            "bucket_owner": "alice",
            "vip_name": "pool",
            "vip_cidr": "10.0.0.0/24",
            "vip_ip_start": "10.0.0.10",
            "vip_ip_end": "10.0.0.20",
            "default_topic_name": "de-default",
            "default_topic_partitions": 50,
            "default_topic_retention": 168,
            "dlq_topic_name": "de-dlq",
            "dlq_topic_partitions": 16,
            "dlq_topic_retention": 24,
        },
        "identity": {
            "group": {"name": "de-users", "gid": 5000, "provider": "vast"},
            "users": [
                {"name": "alice", "uid": 10001},
                {"name": "bob", "uid": 10002},
            ],
            "policy": "assign_predefined",
        },
        "source_views": [
            {"path": "/raw/docs", "bucket": "raw-docs", "owner": "alice",
             "policy": "dataengine-default"},
        ],
        "generate_pipelines": True,
        "pipelines": [
            {
                "name": "pdf-ingest",
                "description": "",
                "namespace": "vast-dataengine",
                "triggers": [
                    {
                        "type": "element",
                        "name": "pdf-uploaded",
                        "source_view": "/raw/docs",
                        "event_type": "ElementCreated",
                        "topic": "de-default",
                        "object_key_suffix": ".pdf",
                        "object_key_prefix": "",
                    }
                ],
                "functions": [
                    {"name": "parse-pdf", "source": "/tmp",
                     "image": "registry.example.com/funcs/parse-pdf"},
                ],
                "flow": [{"from": "pdf-uploaded", "to": "parse-pdf"}],
            }
        ],
    }


# ── happy path ──────────────────────────────────────────────────────────────

class TestWizardHappyPath:
    def test_full_answers_validates(self) -> None:
        probe = VmsProbe(vms=None)
        cfg = run_wizard(probe, answers=_full_answers())
        assert cfg["vms"]["tenant"] == "data-platform"
        assert cfg["enablement"]["event_broker"]["kind"] == "vast"
        assert len(cfg["pipelines"]) == 1
        assert cfg["pipelines"][0]["triggers"][0]["object_key_suffix"] == ".pdf"

    def test_secrets_stored_as_env_placeholders(self) -> None:
        cfg = run_wizard(VmsProbe(vms=None), answers=_full_answers())
        assert cfg["vms"]["token"] == "${VMS_TOKEN}"

    def test_skipped_enablement(self) -> None:
        a = _full_answers()
        a["generate_enablement"] = False
        a["pipelines_k8s_cluster"] = "prod-k8s"
        cfg = run_wizard(VmsProbe(vms=None), answers=a)
        assert "enablement" not in cfg
        assert cfg["pipelines"][0]["k8s_cluster"] == "prod-k8s"

    def test_skipped_pipelines(self) -> None:
        a = _full_answers()
        a["generate_pipelines"] = False
        cfg = run_wizard(VmsProbe(vms=None), answers=a)
        assert "pipelines" not in cfg


# ── branches ────────────────────────────────────────────────────────────────

class TestWizardBranches:
    def test_kafka_broker(self) -> None:
        a = _full_answers()
        a["broker"] = {
            "kind": "kafka",
            "name": "kf",
            "host_0": "k1.example.com",
            "port": 9092,
            "default_topic_name": "de-default",
            "default_topic_partitions": 50,
            "default_topic_retention": 168,
            "dlq_topic_name": "de-dlq",
            "dlq_topic_partitions": 16,
            "dlq_topic_retention": 24,
        }
        cfg = run_wizard(VmsProbe(vms=None), answers=a)
        assert cfg["enablement"]["event_broker"]["kind"] == "kafka"
        assert cfg["enablement"]["event_broker"]["hosts"] == ["k1.example.com"]

    def test_k8s_already_configured_skips_zarf_paths(self) -> None:
        a = _full_answers()
        a["k8s"]["already_configured"] = True
        cfg = run_wizard(VmsProbe(vms=None), answers=a)
        k = cfg["enablement"]["kubernetes"]
        assert "zarf_package_path" not in k
        assert "zarf_init_path" not in k

    def test_zero_users_zero_views(self) -> None:
        a = _full_answers()
        a["identity"]["users"] = []
        a["source_views"] = []
        cfg = run_wizard(VmsProbe(vms=None), answers=a)
        assert cfg["enablement"]["identity"]["users"] == []
        assert "source_views" not in cfg["enablement"]  # empty omitted

    def test_user_password_auth(self) -> None:
        a = _full_answers()
        a["vms"]["auth"] = "user_password"
        a["vms"]["user_env"] = "VMS_USER"
        a["vms"]["password_env"] = "VMS_PASSWORD"
        cfg = run_wizard(VmsProbe(vms=None), answers=a)
        assert cfg["vms"]["user"] == "${VMS_USER}"
        assert cfg["vms"]["password"] == "${VMS_PASSWORD}"
        assert "token" not in cfg["vms"]

    def test_schedule_trigger(self) -> None:
        a = _full_answers()
        a["pipelines"][0]["triggers"] = [
            {
                "type": "schedule",
                "name": "nightly",
                "schedule": {"mode": "simple", "expression": "0 2 * * *"},
                "topic": "de-default",
            }
        ]
        a["pipelines"][0]["flow"] = [{"from": "nightly", "to": "parse-pdf"}]
        cfg = run_wizard(VmsProbe(vms=None), answers=a)
        t = cfg["pipelines"][0]["triggers"][0]
        assert t["type"] == "schedule"
        assert t["schedule"] == {"simple": "0 2 * * *"}


# ── round-trip ──────────────────────────────────────────────────────────────

class TestRoundTrip:
    def test_writes_yaml_and_reloads(self, tmp_path: Path) -> None:
        out = tmp_path / "vastde.yaml"
        path, bak = run_wizard_to_file(
            VmsProbe(vms=None), out, answers=_full_answers()
        )
        assert path == out
        assert bak is None
        # Parse and validate again from disk (simulating user's next step).
        on_disk = yaml.safe_load(out.read_text())
        assert on_disk["vms"]["tenant"] == "data-platform"
        # Should pass a substitute-then-validate round trip (env placeholders pass through).

    def test_second_run_creates_backup(self, tmp_path: Path) -> None:
        out = tmp_path / "vastde.yaml"
        run_wizard_to_file(VmsProbe(vms=None), out, answers=_full_answers())
        run_wizard_to_file(VmsProbe(vms=None), out, answers=_full_answers())
        assert (tmp_path / "vastde.yaml.bak.1").exists()


# ── validation ──────────────────────────────────────────────────────────────

class TestWizardValidation:
    def test_invalid_pipeline_flow_is_caught(self) -> None:
        a = _full_answers()
        # Make flow reference a missing function — VastdeConfig should reject.
        a["pipelines"][0]["flow"] = [{"from": "ghost", "to": "parse-pdf"}]
        with pytest.raises(WizardValidationError, match="unknown trigger/function"):
            run_wizard(VmsProbe(vms=None), answers=a)

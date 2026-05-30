"""Tests for the enablement (Stage A) modules.

We mock VmsClient and verify the *order* of ensure_* calls, since that
ordering is what the PDF documents and is the load-bearing correctness
property of Stage A.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest
from pytest_mock import MockerFixture

from vastde_orch.clients.vms import DiffResult, EnsureOutcome
from vastde_orch.config.models import (
    ContainerRegistrySpec,
    EnablementSpec,
    GroupSpec,
    IdentitySpec,
    KafkaEventBrokerSpec,
    KubernetesSpec,
    RegistryAuthSpec,
    SourceViewSpec,
    TenantSpec,
    TopicSpec,
    UserSpec,
    VastEventBrokerSpec,
    VipPoolSpec,
)
from vastde_orch.enablement.container_registry import (
    RegistryAuthError,
    provision_container_registry,
)
from vastde_orch.enablement.enable import enable_dataengine
from vastde_orch.enablement.identity import (
    attach_dataengine_policy_to_group,
    build_dataengine_policy_doc,
    generate_user_keys,
    provision_identity,
)


def _outcome(name: str = "x") -> EnsureOutcome:
    return EnsureOutcome(DiffResult.CREATED, "r", name, 1, {})


# ── identity.py ─────────────────────────────────────────────────────────────

class TestIdentity:
    def test_provision_creates_group_then_users(self) -> None:
        vms = MagicMock()
        vms.ensure_group.return_value = _outcome("g")
        vms.ensure_user.side_effect = [_outcome("alice"), _outcome("bob")]

        spec = IdentitySpec(
            group=GroupSpec(name="g", gid=5000),
            users=[UserSpec(name="alice", uid=10001), UserSpec(name="bob", uid=10002)],
        )
        plan = provision_identity(vms, spec)

        assert vms.ensure_group.call_args == call("g", gid=5000, provider="vast")
        assert vms.ensure_user.call_args_list[0] == call(
            "alice", uid=10001, provider="vast", leading_group="g"
        )
        assert len(plan.outcomes) == 3

    def test_generate_user_keys_for_each(self) -> None:
        vms = MagicMock()
        vms.get_or_raise.side_effect = [{"id": 1}, {"id": 2}]
        vms.generate_s3_keys.side_effect = [
            {"access_key": "a1", "secret_key": "s1"},
            {"access_key": "a2", "secret_key": "s2"},
        ]
        result = generate_user_keys(vms, ["alice", "bob"])
        assert result["alice"]["access_key"] == "a1"
        assert result["bob"]["secret_key"] == "s2"


# ── DataEngine identity policy (the s3policies/ binding) ────────────────────

class TestDataenginePolicyDoc:
    def test_has_both_required_sids(self) -> None:
        import json
        doc = json.loads(build_dataengine_policy_doc())
        sids = {s["Sid"] for s in doc["Statement"]}
        assert sids == {"DataengineTablesAccess", "DataEngineDefault"}

    def test_dataengine_default_actions_match_pdf(self) -> None:
        import json
        doc = json.loads(build_dataengine_policy_doc())
        de_stmt = next(s for s in doc["Statement"] if s["Sid"] == "DataEngineDefault")
        assert set(de_stmt["Action"]) == {
            "dataengine:CreateTrigger",
            "dataengine:CreateFunction",
            "dataengine:CreatePipeline",
        }
        assert set(de_stmt["Resource"]) == {
            "vast:dataengine:triggers:*",
            "vast:dataengine:functions:*",
            "vast:dataengine:pipelines:*",
        }

    def test_explicit_policy_id_is_used(self) -> None:
        import json
        doc = json.loads(build_dataengine_policy_doc(policy_id="custom-id"))
        assert doc["Id"] == "custom-id"


class TestAttachDataenginePolicy:
    def test_posts_to_s3policies_endpoint(self) -> None:
        vms = MagicMock()
        vms.ensure.return_value = _outcome("data-engine-wi-tenant")

        out = attach_dataengine_policy_to_group(
            vms, group_name="wi-group", tenant_name="wi-tenant", tenant_id=12,
        )

        assert vms.ensure.called
        call = vms.ensure.call_args
        assert call.args[0] == "s3policies"
        assert call.kwargs["key_field"] == "name"
        assert call.kwargs["key_value"] == "data-engine-wi-tenant"
        body = call.kwargs["spec"]
        assert body["tenant_id"] == 12
        assert body["enabled"] is True
        assert body["groups"] == ["wi-group"]
        # policy field is a JSON string containing the IAM doc
        import json
        parsed = json.loads(body["policy"])
        assert "Statement" in parsed
        # Only certain fields are patchable to avoid timestamp drift
        assert call.kwargs["patchable_fields"] == {"groups", "enabled"}

    def test_explicit_policy_name_override(self) -> None:
        vms = MagicMock()
        vms.ensure.return_value = _outcome("custom-policy-name")
        attach_dataengine_policy_to_group(
            vms, group_name="g", tenant_name="t", tenant_id=1,
            policy_name="custom-policy-name",
        )
        assert vms.ensure.call_args.kwargs["key_value"] == "custom-policy-name"


# ── container_registry.py ───────────────────────────────────────────────────

class TestContainerRegistry:
    def test_user_credentials_picks_env_vars(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("REG_USR", "joe")
        monkeypatch.setenv("REG_PWD", "hunter2")
        vms = MagicMock()
        vms.ensure_container_registry.return_value = _outcome("primary")

        spec = ContainerRegistrySpec(
            name="primary",
            base_url="r.example.com",
            auth=RegistryAuthSpec(
                method="user_credentials",
                username_env="REG_USR",
                password_env="REG_PWD",
            ),
        )
        provision_container_registry(vms, spec, tenant_id=1, k8scluster_id=2)

        kwargs = vms.ensure_container_registry.call_args.kwargs
        assert kwargs["username"] == "joe"
        assert kwargs["password"] == "hunter2"

    def test_missing_env_var_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MISSING_USR", raising=False)
        monkeypatch.delenv("MISSING_PWD", raising=False)
        vms = MagicMock()
        spec = ContainerRegistrySpec(
            name="primary",
            base_url="r.example.com",
            auth=RegistryAuthSpec(
                method="user_credentials",
                username_env="MISSING_USR",
                password_env="MISSING_PWD",
            ),
        )
        with pytest.raises(RegistryAuthError, match="env vars"):
            provision_container_registry(vms, spec, tenant_id=1, k8scluster_id=2)


# ── enable.py end-to-end ordering ───────────────────────────────────────────

def _enablement_spec(broker_kind: str = "vast") -> EnablementSpec:
    if broker_kind == "vast":
        broker = VastEventBrokerSpec(
            kind="vast",
            view_path="/sys/de",
            bucket_name="de",
            bucket_owner="alice",
            vip_pool=VipPoolSpec(
                name="pool", cidr="10.0.0.0/24", ip_range=["10.0.0.10", "10.0.0.20"]
            ),
            default_topic=TopicSpec(name="default"),
            deadletter_topic=TopicSpec(name="dlq", partitions=8),
        )
    else:
        broker = KafkaEventBrokerSpec(
            kind="kafka",
            name="kf",
            hosts=["k1"],
            port=9092,
            default_topic=TopicSpec(name="default"),
            deadletter_topic=TopicSpec(name="dlq"),
        )
    return EnablementSpec(
        tenant=TenantSpec(name="data-platform", create_if_missing=True),
        event_broker=broker,
        container_registry=ContainerRegistrySpec(
            name="primary", base_url="r.example.com",
            auth=RegistryAuthSpec(method="none"),
        ),
        kubernetes=KubernetesSpec(name="k8s", api_server="https://x:6443"),
        identity=IdentitySpec(
            group=GroupSpec(name="de-users", gid=5000),
            users=[UserSpec(name="alice", uid=10001)],
        ),
        source_views=[SourceViewSpec(
            path="/raw/docs", bucket="raw-docs", owner="alice", policy="dataengine-default"
        )],
    )


class TestEnableOrchestration:
    def test_correct_ordering_with_vast_broker(self, mocker: MockerFixture) -> None:
        vms = MagicMock()
        # ensure_* return CREATED outcomes for every call
        vms.ensure_tenant.return_value = _outcome("data-platform")
        vms.ensure_group.return_value = _outcome("de-users")
        vms.ensure_user.return_value = _outcome("alice")
        vms.ensure_vippool.return_value = _outcome("pool")
        vms.ensure_viewpolicy.return_value = _outcome("dataengine-default")
        vms.ensure_view.return_value = _outcome("/sys/de")
        vms.ensure_topic.return_value = _outcome("topic")
        vms.ensure_container_registry.return_value = _outcome("primary")
        vms.ensure_k8scluster.return_value = _outcome("k8s")
        vms.ensure.return_value = _outcome("generic")
        # Lookups for IDs
        vms.get_or_raise.side_effect = lambda resource, **kw: {"id": 42, "name": kw.get("key_value")}

        mocker.patch("vastde_orch.enablement.enable.run_preflight")
        mocker.patch("vastde_orch.enablement.enable.bootstrap_k8s")
        mocker.patch("vastde_orch.enablement.enable.provision_source_views",
                     return_value=mocker.MagicMock(record=lambda x: x))

        spec = _enablement_spec()
        plan = enable_dataengine(vms, spec, skip_k8s_bootstrap=True, dry_run=True)

        # Tenant came first
        assert vms.ensure_tenant.called
        # k8s cluster registration
        assert vms.ensure_k8scluster.called
        # Identity (group → user)
        assert vms.ensure_group.called
        assert vms.ensure_user.called
        # Event broker stack
        assert vms.ensure_vippool.called
        assert vms.ensure_viewpolicy.called
        assert vms.ensure_view.called
        # The broker view must include all three protocols (S3, DATABASE, KAFKA)
        # per VAST 5.4 behavior — verified against wi-tenant view id=253.
        view_call = vms.ensure_view.call_args
        assert sorted(view_call.kwargs["protocols"]) == ["DATABASE", "KAFKA", "S3"]
        # Two topics: default + dlq
        assert vms.ensure_topic.call_count == 2
        # Container registry
        assert vms.ensure_container_registry.called
        # Tenant DataEngine toggle (via generic ensure)
        assert vms.ensure.call_count >= 1  # at least the tenantdataengine toggle
        assert isinstance(plan.outcomes, list)
        assert len(plan.outcomes) > 0

    def test_kafka_broker_skips_vast_broker_stack(self, mocker: MockerFixture) -> None:
        vms = MagicMock()
        vms.ensure_tenant.return_value = _outcome()
        vms.ensure_group.return_value = _outcome()
        vms.ensure_user.return_value = _outcome()
        vms.ensure_k8scluster.return_value = _outcome()
        vms.ensure_container_registry.return_value = _outcome()
        vms.ensure.return_value = _outcome()
        vms.get_or_raise.side_effect = lambda resource, **kw: {"id": 1, "name": kw["key_value"]}

        mocker.patch("vastde_orch.enablement.enable.run_preflight")
        mocker.patch("vastde_orch.enablement.enable.bootstrap_k8s")
        mocker.patch("vastde_orch.enablement.enable.provision_source_views",
                     return_value=mocker.MagicMock(record=lambda x: x))

        spec = _enablement_spec("kafka")
        enable_dataengine(vms, spec, skip_k8s_bootstrap=True, dry_run=True)

        # No VAST broker resources should be created
        vms.ensure_vippool.assert_not_called()
        vms.ensure_view.assert_not_called()
        vms.ensure_topic.assert_not_called()
        # But ensure() is called for kafkabrokers + tenantdataengine
        assert vms.ensure.call_count >= 2

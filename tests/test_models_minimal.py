"""Tests for src/vastde_orch/config/models_minimal.py."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from vastde_orch.config.loader import (
    ConfigError,
    detect_schema,
    load_any_config,
    load_config,
    load_minimal_config,
)
from vastde_orch.config.models import VastdeConfig
from vastde_orch.config.models_minimal import (
    BrokerViewSpec,
    BucketOwnerSpec,
    FunctionSpec,
    GroupSpec,
    IdentitySpec,
    K8sSpec,
    PipelineSpec,
    RegistrySpec,
    SetupProvisioningSpec,
    TriggerSpec,
    VastdeMinimalConfig,
    ViewPolicySpec,
)


# ── Building blocks ─────────────────────────────────────────────────────────

def _min_yaml() -> str:
    return """
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


def _parse(yaml_text: str) -> VastdeMinimalConfig:
    return VastdeMinimalConfig.model_validate(yaml.safe_load(yaml_text))


# ── Required-field enforcement ──────────────────────────────────────────────

class TestRequiredFields:
    def test_minimum_input_parses(self) -> None:
        cfg = _parse(_min_yaml())
        assert cfg.vms.tenant_name == "my-tenant"
        assert cfg.vip_pool_name == "my-de-vips"
        assert str(cfg.k8s.kube_api_url) == "https://10.143.2.242:6443"
        assert cfg.registry.url == "docker.io"

    @pytest.mark.parametrize("missing", ["vms", "vip_pool_name", "k8s", "registry"])
    def test_top_level_field_required(self, missing: str) -> None:
        data = yaml.safe_load(_min_yaml())
        del data[missing]
        with pytest.raises(ValidationError):
            VastdeMinimalConfig.model_validate(data)

    @pytest.mark.parametrize("missing", ["address", "tenant_name", "auth"])
    def test_vms_field_required(self, missing: str) -> None:
        data = yaml.safe_load(_min_yaml())
        del data["vms"][missing]
        with pytest.raises(ValidationError):
            VastdeMinimalConfig.model_validate(data)

    @pytest.mark.parametrize("missing", ["user_env", "password_env"])
    def test_auth_env_required(self, missing: str) -> None:
        data = yaml.safe_load(_min_yaml())
        del data["vms"]["auth"][missing]
        with pytest.raises(ValidationError):
            VastdeMinimalConfig.model_validate(data)

    @pytest.mark.parametrize(
        "missing", ["ca_cert_file", "client_cert_file", "client_key_file"]
    )
    def test_mtls_cert_files_required(self, missing: str) -> None:
        data = yaml.safe_load(_min_yaml())
        del data["k8s"]["mtls"][missing]
        with pytest.raises(ValidationError):
            VastdeMinimalConfig.model_validate(data)

    def test_unknown_field_rejected(self) -> None:
        data = yaml.safe_load(_min_yaml())
        data["unknown_top_level"] = "x"
        with pytest.raises(ValidationError, match="extra"):
            VastdeMinimalConfig.model_validate(data)

    def test_unknown_nested_field_rejected(self) -> None:
        data = yaml.safe_load(_min_yaml())
        data["registry"]["unknown_field"] = "x"
        with pytest.raises(ValidationError, match="extra"):
            VastdeMinimalConfig.model_validate(data)


# ── Default-value derivation ────────────────────────────────────────────────

class TestDefaults:
    def test_identity_defaults(self) -> None:
        cfg = _parse(_min_yaml())
        assert cfg.identity.group.name == "de-users"
        assert cfg.identity.group.gid == 65500
        assert cfg.identity.bucket_owner.name == "de-owner"
        assert cfg.identity.bucket_owner.uid == 65500
        assert cfg.identity.bucket_owner.leading_gid == 65500
        assert cfg.identity.bucket_owner.allow_create_bucket is True

    def test_view_policy_defaults(self) -> None:
        cfg = _parse(_min_yaml())
        assert cfg.view_policy.name == "de-policy"
        assert cfg.view_policy.flavor == "S3_NATIVE"

    def test_broker_view_defaults(self) -> None:
        cfg = _parse(_min_yaml())
        assert cfg.broker_view.path == "/de-broker"
        assert cfg.broker_view.bucket == "de-broker"

    def test_setup_provisioning_defaults(self) -> None:
        cfg = _parse(_min_yaml())
        assert cfg.setup_provisioning.default_topic_name == "de-default"
        assert cfg.setup_provisioning.dead_letter_topic_name == "de-dlq"

    def test_k8s_namespaces_default(self) -> None:
        cfg = _parse(_min_yaml())
        assert cfg.k8s.namespaces == ["vast-dataengine"]

    def test_registry_namespace_default(self) -> None:
        cfg = _parse(_min_yaml())
        assert cfg.registry.namespace == "vast-dataengine"
        assert cfg.registry.auth_type == "password"
        assert cfg.registry.username_env == "REGISTRY_USER"
        assert cfg.registry.password_env == "REGISTRY_PASSWORD"

    def test_identity_overrides(self) -> None:
        data = yaml.safe_load(_min_yaml())
        data["identity"] = {"group": {"name": "custom-grp", "gid": 1234}}
        cfg = VastdeMinimalConfig.model_validate(data)
        assert cfg.identity.group.name == "custom-grp"
        assert cfg.identity.group.gid == 1234
        # bucket_owner still gets its defaults
        assert cfg.identity.bucket_owner.name == "de-owner"


# ── Auto-derivation logic ───────────────────────────────────────────────────

class TestAutoDerivation:
    def test_k8s_name_derived_from_url(self) -> None:
        cfg = _parse(_min_yaml())
        assert cfg.k8s.name == "10-143-2-242"

    def test_k8s_name_derived_from_hostname(self) -> None:
        data = yaml.safe_load(_min_yaml())
        data["k8s"]["kube_api_url"] = "https://k8s.example.com:6443"
        cfg = VastdeMinimalConfig.model_validate(data)
        assert cfg.k8s.name == "k8s-example-com"

    def test_k8s_name_explicit_override(self) -> None:
        data = yaml.safe_load(_min_yaml())
        data["k8s"]["name"] = "lab-cluster"
        cfg = VastdeMinimalConfig.model_validate(data)
        assert cfg.k8s.name == "lab-cluster"

    def test_k8s_name_fallback_when_url_has_no_host(self) -> None:
        data = yaml.safe_load(_min_yaml())
        data["k8s"]["kube_api_url"] = "https:///path"
        cfg = VastdeMinimalConfig.model_validate(data)
        assert cfg.k8s.name == "k8s"

    def test_mtls_name_derived_from_k8s_name(self) -> None:
        cfg = _parse(_min_yaml())
        assert cfg.k8s.mtls.name == "10-143-2-242-mtls"

    def test_mtls_name_uses_explicit_k8s_name(self) -> None:
        data = yaml.safe_load(_min_yaml())
        data["k8s"]["name"] = "lab"
        cfg = VastdeMinimalConfig.model_validate(data)
        assert cfg.k8s.mtls.name == "lab-mtls"

    def test_mtls_name_explicit_override(self) -> None:
        data = yaml.safe_load(_min_yaml())
        data["k8s"]["mtls"]["name"] = "custom-mtls"
        cfg = VastdeMinimalConfig.model_validate(data)
        assert cfg.k8s.mtls.name == "custom-mtls"

    @pytest.mark.parametrize(
        "url,expected",
        [
            ("docker.io", "docker"),
            ("registry.example.com", "registry"),
            ("ghcr.io/owner", "ghcr"),
            ("localhost:5000", "localhost:5000"),
        ],
    )
    def test_registry_name_derived(self, url: str, expected: str) -> None:
        data = yaml.safe_load(_min_yaml())
        data["registry"]["url"] = url
        cfg = VastdeMinimalConfig.model_validate(data)
        assert cfg.registry.name == expected

    def test_registry_name_explicit_override(self) -> None:
        data = yaml.safe_load(_min_yaml())
        data["registry"]["name"] = "myreg"
        cfg = VastdeMinimalConfig.model_validate(data)
        assert cfg.registry.name == "myreg"


# ── Cross-resource derived VRNs / names ─────────────────────────────────────

class TestDerivedReferences:
    def test_kafka_broker_name_uses_broker_view_bucket(self) -> None:
        cfg = _parse(_min_yaml())
        assert cfg.kafka_broker_name == "de-broker"

    def test_kafka_broker_name_follows_override(self) -> None:
        data = yaml.safe_load(_min_yaml())
        data["broker_view"] = {"path": "/x", "bucket": "x-broker"}
        cfg = VastdeMinimalConfig.model_validate(data)
        assert cfg.kafka_broker_name == "x-broker"

    def test_k8s_cluster_vrn_format(self) -> None:
        cfg = _parse(_min_yaml())
        assert cfg.k8s_cluster_vrn == "vast:dataengine:kubernetes-clusters:10-143-2-242"

    def test_registry_vrn_format(self) -> None:
        cfg = _parse(_min_yaml())
        assert cfg.registry_vrn == "vast:dataengine:container-registries:docker"


# ── Registry auth validation ────────────────────────────────────────────────

class TestRegistryAuth:
    def test_password_auth_requires_env_vars(self) -> None:
        data = yaml.safe_load(_min_yaml())
        data["registry"]["auth_type"] = "password"
        data["registry"]["username_env"] = None
        with pytest.raises(ValidationError, match="username_env"):
            VastdeMinimalConfig.model_validate(data)

    def test_none_auth_allows_missing_env_vars(self) -> None:
        data = yaml.safe_load(_min_yaml())
        data["registry"]["auth_type"] = "none"
        data["registry"]["username_env"] = None
        data["registry"]["password_env"] = None
        cfg = VastdeMinimalConfig.model_validate(data)
        assert cfg.registry.auth_type == "none"

    def test_invalid_auth_type_rejected(self) -> None:
        data = yaml.safe_load(_min_yaml())
        data["registry"]["auth_type"] = "invalid"
        with pytest.raises(ValidationError):
            VastdeMinimalConfig.model_validate(data)


# ── Pipelines (optional) ────────────────────────────────────────────────────

def _pipeline_yaml() -> str:
    return _min_yaml() + """
pipelines:
  - name: pdf-ingest
    triggers:
      - name: new-pdf
        source_bucket_name: raw-pdfs
        topic_name: new-pdf-topic
        config:
          name_filters: {suffixes: [.pdf]}
    functions:
      - name: parse-pdf
        artifact_source: docker.io/myorg/parse-pdf
        image_tag: v1.0
"""


class TestPipelines:
    def test_pipelines_optional(self) -> None:
        cfg = _parse(_min_yaml())
        assert cfg.pipelines == []

    def test_pipeline_parses(self) -> None:
        cfg = _parse(_pipeline_yaml())
        assert len(cfg.pipelines) == 1
        p = cfg.pipelines[0]
        assert p.name == "pdf-ingest"
        assert len(p.triggers) == 1
        assert len(p.functions) == 1

    def test_pipeline_namespace_inherits_from_k8s(self) -> None:
        cfg = _parse(_pipeline_yaml())
        assert cfg.pipelines[0].namespace == "vast-dataengine"

    def test_pipeline_namespace_explicit_override(self) -> None:
        data = yaml.safe_load(_pipeline_yaml())
        data["pipelines"][0]["namespace"] = "custom-ns"
        cfg = VastdeMinimalConfig.model_validate(data)
        assert cfg.pipelines[0].namespace == "custom-ns"

    def test_pipeline_namespace_inherits_custom_k8s_namespace(self) -> None:
        data = yaml.safe_load(_pipeline_yaml())
        data["k8s"]["namespaces"] = ["my-prod-ns", "my-dev-ns"]
        cfg = VastdeMinimalConfig.model_validate(data)
        assert cfg.pipelines[0].namespace == "my-prod-ns"


class TestTriggers:
    def test_trigger_defaults(self) -> None:
        cfg = _parse(_pipeline_yaml())
        t = cfg.pipelines[0].triggers[0]
        assert t.type == "Element"
        assert t.events == ["ObjectCreated:*"]

    def test_element_trigger_requires_source_bucket(self) -> None:
        data = yaml.safe_load(_pipeline_yaml())
        del data["pipelines"][0]["triggers"][0]["source_bucket_name"]
        with pytest.raises(ValidationError, match="source_bucket_name"):
            VastdeMinimalConfig.model_validate(data)

    def test_schedule_trigger_no_source_bucket_required(self) -> None:
        data = yaml.safe_load(_pipeline_yaml())
        data["pipelines"][0]["triggers"][0]["type"] = "Schedule"
        del data["pipelines"][0]["triggers"][0]["source_bucket_name"]
        cfg = VastdeMinimalConfig.model_validate(data)
        assert cfg.pipelines[0].triggers[0].type == "Schedule"

    def test_invalid_trigger_type_rejected(self) -> None:
        data = yaml.safe_load(_pipeline_yaml())
        data["pipelines"][0]["triggers"][0]["type"] = "Webhook"
        with pytest.raises(ValidationError):
            VastdeMinimalConfig.model_validate(data)

    def test_custom_event_list_accepted(self) -> None:
        data = yaml.safe_load(_pipeline_yaml())
        data["pipelines"][0]["triggers"][0]["events"] = [
            "ObjectCreated:Put", "ObjectRemoved:*",
        ]
        cfg = VastdeMinimalConfig.model_validate(data)
        assert cfg.pipelines[0].triggers[0].events == [
            "ObjectCreated:Put", "ObjectRemoved:*",
        ]


class TestFunctions:
    def test_function_defaults(self) -> None:
        cfg = _parse(_pipeline_yaml())
        f = cfg.pipelines[0].functions[0]
        assert f.runtime == "python-3.11"
        assert f.architecture == "x86"

    @pytest.mark.parametrize("bad_tag", ["latest", "LATEST", "Latest"])
    def test_image_tag_latest_rejected(self, bad_tag: str) -> None:
        data = yaml.safe_load(_pipeline_yaml())
        data["pipelines"][0]["functions"][0]["image_tag"] = bad_tag
        with pytest.raises(ValidationError, match="latest"):
            VastdeMinimalConfig.model_validate(data)

    def test_image_tag_versioned_accepted(self) -> None:
        for tag in ["v1.0", "sha-abc123", "1.2.3", "2026-05-30"]:
            data = yaml.safe_load(_pipeline_yaml())
            data["pipelines"][0]["functions"][0]["image_tag"] = tag
            cfg = VastdeMinimalConfig.model_validate(data)
            assert cfg.pipelines[0].functions[0].image_tag == tag

    def test_invalid_runtime_rejected(self) -> None:
        data = yaml.safe_load(_pipeline_yaml())
        data["pipelines"][0]["functions"][0]["runtime"] = "python-2.7"
        with pytest.raises(ValidationError):
            VastdeMinimalConfig.model_validate(data)

    def test_invalid_architecture_rejected(self) -> None:
        data = yaml.safe_load(_pipeline_yaml())
        data["pipelines"][0]["functions"][0]["architecture"] = "ppc64"
        with pytest.raises(ValidationError):
            VastdeMinimalConfig.model_validate(data)


# ── Other field-level constraints ───────────────────────────────────────────

class TestFieldConstraints:
    def test_view_policy_flavor_enum(self) -> None:
        data = yaml.safe_load(_min_yaml())
        data["view_policy"] = {"flavor": "BOGUS"}
        with pytest.raises(ValidationError):
            VastdeMinimalConfig.model_validate(data)

    def test_group_gid_must_be_positive(self) -> None:
        data = yaml.safe_load(_min_yaml())
        data["identity"] = {"group": {"name": "g", "gid": 0}}
        with pytest.raises(ValidationError):
            VastdeMinimalConfig.model_validate(data)

    def test_bucket_owner_uid_must_be_positive(self) -> None:
        data = yaml.safe_load(_min_yaml())
        data["identity"] = {"bucket_owner": {"name": "o", "uid": -1, "leading_gid": 1}}
        with pytest.raises(ValidationError):
            VastdeMinimalConfig.model_validate(data)


# ── Template file round-trip ────────────────────────────────────────────────

class TestTemplateFile:
    """Smoke test: the shipped template parses cleanly (after stripping comments)."""

    def test_template_required_fields_only_parses(self) -> None:
        # Minimum required block as it appears in sample/vastde.template.yaml
        template_required = """
vms:
  address:     var203.selab.vastdata.com
  tenant_name: my-tenant
  auth:
    user_env:     TENANT_ADMIN_USER
    password_env: TENANT_ADMIN_PASSWORD
vip_pool_name: my-de-vips
k8s:
  kube_api_url: https://10.143.2.242:6443
  mtls:
    ca_cert_file:     ./certs/ca.pem
    client_cert_file: ./certs/client.pem
    client_key_file:  ./certs/client.key
registry:
  url: docker.io
"""
        cfg = VastdeMinimalConfig.model_validate(yaml.safe_load(template_required))
        # Every derived/default field is now populated:
        assert cfg.k8s.name is not None
        assert cfg.k8s.mtls.name is not None
        assert cfg.registry.name is not None
        assert cfg.identity.group.name == "de-users"
        assert cfg.view_policy.flavor == "S3_NATIVE"
        assert cfg.broker_view.bucket == "de-broker"
        assert cfg.setup_provisioning.default_topic_name == "de-default"
        assert cfg.kafka_broker_name == cfg.broker_view.bucket


# ── Standalone-model unit tests (no full config needed) ─────────────────────

class TestSubModels:
    def test_group_spec_defaults(self) -> None:
        g = GroupSpec()
        assert g.name == "de-users"
        assert g.gid == 65500

    def test_bucket_owner_defaults(self) -> None:
        bo = BucketOwnerSpec()
        assert bo.name == "de-owner"
        assert bo.allow_create_bucket is True

    def test_view_policy_defaults(self) -> None:
        vp = ViewPolicySpec()
        assert vp.name == "de-policy"
        assert vp.flavor == "S3_NATIVE"

    def test_broker_view_defaults(self) -> None:
        bv = BrokerViewSpec()
        assert bv.path == "/de-broker"
        assert bv.bucket == "de-broker"

    def test_setup_provisioning_defaults(self) -> None:
        sp = SetupProvisioningSpec()
        assert sp.default_topic_name == "de-default"
        assert sp.dead_letter_topic_name == "de-dlq"

    def test_identity_defaults(self) -> None:
        i = IdentitySpec()
        assert i.group.name == "de-users"
        assert i.bucket_owner.name == "de-owner"


# ── Loader: schema detection + dispatch ─────────────────────────────────────

def _full_schema_yaml() -> str:
    return """
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


class TestSchemaDetection:
    def test_minimal_detected_by_vip_pool_name(self) -> None:
        data = yaml.safe_load(_min_yaml())
        assert detect_schema(data) == "minimal"

    def test_full_detected_when_no_vip_pool_name(self) -> None:
        data = yaml.safe_load(_full_schema_yaml())
        assert detect_schema(data) == "full"

    def test_minimal_takes_precedence_over_enablement_key(self) -> None:
        # vip_pool_name wins even if enablement is also present (mixed/migrating)
        data = yaml.safe_load(_min_yaml())
        data["enablement"] = {}
        assert detect_schema(data) == "minimal"


class TestLoaderDispatch:
    def test_load_minimal_returns_minimal_type(self, tmp_path: Path) -> None:
        p = tmp_path / "vastde.yaml"
        p.write_text(_min_yaml())
        cfg = load_minimal_config(p, env_file=None)
        assert isinstance(cfg, VastdeMinimalConfig)
        assert cfg.vms.tenant_name == "my-tenant"

    def test_load_full_returns_full_type(self, tmp_path: Path) -> None:
        p = tmp_path / "vastde.yaml"
        p.write_text(_full_schema_yaml())
        cfg = load_config(p, env_file=None)
        assert isinstance(cfg, VastdeConfig)
        assert cfg.vms.tenant == "default"

    def test_load_any_routes_minimal(self, tmp_path: Path) -> None:
        p = tmp_path / "vastde.yaml"
        p.write_text(_min_yaml())
        cfg = load_any_config(p, env_file=None)
        assert isinstance(cfg, VastdeMinimalConfig)

    def test_load_any_routes_full(self, tmp_path: Path) -> None:
        p = tmp_path / "vastde.yaml"
        p.write_text(_full_schema_yaml())
        cfg = load_any_config(p, env_file=None)
        assert isinstance(cfg, VastdeConfig)

    def test_load_minimal_on_full_yaml_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "vastde.yaml"
        p.write_text(_full_schema_yaml())
        with pytest.raises(ConfigError, match="validation failed"):
            load_minimal_config(p, env_file=None)

    def test_load_config_on_minimal_yaml_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "vastde.yaml"
        p.write_text(_min_yaml())
        with pytest.raises(ConfigError, match="validation failed"):
            load_config(p, env_file=None)

    def test_load_any_validation_error_names_detected_schema(
        self, tmp_path: Path
    ) -> None:
        p = tmp_path / "vastde.yaml"
        # Marked minimal (has vip_pool_name) but missing other required fields
        p.write_text("vip_pool_name: x\nvms:\n  address: y\n")
        with pytest.raises(ConfigError, match="detected schema: minimal"):
            load_any_config(p, env_file=None)

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="not found"):
            load_minimal_config(tmp_path / "nope.yaml", env_file=None)

    def test_non_mapping_root_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "vastde.yaml"
        p.write_text("- just\n- a\n- list\n")
        with pytest.raises(ConfigError, match="mapping at the root"):
            load_minimal_config(p, env_file=None)

    def test_yaml_parse_error_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "vastde.yaml"
        p.write_text("vms: {bad: [unterminated\n")
        with pytest.raises(ConfigError, match="YAML parse error"):
            load_minimal_config(p, env_file=None)


class TestEnvInterpolation:
    def test_env_var_interpolated_into_minimal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MY_TENANT", "tenant-from-env")
        p = tmp_path / "vastde.yaml"
        p.write_text(_min_yaml().replace("my-tenant", "${MY_TENANT}"))
        cfg = load_minimal_config(p, env_file=None)
        assert cfg.vms.tenant_name == "tenant-from-env"

    def test_missing_env_var_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "vastde.yaml"
        p.write_text(_min_yaml().replace("my-tenant", "${DEFINITELY_NOT_SET_XYZ}"))
        with pytest.raises(ConfigError, match="DEFINITELY_NOT_SET_XYZ"):
            load_minimal_config(p, env_file=None)

"""Pydantic v2 schema for the minimal, tenant-scoped vastde.yaml template.

Matches `sample/vastde.template.yaml`. Compared to `models.py` (full schema
used by the wizard), this schema:

  - assumes the tenant + PROTOCOLS VIP pool + tenant-admin already exist
    (cluster-admin work is out of scope)
  - lets the operator omit ~80% of fields and have them auto-derived
  - bakes in the API shapes confirmed in `docs/vms-api-full-catalog.md`
    (setup-provisioning.vip_pools, k8s.namespaces as flat list, registry
    references k8s via VRN, mTLS via /mtls-authentication-credentials/)
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ── VMS connection ──────────────────────────────────────────────────────────

class AuthEnv(_Strict):
    user_env: str
    password_env: str


class VmsSpec(_Strict):
    address: str
    tenant_name: str
    auth: AuthEnv


# ── Identity (defaults baked in) ────────────────────────────────────────────

class GroupSpec(_Strict):
    name: str = "de-users"
    gid: int = Field(default=65500, ge=1)


class BucketOwnerSpec(_Strict):
    name: str = "de-owner"
    uid: int = Field(default=65500, ge=1)
    leading_gid: int = Field(default=65500, ge=1)
    allow_create_bucket: bool = True


class IdentitySpec(_Strict):
    group: GroupSpec = Field(default_factory=GroupSpec)
    bucket_owner: BucketOwnerSpec = Field(default_factory=BucketOwnerSpec)


# ── View policy + broker view ───────────────────────────────────────────────

class ViewPolicySpec(_Strict):
    name: str = "de-policy"
    # S3_NATIVE is required for the DataEngine broker view because that view has
    # protocols=[S3, DATABASE, KAFKA] and VAST rejects any other flavor on a view
    # whose protocols include DATABASE ("can only have a view policy where the
    # security flavor is S3 native"). Live-validated 2026-05-31.
    flavor: Literal["NFS", "SMB", "S3_NATIVE", "MIXED_LAST_WINS"] = "S3_NATIVE"


class BrokerViewSpec(_Strict):
    """Internal Kafka broker is realized as a VAST view with KAFKA protocol.

    The bucket name becomes `kafka_broker.name` on setup-provisioning.
    Protocols are forced to [S3, DATABASE, KAFKA] — not configurable.
    """
    path: str = "/de-broker"
    bucket: str = "de-broker"


# ── DataEngine setup-provisioning ───────────────────────────────────────────

class SetupProvisioningSpec(_Strict):
    """Body fields for POST /api/dataengine/setup-provisioning/.

    Note: both topic names MUST refer to topics that already exist in the broker
    bucket. setup-provisioning does NOT auto-create them — the orchestrator must
    POST /api/latest/topics/?database_name=<broker.bucket> before calling this.

    Note: `vip_pools` is auto-derived by the orchestrator from the resolved
    `vip_pool_name`. While documented as optional on /setup-provisioning/, omitting
    it leads to a delayed-failure mode where downstream /kubernetes-clusters/
    POSTs fail with "Failed to provision telemetries resources".
    """
    default_topic_name: str = "de-default"
    dead_letter_topic_name: str = "de-dlq"


# ── K8s cluster + mTLS ──────────────────────────────────────────────────────

class MtlsSpec(_Strict):
    name: str | None = None
    ca_cert_file: Path
    client_cert_file: Path
    client_key_file: Path


class K8sSpec(_Strict):
    kube_api_url: str
    mtls: MtlsSpec
    name: str | None = None
    namespaces: list[str] = Field(default_factory=lambda: ["vast-dataengine"])

    @model_validator(mode="after")
    def _derive(self) -> K8sSpec:
        if not self.name:
            host = urlparse(self.kube_api_url).hostname or "k8s"
            self.name = host.replace(".", "-")
        if not self.mtls.name:
            self.mtls.name = f"{self.name}-mtls"
        return self


# ── Container registry ──────────────────────────────────────────────────────

class RegistrySpec(_Strict):
    url: str
    name: str | None = None
    namespace: str = "vast-dataengine"
    auth_type: Literal["password", "none"] = "password"
    username_env: str | None = "REGISTRY_USER"
    password_env: str | None = "REGISTRY_PASSWORD"

    @model_validator(mode="after")
    def _check(self) -> RegistrySpec:
        if not self.name:
            self.name = self.url.split("/")[0].split(".")[0] or "registry"
        if self.auth_type == "password":
            if not (self.username_env and self.password_env):
                raise ValueError("registry.auth_type=password requires username_env + password_env")
        return self


# ── Pipelines (optional) ────────────────────────────────────────────────────

class TriggerConfigSpec(_Strict):
    tag_filters: dict[str, list[str]] | None = None
    name_filters: dict[str, list[str]] | None = None


class TriggerSpec(_Strict):
    name: str
    type: Literal["Element", "Schedule"] = "Element"
    events: list[str] = Field(default_factory=lambda: ["ObjectCreated:*"])
    source_bucket_name: str | None = None
    topic_name: str
    config: TriggerConfigSpec | None = None
    custom_extensions: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> TriggerSpec:
        if self.type == "Element" and not self.source_bucket_name:
            raise ValueError(f"trigger {self.name!r}: Element triggers require source_bucket_name")
        return self


class FunctionSpec(_Strict):
    name: str
    artifact_source: str
    image_tag: str
    runtime: Literal[
        "python-3.6", "python-3.7", "python-3.8",
        "python-3.9", "python-3.10", "python-3.11",
    ] = "python-3.11"
    architecture: Literal["x86", "arm"] = "x86"

    @model_validator(mode="after")
    def _no_latest(self) -> FunctionSpec:
        if self.image_tag.lower() == "latest":
            raise ValueError(f"function {self.name!r}: image_tag must not be 'latest'")
        return self


class PipelineSpec(_Strict):
    name: str
    namespace: str | None = None
    triggers: list[TriggerSpec] = Field(default_factory=list)
    functions: list[FunctionSpec] = Field(default_factory=list)


# ── Root ────────────────────────────────────────────────────────────────────

class VastdeMinimalConfig(_Strict):
    vms: VmsSpec
    vip_pool_name: str
    k8s: K8sSpec
    registry: RegistrySpec
    identity: IdentitySpec = Field(default_factory=IdentitySpec)
    view_policy: ViewPolicySpec = Field(default_factory=ViewPolicySpec)
    broker_view: BrokerViewSpec = Field(default_factory=BrokerViewSpec)
    setup_provisioning: SetupProvisioningSpec = Field(default_factory=SetupProvisioningSpec)
    pipelines: list[PipelineSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _pipeline_defaults(self) -> VastdeMinimalConfig:
        default_ns = self.k8s.namespaces[0]
        for p in self.pipelines:
            if not p.namespace:
                p.namespace = default_ns
        return self

    # Derived references the runtime needs but operator never writes.

    @property
    def kafka_broker_name(self) -> str:
        """Becomes kafka_broker.name on setup-provisioning POST."""
        return self.broker_view.bucket

    @property
    def k8s_cluster_vrn(self) -> str:
        """vrn referenced by container-registries + pipelines."""
        return f"vast:dataengine:kubernetes-clusters:{self.k8s.name}"

    @property
    def registry_vrn(self) -> str:
        """vrn referenced by function-revisions.container_registry_vrn."""
        return f"vast:dataengine:container-registries:{self.registry.name}"

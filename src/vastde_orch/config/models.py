"""Pydantic v2 models for vastde.yaml.

The schema mirrors the documented VAST DataEngine concepts: a `vms` connection
spec, an `enablement` block describing Stage A (one-shot bootstrap), and a
list of `pipelines` describing Stage B (declarative pipeline-as-code).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, IPvAnyAddress, field_validator, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ── VMS connection ──────────────────────────────────────────────────────────

class VmsSpec(_Strict):
    address: str
    token: str | None = None
    user: str | None = None
    password: str | None = None
    tenant: str
    api_version: str | None = None

    @model_validator(mode="after")
    def _require_auth(self) -> VmsSpec:
        if not self.token and not (self.user and self.password):
            raise ValueError("vms: must provide either `token` or both `user` and `password`")
        return self


# ── Enablement (Stage A) ────────────────────────────────────────────────────

class TenantSpec(_Strict):
    name: str
    domain: str | None = None
    create_if_missing: bool = False


class TopicSpec(_Strict):
    name: str
    partitions: int = Field(default=50, ge=1, le=10_000)
    retention_hours: int = Field(default=168, ge=6)
    compaction: bool = False


class VipPoolSpec(_Strict):
    name: str
    cidr: str  # CIDR validated as string; vastpy will reject malformed values
    ip_range: list[IPvAnyAddress] = Field(min_length=2, max_length=2)
    # Short DNS name VMS combines with the cluster DNS suffix to form the
    # FQDN (e.g. "amer" → "amer.<cluster-dns>"). Defaults to `name` in
    # ensure_vippool when not set — matches the "tenant create just works"
    # path. Set explicitly to override, set to "" to opt out.
    domain_name: str | None = None


class VastEventBrokerSpec(_Strict):
    kind: Literal["vast"] = "vast"
    view_path: str
    bucket_name: str
    bucket_owner: str
    vip_pool: VipPoolSpec
    default_topic: TopicSpec
    deadletter_topic: TopicSpec
    view_policy: str = "dataengine-default"
    ca_cert_path: Path | None = None


class KafkaEventBrokerSpec(_Strict):
    kind: Literal["kafka"]
    name: str
    hosts: list[str] = Field(min_length=1, max_length=5)
    port: int = Field(ge=1, le=65535)
    default_topic: TopicSpec
    deadletter_topic: TopicSpec


EventBrokerSpec = VastEventBrokerSpec | KafkaEventBrokerSpec


class RegistryAuthSpec(_Strict):
    method: Literal["user_credentials", "kubernetes_secret", "none"]
    username_env: str | None = None
    password_env: str | None = None
    kubernetes_secret_name: str | None = None

    @model_validator(mode="after")
    def _check(self) -> RegistryAuthSpec:
        if self.method == "user_credentials" and not (self.username_env and self.password_env):
            raise ValueError("user_credentials requires username_env and password_env")
        if self.method == "kubernetes_secret" and not self.kubernetes_secret_name:
            raise ValueError("kubernetes_secret requires kubernetes_secret_name")
        return self


class ContainerRegistrySpec(_Strict):
    name: str
    base_url: str
    description: str | None = None
    auth: RegistryAuthSpec
    tags: list[str] = Field(default_factory=list)


class InotifySpec(_Strict):
    instances: int = Field(default=8192, ge=128)
    watches: int = Field(default=524_288, ge=8192)


class KubernetesSpec(_Strict):
    name: str
    api_server: str
    kubeconfig: Path | None = None
    namespaces: list[str] = Field(default_factory=lambda: ["vast-dataengine"])
    zarf_package_path: Path | None = None
    zarf_init_path: Path | None = None
    inotify: InotifySpec = Field(default_factory=InotifySpec)
    ca_cert_path: Path | None = None
    client_cert_path: Path | None = None
    client_key_path: Path | None = None


class GroupSpec(_Strict):
    name: str
    gid: int = Field(ge=1)
    provider: str = "vast"


class UserSpec(_Strict):
    name: str
    uid: int = Field(ge=1)
    leading_group: str | None = None  # defaults to enablement.identity.group.name


class TenantAdminSpec(_Strict):
    """Optional VMS administrative user (manager) for the tenant.

    Required to call /dataengine/ end-to-end (cluster admin can't impersonate
    a tenant context). When set, `enable` creates a role + manager + sets the
    password, then uses these creds for the final DataEngine enable step.
    """
    username: str
    password_env: str  # name of env var holding the password
    role_name: str | None = None  # default: '<tenant>-admin-role'
    first_name: str = ""
    last_name: str = ""


class IdentitySpec(_Strict):
    group: GroupSpec
    users: list[UserSpec] = Field(default_factory=list)
    policy: Literal["assign_predefined", "custom"] = "assign_predefined"
    custom_statements: list[dict[str, object]] | None = None
    tenant_admin: TenantAdminSpec | None = None

    @model_validator(mode="after")
    def _check(self) -> IdentitySpec:
        if self.policy == "custom" and not self.custom_statements:
            raise ValueError("policy=custom requires custom_statements")
        return self


class SourceViewSpec(_Strict):
    path: str
    bucket: str
    owner: str
    policy: str


class EnablementSpec(_Strict):
    tenant: TenantSpec
    event_broker: EventBrokerSpec
    container_registry: ContainerRegistrySpec
    kubernetes: KubernetesSpec
    identity: IdentitySpec
    source_views: list[SourceViewSpec] = Field(default_factory=list)


# ── Pipelines (Stage B) ─────────────────────────────────────────────────────

class ScheduleSpec(_Strict):
    simple: str | None = None  # cron-like e.g. "0 2 * * *"
    advanced: str | None = None  # Quartz syntax

    @model_validator(mode="after")
    def _exactly_one(self) -> ScheduleSpec:
        if bool(self.simple) == bool(self.advanced):
            raise ValueError("schedule: provide exactly one of `simple` or `advanced`")
        return self


class ElementTriggerSpec(_Strict):
    name: str
    type: Literal["element"]
    source_view: str
    event_type: Literal[
        "ElementCreated", "ElementDeleted", "ElementTagCreated", "ElementTagDeleted"
    ]
    object_key_prefix: str | None = None
    object_key_suffix: str | None = None
    topic: str
    custom_extensions: dict[str, str] = Field(default_factory=dict)
    tags: dict[str, str] = Field(default_factory=dict)
    description: str | None = None

    @field_validator("custom_extensions")
    @classmethod
    def _ext_keys(cls, v: dict[str, str]) -> dict[str, str]:
        for k in v:
            if not (1 <= len(k) <= 20 and k[0].isalpha() and k.islower() and k.isalnum()):
                raise ValueError(
                    f"custom_extensions key '{k}' must be lowercase alnum, 1-20 chars,"
                    " starting with a letter"
                )
        return v


class ScheduleTriggerSpec(_Strict):
    name: str
    type: Literal["schedule"]
    schedule: ScheduleSpec
    topic: str
    custom_extensions: dict[str, str] = Field(default_factory=dict)
    tags: dict[str, str] = Field(default_factory=dict)
    description: str | None = None


TriggerSpec = ElementTriggerSpec | ScheduleTriggerSpec


class MinMax(_Strict):
    min: float = Field(ge=0)
    max: float = Field(gt=0)

    @model_validator(mode="after")
    def _order(self) -> MinMax:
        if self.min > self.max:
            raise ValueError(f"min ({self.min}) must be <= max ({self.max})")
        return self


class MinMaxStr(_Strict):
    """For memory/disk where values are strings like '512Mi', '2Gi'."""

    min: str
    max: str


class DeploymentSpec(_Strict):
    concurrency: MinMax = Field(default_factory=lambda: MinMax(min=0, max=10))
    cpu: MinMax = Field(default_factory=lambda: MinMax(min=0.25, max=1.0))
    memory: MinMaxStr = Field(default_factory=lambda: MinMaxStr(min="256Mi", max="1Gi"))
    autoscaling_rps_factor: float | None = None
    disk_ephemeral: str | None = None
    timeout_seconds: int = Field(default=60, ge=1, le=3600)
    retries: int = Field(default=0, ge=0, le=10)
    log_level: Literal["NOTSET", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    method_of_delivery: Literal["ordered", "unordered"] = "ordered"


class FunctionSpec(_Strict):
    name: str
    source: Path  # path to function source dir (used by `vastde functions init/build`)
    image: str  # registry base URL + path, e.g. registry.example.com/funcs/parse-pdf
    tag: str | None = None  # if None, computed as content hash
    description: str | None = None
    revision_alias: str | None = None
    deployment: DeploymentSpec = Field(default_factory=DeploymentSpec)
    secrets: dict[str, dict[str, str]] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)


class FlowEdge(_Strict):
    from_: str = Field(alias="from")
    to: str

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class PipelineSpec(_Strict):
    name: str
    description: str | None = None
    namespace: str = "vast-dataengine"
    k8s_cluster: str  # name of a registered k8s cluster (typically enablement.kubernetes.name)
    secrets: dict[str, dict[str, str]] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)
    triggers: list[TriggerSpec] = Field(default_factory=list)
    functions: list[FunctionSpec] = Field(default_factory=list)
    flow: list[FlowEdge] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_flow_references(self) -> PipelineSpec:
        names = {t.name for t in self.triggers} | {f.name for f in self.functions}
        function_names = {f.name for f in self.functions}
        for edge in self.flow:
            if edge.from_ not in names:
                raise ValueError(
                    f"pipeline {self.name!r}: flow edge from {edge.from_!r} references"
                    " unknown trigger/function"
                )
            if edge.to not in function_names:
                raise ValueError(
                    f"pipeline {self.name!r}: flow edge to {edge.to!r} must reference a function"
                    " (triggers cannot be targets)"
                )
        # cycle check via Kahn's algorithm
        outgoing: dict[str, list[str]] = {n: [] for n in function_names}
        indeg: dict[str, int] = {n: 0 for n in function_names}
        for e in self.flow:
            if e.from_ in function_names:
                outgoing[e.from_].append(e.to)
                indeg[e.to] += 1
        queue = [n for n, d in indeg.items() if d == 0]
        visited = 0
        while queue:
            n = queue.pop()
            visited += 1
            for m in outgoing[n]:
                indeg[m] -= 1
                if indeg[m] == 0:
                    queue.append(m)
        if visited != len(function_names):
            raise ValueError(f"pipeline {self.name!r}: flow contains a cycle")
        return self


# ── Root ────────────────────────────────────────────────────────────────────

class VastdeConfig(_Strict):
    vms: VmsSpec
    enablement: EnablementSpec | None = None
    pipelines: list[PipelineSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _cross_refs(self) -> VastdeConfig:
        if self.enablement is None:
            return self
        registered_k8s = {self.enablement.kubernetes.name}
        for p in self.pipelines:
            if p.k8s_cluster not in registered_k8s:
                raise ValueError(
                    f"pipeline {p.name!r}: k8s_cluster {p.k8s_cluster!r} not declared"
                    f" in enablement.kubernetes (known: {sorted(registered_k8s)})"
                )
        return self

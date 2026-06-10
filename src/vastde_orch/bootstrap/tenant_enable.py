"""Auto-discover tenant state, then run the full DataEngine enable flow.

Lets you skip the duplication that `vastde-orch enable` traditionally required.
If you've already run `vastde-orch tenant create`, the tenant + identity +
view policy + vippool all exist on VMS — there's no reason to re-declare them
in the enable YAML. This module queries VMS for the existing state and only
asks you for what `tenant create` doesn't know: K8s connection + container
registry.

Public surface:
  - load_tenant_enable_config(path)        env-interpolated YAML loader
  - discover_tenant_state(vms, ...)        returns DiscoveredTenant
  - compose_enablement_spec(...)           builds the full EnablementSpec
  - tenant_enable(cfg, vms, *, ...)        end-to-end entry point

Calls existing `vastde_orch.enablement.enable_dataengine` under the hood — no
parallel reconciliation logic.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vastde_orch.bootstrap.tenant import load_tenant_config as _load_yaml
from vastde_orch.clients.vms import VmsClient
from vastde_orch.config.models import (
    ContainerRegistrySpec,
    EnablementSpec,
    GroupSpec,
    IdentitySpec,
    KubernetesSpec,
    RegistryAuthSpec,
    TenantAdminSpec,
    TenantSpec,
    TopicSpec,
    UserSpec,
    VastEventBrokerSpec,
    VipPoolSpec,
)
from vastde_orch.enablement.enable import enable_dataengine

# Re-export the loader so the CLI can use a single import.
load_tenant_enable_config = _load_yaml


# ── discovery ─────────────────────────────────────────────────────────────

@dataclass
class DiscoveredTenant:
    tenant_id: int
    local_provider_id: int
    group_name: str
    group_gid: int
    bucket_owner_name: str
    bucket_owner_uid: int
    view_policy_name: str          # S3_NATIVE policy (the broker's policy)
    vip_pool_name: str
    vip_pool_cidr: str
    vip_pool_start: str
    vip_pool_end: str
    tenant_admin_username: str | None  # may be None if no manager exists yet


def _err(msg: str) -> None:
    sys.exit(f"FATAL: {msg}")


def discover_tenant_state(
    vms: VmsClient,
    tenant_name: str,
    *,
    vip_pool_name: str | None = None,
    group_name: str | None = None,
    bucket_owner_name: str | None = None,
) -> DiscoveredTenant:
    """Query VMS to find everything `tenant create` left behind.

    Overrides may be passed for any field where auto-discovery is ambiguous
    (e.g. multiple PROTOCOLS vippools on the tenant).
    """
    # Tenant
    tenant_record = next(
        (t for t in vms.raw.tenants.get() if t.get("name") == tenant_name), None,
    )
    if not tenant_record:
        _err(f"tenant {tenant_name!r} not found on VMS. Run `vastde-orch tenant "
             f"create -c <cfg>` first, or check the address/credentials.")
    tenant_id = tenant_record["id"]
    local_provider_id = tenant_record.get("local_provider_id")
    if not local_provider_id or local_provider_id == 1:
        _err(f"tenant {tenant_name!r} has no dedicated local provider "
             f"(local_provider_id={local_provider_id}). Did `tenant create` succeed?")

    # Group on the tenant's local provider
    groups_on_tenant = [
        g for g in vms.raw.groups.get()
        if (g.get("local_provider") or {}).get("id") == local_provider_id
    ]
    if group_name:
        grp = next((g for g in groups_on_tenant if g.get("name") == group_name), None)
        if not grp:
            _err(f"group {group_name!r} not found on tenant {tenant_name!r}")
    elif len(groups_on_tenant) == 1:
        grp = groups_on_tenant[0]
    elif not groups_on_tenant:
        _err(f"no groups on tenant {tenant_name!r}'s local provider — "
             f"did `tenant create` complete?")
    else:
        names = [g.get("name") for g in groups_on_tenant]
        _err(f"multiple groups on tenant {tenant_name!r}: {names}. "
             f"Specify `group_name:` in the config.")

    # Bucket-owner user
    users_on_tenant = [
        u for u in vms.raw.users.get()
        if (u.get("local_provider") or {}).get("id") == local_provider_id
    ]
    # VAST creates system users on DE-enabled tenants ('dataengine',
    # 'dataengine-collector', 'telemetries-collector-...') that also have
    # allow_create_bucket=true. Exclude them from auto-discovery so we don't
    # pick the wrong owner; user can still override via bucket_owner_name.
    _SYSTEM_USER_PREFIXES = ("dataengine", "telemetries-collector-")
    if bucket_owner_name:
        owner = next((u for u in users_on_tenant if u.get("name") == bucket_owner_name), None)
        if not owner:
            _err(f"bucket-owner user {bucket_owner_name!r} not found")
    else:
        owners = [
            u for u in users_on_tenant
            if u.get("allow_create_bucket")
            and not any((u.get("name") or "").startswith(p) for p in _SYSTEM_USER_PREFIXES)
        ]
        if len(owners) == 1:
            owner = owners[0]
        elif not owners:
            _err(f"no non-system user with allow_create_bucket=true on tenant "
                 f"{tenant_name!r}. Did `tenant create` step 3 complete?")
        else:
            names = [u.get("name") for u in owners]
            _err(f"multiple bucket-owner candidates on tenant {tenant_name!r}: "
                 f"{names}. Specify `bucket_owner_name:` in the config.")

    # S3_NATIVE view policy (the broker's policy)
    s3_native_policies = [
        p for p in vms.raw.viewpolicies.get()
        if p.get("tenant_id") == tenant_id and p.get("flavor") == "S3_NATIVE"
    ]
    if not s3_native_policies:
        _err(f"no S3_NATIVE view policy on tenant {tenant_name!r}. "
             f"`tenant create` step 7b should have created one.")
    view_policy = s3_native_policies[0]

    # VIP pool — prefer PROTOCOLS role
    vippools_on_tenant = [
        p for p in vms.raw.vippools.get() if p.get("tenant_id") == tenant_id
    ]
    if vip_pool_name:
        pool = next((p for p in vippools_on_tenant if p.get("name") == vip_pool_name), None)
        if not pool:
            _err(f"vippool {vip_pool_name!r} not found on tenant {tenant_name!r}")
    else:
        protocols_pools = [p for p in vippools_on_tenant if p.get("role") == "PROTOCOLS"]
        if len(protocols_pools) == 1:
            pool = protocols_pools[0]
        elif not protocols_pools:
            _err(f"no PROTOCOLS vippool on tenant {tenant_name!r}. Add a "
                 f"`vip_pool:` block to your tenant-setup YAML and re-run "
                 f"`tenant create`, or pass `vip_pool_name:` here.")
        else:
            names = [p.get("name") for p in protocols_pools]
            _err(f"multiple PROTOCOLS vippools on tenant {tenant_name!r}: {names}. "
                 f"Specify `vip_pool_name:` in the config.")
    ranges = pool.get("ip_ranges") or []
    if not ranges or len(ranges[0]) < 2:
        _err(f"vippool {pool.get('name')!r} has no ip_ranges set — can't continue.")

    # Tenant-admin manager (used for the /dataengine/ enable call later)
    mgr = next(
        (m for m in vms.raw.managers.get()
         if m.get("tenant_id") == tenant_id and m.get("user_type") == "TENANT_ADMIN"),
        None,
    )
    tenant_admin_username = mgr.get("username") if mgr else None

    return DiscoveredTenant(
        tenant_id=tenant_id,
        local_provider_id=local_provider_id,
        group_name=grp["name"],
        group_gid=grp.get("gid") or 0,
        bucket_owner_name=owner["name"],
        bucket_owner_uid=owner.get("uid") or 0,
        view_policy_name=view_policy["name"],
        vip_pool_name=pool["name"],
        vip_pool_cidr=str(pool.get("subnet_cidr", "24")),
        vip_pool_start=str(ranges[0][0]),
        vip_pool_end=str(ranges[0][1]),
        tenant_admin_username=tenant_admin_username,
    )


# ── compose ───────────────────────────────────────────────────────────────

def compose_enablement_spec(
    minimal_cfg: dict[str, Any], d: DiscoveredTenant,
) -> EnablementSpec:
    """Build a full EnablementSpec from the minimal config + discovered state."""
    tenant_name = minimal_cfg["vms"]["tenant"]

    # broker_view / topics — derive from tenant name, allow overrides
    bv = minimal_cfg.get("broker_view") or {}
    view_path = bv.get("path") or f"/{tenant_name}/de-broker"
    bucket_name = bv.get("bucket") or f"{tenant_name}-de-broker"

    topics = minimal_cfg.get("topics") or {}
    default_t = topics.get("default") or {}
    dead_t = topics.get("dead_letter") or {}
    default_topic = TopicSpec(
        name=default_t.get("name") or f"{tenant_name}-default",
        partitions=default_t.get("partitions", 16),
        retention_hours=default_t.get("retention_hours", 24),
    )
    deadletter_topic = TopicSpec(
        name=dead_t.get("name") or f"{tenant_name}-dlq",
        partitions=dead_t.get("partitions", 4),
        retention_hours=dead_t.get("retention_hours", 24),
    )

    # K8s + registry — straight passthrough
    k8s = minimal_cfg["kubernetes"]
    kubernetes = KubernetesSpec(**{k: v for k, v in k8s.items() if v is not None})

    reg = minimal_cfg["container_registry"]
    registry = ContainerRegistrySpec(
        name=reg["name"],
        base_url=reg["base_url"],
        auth=RegistryAuthSpec(**reg["auth"]),
        description=reg.get("description"),
        tags=reg.get("tags") or [],
    )

    # Tenant-admin: prefer explicit YAML, else use discovered
    ta_cfg = minimal_cfg.get("tenant_admin") or {}
    tenant_admin: TenantAdminSpec | None = None
    if ta_cfg.get("username") or d.tenant_admin_username:
        tenant_admin = TenantAdminSpec(
            username=ta_cfg.get("username") or d.tenant_admin_username,
            password_env=ta_cfg.get("password_env", "TENANT_ADMIN_PASSWORD"),
            role_name=ta_cfg.get("role_name"),
            first_name=ta_cfg.get("first_name", ""),
            last_name=ta_cfg.get("last_name", ""),
        )

    return EnablementSpec(
        tenant=TenantSpec(name=tenant_name, create_if_missing=False),
        kubernetes=kubernetes,
        container_registry=registry,
        event_broker=VastEventBrokerSpec(
            view_path=view_path,
            bucket_name=bucket_name,
            bucket_owner=d.bucket_owner_name,
            view_policy=d.view_policy_name,
            vip_pool=VipPoolSpec(
                name=d.vip_pool_name,
                cidr=d.vip_pool_cidr,
                ip_range=[d.vip_pool_start, d.vip_pool_end],
            ),
            default_topic=default_topic,
            deadletter_topic=deadletter_topic,
        ),
        identity=IdentitySpec(
            group=GroupSpec(name=d.group_name, gid=d.group_gid),
            users=[UserSpec(name=d.bucket_owner_name, uid=d.bucket_owner_uid)],
            policy="assign_predefined",
            tenant_admin=tenant_admin,
        ),
    )


# ── entry point ───────────────────────────────────────────────────────────

def tenant_enable(
    minimal_cfg: dict[str, Any], vms: VmsClient,
    *, skip_k8s_bootstrap: bool = True, skip_preflight: bool = False,
) -> int:
    """End-to-end: discover → compose → enable_dataengine. Returns exit code."""
    tenant_name = minimal_cfg["vms"]["tenant"]
    dep_cfg = minimal_cfg.get("dataengine_policy") or {}

    print(f"\n── Discovering existing state for tenant {tenant_name!r} ──")
    discovered = discover_tenant_state(
        vms, tenant_name,
        vip_pool_name=minimal_cfg.get("vip_pool_name"),
        group_name=dep_cfg.get("group_name"),
        bucket_owner_name=dep_cfg.get("bucket_owner_name"),
    )
    print(f"  tenant_id           {discovered.tenant_id}")
    print(f"  local_provider_id   {discovered.local_provider_id}")
    print(f"  group               {discovered.group_name} (gid={discovered.group_gid})")
    print(f"  bucket_owner        {discovered.bucket_owner_name} (uid={discovered.bucket_owner_uid})")
    print(f"  view_policy         {discovered.view_policy_name}")
    print(f"  vip_pool            {discovered.vip_pool_name} "
          f"({discovered.vip_pool_start}-{discovered.vip_pool_end})")
    print(f"  tenant_admin        {discovered.tenant_admin_username or '(none — set in YAML)'}")

    spec = compose_enablement_spec(minimal_cfg, discovered)

    plan = enable_dataengine(
        vms, spec,
        skip_preflight=skip_preflight,
        skip_k8s_bootstrap=skip_k8s_bootstrap,
        dry_run=vms._dry_run,
    )
    print(f"\n{'='*60}")
    label = "DRY-RUN" if vms._dry_run else "APPLIED"
    print(f"{label}: enable on tenant {tenant_name}")
    print(f"{'='*60}\n")
    plan.render()
    return 0

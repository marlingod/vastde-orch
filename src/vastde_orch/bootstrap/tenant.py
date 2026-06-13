"""Tenant bootstrap: create or destroy the prerequisites VAST DataEngine needs.

Cluster-admin operations. Implements the "Configure Prerequisites on the VAST
Cluster" section of the VAST DataEngine docs + the official identity-policy
flow from the KB "Provisioning User Access and Permissions for DataEngine"
(committed at docs/provision-user.pdf).

Public surface:
  - load_tenant_config(path)   YAML loader with ${ENV_VAR} interpolation
  - create_tenant(cfg, vms)    9-step create (idempotent)
  - destroy_tenant(cfg, vms)   strict-inverse destroy (idempotent + safety preflight)

Both create + destroy honor `vms._dry_run` — no separate `plan` arg needed.

10 steps (create order):
    1. Tenant            (name + optional domain)
    2. Group             (gid; tenant-scoped via local_provider_id)
    3. Bucket-owner user (uid, allow_create_bucket, leading_group)
    4. Tenant-admin role (defaults to "<tenant>-admin-role")
    5. Tenant-admin manager (name defaults to "<tenant>-admin",
                             password from cfg or $TENANT_ADMIN_PASSWORD)
    6. VIP pool          (optional; auto-picks an unclaimed range if needed)
    7. View policies     (NFS + S3)
    8. Assign DE group to tenant  (PATCH `application_users_group_name` — the
                          REST equivalent of the Web UI checkbox "Assign Group
                          to DataEngine role". Must precede `tenant enable` so
                          setup-provisioning auto-creates `data-engine-<tenant>`)
    9. DataEngine identity policy + group binding  (REQUIRED — step 8 alone
                          does NOT trigger VMS to auto-create the documented
                          `data-engine-<tenant>` policy; verified on
                          usc-tenant 2026-06-10. This is the only thing
                          binding write permissions to the DE group.)
   10. (Opt-in) Bind to AllowAllTabular  (broader S3+Kafka access)
"""

from __future__ import annotations

import ipaddress
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from vastde_orch.clients.vms import VmsClient
from vastde_orch.enablement.identity import build_dataengine_policy_doc
from vastde_orch.reconciler import Plan
from vastde_orch.vippool_planner import (
    claimed_per_subnet,
    format_range,
    free_ranges_in_subnet,
    is_range_available,
    pick_gap,
)


# ── env interpolation ──────────────────────────────────────────────────────

_ENV_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


def _interpolate(value: Any) -> Any:
    """Replace ${VAR} with os.environ[VAR]; recurse into dicts/lists."""
    if isinstance(value, str):
        def repl(m: re.Match[str]) -> str:
            var = m.group(1)
            if var not in os.environ:
                sys.exit(f"FATAL: env var {var!r} referenced in config but not set")
            return os.environ[var]
        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    return value


def load_tenant_config(path: Path | str) -> dict[str, Any]:
    """Load + env-interpolate a tenant-setup YAML."""
    p = Path(path)
    if not p.is_file():
        sys.exit(f"config file not found: {p}")
    return _interpolate(yaml.safe_load(p.read_text()))


# ── create ────────────────────────────────────────────────────────────────

def create_tenant(cfg: dict[str, Any], vms: VmsClient) -> int:
    """Run the 9-step create flow. Returns shell exit code (0 = success)."""
    plan_mode = vms._dry_run
    plan = Plan()

    # ── 1. Tenant ──────────────────────────────────────────────────────────
    t = cfg["tenant"]
    print(f"\n── 1. Tenant {t['name']!r} ──")
    out = vms.ensure_tenant(t["name"], domain=t.get("domain"))
    plan.record(out)
    print(f"  {out.result.value}: {out.resource}/{out.name}")

    # Resolve tenant_id AND the tenant's auto-created local_provider_id.
    # VAST scopes users/groups to a tenant via local_provider_id (NOT via a
    # tenant_id field on the user/group). When a tenant is created, VMS
    # auto-creates a matching local provider (e.g. `provider-ca-tenant`) and
    # records its id on the tenant under `local_provider_id`. We MUST use
    # this id when creating the tenant's group + bucket-owner user — otherwise
    # they end up on the cluster `default` provider (id=1) and VMS lookups
    # like `bucket_owner=ca-de-owner under tenant_guid=...` will 404.
    # YAML `identity.group.local_provider_id` overrides if explicitly set.
    tenant_record = vms.get_or_placeholder(
        "tenants", key_field="name", key_value=t["name"], placeholder_id=0,
    )
    tenant_id = out.id or tenant_record["id"]
    tenant_provider_id = tenant_record.get("local_provider_id") or 1
    if tenant_provider_id == 1:
        print(
            "  WARN: tenant has no dedicated local provider — falling back to "
            "default (id=1). Users/groups won't be tenant-scoped."
        )
    else:
        prov_name = tenant_record.get("local_provider_title", f"id={tenant_provider_id}")
        print(f"  → tenant local provider: {prov_name} (id={tenant_provider_id})")

    # ── 2. Group ───────────────────────────────────────────────────────────
    g = cfg["identity"]["group"]
    group_provider_id = g.get("local_provider_id", tenant_provider_id)
    print(f"\n── 2. Group {g['name']!r} (gid={g['gid']}, "
          f"local_provider_id={group_provider_id}) ──")
    out = vms.ensure_group(
        g["name"],
        gid=g["gid"],
        provider="vast",
        local_provider_id=group_provider_id,
    )
    plan.record(out)
    print(f"  {out.result.value}: {out.resource}/{out.name}")

    # ── 3. Bucket-owner user ───────────────────────────────────────────────
    u = cfg["identity"]["bucket_owner"]
    user_provider_id = u.get("local_provider_id", tenant_provider_id)
    print(f"\n── 3. Bucket-owner user {u['name']!r} (uid={u['uid']}, "
          f"local_provider_id={user_provider_id}) ──")
    out = vms.ensure_user(
        u["name"],
        uid=u["uid"],
        provider="vast",
        leading_group=g["name"],
        local_provider_id=user_provider_id,
        allow_create_bucket=u.get("allow_create_bucket", True),
        allow_delete_bucket=u.get("allow_delete_bucket", True),
    )
    plan.record(out)
    print(f"  {out.result.value}: {out.resource}/{out.name}")

    # ── 4. Tenant-admin role (always created) ──────────────────────────────
    ta = cfg.get("tenant_admin") or {}
    admin_name = ta.get("name") or f"{t['name']}-admin"
    role_name = ta.get("role_name") or f"{t['name']}-admin-role"
    print(f"\n── 4. Tenant-admin role {role_name!r} ──")
    out = vms.ensure_role(role_name, tenant_id=tenant_id)
    plan.record(out)
    print(f"  {out.result.value}: {out.resource}/{out.name}")

    role_id = out.id or vms.get_or_placeholder(
        "roles", key_field="name", key_value=role_name, placeholder_id=0,
    )["id"]

    # ── 5. Tenant-admin manager (always created) ───────────────────────────
    # Password resolution order: explicit YAML → TENANT_ADMIN_PASSWORD env var
    password = ta.get("password") or os.environ.get("TENANT_ADMIN_PASSWORD")
    if not password:
        sys.exit(
            "FATAL: tenant_admin password not set.\n"
            "  Either put 'password: ${TENANT_ADMIN_PASSWORD}' under tenant_admin "
            "in the YAML, or set $TENANT_ADMIN_PASSWORD in the environment."
        )

    print(f"\n── 5. Tenant-admin manager {admin_name!r} ──")
    out = vms.ensure_manager(
        admin_name,
        tenant_id=tenant_id,
        password=password,
        user_type="TENANT_ADMIN",
        role_ids=[role_id] if role_id else None,
        first_name=ta.get("first_name", t["name"]),
        last_name=ta.get("last_name", "admin"),
    )
    plan.record(out)
    print(f"  {out.result.value}: {out.resource}/{out.name}")

    # ── 6. VIP pool (optional, with smart range allocation) ────────────────
    if "vip_pool" in cfg:
        vp = cfg["vip_pool"]
        try:
            subnet = ipaddress.ip_network(vp["cidr"], strict=False)
        except ValueError as exc:
            sys.exit(f"FATAL: vip_pool.cidr {vp['cidr']!r} is not a valid CIDR: {exc}")
        if not isinstance(subnet, ipaddress.IPv4Network):
            sys.exit("FATAL: vip_pool.cidr must be an IPv4 subnet (IPv6 not supported)")

        existing = list(vms.raw.vippools.get())
        existing_named = next(
            (p for p in existing if p.get("name") == vp["name"]), None
        )
        claims_here = [
            c for c in claimed_per_subnet(existing).get(subnet, [])
            if c.pool_name != vp["name"]
        ]
        free = free_ranges_in_subnet(subnet, claims_here)

        requested = vp.get("ip_range") or {}
        chosen_start: ipaddress.IPv4Address | None = None
        chosen_end: ipaddress.IPv4Address | None = None
        source = ""

        if not requested.get("start") and existing_named:
            current = existing_named.get("ip_ranges") or []
            if current:
                chosen_start = ipaddress.IPv4Address(current[0][0])
                chosen_end = ipaddress.IPv4Address(current[0][1])
                source = "existing pool (range preserved)"

        if chosen_start is None and requested.get("start") and requested.get("end"):
            try:
                rs = ipaddress.IPv4Address(requested["start"])
                re_ = ipaddress.IPv4Address(requested["end"])
            except ValueError as exc:
                sys.exit(f"FATAL: vip_pool.ip_range invalid: {exc}")
            if is_range_available(rs, re_, free):
                chosen_start, chosen_end = rs, re_
                source = "requested (available)"
            else:
                print(
                    f"  WARN: requested range {format_range(rs, re_)} is not free in "
                    f"{subnet}; falling back to auto-pick"
                )

        if chosen_start is None:
            size = int(vp.get("default_size", 3))
            picked = pick_gap(free, size)
            if picked is None:
                sys.exit(
                    f"FATAL: no free gap of {size} IP(s) in {subnet}. "
                    f"Free gaps available: "
                    f"{[format_range(f.start, f.end) for f in free] or 'none'}. "
                    "Pick a different subnet or run `vastde-orch tenant list-vippools`."
                )
            chosen_start, chosen_end = picked
            source = f"auto-picked (size {size})"

        size = int(chosen_end) - int(chosen_start) + 1
        print(
            f"\n── 6. VIP pool {vp['name']!r} ── "
            f"{format_range(chosen_start, chosen_end)} "
            f"in {subnet} [{source}, {size} IP{'s' if size != 1 else ''}]"
        )
        out = vms.ensure_vippool(
            vp["name"],
            tenant_id=tenant_id,
            cidr=vp["cidr"],
            ip_range_start=str(chosen_start),
            ip_range_end=str(chosen_end),
            role=vp.get("role", "PROTOCOLS"),
            domain_name=vp.get("domain_name"),
        )
        plan.record(out)
        print(f"  {out.result.value}: {out.resource}/{out.name}")

    # ── 7. View policies (NFS + S3, always created) ────────────────────────
    vp_cfg = cfg.get("view_policies") or {}
    nfs_cfg = vp_cfg.get("nfs") or {}
    s3_cfg = vp_cfg.get("s3") or {}
    nfs_policy_name = nfs_cfg.get("name") or f"{t['name']}-nfs-policy"
    s3_policy_name = s3_cfg.get("name") or f"{t['name']}-s3-policy"

    print(f"\n── 7a. NFS view policy {nfs_policy_name!r} ──")
    out = vms.ensure_viewpolicy(
        nfs_policy_name,
        tenant_id=tenant_id,
        security_flavor=nfs_cfg.get("flavor", "NFS"),
    )
    plan.record(out)
    print(f"  {out.result.value}: {out.resource}/{out.name}")

    print(f"\n── 7b. S3 view policy {s3_policy_name!r} ──")
    out = vms.ensure_viewpolicy(
        s3_policy_name,
        tenant_id=tenant_id,
        security_flavor=s3_cfg.get("flavor", "S3_NATIVE"),
    )
    plan.record(out)
    print(f"  {out.result.value}: {out.resource}/{out.name}")

    # ── 8. Assign DE group to tenant (the DOCUMENTED path) ────────────────
    # Per the VAST KB ("Provisioning User Access and Permissions for
    # DataEngine", docs/provision-user.pdf p.5), the Web UI checkbox
    # "Assign Group to DataEngine role" sets `application_users_group_name`
    # on the tenant record. When `setup-provisioning` then enables DataEngine,
    # VAST auto-creates the `data-engine-<tenant>` identity policy and binds
    # it to that group. This PATCH is the REST equivalent of that checkbox.
    #
    # Discovery (var203 2026-06-07): wi-tenant (has the auto-policy) has
    # `application_users_group_name='wi-group'`; ca-tenant (no auto-policy)
    # had it empty. PATCHing the field is accepted with cluster-admin creds,
    # but the auto-policy creation only happens when setup-provisioning runs
    # AFTER the field is set — so this step MUST come before `tenant enable`.
    # Catalog point #13: no other PATCH knob on /tenants/ triggers DE state.
    de_group_name = (cfg.get("dataengine_policy") or {}).get("group") or g["name"]
    print(f"\n── 8. Assign DE group {de_group_name!r} to tenant "
          f"(application_users_group_name) ──")
    current_assigned = tenant_record.get("application_users_group_name") or ""
    if current_assigned == de_group_name:
        print(f"  unchanged: tenants/{t['name']}.application_users_group_name "
              f"already = {de_group_name!r}")
    elif plan_mode:
        print(f"  would_update: tenants/{t['name']}.application_users_group_name "
              f"{current_assigned!r} → {de_group_name!r}")
    else:
        vms.raw.tenants[tenant_id].patch(application_users_group_name=de_group_name)
        print(f"  updated: tenants/{t['name']}.application_users_group_name = {de_group_name!r}")

    # ── 9. DataEngine identity policy + group binding (REQUIRED) ──────────
    # Verified live on usc-tenant 2026-06-10: step 8's
    # `application_users_group_name` PATCH alone does NOT trigger VMS to
    # auto-create the `data-engine-<tenant>` policy at `tenant enable` time.
    # The Web UI's "Assign DataEngine identity policy to group" checkbox
    # does something additional we still haven't reverse-engineered from REST.
    #
    # Result on usc-tenant after full create + enable:
    #   AllowAllTabular      groups=[]                users=['dataengine']
    #   usc-tenant-de-write  groups=['usc-de-users']                       ← THIS step
    #   (data-engine-usc-tenant does NOT exist)
    #
    # So this step is REQUIRED — it's the only thing binding write
    # (CreateTrigger/Function/Pipeline) permissions to the DE group.
    # Without it, application users get 403 on every DE action.
    #
    # The doc's recommended name is `data-engine-<tenant>` — RESERVED by VMS
    # (POST returns 403). So this uses `<tenant>-de-write` instead, same
    # policy document from build_dataengine_policy_doc() (matches KB verbatim).
    #
    # Per the KB note on p.7: Create* implicitly grants Update/Get/Delete on
    # resources the user CREATED, so Create*-only is sufficient.
    #
    # Binding: VMS makes /s3policies/{id}/.groups read-only — PATCHing it
    # silently no-ops. Bind from the GROUP side via
    # /groups/{id}/.s3_policies_ids. Verified live on var203 2026-06-07.
    dep_cfg = cfg.get("dataengine_policy") or {}
    dep_group = dep_cfg.get("group") or g["name"]
    write_name = (dep_cfg.get("name") or dep_cfg.get("write_policy_name")
                  or f"{t['name']}-de-write")
    print(f"\n── 9. DataEngine identity policy {write_name!r} + bind to "
          f"{dep_group!r} (REQUIRED — see comment above) ──")
    pol_matches = [
        p for p in vms.raw.s3policies.get()
        if p.get("tenant_id") == tenant_id and p.get("name") == write_name
    ]
    if pol_matches:
        write_pol_id = pol_matches[0]["id"]
        print(f"  unchanged: s3policies/{write_name} (id={write_pol_id}) already exists")
    elif plan_mode:
        write_pol_id = None
        print(f"  would_create: s3policies/{write_name} "
              f"(tenant_id={tenant_id}, actions=CreateTrigger/Function/Pipeline)")
    else:
        created = vms.raw.s3policies.post(
            name=write_name,
            tenant_id=tenant_id,
            enabled=True,
            policy=build_dataengine_policy_doc(),
        )
        write_pol_id = created["id"]
        print(f"  created: s3policies/{write_name} (id={write_pol_id})")

    if write_pol_id is not None:
        group_record = next(
            (gr for gr in vms.raw.groups.get() if gr.get("name") == dep_group),
            None,
        )
        if group_record:
            existing_ids = group_record.get("s3_policies_ids") or []
            if write_pol_id in existing_ids:
                print(f"  unchanged: group {dep_group} already bound to {write_name}")
            else:
                new_ids = existing_ids + [write_pol_id]
                if plan_mode:
                    print(f"  would_update: groups/{dep_group}.s3_policies_ids "
                          f"{existing_ids} → {new_ids}  (binding {write_name})")
                else:
                    vms.raw.groups[group_record["id"]].patch(s3_policies_ids=new_ids)
                    print(f"  updated: groups/{dep_group}.s3_policies_ids = {new_ids}")

    # ── 10. (Optional) Attach AllowAllTabular ─────────────────────────────
    if dep_cfg.get("attach_allow_all_tabular", False):
        aat_name = dep_cfg.get("allow_all_tabular_name", "AllowAllTabular")
        print(f"\n── 10. (opt-in) Bind {dep_group!r} to {aat_name!r} ──")
        aat_matches = [
            p for p in vms.raw.s3policies.get()
            if p.get("tenant_id") == tenant_id and p.get("name") == aat_name
        ]
        if not aat_matches:
            print(f"  skipped: s3policies/{aat_name} not found "
                  "(it's auto-created by VAST when DE is enabled via Web UI)")
        else:
            aat_id = aat_matches[0]["id"]
            group_record = next(
                (gr for gr in vms.raw.groups.get() if gr.get("name") == dep_group),
                None,
            )
            if group_record:
                existing_ids = group_record.get("s3_policies_ids") or []
                if aat_id in existing_ids:
                    print(f"  unchanged: group {dep_group} already bound to {aat_name}")
                else:
                    new_ids = existing_ids + [aat_id]
                    if plan_mode:
                        print(f"  would_update: groups/{dep_group}.s3_policies_ids "
                              f"{existing_ids} → {new_ids}")
                    else:
                        vms.raw.groups[group_record["id"]].patch(s3_policies_ids=new_ids)
                        print(f"  updated: groups/{dep_group}.s3_policies_ids = {new_ids}")

    # ── Summary ────────────────────────────────────────────────────────────
    _scrub_secrets(plan)
    print(f"\n{'='*60}")
    label = "DRY-RUN" if plan_mode else "APPLIED"
    print(f"{label}: {len(plan.outcomes)} outcome(s) on {t['name']}")
    print(f"{'='*60}\n")
    plan.render()
    return 0


# ── destroy ────────────────────────────────────────────────────────────────

def destroy_tenant(cfg: dict[str, Any], vms: VmsClient, *, yes: bool = False) -> int:
    """Tear down everything create_tenant created, in REVERSE dependency order.

    Strictly inverse of the create flow — does NOT disable DataEngine, does
    NOT delete the K8s cluster / container registry registrations, and does
    NOT delete views created by `vastde-orch enable` (those reference the
    view policies we'd otherwise delete; we surface a warning and stop).

    Each step is idempotent: if the resource isn't found, it's skipped.

    Args:
        yes: skip the interactive "type 'destroy' to confirm" prompt.
             Ignored in dry-run mode (which never prompts).
    """
    plan_mode = vms._dry_run

    t = cfg["tenant"]
    g = cfg["identity"]["group"]
    u = cfg["identity"]["bucket_owner"]
    ta = cfg.get("tenant_admin") or {}
    admin_name = ta.get("name") or f"{t['name']}-admin"
    role_name = ta.get("role_name") or f"{t['name']}-admin-role"
    nfs_cfg = (cfg.get("view_policies") or {}).get("nfs") or {}
    s3_cfg = (cfg.get("view_policies") or {}).get("s3") or {}
    nfs_policy_name = nfs_cfg.get("name") or f"{t['name']}-nfs-policy"
    s3_policy_name = s3_cfg.get("name") or f"{t['name']}-s3-policy"
    vp = cfg.get("vip_pool")
    dep_cfg = cfg.get("dataengine_policy") or {}
    dep_group = dep_cfg.get("group") or g["name"]
    write_name = (dep_cfg.get("name") or dep_cfg.get("write_policy_name")
                  or f"{t['name']}-de-write")
    aat_name = dep_cfg.get("allow_all_tabular_name", "AllowAllTabular")

    tenant_record = next(
        (tr for tr in vms.raw.tenants.get() if tr.get("name") == t["name"]),
        None,
    )
    if not tenant_record:
        # Tenant gone, but the auto-created local provider may have survived
        # a prior partial destroy. Reap it if found by name so a subsequent
        # `tenant create` doesn't 400 with "local provider with this name
        # already exists." Skip provider id=1 (the cluster default).
        print(f"Tenant {t['name']!r} not found — checking for orphan local provider.")
        _reap_local_provider(vms, name=t["name"], plan_mode=plan_mode)
        return 0
    tenant_id = tenant_record["id"]
    tenant_local_provider_id = tenant_record.get("local_provider_id")

    # Pre-flight: refuse if a broker view exists on the tenant (would orphan or
    # block the view-policy deletes; that's `vastde-orch destroy`'s job).
    blocking_views = [
        v for v in vms.raw.views.get()
        if v.get("tenant_id") == tenant_id and v.get("policy_id") in [
            p["id"] for p in vms.raw.viewpolicies.get()
            if p.get("name") in (s3_policy_name, nfs_policy_name)
        ]
    ]
    if blocking_views:
        symbol = "⚠ " if plan_mode else "⛔ "
        verb = "Live destroy would FAIL" if plan_mode else "Refusing to destroy"
        print(
            f"\n{symbol}{verb}: {len(blocking_views)} view(s) on tenant "
            f"{t['name']!r} still reference the view policies "
            f"({s3_policy_name!r} / {nfs_policy_name!r}):"
        )
        for v in blocking_views:
            print(f"   - {v.get('path')}")
        print(
            "Delete those views first (typically created by `vastde-orch enable`).\n"
            "Run `vastde-orch destroy -c <cfg> --include-enablement` to clear them.\n"
        )
        if not plan_mode:
            return 2

    # Confirmation (unless --plan or --yes)
    if not plan_mode and not yes:
        print(f"\nThis will DELETE (in reverse dependency order):")
        print(f"  tenant            {t['name']}")
        print(f"  group             {g['name']}")
        print(f"  user              {u['name']}")
        print(f"  role              {role_name}")
        print(f"  manager           {admin_name}")
        if vp:
            print(f"  vippool           {vp['name']}")
        print(f"  viewpolicies      {nfs_policy_name}, {s3_policy_name}")
        print(f"  s3policy          {write_name}  (DE write policy)")
        print(f"  group binding     {dep_group} → {write_name} (and {aat_name} if bound)")
        print(f"  local provider    auto-reaped after tenant delete (avoids re-create 400)")
        if input("\nProceed? type 'destroy' to confirm: ").strip() != "destroy":
            print("Aborted.")
            return 1

    plan = Plan()

    # ── 9'/8'. Unbind group from AllowAllTabular + DE write policy ─────────
    print(f"\n── 9'/8'. Unbind {dep_group!r} from DE policies ──")
    group_record = next(
        (gr for gr in vms.raw.groups.get() if gr.get("name") == dep_group),
        None,
    )
    if group_record:
        existing_ids = group_record.get("s3_policies_ids") or []
        pol_ids_to_remove = {
            pol["id"] for pol in vms.raw.s3policies.get()
            if pol.get("tenant_id") == tenant_id
            and pol.get("name") in (write_name, aat_name)
        }
        new_ids = [pid for pid in existing_ids if pid not in pol_ids_to_remove]
        if new_ids == existing_ids:
            print(f"  unchanged: no DE policies bound to {dep_group}")
        else:
            if plan_mode:
                print(f"  would_update: groups/{dep_group}.s3_policies_ids "
                      f"{existing_ids} → {new_ids}")
            else:
                vms.raw.groups[group_record["id"]].patch(s3_policies_ids=new_ids)
                print(f"  updated: groups/{dep_group}.s3_policies_ids = {new_ids}")
    else:
        print(f"  skipped: group {dep_group!r} not found")

    # ── 8'. Delete the DE write policy ─────────────────────────────────────
    print(f"\n── 8'. Delete DE write policy {write_name!r} ──")
    out = vms.delete("s3policies", key_field="name", key_value=write_name)
    plan.record(out)
    print(f"  {out.result.value}: {out.resource}/{out.name}")

    # ── 7'. Delete view policies (S3 first, then NFS) ──────────────────────
    for vp_name in (s3_policy_name, nfs_policy_name):
        print(f"\n── 7'. Delete view policy {vp_name!r} ──")
        out = vms.delete("viewpolicies", key_field="name", key_value=vp_name)
        plan.record(out)
        print(f"  {out.result.value}: {out.resource}/{out.name}")

    # ── 6'. Delete VIP pool ───────────────────────────────────────────────
    if vp:
        print(f"\n── 6'. Delete VIP pool {vp['name']!r} ──")
        out = vms.delete("vippools", key_field="name", key_value=vp["name"])
        plan.record(out)
        print(f"  {out.result.value}: {out.resource}/{out.name}")

    # ── 5'. Delete manager (before role, since manager references it) ──────
    print(f"\n── 5'. Delete manager {admin_name!r} ──")
    mgr_matches = vms.raw.managers.get(username=admin_name)
    if not mgr_matches:
        print(f"  unchanged: managers/{admin_name}")
    elif plan_mode:
        print(f"  would_delete: managers/{admin_name}")
    else:
        vms.raw.managers[mgr_matches[0]["id"]].delete()
        print(f"  deleted: managers/{admin_name}")

    # ── 4'. Delete role ────────────────────────────────────────────────────
    print(f"\n── 4'. Delete role {role_name!r} ──")
    out = vms.delete("roles", key_field="name", key_value=role_name)
    plan.record(out)
    print(f"  {out.result.value}: {out.resource}/{out.name}")

    # ── 3'. Delete bucket-owner user (references group via leading_gid) ────
    print(f"\n── 3'. Delete user {u['name']!r} ──")
    out = vms.delete("users", key_field="name", key_value=u["name"])
    plan.record(out)
    print(f"  {out.result.value}: {out.resource}/{out.name}")

    # ── 2'. Delete group ───────────────────────────────────────────────────
    print(f"\n── 2'. Delete group {g['name']!r} ──")
    out = vms.delete("groups", key_field="name", key_value=g["name"])
    plan.record(out)
    print(f"  {out.result.value}: {out.resource}/{out.name}")

    # ── 1'. Delete tenant (last — everything else must be gone) ────────────
    print(f"\n── 1'. Delete tenant {t['name']!r} ──")
    out = vms.delete("tenants", key_field="name", key_value=t["name"])
    plan.record(out)
    print(f"  {out.result.value}: {out.resource}/{out.name}")

    # ── 0'. Delete the tenant's auto-created local provider ────────────────
    # VMS auto-creates a local_provider with the tenant's name on tenant POST,
    # but tenant DELETE does NOT cascade to remove it. Left alone, a re-create
    # 400s with "local provider with this name already exists." Always skip
    # provider id=1 (the cluster's default VAST provider).
    print(f"\n── 0'. Delete tenant local provider (id={tenant_local_provider_id}) ──")
    _reap_local_provider(
        vms,
        name=t["name"],
        provider_id=tenant_local_provider_id,
        plan_mode=plan_mode,
    )

    print(f"\n{'='*60}")
    label = "DRY-RUN" if plan_mode else "DESTROYED"
    print(f"{label}: tenant {t['name']!r} teardown complete")
    print(f"{'='*60}\n")
    plan.render()
    return 0


# ── internal helpers ───────────────────────────────────────────────────────


def _reap_local_provider(
    vms: VmsClient,
    *,
    name: str,
    provider_id: int | None = None,
    plan_mode: bool = False,
) -> None:
    """Delete the tenant's local provider, by id if known, else by name match.

    Always refuses to delete provider id=1 (the cluster's default). Idempotent:
    silent no-op if nothing matches.
    """
    if provider_id == 1:
        print(f"  skipped: provider_id=1 is the cluster default (will not delete)")
        return

    target_id = provider_id
    if target_id is None:
        match = next(
            (lp for lp in vms.raw.localproviders.get() if lp.get("name") == name),
            None,
        )
        if not match:
            print(f"  unchanged: localproviders/{name} (no orphan found)")
            return
        target_id = match["id"]
        if target_id == 1:
            print(f"  skipped: localproviders/{name} resolves to id=1 (default)")
            return

    if plan_mode:
        print(f"  would_delete: localproviders/{name} (id={target_id})")
        return
    try:
        vms.raw.localproviders[target_id].delete()
        print(f"  deleted: localproviders/{name} (id={target_id})")
    except Exception as exc:
        # Don't crash the whole destroy on cleanup failure — log it so the
        # operator can finish the reap manually.
        print(f"  WARN: failed to delete localproviders/{name} (id={target_id}): {exc}")


_SENSITIVE_FIELDS = {"password", "secret", "token", "access_key", "secret_key"}


def _scrub_secrets(plan: Plan) -> None:
    """Mutate the Plan in place: replace sensitive drift values with '***'."""
    for o in plan.outcomes:
        for k in list(o.drift):
            if k.lower() in _SENSITIVE_FIELDS and o.drift[k]:
                o.drift[k] = "***"

"""Create a VAST tenant + DataEngine prerequisites via vastde_orch.

Wraps VmsClient.ensure_* — no duplicated REST code. Implements the
"Configure Prerequisites on the VAST Cluster" section of the VAST
DataEngine docs (pages 11-15):

    1. Tenant            (name + optional domain)
    2. Group             (gid; local_provider_id required by VMS)
    3. Bucket-owner user (uid, allow_create_bucket, leading_group)
    4. Tenant-admin role (always created; defaults to "<tenant>-admin-role")
    5. Tenant-admin manager (always created; name defaults to "<tenant>-admin",
                             password from cfg or $TENANT_ADMIN_PASSWORD)
    6. VIP pool          (optional; auto-picks an unclaimed range if needed)
    7. View policies     (NFS + S3, always created; names default to
                          "<tenant>-nfs-policy" and "<tenant>-s3-policy")
    8. DataEngine identity policy + group binding (always created + bound).
                          Matches the official VAST KB doc's `data-engine-
                          <tenant>` policy verbatim, but under a non-reserved
                          name (default `<tenant>-de-write`) since the
                          official name is reserved by VMS — it can only be
                          created via the Web UI "Assign DataEngine identity
                          policy to group" checkbox. Grants Create{Trigger,
                          Function,Pipeline} (which per KB note implicitly
                          grants Update/Get/Delete on user-owned resources).
    9. (Opt-in) Bind group to `AllowAllTabular` — VAST's auto-created
                          policy with broader S3 + Kafka access. Off by
                          default; not required for DataEngine per the KB
                          doc. Enable via dataengine_policy.attach_allow_all_tabular.

Idempotent: every step is `get → create-or-patch → no-op`. Safe to re-run.

Usage:
    cp sample/tenant-setup.example.yaml tenant-setup.yaml
    python scripts/setup_tenant.py -c tenant-setup.yaml --plan   # dry-run
    python scripts/setup_tenant.py -c tenant-setup.yaml          # apply

Env vars referenced from the YAML:
    VMS_USER, VMS_PASSWORD         — cluster-admin creds
    TENANT_ADMIN_PASSWORD          — password for the new tenant-admin
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import re
import sys
from pathlib import Path

import yaml

# vastde_orch must be installed/importable on the operator machine.
# Install via: pip install -e /path/to/dataengine
try:
    from vastde_orch.clients.vms import VmsClient
    from vastde_orch.config.models import VmsSpec
    from vastde_orch.enablement.identity import build_dataengine_policy_doc
    from vastde_orch.reconciler import Plan
    from vastde_orch.vippool_planner import (
        claimed_per_subnet,
        format_range,
        free_ranges_in_subnet,
        is_range_available,
        pick_gap,
    )
except ImportError as exc:
    sys.exit(
        f"FATAL: cannot import vastde_orch ({exc}).\n"
        "Install it on this machine first:\n"
        "  pip install -e /path/to/dataengine"
    )


# ── env interpolation ──────────────────────────────────────────────────────
_ENV_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


def _interpolate(value):
    """Replace ${VAR} with os.environ[VAR]; recurse into dicts/lists."""
    if isinstance(value, str):
        def repl(m):
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


# ── main ────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-c", "--config", required=True, type=Path,
                    help="Path to YAML config")
    ap.add_argument("--plan", action="store_true",
                    help="Dry-run — print diff, do not write")
    args = ap.parse_args()

    if not args.config.is_file():
        sys.exit(f"config file not found: {args.config}")

    cfg = _interpolate(yaml.safe_load(args.config.read_text()))

    # VMS connection (cluster-admin creds)
    vms_cfg = cfg["vms"]
    vms_spec = VmsSpec(
        address=vms_cfg["address"],
        user=vms_cfg["user"],
        password=vms_cfg["password"],
        tenant=cfg["tenant"]["name"],  # required by VmsSpec; refers to the new tenant
    )
    vms = VmsClient(vms_spec, dry_run=args.plan)

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
            "  Either put 'password: ${TENANT_ADMIN_PASSWORD}' under tenant_admin in the YAML, "
            "or set $TENANT_ADMIN_PASSWORD in the environment."
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

        # Read current pools to compute claimed + free ranges in this subnet
        existing = list(vms.raw.vippools.get())

        # Idempotency: if a pool with this name already exists and no explicit
        # range was requested, preserve its current range instead of re-picking
        # (otherwise auto-pick treats the pool's own claim as occupied and
        # allocates a different free range, breaking re-runs).
        existing_named = next(
            (p for p in existing if p.get("name") == vp["name"]), None
        )

        # Exclude the pool's own claims when computing what's free
        claims_here = [
            c for c in claimed_per_subnet(existing).get(subnet, [])
            if c.pool_name != vp["name"]
        ]
        free = free_ranges_in_subnet(subnet, claims_here)

        # Decide which range to use
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
                    "Pick a different subnet or run scripts/list_vippools.py."
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

    # ── 8. DataEngine identity policy + group binding ─────────────────────
    # Per the VAST KB "Provisioning User Access and Permissions for DataEngine"
    # (docs/provision-user.pdf p.5-7), application users need ONE identity
    # policy granting Create{Trigger,Function,Pipeline} to perform DataEngine
    # tasks. The doc's recommended name is `data-engine-<tenant>` — auto-
    # created by enabling "Assign DataEngine identity policy to group" in the
    # Web UI when editing the tenant. That name is RESERVED by VMS — POSTing
    # it manually returns 403 ("is reserved. Please, use a different name").
    # So this script creates an equivalent policy under a non-reserved name
    # (default `<tenant>-de-write`) using the IDENTICAL policy document — the
    # one in vastde_orch.enablement.identity.build_dataengine_policy_doc(),
    # which matches the KB's verbatim example (Sids DataengineTablesAccess +
    # DataEngineDefault, same Actions + Resources).
    #
    # Per the KB note on p.7: Create* implicitly grants Update/Get/Delete on
    # resources the user CREATED, so Create*-only is sufficient for self-
    # managed pipelines. Add explicit Update/Get/Delete via a separate policy
    # if you need cross-user management.
    #
    # Binding: VMS makes /s3policies/{id}/.groups read-only — PATCHing it
    # silently no-ops. Bind from the GROUP side via
    # /groups/{id}/.s3_policies_ids. Verified live on var203 2026-06-07.
    dep_cfg = cfg.get("dataengine_policy") or {}
    dep_group = dep_cfg.get("group") or g["name"]
    write_name = (dep_cfg.get("name") or dep_cfg.get("write_policy_name")
                  or f"{t['name']}-de-write")
    print(f"\n── 8. DataEngine identity policy {write_name!r} + bind to {dep_group!r} ──")
    pol_matches = [
        p for p in vms.raw.s3policies.get()
        if p.get("tenant_id") == tenant_id and p.get("name") == write_name
    ]
    if pol_matches:
        write_pol_id = pol_matches[0]["id"]
        print(f"  unchanged: s3policies/{write_name} (id={write_pol_id}) already exists")
    elif args.plan:
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

    # Bind to DE group (group-side PATCH; /s3policies/.groups is read-only).
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
                if args.plan:
                    print(f"  would_update: groups/{dep_group}.s3_policies_ids "
                          f"{existing_ids} → {new_ids}  (binding {write_name})")
                else:
                    vms.raw.groups[group_record["id"]].patch(s3_policies_ids=new_ids)
                    print(f"  updated: groups/{dep_group}.s3_policies_ids = {new_ids}")

    # ── 9. (Optional) Attach AllowAllTabular ──────────────────────────────
    # AllowAllTabular is auto-created by VAST when DataEngine is enabled,
    # but is NOT mentioned in the KB DataEngine provisioning doc — it grants
    # broader access (`s3:*` on `*`, `*` on all kafka topics) than DataEngine
    # itself requires. The official DataEngine identity policy (step 8 above)
    # is sufficient on its own. Opt in here ONLY if your users need the
    # broader S3/Kafka access (e.g. interacting with non-DE buckets).
    if dep_cfg.get("attach_allow_all_tabular", False):
        aat_name = dep_cfg.get("allow_all_tabular_name", "AllowAllTabular")
        print(f"\n── 9. (opt-in) Bind {dep_group!r} to {aat_name!r} ──")
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
                    if args.plan:
                        print(f"  would_update: groups/{dep_group}.s3_policies_ids "
                              f"{existing_ids} → {new_ids}")
                    else:
                        vms.raw.groups[group_record["id"]].patch(s3_policies_ids=new_ids)
                        print(f"  updated: groups/{dep_group}.s3_policies_ids = {new_ids}")

    # ── Summary ────────────────────────────────────────────────────────────
    # Scrub sensitive fields from rendered output (password, secret, token).
    _SENSITIVE = {"password", "secret", "token", "access_key", "secret_key"}
    for o in plan.outcomes:
        for k in list(o.drift):
            if k.lower() in _SENSITIVE and o.drift[k]:
                o.drift[k] = "***"

    print(f"\n{'='*60}")
    label = "DRY-RUN" if args.plan else "APPLIED"
    print(f"{label}: {len(plan.outcomes)} outcome(s) on {t['name']}")
    print(f"{'='*60}\n")
    plan.render()
    return 0


if __name__ == "__main__":
    sys.exit(main())

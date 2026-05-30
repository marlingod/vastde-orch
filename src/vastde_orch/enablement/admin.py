"""Tenant-admin manager provisioning.

VAST has TWO user systems (see docs/vms-endpoints-reference.md):
  - /users/    → filesystem/protocol users (already handled by identity.py)
  - /managers/ → VMS admin users; only these can call tenant-scoped endpoints
                 like /dataengine/ to enable DataEngine on a tenant.

This module wires the 4-step dance discovered against the live cluster:
  1. ensure_role(<tenant>-admin-role, tenant_id=N)        → /roles/ POST
  2. assign_role_to_realm(realm_id, role_id)              → /realms/{id}/assign/ PATCH
       (skipped silently if no realm exists yet on tenant — VAST may
       auto-populate standard tenant-admin permissions on role create)
  3. ensure_manager(<username>, tenant_id, role_ids)      → /managers/ POST
  4. set_manager_password(<username>, <password>)         → /managers/password/ PATCH

Idempotent — re-running is safe.
"""

from __future__ import annotations

import os

from vastde_orch.clients.vms import DiffResult, EnsureOutcome, VmsClient
from vastde_orch.config.models import TenantAdminSpec
from vastde_orch.reconciler import Plan


class TenantAdminCredError(RuntimeError):
    """Raised when the tenant-admin password env var is not set."""


def provision_tenant_admin(
    vms: VmsClient,
    spec: TenantAdminSpec,
    *,
    tenant_id: int,
    tenant_name: str,
    plan: Plan | None = None,
) -> Plan:
    plan = plan or Plan()

    password = os.environ.get(spec.password_env or "", "")
    if not password and not vms._dry_run:
        raise TenantAdminCredError(
            f"tenant-admin {spec.username!r}: env var {spec.password_env!r} is not set"
        )

    # 1. Role — created with explicit permissions_list (verified live: VMS
    #    does NOT auto-populate tenant-admin perms; an empty role results in
    #    "401 Unauthorized" for downstream tenant operations).
    role_name = spec.role_name or f"{tenant_name}-admin-role"
    role_outcome = vms.ensure_role(role_name, tenant_id=tenant_id)
    plan.record(role_outcome)
    role_id = role_outcome.id or vms.get_or_placeholder(
        "roles", key_field="name", key_value=role_name, placeholder_id=0,
    )["id"]

    # 3. Manager. Password must be sent on the POST body — VMS rejects
    #    null password_id on create. set_manager_password() is only useful
    #    for rotating the password later.
    mgr_outcome = vms.ensure_manager(
        spec.username,
        tenant_id=tenant_id,
        password=password,
        user_type="TENANT_ADMIN",
        role_ids=[role_id] if role_id else None,
        first_name=spec.first_name or tenant_name,
        last_name=spec.last_name or "admin",
    )
    plan.record(mgr_outcome)

    # 4. Password (only PATCH on already-existing manager — skip if just created).
    if mgr_outcome.result not in (DiffResult.CREATED, DiffResult.WOULD_CREATE):
        plan.record(vms.set_manager_password(spec.username, password))

    return plan

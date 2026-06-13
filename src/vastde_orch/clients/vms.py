"""Thin idempotency layer over vastpy.VASTClient.

The pattern: every mutation goes through `ensure_*(name, **spec)`:
  1. GET resource filtered by name.
  2. If not present → POST and return CREATED.
  3. If present and any spec field differs → PATCH and return UPDATED.
  4. Else → UNCHANGED.

That is the only mutation pattern this module exposes; callers cannot bypass
it. Plan/dry-run mode is implemented at this layer too: if `dry_run=True`,
no POST/PATCH/DELETE is sent — only the would-be DiffResult is returned.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from vastpy import VASTClient

from vastde_orch.config.models import VmsSpec


class DiffResult(str, Enum):
    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    DELETED = "deleted"
    WOULD_CREATE = "would_create"
    WOULD_UPDATE = "would_update"
    WOULD_DELETE = "would_delete"


@dataclass
class EnsureOutcome:
    result: DiffResult
    resource: str
    name: str
    id: int | None
    drift: dict[str, Any]  # fields that differed (empty for CREATED/UNCHANGED)


def _extract_id(value: Any) -> Any:
    """Extract `.id` from a nested object, e.g. {"id": 6, "name": "..."} → 6."""
    if isinstance(value, dict):
        return value.get("id")
    return value


def _extract_id_list(value: Any) -> Any:
    """Extract `.id` from each item, e.g. [{"id": 27, ...}] → [27]."""
    if isinstance(value, list):
        return [_extract_id(item) for item in value]
    return value


# Map WRITE field name → (READ field name, optional extractor for nested shape).
# VAST's API returns several fields under different names or shapes than what
# POST/PATCH accepts; without this map every re-run reports spurious drift on
# those fields. Add new entries here whenever you spot a write/read mismatch.
_OBSERVED_FIELD: dict[str, tuple[str, Any]] = {
    "leading_group":     ("leading_group_name", None),
    "local_provider_id": ("local_provider",      _extract_id),
    "roles":             ("roles",               _extract_id_list),
    "role_ids":          ("roles",               _extract_id_list),
}


def _values_equal(desired: Any, observed: Any) -> bool:
    """True if `desired` and `observed` represent the same value.

    Beyond `==`, also returns True for common numeric-string mismatches the
    VAST API exhibits (e.g. POST `subnet_cidr='24'` is read back as int `24`).
    """
    if desired == observed:
        return True
    if isinstance(desired, str) and isinstance(observed, int):
        try:
            return int(desired) == observed
        except (ValueError, TypeError):
            return False
    if isinstance(desired, int) and isinstance(observed, str):
        try:
            return desired == int(observed)
        except (ValueError, TypeError):
            return False
    return False


def _drift(desired: dict[str, Any], observed: dict[str, Any]) -> dict[str, Any]:
    """Return only the keys in `desired` whose value differs from observed.

    Handles three VAST API quirks that previously caused spurious drift:
      1. Field-name aliases — POST `leading_group`, read `leading_group_name`.
         Mapped via `_OBSERVED_FIELD`.
      2. Nested-object extraction — POST `local_provider_id=6`, read
         `local_provider={"id": 6, ...}`. Extractor handles the unwrap.
      3. String/int coercion — POST `subnet_cidr='24'`, read `24`. Handled
         by `_values_equal`.

    Lists are compared element-wise (order-sensitive — VAST APIs are
    order-sensitive for lists like protocols=['NFS','SMB']).
    """
    out: dict[str, Any] = {}
    for k, v in desired.items():
        read_key, extractor = _OBSERVED_FIELD.get(k, (k, None))
        obs_val = observed.get(read_key)
        if extractor is not None and obs_val is not None:
            obs_val = extractor(obs_val)
        if not _values_equal(v, obs_val):
            out[k] = v
    return out


class VmsClient:
    """Thin idempotent wrapper around vastpy.VASTClient."""

    def __init__(self, spec: VmsSpec, *, dry_run: bool = False) -> None:
        self._dry_run = dry_run
        self._tenant_name = spec.tenant  # The TARGET tenant — used by callers, not as a request header.
        self._address = spec.address  # used by the DE-API helpers
        # Important: do NOT pass `tenant=` to vastpy here. The vms-level
        # credentials are typically a cluster-admin; sending X-Tenant-Name with
        # those creds causes VMS to reject the request as "invalid user/password".
        # Tenant-scoped requests (e.g. /dataengine/) construct a separate
        # client via `enablement/enable.py:_tenant_scoped_raw()`.
        self._raw = VASTClient(
            address=spec.address,
            token=spec.token,
            user=spec.user,
            password=spec.password,
            version=spec.api_version or "latest",
        )

    @property
    def raw(self) -> VASTClient:
        """Direct access to the underlying vastpy client for one-off calls."""
        return self._raw

    # ── generic ensure ───────────────────────────────────────────────────

    def ensure(
        self,
        resource: str,
        *,
        key_field: str,
        key_value: str,
        spec: dict[str, Any],
        patchable_fields: set[str] | None = None,
    ) -> EnsureOutcome:
        """Generic get-then-create-or-patch.

        Args:
            resource: vastpy attribute name, e.g. "views", "viewpolicies".
            key_field: field to filter on for the lookup, typically "name" or "path".
            key_value: the value to filter by.
            spec: full desired body to POST when creating.
            patchable_fields: if provided, restrict PATCH to this subset of `spec`.
                When None, every drifted field is sent.
        """
        endpoint = getattr(self._raw, resource)
        matches = endpoint.get(**{key_field: key_value})
        if not matches:
            if self._dry_run:
                return EnsureOutcome(DiffResult.WOULD_CREATE, resource, key_value, None, spec)
            created = endpoint.post(**spec)
            return EnsureOutcome(DiffResult.CREATED, resource, key_value, created.get("id"), {})

        existing = matches[0]
        drift = _drift(spec, existing)
        if patchable_fields is not None:
            drift = {k: v for k, v in drift.items() if k in patchable_fields}

        if not drift:
            return EnsureOutcome(
                DiffResult.UNCHANGED, resource, key_value, existing.get("id"), {}
            )

        if self._dry_run:
            return EnsureOutcome(
                DiffResult.WOULD_UPDATE, resource, key_value, existing.get("id"), drift
            )
        endpoint[existing["id"]].patch(**drift)
        return EnsureOutcome(DiffResult.UPDATED, resource, key_value, existing["id"], drift)

    def delete(self, resource: str, *, key_field: str, key_value: str) -> EnsureOutcome:
        endpoint = getattr(self._raw, resource)
        matches = endpoint.get(**{key_field: key_value})
        if not matches:
            return EnsureOutcome(DiffResult.UNCHANGED, resource, key_value, None, {})
        existing = matches[0]
        if self._dry_run:
            return EnsureOutcome(
                DiffResult.WOULD_DELETE, resource, key_value, existing.get("id"), {}
            )
        endpoint[existing["id"]].delete()
        return EnsureOutcome(DiffResult.DELETED, resource, key_value, existing["id"], {})

    # ── typed conveniences ──────────────────────────────────────────────

    def ensure_tenant(self, name: str, *, domain: str | None = None) -> EnsureOutcome:
        spec: dict[str, Any] = {"name": name}
        if domain is not None:
            spec["domain"] = domain
        return self.ensure("tenants", key_field="name", key_value=name, spec=spec)

    def ensure_viewpolicy(
        self,
        name: str,
        *,
        tenant_id: int,
        security_flavor: str = "S3_NATIVE",
        bucket_listing_groups: list[str] | None = None,
    ) -> EnsureOutcome:
        spec: dict[str, Any] = {
            "name": name,
            "tenant_id": tenant_id,
            "flavor": security_flavor,
        }
        if bucket_listing_groups:
            spec["s3_bucket_listing_groups"] = bucket_listing_groups
        return self.ensure("viewpolicies", key_field="name", key_value=name, spec=spec)

    def ensure_view(
        self,
        path: str,
        *,
        policy_id: int,
        protocols: list[str],
        tenant_id: int | None = None,
        bucket_name: str | None = None,
        bucket_owner: str | None = None,
        vip_pool_ids: list[int] | None = None,
        create_dir: bool = True,
    ) -> EnsureOutcome:
        spec: dict[str, Any] = {
            "path": path,
            "policy_id": policy_id,
            "protocols": protocols,
            "create_dir": create_dir,
        }
        if tenant_id is not None:
            spec["tenant_id"] = tenant_id
        if bucket_name is not None:
            spec["bucket"] = bucket_name
        if bucket_owner is not None:
            spec["bucket_owner"] = bucket_owner
        if vip_pool_ids:
            # Required when protocols include KAFKA. Field name verified
            # against swagger: `kafka_vip_pools` (NOT `vip_pools`).
            spec["kafka_vip_pools"] = vip_pool_ids
        return self.ensure(
            "views",
            key_field="path",
            key_value=path,
            spec=spec,
            patchable_fields={"protocols", "policy_id"},
        )

    def ensure_vippool(
        self,
        name: str,
        *,
        tenant_id: int,
        cidr: str,
        ip_range_start: str,
        ip_range_end: str,
        role: str = "PROTOCOLS",
        domain_name: str | None = None,
    ) -> EnsureOutcome:
        # VAST's vippool API stores subnet_cidr as JUST the mask suffix
        # (e.g. "24", "16"), NOT the full CIDR "10.0.0.0/24".
        # Accept either form for operator convenience.
        # See docs/vms-endpoints-reference.md §/vippools/.
        mask_only = cidr.split("/")[-1] if "/" in cidr else cidr
        # domain_name=None → default to pool name (VMS builds the FQDN by
        # appending the cluster DNS suffix). Pass "" explicitly to opt out.
        effective_domain = name if domain_name is None else domain_name
        spec: dict[str, Any] = {
            "name": name,
            "tenant_id": tenant_id,
            "subnet_cidr": mask_only,
            "ip_ranges": [[ip_range_start, ip_range_end]],
            "role": role,
            "domain_name": effective_domain,
        }
        return self.ensure("vippools", key_field="name", key_value=name, spec=spec)

    def ensure_topic(
        self,
        name: str,
        *,
        tenant_id: int,
        database_name: str,
    ) -> EnsureOutcome:
        """Create or verify a Kafka topic on a broker view.

        Verified against VAST 5.4.3: /topics/ requires both tenant_id and
        database_name as QUERY params. database_name is the broker view's
        `bucket` field. Body only needs `name`.

        Per /schemas/?tenant_id=N&database_name=B, the implicit schema is
        always `kafka_topics` for Kafka-protocol views; we don't need to
        pass schema_name on creation.

        Returns CREATED/UNCHANGED. There is no PATCH — partitions/retention
        and similar config live elsewhere (likely the broker view itself).
        See docs/vms-endpoints-reference.md `/topics/`.
        """
        # GET-first idempotency.
        existing = self._raw.topics.get(tenant_id=tenant_id, database_name=database_name)
        rows = existing.get("results", []) if isinstance(existing, dict) else (existing or [])
        if any(t.get("name") == name for t in rows):
            return EnsureOutcome(
                result=DiffResult.UNCHANGED, resource="topics",
                name=name, id=None, drift={},
            )
        if self._dry_run:
            return EnsureOutcome(
                result=DiffResult.WOULD_CREATE, resource="topics",
                name=name, id=None,
                drift={"name": name, "database_name": database_name, "tenant_id": tenant_id},
            )
        self._raw.topics.post(name=name, tenant_id=tenant_id, database_name=database_name)
        return EnsureOutcome(
            result=DiffResult.CREATED, resource="topics",
            name=name, id=None, drift={},
        )

    def ensure_group(
        self, name: str, *, gid: int, provider: str, local_provider_id: int = 1,
    ) -> EnsureOutcome:
        """Create a group. VAST API requires local_provider_id (default 1 = the
        default VAST provider). The `provider` kwarg is kept for backward
        compatibility but only the local_provider_id is sent.

        VAST treats group fields (name, gid, local_provider_id) as immutable
        after creation — PATCHing local_provider_id raises 409 ("Cannot update
        group's Local Provider"). Pass `patchable_fields=set()` so post-create
        runs report UNCHANGED instead of trying to mutate immutable fields.
        """
        spec = {"name": name, "gid": gid, "local_provider_id": local_provider_id}
        return self.ensure(
            "groups", key_field="name", key_value=name, spec=spec,
            patchable_fields=set(),
        )

    def ensure_user(
        self,
        name: str,
        *,
        uid: int,
        provider: str,
        leading_group: str | None = None,
        local_provider_id: int = 1,
        allow_create_bucket: bool = True,
        allow_delete_bucket: bool = True,
    ) -> EnsureOutcome:
        """Create a user. Immutable fields (name, uid, local_provider_id) are
        sent on POST but excluded from PATCH via patchable_fields. Bucket-perm
        flags and leading_group ARE mutable.
        """
        spec: dict[str, Any] = {
            "name": name,
            "uid": uid,
            "local_provider_id": local_provider_id,
            "allow_create_bucket": allow_create_bucket,
            "allow_delete_bucket": allow_delete_bucket,
        }
        if leading_group:
            spec["leading_group"] = leading_group
        return self.ensure(
            "users", key_field="name", key_value=name, spec=spec,
            patchable_fields={
                "allow_create_bucket", "allow_delete_bucket", "leading_group",
            },
        )

    def ensure_k8scluster(
        self, name: str, *, api_server: str, tenant_id: int
    ) -> EnsureOutcome:
        spec = {"name": name, "api_server_url": api_server, "tenant_id": tenant_id}
        return self.ensure("k8sclusters", key_field="name", key_value=name, spec=spec)

    # The 36 standard tenant-admin permissions matching what the VMS Web UI
    # grants when a tenant admin role has all 9 realms × 4 actions selected.
    # Verified live on var203 by attaching them to demo-tenant-admin-role
    # via `permissions_list` (the undocumented PATCH /roles/{id}/ field).
    STANDARD_TENANT_ADMIN_PERMISSIONS: tuple[str, ...] = tuple(
        f"{action}_{realm}"
        for realm in (
            "applications", "database", "events", "hardware", "logical",
            "monitoring", "security", "settings", "support",
        )
        for action in ("create", "view", "edit", "delete")
    )

    def ensure_role(
        self,
        name: str,
        *,
        tenant_id: int,
        ldap_groups: list[str] | None = None,
        permissions: list[str] | None = None,
    ) -> EnsureOutcome:
        """Create or update a tenant-scoped role with explicit permissions.

        ⚠️ IMPORTANT: contrary to what swagger suggests, VMS does NOT
        auto-populate tenant-admin permissions on role creation. The role
        is created empty (0 perms) and must be granted permissions via the
        UNDOCUMENTED `permissions_list` field on PATCH /roles/{id}/.

        Discovered by grep'ing the VMS Web UI bundle for "Update Administrative
        Role" → `onSubmit()` sends `{name, permissions_list, ldap_groups, ...}`
        to the existing role's PATCH endpoint.

        Args:
            permissions: list of perm codenames like "create_security",
                "view_database", etc. If None, defaults to
                STANDARD_TENANT_ADMIN_PERMISSIONS (the 9 realms × 4 actions
                set used by every other tenant-admin role on the cluster).

        See docs/vms-endpoints-reference.md `/roles/`.
        """
        spec: dict[str, Any] = {"name": name, "tenant_id": tenant_id}
        if ldap_groups is not None:
            spec["ldap_groups"] = ldap_groups
        outcome = self.ensure(
            "roles", key_field="name", key_value=name,
            spec=spec, patchable_fields={"ldap_groups"},
        )
        # Always (re-)apply permissions — they live in a separate field
        # and the API ignores the standard `permissions` field on POST.
        # In dry-run we skip the side-effect.
        if not self._dry_run and outcome.id is not None:
            perms = list(permissions) if permissions is not None else list(
                self.STANDARD_TENANT_ADMIN_PERMISSIONS
            )
            try:
                # The role might exist already with the desired perms; only
                # PATCH if the live set differs (avoids noisy "updated" outcomes).
                live = self._raw.roles[outcome.id].get()
                current = set(live.get("permissions") or [])
                if current != set(perms):
                    self._raw.roles[outcome.id].patch(permissions_list=perms)
            except Exception:
                # Swallow — the role itself was created/exists; perms can be
                # set manually if this fails (e.g. on VMS versions where
                # permissions_list isn't accepted).
                pass
        return outcome

    def ensure_manager(
        self,
        username: str,
        *,
        tenant_id: int | None,
        password: str | None = None,
        user_type: str = "TENANT_ADMIN",
        role_ids: list[int] | None = None,
        first_name: str = "",
        last_name: str = "",
        is_active: bool = True,
    ) -> EnsureOutcome:
        """Create or update a VMS manager (admin user).

        Managers are CLUSTER-LEVEL accounts that can log in to the VMS web UI
        and call tenant-scoped REST endpoints like /dataengine/. They are
        separate from filesystem users at /users/.

        Required for tenant-admin: tenant_id, user_type=TENANT_ADMIN, at least
        one role (role IDs are passed; the API resolves them), AND a password.

        Password handling:
          - On POST (create), `password` MUST be included or VMS returns
            "null value in column password_id of relation permissions_manager".
            Swagger does not list `password` in the body schema but the
            live API accepts and requires it on create.
          - On PATCH (update), `password` is omitted from drift detection
            — use set_manager_password() to change it later.
        """
        spec: dict[str, Any] = {
            "username": username,
            "user_type": user_type,
            "is_active": is_active,
            "first_name": first_name,
            "last_name": last_name,
        }
        if tenant_id is not None:
            spec["tenant_id"] = tenant_id
        if role_ids:
            spec["roles"] = role_ids
        if password is not None:
            spec["password"] = password
        return self.ensure(
            "managers",
            key_field="username",
            key_value=username,
            spec=spec,
            # Don't try to PATCH `password` (it's never returned on GET so
            # we'd false-positive a diff every time). Use set_manager_password().
            patchable_fields={"is_active", "first_name", "last_name", "roles"},
        )

    def set_manager_password(self, username: str, password: str) -> EnsureOutcome:
        """Rotate a manager's password.

        Verified against VAST 5.4.3 SP4: /managers/password/ PATCH only accepts
        the currently-authenticated user's password change (returns "Only
        password field(s) can be updated" if `username` is in the body). To
        change another user's password we PATCH /managers/{id}/ with just
        {"password": "..."}.
        """
        if self._dry_run:
            return EnsureOutcome(
                result=DiffResult.WOULD_UPDATE, resource="managers/password",
                name=username, id=None, drift={"password": "***"},
            )
        try:
            matches = self._raw.managers.get(username=username)
            if not matches:
                return EnsureOutcome(
                    result=DiffResult.UNCHANGED, resource="managers/password",
                    name=username, id=None,
                    drift={"error": f"manager {username!r} not found"},
                )
            mgr_id = matches[0]["id"]
            self._raw.managers[mgr_id].patch(password=password)
        except Exception as exc:
            return EnsureOutcome(
                result=DiffResult.UNCHANGED, resource="managers/password",
                name=username, id=None,
                drift={"error": str(exc)[:200]},
            )
        return EnsureOutcome(
            result=DiffResult.UPDATED, resource="managers/password",
            name=username, id=None, drift={"password": "set"},
        )

    def assign_role_to_realm(self, realm_id: int, role_id: int) -> EnsureOutcome:
        """PATCH /realms/{id}/assign/ to bind a role to a tenant realm.

        Swagger is partial; we send {role_id} based on observed behavior.
        Idempotent on the VAST side (re-assigning a bound role is a no-op).
        """
        if self._dry_run:
            return EnsureOutcome(
                result=DiffResult.WOULD_UPDATE, resource="realms/assign",
                name=f"realm={realm_id} role={role_id}", id=realm_id,
                drift={"role_id": role_id},
            )
        try:
            self._raw.realms[realm_id].assign.patch(role_id=role_id)
        except Exception as exc:
            return EnsureOutcome(
                result=DiffResult.UNCHANGED, resource="realms/assign",
                name=f"realm={realm_id} role={role_id}", id=realm_id,
                drift={"error": str(exc)[:200]},
            )
        return EnsureOutcome(
            result=DiffResult.UPDATED, resource="realms/assign",
            name=f"realm={realm_id} role={role_id}", id=realm_id, drift={},
        )

    def ensure_container_registry(
        self,
        name: str,
        *,
        base_url: str,
        tenant_id: int,
        k8scluster_id: int,
        auth_method: str,
        username: str | None = None,
        password: str | None = None,
        secret_name: str | None = None,
    ) -> EnsureOutcome:
        spec: dict[str, Any] = {
            "name": name,
            "base_url": base_url,
            "tenant_id": tenant_id,
            "primary_k8scluster_id": k8scluster_id,
            "auth_method": auth_method,
        }
        if username:
            spec["username"] = username
        if password:
            spec["password"] = password
        if secret_name:
            spec["secret_name"] = secret_name
        return self.ensure("containerregistries", key_field="name", key_value=name, spec=spec)

    # ── one-off helpers ─────────────────────────────────────────────────

    def get_or_raise(self, resource: str, *, key_field: str, key_value: str) -> dict[str, Any]:
        matches = getattr(self._raw, resource).get(**{key_field: key_value})
        if not matches:
            raise LookupError(f"{resource} with {key_field}={key_value!r} not found")
        return matches[0]

    def get_or_placeholder(
        self, resource: str, *, key_field: str, key_value: str, placeholder_id: int = 0,
    ) -> dict[str, Any]:
        """Lookup that tolerates "not found" in dry-run mode.

        In dry-run, downstream orchestration steps reference IDs from earlier
        steps that haven't actually been created yet. Use this lookup variant
        when the missing resource is one we would have created in the same
        plan; it returns a placeholder dict so the orchestrator can keep
        building the plan diff.

        In real-run mode, behaves identically to `get_or_raise`.
        """
        try:
            return self.get_or_raise(resource, key_field=key_field, key_value=key_value)
        except LookupError:
            if not self._dry_run:
                raise
            return {"id": placeholder_id, key_field: key_value, "_placeholder": True}

    def generate_s3_keys(
        self, user_id: int, *, tenant_id: int | None = None,
    ) -> dict[str, str]:
        """Generate and return a new S3 access key pair for a user.

        VMS rejects the POST without `tenant_id` in the body — error 400
        ("It is required to provide `tenant_id` for S3 Data requests")
        verified live on var203 2026-06-07. The wrapper now requires it;
        the kwarg is keyword-only and defaults to None only to let dry-run
        callers skip it.

        The secret key is only available at creation time; persist it immediately.
        """
        if self._dry_run:
            return {"access_key": "<dry-run>", "secret_key": "<dry-run>"}
        if tenant_id is None:
            raise ValueError(
                "generate_s3_keys requires tenant_id (VMS rejects the POST "
                "without it). Pass tenant_id=<int>."
            )
        return self._raw.users[user_id].access_keys.post(tenant_id=tenant_id)

    # ── DataEngine direct REST (catalog A.3 / A.4 / A.5) ─────────────────
    # K8s clusters, container registries, and mTLS credentials live on the
    # DataEngine API (/api/dataengine/*), NOT the VMS swagger surface that
    # vastpy talks to. These helpers POST directly with `requests`, using a
    # tenant-admin JWT (the only auth the DE endpoints accept — Basic gets
    # "Failed to parse VMS auth jwt!"). Returns (value, was_created) so the
    # caller can record CREATED vs UNCHANGED in its plan.

    def _fetch_tenant_jwt(self, tenant_admin_user: str, tenant_admin_password: str) -> str:
        """POST /api/latest/token/<tenant>/ → access JWT for tenant-scoped calls.

        Cached on the VmsClient instance for the (tenant, username) pair —
        valid for ~1h per VMS default.
        """
        cache = getattr(self, "_jwt_cache", None)
        if cache is None:
            self._jwt_cache: dict[tuple[str, str], str] = {}
            cache = self._jwt_cache
        key = (self._tenant_name, tenant_admin_user)
        if key in cache:
            return cache[key]
        import json
        import urllib3
        urllib3.disable_warnings()
        http = urllib3.PoolManager(cert_reqs="CERT_NONE")
        r = http.request(
            "POST",
            f"https://{self._address}/api/latest/token/{self._tenant_name}",
            headers={"Content-Type": "application/json"},
            body=json.dumps(
                {"username": tenant_admin_user, "password": tenant_admin_password}
            ).encode(),
        )
        if r.status != 200:
            raise RuntimeError(
                f"JWT fetch for tenant {self._tenant_name!r} failed "
                f"(HTTP {r.status}): {r.data.decode()[:200]}"
            )
        jwt = json.loads(r.data)["access"]
        cache[key] = jwt
        return jwt

    def _de_api_request(
        self, method: str, path: str, *,
        tenant_admin_user: str, tenant_admin_password: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """{method} /api/dataengine/{path} with Bearer JWT auth.

        DE endpoints reject HTTP Basic with a JWT-decode error
        (`Failed to parse VMS auth jwt!`) — they require the tenant-scoped
        access token from /api/latest/token/{tenant}/.
        """
        import requests
        import urllib3
        urllib3.disable_warnings()  # lab self-signed cert
        jwt = self._fetch_tenant_jwt(tenant_admin_user, tenant_admin_password)
        url = f"https://{self._address}/api/dataengine/{path}"
        resp = requests.request(
            method, url, json=body,
            headers={"Authorization": f"Bearer {jwt}"},
            verify=False, timeout=60,
        )
        if not resp.ok:
            raise RuntimeError(
                f"{method} /api/dataengine/{path} failed: "
                f"{resp.status_code} {resp.text[:400]}"
            )
        return resp.json() if resp.text else {}

    def _de_api_list(
        self, path: str, *, tenant_admin_user: str, tenant_admin_password: str,
    ) -> list[dict[str, Any]]:
        resp = self._de_api_request(
            "GET", path,
            tenant_admin_user=tenant_admin_user,
            tenant_admin_password=tenant_admin_password,
        )
        # DE-API list responses use `data` (not `results`) with a sibling
        # `pagination` object.
        if isinstance(resp, dict):
            return resp.get("data") or resp.get("results") or []
        return list(resp)

    def register_de_mtls_credential(
        self, name: str, *,
        ca_path: Path, client_cert_path: Path, client_key_path: Path,
        tenant_admin_user: str, tenant_admin_password: str,
    ) -> tuple[str, bool]:
        """Returns (guid, was_created). Idempotent by name."""
        existing = self._de_api_list(
            "mtls-authentication-credentials/",
            tenant_admin_user=tenant_admin_user,
            tenant_admin_password=tenant_admin_password,
        )
        for cred in existing:
            if cred.get("name") == name:
                return cred["guid"], False
        if self._dry_run:
            return "<dry-run-mtls-guid>", True
        import base64
        body = {
            "name": name,
            "certificate_authority_b64": base64.b64encode(Path(ca_path).read_bytes()).decode(),
            "client_certificate_b64": base64.b64encode(Path(client_cert_path).read_bytes()).decode(),
            "client_key_b64": base64.b64encode(Path(client_key_path).read_bytes()).decode(),
        }
        created = self._de_api_request(
            "POST", "mtls-authentication-credentials/",
            tenant_admin_user=tenant_admin_user,
            tenant_admin_password=tenant_admin_password,
            body=body,
        )
        return created["guid"], True

    def register_de_k8s_cluster(
        self, name: str, *,
        kube_api_url: str,
        mtls_credentials_guid: str,
        namespaces: list[str],
        tenant_admin_user: str, tenant_admin_password: str,
    ) -> tuple[str, bool]:
        """Returns (vrn, was_created). Idempotent by name.

        Side-effect: synchronously creates a `VastTenant` CR on the K8s
        cluster — if one already exists in any state (incl. `Deleting` with
        the 300s operator delay), the POST 400s with "Failed to provision
        telemetries resources".
        """
        existing = self._de_api_list(
            "kubernetes-clusters/",
            tenant_admin_user=tenant_admin_user,
            tenant_admin_password=tenant_admin_password,
        )
        for c in existing:
            if c.get("name") == name:
                vrn = c.get("vrn") or f"vast:dataengine:kubernetes-clusters:{name}"
                return vrn, False
        if self._dry_run:
            return f"<dry-run-cluster-vrn:{name}>", True
        body = {
            "name": name,
            "kube_api_url": kube_api_url,
            "mtls_credentials_guid": mtls_credentials_guid,
            "namespaces": namespaces,
        }
        created = self._de_api_request(
            "POST", "kubernetes-clusters/",
            tenant_admin_user=tenant_admin_user,
            tenant_admin_password=tenant_admin_password,
            body=body,
        )
        vrn = created.get("vrn") or f"vast:dataengine:kubernetes-clusters:{name}"
        return vrn, True

    def register_de_container_registry(
        self, name: str, *,
        url: str,
        primary_cluster_vrn: str,
        primary_namespace: str,
        auth_type: str,
        tenant_admin_user: str, tenant_admin_password: str,
        username: str | None = None,
        password: str | None = None,
        email: str | None = None,
        secret: str | None = None,
    ) -> tuple[str, bool]:
        """Returns (guid, was_created). Idempotent by name. Uses the cluster
        VRN to reference its primary K8s cluster (not GUID).
        """
        existing = self._de_api_list(
            "container-registries/",
            tenant_admin_user=tenant_admin_user,
            tenant_admin_password=tenant_admin_password,
        )
        for r in existing:
            if r.get("name") == name:
                return r.get("guid", ""), False
        if self._dry_run:
            return f"<dry-run-registry-guid:{name}>", True
        body: dict[str, Any] = {
            "name": name,
            "url": url,
            "primary_kubernetes_cluster": {
                "kubernetes_cluster_vrn": primary_cluster_vrn,
                "namespace": primary_namespace,
            },
        }
        if auth_type and auth_type != "none":
            body["auth_type"] = auth_type
            if username:
                body["username"] = username
            if password:
                body["password"] = password
            if email:
                body["email"] = email
            if secret:
                body["secret"] = secret
        created = self._de_api_request(
            "POST", "container-registries/",
            tenant_admin_user=tenant_admin_user,
            tenant_admin_password=tenant_admin_password,
            body=body,
        )
        return created.get("guid", ""), True

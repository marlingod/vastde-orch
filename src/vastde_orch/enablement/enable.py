"""Top-level orchestrator for Stage A: enable DataEngine on a tenant.

Order (per PDF p.6 and the dependency graph between resources):
  1. Pre-flight checks.
  2. Tenant.
  3. K8s bootstrap (Zarf, namespaces, package).
  4. K8s cluster registration on the tenant.
  5. Identity (group + users).
  5b. Tenant admin (VMS manager) — required for the /dataengine/ step.
  6. Event broker (VAST view + topics, or Kafka).
  7. Container registry.
  8. Source views.
  9. Toggle "Enable DataEngine" on the tenant + attach predefined policy.
"""

from __future__ import annotations

import os

from vastde_orch.clients.vms import DiffResult, EnsureOutcome, VmsClient
from vastde_orch.config.models import EnablementSpec, VastEventBrokerSpec
from vastde_orch.enablement.admin import provision_tenant_admin
from vastde_orch.enablement.event_broker import (
    provision_kafka_broker,
    provision_vast_broker,
)
from vastde_orch.enablement.identity import (
    attach_dataengine_policy_to_group,
    provision_identity,
)
from vastde_orch.enablement.k8s_bootstrap import bootstrap_k8s
from vastde_orch.enablement.preflight import run_preflight
from vastde_orch.enablement.source_views import provision_source_views
from vastde_orch.reconciler import Plan


def enable_dataengine(
    vms: VmsClient,
    spec: EnablementSpec,
    *,
    skip_preflight: bool = False,
    skip_k8s_bootstrap: bool = False,
    dry_run: bool = False,
) -> Plan:
    if not skip_preflight:
        run_preflight(vms, include_k8s=not skip_k8s_bootstrap, kubeconfig=spec.kubernetes.kubeconfig)

    plan = Plan()

    # 2. Tenant.
    plan.record(vms.ensure_tenant(spec.tenant.name, domain=spec.tenant.domain))
    tenant = vms.get_or_raise("tenants", key_field="name", key_value=spec.tenant.name)

    # 3. K8s bootstrap (operator-machine + cluster-side; not a VMS resource).
    if not skip_k8s_bootstrap:
        bootstrap_k8s(spec.kubernetes, dry_run=dry_run)

    # 4/5. Identity (group + users). Must precede event broker (bucket owner),
    #      tenant admin, and the DE-API registration steps (which use the
    #      tenant_admin JWT).
    provision_identity(vms, spec.identity, plan=plan)

    # 5b. Tenant admin (VMS manager) — required for the /api/dataengine/
    #     endpoints (K8s cluster, container registry, dataengine toggle).
    if spec.identity.tenant_admin is not None:
        provision_tenant_admin(
            vms, spec.identity.tenant_admin,
            tenant_id=tenant["id"], tenant_name=spec.tenant.name, plan=plan,
        )

    # 6. Event broker.
    if isinstance(spec.event_broker, VastEventBrokerSpec):
        provision_vast_broker(
            vms,
            spec.event_broker,
            tenant_id=tenant["id"],
            dataengine_group=spec.identity.group.name,
            plan=plan,
        )
    else:
        provision_kafka_broker(vms, spec.event_broker, tenant_id=tenant["id"], plan=plan)

    # 8. Source views.
    if spec.source_views:
        provision_source_views(vms, spec.source_views, tenant_id=tenant["id"], plan=plan)

    # 9. Toggle DataEngine on tenant via the /dataengine/ endpoint.
    #
    # IMPORTANT: this endpoint requires *tenant-scoped* credentials (a tenant
    # admin user, not a cluster admin). If `identity.tenant_admin` was set,
    # we use its credentials; otherwise we record a skipped outcome — the
    # operator can finish via the DataEngine Web UI.
    #
    # See docs/vms-endpoints-reference.md (`/dataengine/`) for the discovery.
    de_outcome = _toggle_dataengine_on_tenant(
        vms, tenant, spec, assign_policy=(spec.identity.policy == "assign_predefined"),
    )
    plan.record(de_outcome)

    # 9b. Register mTLS bundle + K8s cluster + container registry against
    #     /api/dataengine/* (catalog A.3 / A.4 / A.5). MUST run AFTER the
    #     setup-provisioning toggle above: the DE-API endpoints 500 on
    #     tenants where DataEngine isn't enabled yet (verified live on
    #     lax-tenant 2026-06-12 — GET /mtls-authentication-credentials/
    #     returned 500 before toggle, fine after). Needs tenant_admin for
    #     the JWT auth required by DE-API.
    _register_de_compute_resources(vms, spec, plan=plan, dry_run=dry_run)

    # 10. Attach predefined identity policy if requested. The name
    #     `data-engine-<tenant>` is reserved by VAST and gets auto-created
    #     when DataEngine is enabled via the Web UI. If we try to pre-create
    #     it we get 403 "X is reserved" — treat that as expected, not an error.
    if spec.identity.policy == "assign_predefined":
        try:
            plan.record(
                attach_dataengine_policy_to_group(
                    vms,
                    spec.identity.group.name,
                    spec.tenant.name,
                    tenant_id=tenant["id"],
                )
            )
        except Exception as exc:
            msg = str(exc)
            if "reserved" in msg.lower():
                plan.record(EnsureOutcome(
                    result=DiffResult.UNCHANGED, resource="s3policies",
                    name=f"data-engine-{spec.tenant.name}", id=None,
                    drift={
                        "skipped": (
                            "name is reserved by VAST — will be auto-created when "
                            "DataEngine is enabled via the Web UI"
                        )
                    },
                ))
            else:
                raise

    return plan


def disable_dataengine(vms: VmsClient, tenant_name: str) -> Plan:
    """Reverse of enable: toggle DataEngine off on the tenant (PDF p.39).

    Calls DELETE /dataengine/ with X-Tenant-Name set to the target tenant.
    Requires a tenant-scoped credential. Honors `vms._dry_run` — in dry-run
    mode, records WOULD_DELETE without hitting the API.

    Auto-created views/policies (`/dataengine`, `/dataengine-telemetries-*`,
    and the auto view policies) are intentionally NOT deleted here — VAST
    removes them automatically when DataEngine is disabled on the tenant.
    See src/vastde_orch/enablement/auto_resources.py.
    """
    plan = Plan()
    tenant = vms.get_or_raise("tenants", key_field="name", key_value=tenant_name)
    if vms._dry_run:
        plan.record(EnsureOutcome(
            result=DiffResult.WOULD_DELETE, resource="dataengine",
            name=tenant_name, id=tenant["id"], drift={},
        ))
        return plan
    try:
        _tenant_scoped_raw(vms, tenant_name).dataengine.delete()
        result = DiffResult.DELETED
    except Exception as exc:
        # Surface as no-op + warning rather than hard fail; the operator can
        # finish via the DataEngine Web UI.
        result = DiffResult.UNCHANGED
        plan.record(EnsureOutcome(
            result=result, resource="dataengine", name=tenant_name,
            id=tenant["id"], drift={"error": str(exc)[:200]},
        ))
        return plan
    plan.record(EnsureOutcome(
        result=result, resource="dataengine", name=tenant_name, id=tenant["id"], drift={},
    ))
    return plan


# ── internal helpers ─────────────────────────────────────────────────────────

def _register_de_compute_resources(
    vms: VmsClient,
    spec: EnablementSpec,
    *,
    plan: Plan,
    dry_run: bool,
) -> None:
    """Register mTLS bundle + K8s cluster + container registry via DE-API.

    All three resources live under /api/dataengine/ (not the public VMS
    swagger), need tenant_admin JWT auth, and are linked: registry
    references the K8s cluster's VRN, which references the mTLS guid.

    Skips with a clear message — and records skipped outcomes in the plan —
    if any prerequisite is missing (no tenant_admin, no password env var,
    no mTLS cert paths).
    """
    ta = spec.identity.tenant_admin
    if ta is None:
        msg = "no enablement.identity.tenant_admin configured (DE-API requires it)"
        _record_skip(plan, "kubernetes-clusters", spec.kubernetes.name, msg)
        _record_skip(plan, "container-registries", spec.container_registry.name, msg)
        print(f"  skipped (k8s + registry): {msg}")
        return

    ta_pass = os.environ.get(ta.password_env, "")
    if not ta_pass:
        msg = f"${ta.password_env} not set; can't auth as tenant_admin {ta.username!r}"
        _record_skip(plan, "kubernetes-clusters", spec.kubernetes.name, msg)
        _record_skip(plan, "container-registries", spec.container_registry.name, msg)
        print(f"  skipped (k8s + registry): {msg}")
        return

    k = spec.kubernetes
    if not (k.ca_cert_path and k.client_cert_path and k.client_key_path):
        msg = ("kubernetes.ca_cert_path / client_cert_path / client_key_path "
               "all required for DE-API K8s registration")
        _record_skip(plan, "kubernetes-clusters", k.name, msg)
        _record_skip(plan, "container-registries", spec.container_registry.name, msg)
        print(f"  skipped (k8s + registry): {msg}")
        return
    # Catches a class of YAML typos (e.g. name: '-tenant-k8s' meant
    # 'lax-tenant-k8s') BEFORE we POST garbage to VMS.
    if not k.name or not k.name[0].isalnum():
        msg = (f"kubernetes.name {k.name!r} looks malformed (must start with "
               "an alphanumeric). Fix the YAML and re-run.")
        _record_skip(plan, "kubernetes-clusters", k.name, msg)
        _record_skip(plan, "container-registries", spec.container_registry.name, msg)
        print(f"  skipped (k8s + registry): {msg}")
        return

    mtls_name = f"{k.name}-mtls"

    if dry_run:
        # Can't usefully probe DE-API in dry_run (the JWT fetch hits VMS).
        # Record three would_create outcomes and move on.
        for resource, name in (
            ("mtls-authentication-credentials", mtls_name),
            ("kubernetes-clusters", k.name),
            ("container-registries", spec.container_registry.name),
        ):
            plan.record(EnsureOutcome(
                result=DiffResult.WOULD_CREATE, resource=resource,
                name=name, id=None, drift={},
            ))
        return

    # 0. Wait for setup-provisioning to finish before any DE-API write.
    #    POST /api/latest/dataengine/setup-provisioning/ returns 200 fast
    #    but provisions the tenant's DE namespace asynchronously. The
    #    /api/dataengine/* endpoints respond with 503
    #    {"detail":"Can't access while setup provisioning is not completed"}
    #    until that finishes. Probe by listing mtls-creds; retry with
    #    backoff for up to 5 min total. Verified live on lax-tenant
    #    2026-06-13.
    _wait_for_de_setup(vms, ta.username, ta_pass)

    # 1. mTLS credential.
    print(f"\n── 5c.1 mTLS credential {mtls_name!r} (DE-API) ──")
    mtls_guid, created = vms.register_de_mtls_credential(
        mtls_name,
        ca_path=k.ca_cert_path,
        client_cert_path=k.client_cert_path,
        client_key_path=k.client_key_path,
        tenant_admin_user=ta.username,
        tenant_admin_password=ta_pass,
    )
    plan.record(EnsureOutcome(
        result=DiffResult.CREATED if created else DiffResult.UNCHANGED,
        resource="mtls-authentication-credentials",
        name=mtls_name, id=None, drift={"guid": mtls_guid},
    ))
    print(f"  {'created' if created else 'unchanged'}: mtls/{mtls_name} (guid={mtls_guid})")

    # 2. K8s cluster.
    print(f"\n── 5c.2 K8s cluster {k.name!r} (DE-API) ──")
    cluster_vrn, created = vms.register_de_k8s_cluster(
        k.name,
        kube_api_url=k.api_server,
        mtls_credentials_guid=mtls_guid,
        namespaces=k.namespaces,
        tenant_admin_user=ta.username,
        tenant_admin_password=ta_pass,
    )
    plan.record(EnsureOutcome(
        result=DiffResult.CREATED if created else DiffResult.UNCHANGED,
        resource="kubernetes-clusters",
        name=k.name, id=None, drift={"vrn": cluster_vrn},
    ))
    print(f"  {'created' if created else 'unchanged'}: kubernetes-clusters/{k.name} (vrn={cluster_vrn})")

    # 3. Container registry. Translates RegistryAuthSpec.method to DE-API
    #    auth_type. user_credentials → password; kubernetes_secret → secret;
    #    none → none.
    reg = spec.container_registry
    auth_type_map = {
        "user_credentials": "password",
        "kubernetes_secret": "secret",
        "none": "none",
    }
    de_auth_type = auth_type_map.get(reg.auth.method, "none")
    username = os.environ.get(reg.auth.username_env, "") if reg.auth.username_env else None
    password = os.environ.get(reg.auth.password_env, "") if reg.auth.password_env else None
    primary_ns = k.namespaces[0] if k.namespaces else "vast-dataengine"

    print(f"\n── 5c.3 Container registry {reg.name!r} (DE-API) ──")
    reg_guid, created = vms.register_de_container_registry(
        reg.name,
        url=reg.base_url,
        primary_cluster_vrn=cluster_vrn,
        primary_namespace=primary_ns,
        auth_type=de_auth_type,
        tenant_admin_user=ta.username,
        tenant_admin_password=ta_pass,
        username=username,
        password=password,
        secret=reg.auth.kubernetes_secret_name,
    )
    plan.record(EnsureOutcome(
        result=DiffResult.CREATED if created else DiffResult.UNCHANGED,
        resource="container-registries",
        name=reg.name, id=None, drift={"guid": reg_guid},
    ))
    print(f"  {'created' if created else 'unchanged'}: container-registries/{reg.name} (guid={reg_guid})")


def _wait_for_de_setup(
    vms: VmsClient,
    tenant_admin_user: str,
    tenant_admin_password: str,
    *,
    total_timeout_s: int = 300,
    initial_backoff_s: float = 2.0,
    max_backoff_s: float = 15.0,
) -> None:
    """Poll a DE-API endpoint until the async setup-provisioning finishes.

    Raises RuntimeError if the wait exceeds `total_timeout_s`. Any other
    error from the probe (auth, network) is re-raised immediately so the
    operator sees the real cause instead of a wait timeout.
    """
    import time
    deadline = time.monotonic() + total_timeout_s
    backoff = initial_backoff_s
    waited = False
    while True:
        try:
            vms._de_api_list(
                "mtls-authentication-credentials/",
                tenant_admin_user=tenant_admin_user,
                tenant_admin_password=tenant_admin_password,
            )
            if waited:
                print(f"  setup-provisioning complete; continuing")
            return
        except RuntimeError as exc:
            msg = str(exc)
            if "setup provisioning is not completed" not in msg:
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(
                    f"DE setup-provisioning did not complete within "
                    f"{total_timeout_s}s. Last response: {msg}"
                ) from exc
            if not waited:
                print(f"\n── 5c.0 Waiting for DataEngine setup-provisioning to finish ──")
                waited = True
            print(f"  not yet ready; retrying in {backoff:.0f}s "
                  f"({int(remaining)}s budget left)")
            time.sleep(min(backoff, remaining))
            backoff = min(backoff * 1.5, max_backoff_s)


def _record_skip(plan: Plan, resource: str, name: str, reason: str) -> None:
    plan.record(EnsureOutcome(
        result=DiffResult.UNCHANGED, resource=resource,
        name=name, id=None, drift={"skipped": reason},
    ))


def _tenant_scoped_raw(
    vms: VmsClient,
    tenant_name: str,
    *,
    admin_username: str | None = None,
    admin_password: str | None = None,
):
    """Return a vastpy client whose requests carry X-Tenant-Name=<tenant_name>.

    If `admin_username`/`admin_password` are supplied, use them; otherwise
    fall back to the cluster-admin credentials embedded in `vms` (which
    typically fail with 403 on tenant-scoped endpoints — but the caller will
    catch and report).

    See docs/vms-endpoints-reference.md (`/dataengine/`).
    """
    from vastpy import VASTClient

    raw = vms.raw
    if admin_username and admin_password:
        return VASTClient(
            address=raw._address,
            user=admin_username,
            password=admin_password,
            tenant=tenant_name,
            version=raw._version or "latest",
        )
    return VASTClient(
        address=raw._address,
        user=raw._user,
        password=raw._password,
        token=raw._token,
        tenant=tenant_name,
        version=raw._version or "latest",
    )


def _toggle_dataengine_on_tenant(
    vms: VmsClient,
    tenant: dict,
    spec: EnablementSpec,
    *,
    assign_policy: bool,
) -> EnsureOutcome:
    """POST/PATCH /dataengine/ with tenant-scoped auth to enable DataEngine.

    Returns an EnsureOutcome that is either CREATED/UPDATED/UNCHANGED on
    success, or UNCHANGED-with-error on failure (so the rest of the plan
    survives a missing tenant-admin credential).
    """
    body: dict[str, object] = {
        "enabled": True,
        "default_broker": (
            spec.event_broker.view_path
            if isinstance(spec.event_broker, VastEventBrokerSpec)
            else spec.event_broker.name
        ),
        "container_registry_name": spec.container_registry.name,
        "k8scluster_name": spec.kubernetes.name,
        "assign_dataengine_policy_to_group": assign_policy,
    }
    if vms._dry_run:
        return EnsureOutcome(
            result=DiffResult.WOULD_UPDATE,
            resource="dataengine", name=spec.tenant.name,
            id=tenant["id"], drift=body,
        )
    # Use the tenant-admin credentials if provided in the spec; otherwise
    # fall through to cluster-admin (which will fail with 403, then we
    # report the friendly error).
    admin_user = admin_pwd = None
    if spec.identity.tenant_admin is not None:
        admin_user = spec.identity.tenant_admin.username
        admin_pwd = os.environ.get(spec.identity.tenant_admin.password_env, "")
    # The REAL endpoint (discovered via VMS UI bundle):
    #   POST /api/latest/dataengine/setup-provisioning/
    # Body shape (extracted from `onSubmit` in the Angular wizard):
    #   { kafka_broker: { name, type, [url] },
    #     default_topic_name, dead_letter_topic_name,
    #     tenant_id,
    #     [kafka_ca_certificate] }
    # See docs/vms-endpoints-reference.md `/dataengine/setup-provisioning/`.
    #
    # Auth requirement: this endpoint forwards to a backend service that
    # accepts Api-Token auth but rejects HTTP Basic. We try with the
    # tenant-admin's username/password (the only creds we have on hand);
    # if that errors at the backend layer, we record a "manual step
    # required" outcome.
    if not isinstance(spec.event_broker, VastEventBrokerSpec):
        return EnsureOutcome(
            result=DiffResult.UNCHANGED, resource="dataengine",
            name=spec.tenant.name, id=tenant["id"],
            drift={"skipped": "external Kafka broker enable path not yet implemented"},
        )
    if not (admin_user and admin_pwd):
        return EnsureOutcome(
            result=DiffResult.UNCHANGED, resource="dataengine",
            name=spec.tenant.name, id=tenant["id"],
            drift={
                "manual_step_required": (
                    "no tenant_admin credentials in spec — set identity.tenant_admin.{username,password_env}"
                )
            },
        )
    setup_body = {
        "kafka_broker": {"name": spec.event_broker.bucket_name, "type": "Internal"},
        "default_topic_name": spec.event_broker.default_topic.name,
        "dead_letter_topic_name": spec.event_broker.deadletter_topic.name,
        "tenant_id": tenant["id"],
    }
    try:
        access_jwt = _fetch_tenant_jwt(
            vms.raw._address, spec.tenant.name, admin_user, admin_pwd,
        )
        _post_setup_provisioning(vms.raw._address, access_jwt, setup_body)
        return EnsureOutcome(
            result=DiffResult.CREATED, resource="dataengine",
            name=spec.tenant.name, id=tenant["id"], drift=setup_body,
        )
    except Exception as exc:
        msg = str(exc)
        return EnsureOutcome(
            result=DiffResult.UNCHANGED, resource="dataengine",
            name=spec.tenant.name, id=tenant["id"],
            drift={
                "manual_step_required": (
                    f"POST /dataengine/setup-provisioning/ failed: {msg[:300]}. "
                    f"Finish via Web UI: right-click tenant '{spec.tenant.name}' → Enable DataEngine, "
                    f"with broker={spec.event_broker.bucket_name}, "
                    f"default topic={spec.event_broker.default_topic.name}, "
                    f"DLQ={spec.event_broker.deadletter_topic.name}"
                )
            },
        )


def _fetch_tenant_jwt(address: str, tenant_name: str, username: str, password: str) -> str:
    """POST /api/latest/token/<tenant>/ to get a JWT for tenant-scoped calls.

    Discovered from swagger (`/token/{tenant_name}`) + VMS Web UI source. The
    returned `access` JWT is what /dataengine/setup-provisioning/ accepts
    (HTTP Basic is rejected by the gateway with a misleading
    "Authorization header must contain two space-delimited values" error).
    """
    import urllib3, json
    urllib3.disable_warnings()
    http = urllib3.PoolManager(cert_reqs="CERT_NONE")
    r = http.request(
        "POST",
        f"https://{address}/api/latest/token/{tenant_name}",
        headers={"Content-Type": "application/json"},
        body=json.dumps({"username": username, "password": password}).encode(),
    )
    if r.status != 200:
        raise RuntimeError(f"login failed ({r.status}): {r.data.decode()[:200]}")
    return json.loads(r.data)["access"]


def _post_setup_provisioning(address: str, access_jwt: str, body: dict) -> None:
    """POST /api/latest/dataengine/setup-provisioning/ with Bearer JWT.

    Raises on non-2xx; on success the tenant goes data_engine_enabled=True.
    """
    import urllib3, json
    urllib3.disable_warnings()
    http = urllib3.PoolManager(cert_reqs="CERT_NONE")
    r = http.request(
        "POST",
        f"https://{address}/api/latest/dataengine/setup-provisioning/",
        headers={
            "Authorization": f"Bearer {access_jwt}",
            "Content-Type": "application/json",
        },
        body=json.dumps(body).encode(),
    )
    if r.status >= 300:
        raise RuntimeError(f"HTTP {r.status}: {r.data.decode()[:300]}")

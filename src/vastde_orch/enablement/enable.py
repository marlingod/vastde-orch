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
from vastde_orch.enablement.container_registry import provision_container_registry
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

    # 4. Register K8s cluster on the tenant. On VAST versions that do not
    #    expose /k8sclusters/ in REST (e.g. 5.4.3 SP4), this 404s — we record
    #    a skipped outcome so the rest of the plan continues. The operator
    #    must finish via the DataEngine Web UI or the `vastde` CLI.
    k8s_outcome = _try_or_skip_404(
        lambda: vms.ensure_k8scluster(
            spec.kubernetes.name,
            api_server=spec.kubernetes.api_server,
            tenant_id=tenant["id"],
        ),
        resource="k8sclusters",
        name=spec.kubernetes.name,
    )
    plan.record(k8s_outcome)
    k8s_id: int | None = None
    if k8s_outcome.result not in (DiffResult.UNCHANGED,) or k8s_outcome.id is not None:
        try:
            k8s_id = vms.get_or_raise(
                "k8sclusters", key_field="name", key_value=spec.kubernetes.name,
            )["id"]
        except Exception:
            k8s_id = None  # endpoint absent

    # 5. Identity (group + users). Must precede event broker (bucket owner) and policy attach.
    provision_identity(vms, spec.identity, plan=plan)

    # 5b. Tenant admin (VMS manager) — if configured. Required for the
    #     /dataengine/ enable step to actually succeed; without it, the
    #     final toggle will fall back to "skipped" mode.
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

    # 7. Container registry. Same 404-tolerance as k8s cluster.
    reg_outcome = _try_or_skip_404(
        lambda: provision_container_registry(
            vms,
            spec.container_registry,
            tenant_id=tenant["id"],
            k8scluster_id=k8s_id or 0,
        ),
        resource="containerregistries",
        name=spec.container_registry.name,
    )
    plan.record(reg_outcome)

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


def disable_dataengine(vms: VmsClient, tenant_name: str, *, dry_run: bool = False) -> Plan:
    """Reverse of enable: toggle DataEngine off on the tenant (PDF p.39).

    Calls DELETE /dataengine/ with X-Tenant-Name set to the target tenant.
    Requires a tenant-scoped credential.

    Auto-created views/policies (`/dataengine`, `/dataengine-telemetries-*`,
    and the auto view policies) are intentionally NOT deleted here — VAST
    removes them automatically when DataEngine is disabled on the tenant.
    See src/vastde_orch/enablement/auto_resources.py.
    """
    plan = Plan()
    tenant = vms.get_or_raise("tenants", key_field="name", key_value=tenant_name)
    try:
        _tenant_scoped_raw(vms, tenant_name).dataengine.delete()
        result = DiffResult.DELETED if not vms._dry_run else DiffResult.WOULD_DELETE
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

def _try_or_skip_404(fn, *, resource: str, name: str) -> EnsureOutcome:
    """Run `fn()`; if it raises a 404 from VMS, return a skipped outcome.

    Used to keep `enable` running on VAST versions where /k8sclusters/ and
    /containerregistries/ aren't exposed (see docs/vms-endpoints-reference.md
    "Endpoints we expected but they don't exist on this VAST version").
    """
    try:
        return fn()
    except Exception as exc:
        msg = str(exc)
        if "404" in msg or "Not Found" in msg:
            return EnsureOutcome(
                result=DiffResult.UNCHANGED,
                resource=resource,
                name=name,
                id=None,
                drift={
                    "skipped": (
                        f"/{resource}/ not exposed by this VMS version — "
                        "finish via DataEngine Web UI or vastde CLI"
                    )
                },
            )
        raise


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

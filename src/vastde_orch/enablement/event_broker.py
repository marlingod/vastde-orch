"""VAST Event Broker (PDF p.16-21) or third-party Kafka broker provisioning.

For the VAST kind, ordering matters:
  1. Ensure bucket-owner user (already done by `identity.py`; we just look it up).
  2. Ensure VIP pool dedicated to the broker.
  3. Ensure view policy (S3 Native, bucket-listing perm to the dataengine group).
  4. Ensure view at `view_path` with protocol=Kafka.
  5. Create default topic + deadletter topic.

For the Kafka kind, we just register the broker config on the tenant.
"""

from __future__ import annotations

from vastde_orch.clients.vms import EnsureOutcome, VmsClient
from vastde_orch.config.models import (
    KafkaEventBrokerSpec,
    VastEventBrokerSpec,
)
from vastde_orch.reconciler import Plan


def provision_vast_broker(
    vms: VmsClient,
    spec: VastEventBrokerSpec,
    *,
    tenant_id: int,
    dataengine_group: str,
    plan: Plan | None = None,
) -> Plan:
    plan = plan or Plan()

    # 1. Bucket owner — must already exist (created in identity.provision_identity).
    owner = vms.get_or_placeholder("users", key_field="name", key_value=spec.bucket_owner)

    # 2. VIP pool.
    plan.record(
        vms.ensure_vippool(
            spec.vip_pool.name,
            tenant_id=tenant_id,
            cidr=spec.vip_pool.cidr,
            ip_range_start=str(spec.vip_pool.ip_range[0]),
            ip_range_end=str(spec.vip_pool.ip_range[1]),
            domain_name=spec.vip_pool.domain_name,
        )
    )
    vippool = vms.get_or_placeholder("vippools", key_field="name", key_value=spec.vip_pool.name)

    # 3. View policy with bucket listing perm to the DE group.
    #    Reuse an existing S3_NATIVE policy on this tenant if one is already
    #    there (e.g. created by `scripts/setup_tenant.py` at tenant bootstrap).
    #    Avoids duplicating policies for the same tenant.
    policy = _pick_s3_native_policy(vms, tenant_id, spec.view_policy)
    if policy is None:
        plan.record(
            vms.ensure_viewpolicy(
                spec.view_policy,
                tenant_id=tenant_id,
                security_flavor="S3_NATIVE",
                bucket_listing_groups=[dataengine_group],
            )
        )
        policy = vms.get_or_placeholder(
            "viewpolicies", key_field="name", key_value=spec.view_policy,
        )
    else:
        listing = policy.get("s3_bucket_listing_groups") or []
        if dataengine_group not in listing:
            # The reused policy doesn't list the DE group for bucket listing.
            # The broker view will still work; only s3:ListAllMyBuckets is
            # affected. Surface as a non-fatal warning rather than mutating
            # a tenant-owned policy out from under the operator.
            print(
                f"  WARN: reusing existing view policy {policy['name']!r} but "
                f"its s3_bucket_listing_groups does not include {dataengine_group!r}. "
                "DE group will not see buckets via s3:ListAllMyBuckets until added."
            )

    # 4. Kafka broker view. Must include S3 and DATABASE alongside KAFKA per
    #    VAST 5.4 — the broker is realized as a single view with all three
    #    protocols enabled. See docs/vms-endpoints-reference.md.
    plan.record(
        vms.ensure_view(
            spec.view_path,
            policy_id=policy["id"],
            protocols=["S3", "DATABASE", "KAFKA"],
            tenant_id=tenant_id,
            bucket_name=spec.bucket_name,
            bucket_owner=owner["name"],
            vip_pool_ids=[vippool["id"]],
            create_dir=True,
        )
    )
    view = vms.get_or_placeholder("views", key_field="path", key_value=spec.view_path)

    # 5. Topics on the broker. Uses /topics/?tenant_id=N&database_name=BUCKET
    #    where BUCKET is the broker view's `bucket` field. The implicit schema
    #    is always 'kafka_topics' for KAFKA-protocol views.
    #    See docs/vms-endpoints-reference.md `/topics/`.
    from vastde_orch.clients.vms import DiffResult, EnsureOutcome
    for topic in (spec.default_topic, spec.deadletter_topic):
        try:
            plan.record(
                vms.ensure_topic(
                    topic.name,
                    tenant_id=tenant_id,
                    database_name=spec.bucket_name,
                )
            )
        except Exception as exc:
            plan.record(EnsureOutcome(
                result=DiffResult.UNCHANGED, resource="topics",
                name=topic.name, id=None,
                drift={"error": str(exc)[:200]},
            ))

    return plan


def provision_kafka_broker(
    vms: VmsClient,
    spec: KafkaEventBrokerSpec,
    *,
    tenant_id: int,
    plan: Plan | None = None,
) -> Plan:
    plan = plan or Plan()
    plan.record(
        vms.ensure(
            "kafkabrokers",
            key_field="name",
            key_value=spec.name,
            spec={
                "name": spec.name,
                "tenant_id": tenant_id,
                "hosts": spec.hosts,
                "port": spec.port,
            },
        )
    )
    return plan


def _pick_s3_native_policy(
    vms: VmsClient, tenant_id: int, preferred_name: str,
) -> dict | None:
    """Return an S3_NATIVE view policy already on the tenant, or None.

    Preference order:
      1. A policy whose name == preferred_name (lets operators be explicit
         by naming the tenant's existing policy in vastde.yaml).
      2. The first S3_NATIVE policy on the tenant.

    Returns None if no S3_NATIVE policy exists for this tenant — caller
    should create one.
    """
    try:
        all_policies = list(vms.raw.viewpolicies.get())
    except Exception:
        return None  # if listing fails, fall through to the create path
    matches = [
        p for p in all_policies
        if p.get("tenant_id") == tenant_id and p.get("flavor") == "S3_NATIVE"
    ]
    if not matches:
        return None
    by_name = next((p for p in matches if p.get("name") == preferred_name), None)
    return by_name or matches[0]


def get_broker_view_id(vms: VmsClient, spec: VastEventBrokerSpec | KafkaEventBrokerSpec) -> int | None:
    if isinstance(spec, VastEventBrokerSpec):
        v = vms.get_or_raise("views", key_field="path", key_value=spec.view_path)
        return v["id"]
    return None  # Kafka brokers don't have a VAST view

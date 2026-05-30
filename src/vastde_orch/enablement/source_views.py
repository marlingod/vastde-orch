"""Source views (S3 buckets) on which element triggers will watch for events.

Per PDF p.32, source views are created by tenant/cluster admins (not by
application users) and must have S3 Native security flavor.
"""

from __future__ import annotations

from vastde_orch.clients.vms import VmsClient
from vastde_orch.config.models import SourceViewSpec
from vastde_orch.reconciler import Plan


def provision_source_views(
    vms: VmsClient,
    specs: list[SourceViewSpec],
    *,
    tenant_id: int | None = None,
    plan: Plan | None = None,
) -> Plan:
    plan = plan or Plan()
    for sv in specs:
        policy = vms.get_or_placeholder("viewpolicies", key_field="name", key_value=sv.policy)
        owner = vms.get_or_placeholder("users", key_field="name", key_value=sv.owner)
        plan.record(
            vms.ensure_view(
                sv.path,
                policy_id=policy["id"],
                protocols=["S3"],
                tenant_id=tenant_id,
                bucket_name=sv.bucket,
                bucket_owner=owner["name"],
                create_dir=True,
            )
        )
    return plan

"""Identity provisioning: group, users, S3 access keys, identity policy.

Translates the YAML `enablement.identity` block (PDF pp.24-31) into a series
of idempotent VMS API calls. Returns a Plan with one outcome per resource.

NOTE: "DataEngine identity policy" lives at /s3policies/ on this VAST version,
NOT at /identitypolicies/ or /identitypolicybindings/. See
docs/vms-endpoints-reference.md for the full endpoint catalogue.
"""

from __future__ import annotations

import json
import time

from vastde_orch.clients.vms import EnsureOutcome, VmsClient
from vastde_orch.config.models import IdentitySpec
from vastde_orch.reconciler import Plan


def provision_identity(
    vms: VmsClient, spec: IdentitySpec, *, plan: Plan | None = None
) -> Plan:
    plan = plan or Plan()

    # 1. Group.
    plan.record(
        vms.ensure_group(spec.group.name, gid=spec.group.gid, provider=spec.group.provider)
    )

    # 2. Users — each in the group, with bucket create/delete perms granted at user level
    #    (alternately, can be granted via identity policy on the group).
    for u in spec.users:
        plan.record(
            vms.ensure_user(
                u.name,
                uid=u.uid,
                provider=spec.group.provider,
                leading_group=u.leading_group or spec.group.name,
            )
        )

    return plan


def generate_user_keys(vms: VmsClient, usernames: list[str]) -> dict[str, dict[str, str]]:
    """For each user, generate an S3 access-key pair via VMS.

    Returns {username: {access_key, secret_key}}. The secret_key is only
    available at creation time — surface it to the operator immediately.
    """
    keys: dict[str, dict[str, str]] = {}
    for name in usernames:
        user = vms.get_or_raise("users", key_field="name", key_value=name)
        keys[name] = vms.generate_s3_keys(user["id"])
    return keys


def build_dataengine_policy_doc(*, policy_id: str | None = None) -> str:
    """Return the canonical DataEngine S3 identity-policy JSON document.

    Matches the policy installed on the wi-tenant reference (s3policy id=14)
    and the PDF spec on p.27. The `policy_id` is purely a label inside the
    document; if omitted a deterministic timestamp-based id is used.
    """
    pid = policy_id or f"DataEnginePolicy{int(time.time())}"
    doc = {
        "Id": pid,
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "DataengineTablesAccess",
                "Action": [
                    "s3:HeadBucket",
                    "s3:Tabular*Transaction",
                    "s3:TabularList*",
                    "s3:TabularGet*",
                    "s3:TabularQueryData",
                ],
                "Effect": "Allow",
                "Resource": ["dataengine-*", "dataengine-*/*"],
            },
            {
                "Sid": "DataEngineDefault",
                "Action": [
                    "dataengine:CreateTrigger",
                    "dataengine:CreateFunction",
                    "dataengine:CreatePipeline",
                ],
                "Effect": "Allow",
                "Resource": [
                    "vast:dataengine:triggers:*",
                    "vast:dataengine:functions:*",
                    "vast:dataengine:pipelines:*",
                ],
            },
        ],
    }
    return json.dumps(doc)


def attach_dataengine_policy_to_group(
    vms: VmsClient,
    group_name: str,
    tenant_name: str,
    *,
    tenant_id: int,
    policy_name: str | None = None,
) -> EnsureOutcome:
    """Create the DataEngine S3 identity policy and bind it to a group.

    Uses /s3policies/ POST (the real endpoint on VAST 5.4 — see
    docs/vms-endpoints-reference.md). The body includes:
      - name        : human label (default: data-engine-<tenant>)
      - tenant_id   : which tenant the policy belongs to
      - enabled     : True
      - groups      : [group_name]
      - policy      : JSON string of the IAM policy document

    Idempotent: re-running on an existing policy with the same fields is a
    no-op; if `groups` differs, it's PATCHed.
    """
    name = policy_name or f"data-engine-{tenant_name}"
    return vms.ensure(
        "s3policies",
        key_field="name",
        key_value=name,
        spec={
            "name": name,
            "tenant_id": tenant_id,
            "enabled": True,
            "groups": [group_name],
            "policy": build_dataengine_policy_doc(),
        },
        # Don't fight the API on the policy document's exact byte form
        # (timestamps in the `Id` field will differ). Treat only the binding
        # as drift-worthy.
        patchable_fields={"groups", "enabled"},
    )

"""Defensive filters for resources VAST auto-creates when DataEngine is enabled.

When `data_engine_enabled = True` is toggled on a tenant, VAST automatically
creates several views and view policies. They are not in our YAML and must
never be modified or deleted by `vastde-orch` — they are managed by VAST.

See docs/vms-endpoints-reference.md → "Auto-managed resources" for the
canonical list discovered against `wi-tenant`.
"""

from __future__ import annotations

import re

# View paths that VAST auto-creates per DE-enabled tenant.
# - `/dataengine` is a fixed name.
# - `/dataengine-telemetries-<uuid>` has a fresh UUID per enable.
_AUTO_VIEW_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^/dataengine$"),
    re.compile(r"^/dataengine-telemetries-[0-9a-f-]+$"),
)

# View policy NAMES that VAST auto-creates per DE-enabled tenant.
_AUTO_VIEWPOLICY_NAMES: frozenset[str] = frozenset({
    "dataengine-policy",
    "vast-data-engine-telemetries-policy",
})

# Tenant-default policy NAMES auto-created at tenant creation
# (pattern: "<tenant>__default_policy", "<tenant>__s3_default_policy").
_AUTO_TENANT_POLICY_PATTERN = re.compile(r"^.+__(s3_)?default_policy$")


def is_auto_view(path: str) -> bool:
    """Return True if `path` is a VAST-managed view that we must not touch."""
    return any(p.match(path) for p in _AUTO_VIEW_PATTERNS)


def is_auto_viewpolicy(name: str) -> bool:
    """Return True if `name` is a VAST-managed view policy."""
    if name in _AUTO_VIEWPOLICY_NAMES:
        return True
    return bool(_AUTO_TENANT_POLICY_PATTERN.match(name))


def filter_user_views(views: list[dict]) -> list[dict]:
    """Drop VAST-managed views from a list of view dicts."""
    return [v for v in views if not is_auto_view(v.get("path", ""))]


def filter_user_viewpolicies(policies: list[dict]) -> list[dict]:
    """Drop VAST-managed view policies from a list of policy dicts."""
    return [p for p in policies if not is_auto_viewpolicy(p.get("name", ""))]

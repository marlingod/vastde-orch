"""Read-only VMS probing for the wizard.

The wizard offers "pick an existing tenant / view / user" by calling these
helpers. Results are cached for the lifetime of one `VmsProbe` instance
(typically one wizard run). On VMS error we warn and return an empty list —
the wizard then degrades to manual-entry mode without aborting.
"""

from __future__ import annotations

import click

from vastde_orch.clients.vms import VmsClient


class VmsProbe:
    """Cached, fail-soft read-only enumeration of VMS resources."""

    def __init__(self, vms: VmsClient | None) -> None:
        self._vms = vms
        self._cache: dict[str, list[dict]] = {}

    @property
    def available(self) -> bool:
        return self._vms is not None

    def _list(self, resource: str) -> list[dict]:
        if not self._vms:
            return []
        if resource in self._cache:
            return self._cache[resource]
        try:
            result = getattr(self._vms.raw, resource).get() or []
        except Exception as exc:
            click.echo(
                f"  Warning: could not list {resource} on VMS ({exc}). "
                "Continuing with manual entry.",
                err=True,
            )
            result = []
        self._cache[resource] = result
        return result

    # ── typed conveniences ─────────────────────────────────────────────

    def tenants(self) -> list[str]:
        return [t.get("name", "") for t in self._list("tenants") if t.get("name")]

    def users(self) -> list[str]:
        return [u.get("name", "") for u in self._list("users") if u.get("name")]

    def views(self) -> list[str]:
        return [v.get("path", "") for v in self._list("views") if v.get("path")]

    def viewpolicies(self) -> list[str]:
        return [p.get("name", "") for p in self._list("viewpolicies") if p.get("name")]

    def container_registries(self) -> list[str]:
        return [
            r.get("name", "") for r in self._list("containerregistries") if r.get("name")
        ]

    def k8sclusters(self) -> list[str]:
        return [k.get("name", "") for k in self._list("k8sclusters") if k.get("name")]

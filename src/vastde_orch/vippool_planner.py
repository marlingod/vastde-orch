"""Compute claimed/free IP ranges across VIP pools and pick gaps to fill.

Used by:
  - scripts/list_vippools.py    — operator-facing report
  - scripts/setup_tenant.py     — auto-allocate a range when the operator
                                  omits one (or asks for one already taken)

Read-only over VMS data; the planner never mutates.
"""

from __future__ import annotations

import ipaddress
from collections import defaultdict
from dataclasses import dataclass


@dataclass
class ClaimedRange:
    start: ipaddress.IPv4Address
    end: ipaddress.IPv4Address
    pool_name: str
    tenant_id: int | None
    role: str

    @property
    def size(self) -> int:
        return int(self.end) - int(self.start) + 1


@dataclass
class FreeRange:
    start: ipaddress.IPv4Address
    end: ipaddress.IPv4Address

    @property
    def size(self) -> int:
        return int(self.end) - int(self.start) + 1


def all_ip_claims(vms) -> list[dict]:
    """Return every IP claim on the VMS as a list of pool-like dicts.

    Merges `/vippools/` (native `ip_ranges`) with `/computeclusters/` (whose
    `static_ip_ranges` claim IPs from the same subnet but were previously
    invisible to overlap detection — VMS 400s "Given range overlaps with
    <cc> from computecluster <name>" for ranges that collided with a
    compute cluster's static IPs even though no VIP pool held them).

    Each returned dict has: `name`, `ip_ranges`, `tenant_id`, `role`. That
    matches the subset of fields the planner functions read, so callers
    can pass the merged list unchanged to `claims_overlapping_subnet` /
    `claimed_per_subnet`.
    """
    pools = list(vms.raw.vippools.get())

    # Best-effort: not every VAST version exposes /computeclusters/. Live-
    # observed 404 on var202 (older cluster / DE-not-licensed), 200 on
    # var204. When it's missing there's nothing we can cross-check against,
    # so we fall back silently to vippools-only (same behavior as before
    # the compute-cluster overlap fix landed).
    try:
        cc_resp = vms.raw.computeclusters.get()
    except Exception as exc:
        # Only swallow the "endpoint doesn't exist" case; re-raise auth /
        # network / permission failures so they surface clearly.
        msg = str(exc)
        if "404" in msg or "Not Found" in msg:
            return pools
        raise

    # /computeclusters/ returns {count, next, previous, results}, not a bare
    # list — unlike /vippools/. Handle both shapes defensively.
    if isinstance(cc_resp, dict) and "results" in cc_resp:
        compute = cc_resp["results"]
    else:
        compute = list(cc_resp)

    for cc in compute:
        pools.append({
            "name": f"[computecluster] {cc.get('name', '?')}",
            "ip_ranges": cc.get("static_ip_ranges") or [],
            "tenant_id": None,
            "role": "COMPUTE_CLUSTER",
        })
    return pools


def claimed_per_subnet(
    pools: list[dict],
) -> dict[ipaddress.IPv4Network, list[ClaimedRange]]:
    """Group claimed ranges by the implied subnet (range start + pool subnet_cidr)."""
    by_subnet: dict[ipaddress.IPv4Network, list[ClaimedRange]] = defaultdict(list)
    for p in pools:
        cidr = p.get("subnet_cidr")
        ranges = p.get("ip_ranges") or []
        if not cidr or not ranges:
            continue
        for r in ranges:
            try:
                start = ipaddress.IPv4Address(r[0])
                end = ipaddress.IPv4Address(r[1])
            except (ValueError, IndexError):
                continue
            try:
                subnet = ipaddress.ip_network(f"{start}/{cidr}", strict=False)
            except ValueError:
                continue
            if not isinstance(subnet, ipaddress.IPv4Network):
                continue
            by_subnet[subnet].append(
                ClaimedRange(
                    start=start,
                    end=end,
                    pool_name=p.get("name", "?"),
                    tenant_id=p.get("tenant_id"),
                    role=p.get("role", "?"),
                )
            )
    for subnet in by_subnet:
        by_subnet[subnet].sort(key=lambda c: int(c.start))
    return by_subnet


def claims_overlapping_subnet(
    pools: list[dict],
    target: ipaddress.IPv4Network,
) -> list[ClaimedRange]:
    """All claimed ranges (across all pools) that overlap with `target`.

    Each pool stores its own `subnet_cidr`, which need not match the subnet
    we're trying to allocate inside. For example, an existing VMS cluster
    pool can be declared as /16 yet its `ip_ranges` only use a small slice
    that lands inside the /24 you're about to claim. `claimed_per_subnet`
    buckets by the pool's declared mask, so the /16 pool's claim isn't
    found when you ask for the /24 bucket — leading to a 400
    "Given range … overlaps with … from vippool main" when we try to POST.

    This function is the right primitive for "pick a free gap in this
    subnet": it iterates every claimed range, clips it to `target`'s
    bounds, and returns the clipped ranges. Pools whose ranges don't touch
    `target` at all are skipped.
    """
    target_first = int(target.network_address)
    target_last = int(target.broadcast_address)
    out: list[ClaimedRange] = []
    for p in pools:
        ranges = p.get("ip_ranges") or []
        for r in ranges:
            try:
                start = ipaddress.IPv4Address(r[0])
                end = ipaddress.IPv4Address(r[1])
            except (ValueError, IndexError):
                continue
            s, e = int(start), int(end)
            if s > target_last or e < target_first:
                continue
            # Clip to the target subnet so free_ranges_in_subnet's
            # cursor advancement stays within bounds.
            s = max(s, target_first)
            e = min(e, target_last)
            out.append(
                ClaimedRange(
                    start=ipaddress.IPv4Address(s),
                    end=ipaddress.IPv4Address(e),
                    pool_name=p.get("name", "?"),
                    tenant_id=p.get("tenant_id"),
                    role=p.get("role", "?"),
                )
            )
    out.sort(key=lambda c: int(c.start))
    return out


def free_ranges_in_subnet(
    subnet: ipaddress.IPv4Network,
    claims: list[ClaimedRange],
) -> list[FreeRange]:
    """Return contiguous unclaimed ranges inside the subnet (excludes .0 and .broadcast)."""
    first = int(subnet.network_address) + 1
    last = int(subnet.broadcast_address) - 1
    if first > last:
        return []

    merged: list[tuple[int, int]] = []
    for c in sorted(claims, key=lambda c: int(c.start)):
        s, e = int(c.start), int(c.end)
        if merged and s <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    free: list[FreeRange] = []
    cursor = first
    for s, e in merged:
        if cursor < s:
            free.append(
                FreeRange(
                    start=ipaddress.IPv4Address(cursor),
                    end=ipaddress.IPv4Address(min(s - 1, last)),
                )
            )
        cursor = max(cursor, e + 1)
        if cursor > last:
            break
    if cursor <= last:
        free.append(
            FreeRange(
                start=ipaddress.IPv4Address(cursor),
                end=ipaddress.IPv4Address(last),
            )
        )
    return free


def is_range_available(
    requested_start: ipaddress.IPv4Address,
    requested_end: ipaddress.IPv4Address,
    free: list[FreeRange],
) -> bool:
    """True iff [requested_start, requested_end] is fully inside ONE free gap."""
    rs, re_ = int(requested_start), int(requested_end)
    if rs > re_:
        return False
    return any(int(f.start) <= rs and re_ <= int(f.end) for f in free)


def pick_gap(
    free: list[FreeRange],
    size: int,
) -> tuple[ipaddress.IPv4Address, ipaddress.IPv4Address] | None:
    """Pick the first `size` IPs of the SMALLEST gap that fits (least-wasteful).

    Returns (start, end) of the chosen range, or None if no gap fits.
    """
    candidates = [f for f in free if f.size >= size]
    if not candidates:
        return None
    best = min(candidates, key=lambda f: (f.size, int(f.start)))
    start = best.start
    end = ipaddress.IPv4Address(int(best.start) + size - 1)
    return start, end


def format_range(
    start: ipaddress.IPv4Address,
    end: ipaddress.IPv4Address,
) -> str:
    """Render a range in compact VMS-UI style (e.g. '172.200.204.[164-169]')."""
    if start == end:
        return str(start)
    s, e = str(start), str(end)
    common = s.rsplit(".", 1)[0]
    if e.startswith(common + "."):
        return f"{common}.[{s.rsplit('.', 1)[1]}-{e.rsplit('.', 1)[1]}]"
    return f"{s} – {e}"

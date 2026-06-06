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

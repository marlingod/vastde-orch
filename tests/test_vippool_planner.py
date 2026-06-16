"""Tests for vippool_planner — focused on the cross-subnet overlap bug.

The original `claimed_per_subnet` buckets claims by each pool's own
declared `subnet_cidr`. When a pre-existing cluster pool is declared
with a wide mask (e.g. /16) but uses IPs that fall inside the /24 we're
about to claim, that pool's claim is invisible to the bucket lookup and
the auto-picker hands us an overlapping range. `claims_overlapping_subnet`
is the right primitive: it checks raw IP-range overlap with the target
subnet regardless of how each pool declared its own cidr.
"""

from __future__ import annotations

import ipaddress

from vastde_orch.vippool_planner import (
    claims_overlapping_subnet,
    free_ranges_in_subnet,
    pick_gap,
)


def test_finds_overlapping_claim_from_wider_cidr_pool() -> None:
    """The bug we hit live: `main` declared with /16 but using .203.[1-6]."""
    pools = [
        {
            "name": "main",
            "subnet_cidr": 16,
            "ip_ranges": [["172.200.203.1", "172.200.203.6"]],
        },
    ]
    target = ipaddress.IPv4Network("172.200.203.0/24")
    claims = claims_overlapping_subnet(pools, target)
    assert len(claims) == 1
    assert str(claims[0].start) == "172.200.203.1"
    assert str(claims[0].end) == "172.200.203.6"
    assert claims[0].pool_name == "main"


def test_skips_claims_outside_target() -> None:
    pools = [
        {"name": "elsewhere", "subnet_cidr": 24,
         "ip_ranges": [["10.0.0.10", "10.0.0.20"]]},
    ]
    target = ipaddress.IPv4Network("172.200.203.0/24")
    assert claims_overlapping_subnet(pools, target) == []


def test_clips_claims_that_straddle_target_boundary() -> None:
    """A pool whose range starts before target_first should be clipped."""
    pools = [
        {"name": "wide", "subnet_cidr": 16,
         "ip_ranges": [["172.200.202.250", "172.200.203.5"]]},
    ]
    target = ipaddress.IPv4Network("172.200.203.0/24")
    claims = claims_overlapping_subnet(pools, target)
    assert len(claims) == 1
    assert str(claims[0].start) == "172.200.203.0"
    assert str(claims[0].end) == "172.200.203.5"


def test_free_ranges_after_finding_main_pool_claim() -> None:
    """End-to-end: planner correctly excludes main's .1-.6 from free gaps."""
    pools = [
        {"name": "main", "subnet_cidr": 16,
         "ip_ranges": [["172.200.203.1", "172.200.203.6"]]},
    ]
    target = ipaddress.IPv4Network("172.200.203.0/24")
    claims = claims_overlapping_subnet(pools, target)
    free = free_ranges_in_subnet(target, claims)
    # First free gap must NOT start at .1 (which would overlap main).
    assert int(free[0].start) > int(ipaddress.IPv4Address("172.200.203.6"))
    picked = pick_gap(free, 3)
    assert picked is not None
    chosen_start, _ = picked
    assert int(chosen_start) > int(ipaddress.IPv4Address("172.200.203.6"))


def test_multiple_pools_merged_correctly() -> None:
    pools = [
        {"name": "a", "subnet_cidr": 24,
         "ip_ranges": [["172.200.203.10", "172.200.203.15"]]},
        {"name": "b", "subnet_cidr": 16,
         "ip_ranges": [["172.200.203.20", "172.200.203.25"]]},
    ]
    target = ipaddress.IPv4Network("172.200.203.0/24")
    claims = claims_overlapping_subnet(pools, target)
    assert {c.pool_name for c in claims} == {"a", "b"}
    # Sorted by start
    assert int(claims[0].start) < int(claims[1].start)

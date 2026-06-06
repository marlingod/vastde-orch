"""List existing VAST VIP pools + show available IP ranges.

Pulls every VIP pool from VMS, groups by the implied subnet (first IP +
subnet_cidr), prints claimed ranges, then computes and prints
*available* gaps inside that subnet — so you can see where to put a new
pool without colliding.

Optional `--size N` filters the suggested gaps to ones that fit at least N IPs
and shows the smallest qualifying gap per subnet (the "least wasteful" pick).

Read-only — never POSTs. Safe to run any time.

Usage:
    # Use creds from .env
    python scripts/list_vippools.py

    # Suggest gaps that hold at least 4 IPs
    python scripts/list_vippools.py --size 4

    # Scope to a specific subnet
    python scripts/list_vippools.py --subnet 172.200.203.0/24
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import sys

from dotenv import load_dotenv

try:
    from vastde_orch.clients.vms import VmsClient
    from vastde_orch.config.models import VmsSpec
    from vastde_orch.vippool_planner import (
        ClaimedRange,
        FreeRange,
        claimed_per_subnet,
        format_range as _fmt_range,
        free_ranges_in_subnet,
    )
except ImportError as exc:
    sys.exit(
        f"FATAL: cannot import vastde_orch ({exc}).\n"
        "Install via: pip install -e /path/to/dataengine"
    )


def fetch_pools(vms: VmsClient) -> list[dict]:
    """Pull every vippool from VMS. Read-only."""
    return list(vms.raw.vippools.get())


def print_subnet(
    subnet: ipaddress.IPv4Network,
    claims: list[ClaimedRange],
    free: list[FreeRange],
    *,
    min_size: int | None = None,
    tenant_names: dict[int, str] | None = None,
) -> None:
    tenant_names = tenant_names or {}
    print(f"\n── Subnet {subnet} ({len(claims)} claim(s)) ──")

    if claims:
        print("  CLAIMED:")
        for c in claims:
            tenant = tenant_names.get(c.tenant_id, str(c.tenant_id) if c.tenant_id else "all")
            print(
                f"    {_fmt_range(c.start, c.end):<28} "
                f"{c.pool_name:<22} role={c.role:<10} tenant={tenant} "
                f"({c.size} IP{'s' if c.size != 1 else ''})"
            )

    qualifying = [f for f in free if min_size is None or f.size >= min_size]
    if not qualifying:
        msg = "no available gaps" if not free else f"no gaps with ≥{min_size} IPs"
        print(f"  AVAILABLE: {msg}")
        return

    print(f"  AVAILABLE ({len(qualifying)} gap(s)" +
          (f", min size {min_size}" if min_size else "") + "):")
    for f in qualifying:
        print(f"    {_fmt_range(f.start, f.end):<28} ({f.size} IP{'s' if f.size != 1 else ''})")

    if min_size is not None:
        best = min(qualifying, key=lambda f: (f.size, int(f.start)))
        print(f"  ⮕ SUGGESTED (smallest gap fitting ≥{min_size}): "
              f"{_fmt_range(best.start, best.end)}")


# ── main ────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--address",  help="VMS hostname (default: $VMS_ADDRESS)")
    ap.add_argument("--user",     help="VMS user (default: $VMS_USER)")
    ap.add_argument("--password", help="VMS password (default: $VMS_PASSWORD)")
    ap.add_argument("--tenant",   default="default",
                    help="Tenant context for vastpy (default: 'default' — read-only call, any tenant works)")
    ap.add_argument("--size", type=int,
                    help="Minimum IPs the suggested gap must hold")
    ap.add_argument("--subnet",
                    help="Scope output to one subnet (e.g. 172.200.203.0/24)")
    args = ap.parse_args()

    load_dotenv(".env")

    address  = args.address  or os.environ.get("VMS_ADDRESS")
    user     = args.user     or os.environ.get("VMS_USER")
    password = args.password or os.environ.get("VMS_PASSWORD")
    if not (address and user and password):
        sys.exit("FATAL: VMS_ADDRESS, VMS_USER, VMS_PASSWORD must be set "
                 "(via env or --address/--user/--password)")

    vms = VmsClient(VmsSpec(
        address=address, user=user, password=password, tenant=args.tenant,
    ), dry_run=True)  # read-only

    # Pull pools + a tenant ID→name index for nicer output
    pools = fetch_pools(vms)
    tenant_names: dict[int, str] = {}
    try:
        for t in vms.raw.tenants.get():
            if "id" in t and "name" in t:
                tenant_names[t["id"]] = t["name"]
    except Exception:
        pass  # tenant lookup is decorative; don't fail the report on it

    if not pools:
        print("No VIP pools found.")
        return 0

    print(f"\n{len(pools)} VIP pool(s) across this VMS.")

    by_subnet = claimed_per_subnet(pools)

    # Optional subnet filter
    if args.subnet:
        try:
            wanted = ipaddress.ip_network(args.subnet, strict=False)
        except ValueError as exc:
            sys.exit(f"FATAL: bad --subnet {args.subnet!r}: {exc}")
        by_subnet = {s: c for s, c in by_subnet.items() if s == wanted}
        if not by_subnet:
            print(f"\nNo pools found in subnet {wanted}.")
            return 0

    # Sort subnets by number of claims (busiest first)
    for subnet in sorted(by_subnet, key=lambda s: (-len(by_subnet[s]), int(s.network_address))):
        claims = by_subnet[subnet]
        free   = free_ranges_in_subnet(subnet, claims)
        print_subnet(subnet, claims, free, min_size=args.size, tenant_names=tenant_names)

    return 0


if __name__ == "__main__":
    sys.exit(main())

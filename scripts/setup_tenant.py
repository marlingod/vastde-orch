"""DEPRECATED shim — use `vastde-orch tenant create|destroy` instead.

This script used to be the only entry point for tenant bootstrap. Logic now
lives in `vastde_orch.bootstrap.tenant` and is exposed as proper CLI commands
(`vastde-orch tenant create`, `vastde-orch tenant destroy`). This file is
kept ONLY so existing automation that invokes
`python scripts/setup_tenant.py -c cfg.yaml [--plan|--destroy|--yes]`
keeps working unchanged. It just forwards to the module.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from vastde_orch.bootstrap.tenant import (
    create_tenant,
    destroy_tenant,
    load_tenant_config,
)
from vastde_orch.clients.vms import VmsClient
from vastde_orch.config.models import VmsSpec


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-c", "--config", required=True, type=Path)
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--destroy", action="store_true")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    sys.stderr.write(
        "NOTE: scripts/setup_tenant.py is deprecated — use "
        "`vastde-orch tenant " + ("destroy" if args.destroy else "create") + " "
        f"-c {args.config}" + (" --plan" if args.plan else "")
        + (" --yes" if args.yes and args.destroy else "") + "` instead.\n\n"
    )

    cfg = load_tenant_config(args.config)
    vms_cfg = cfg["vms"]
    vms = VmsClient(VmsSpec(
        address=vms_cfg["address"],
        user=vms_cfg["user"],
        password=vms_cfg["password"],
        tenant=cfg["tenant"]["name"],
    ), dry_run=args.plan)

    if args.destroy:
        return destroy_tenant(cfg, vms, yes=args.yes)
    return create_tenant(cfg, vms)


if __name__ == "__main__":
    sys.exit(main())

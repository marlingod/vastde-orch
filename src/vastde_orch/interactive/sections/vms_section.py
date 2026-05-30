"""Wizard: VMS connection + tenant selection."""

from __future__ import annotations

from typing import Any

from vastde_orch.interactive._prompts import Prompter
from vastde_orch.interactive._vms_probe import VmsProbe

CREATE_NEW = "+ create new"


def build_vms_section(probe: VmsProbe, p: Prompter) -> dict[str, Any]:
    address = p.text("vms.address", "VMS address", default="vms.example.com")
    auth = p.choice("vms.auth", "Authentication method", choices=["token", "user_password"])

    out: dict[str, Any] = {"address": address, "tenant": ""}

    if auth == "token":
        # Store the env var name, not the literal token, in YAML.
        token_env = p.text(
            "vms.token_env",
            "Environment variable holding the API token",
            default="VMS_TOKEN",
        )
        out["token"] = "${" + token_env + "}"
    else:
        user_env = p.text("vms.user_env", "Env var for VMS username", default="VMS_USER")
        pw_env = p.text("vms.password_env", "Env var for VMS password", default="VMS_PASSWORD")
        out["user"] = "${" + user_env + "}"
        out["password"] = "${" + pw_env + "}"

    # Tenant: offer existing if we can probe.
    existing = probe.tenants() if probe.available else []
    if existing:
        choices = existing + [CREATE_NEW]
        pick = p.choice("vms.tenant", "Tenant", choices=choices, default=existing[0])
        if pick == CREATE_NEW:
            out["tenant"] = p.text("vms.new_tenant_name", "New tenant name")
        else:
            out["tenant"] = pick
    else:
        out["tenant"] = p.text("vms.tenant", "Tenant name")

    api_version = p.text("vms.api_version", "API version (blank for latest)", default="")
    if api_version:
        out["api_version"] = api_version

    return out

"""Cluster-admin tenant bootstrap — the create/destroy half of the DE lifecycle.

Counterpart to `vastde_orch.enablement` (tenant-admin operations):

    tenant create   (cluster-admin)   ← here: tenant + identity + DE policy
    enable          (tenant-admin)    register K8s, broker view, DE on
    apply           (tenant-admin)    pipelines
    destroy         (tenant-admin)    pipelines + optional enablement disable
    tenant destroy  (cluster-admin)   ← here: tear down the bootstrap
"""

from vastde_orch.bootstrap.tenant import (
    create_tenant,
    destroy_tenant,
    load_tenant_config,
)
from vastde_orch.bootstrap.tenant_enable import (
    compose_enablement_spec,
    discover_tenant_state,
    load_tenant_enable_config,
    tenant_enable,
)

__all__ = [
    "create_tenant",
    "destroy_tenant",
    "load_tenant_config",
    "compose_enablement_spec",
    "discover_tenant_state",
    "load_tenant_enable_config",
    "tenant_enable",
]

"""Container registry registration on the tenant (PDF p.36-37)."""

from __future__ import annotations

import os

from vastde_orch.clients.vms import EnsureOutcome, VmsClient
from vastde_orch.config.models import ContainerRegistrySpec


class RegistryAuthError(RuntimeError):
    """Raised when registry auth env vars are missing."""


def provision_container_registry(
    vms: VmsClient,
    spec: ContainerRegistrySpec,
    *,
    tenant_id: int,
    k8scluster_id: int,
) -> EnsureOutcome:
    username = password = secret_name = None

    if spec.auth.method == "user_credentials":
        username = os.environ.get(spec.auth.username_env or "")
        password = os.environ.get(spec.auth.password_env or "")
        if not (username and password):
            raise RegistryAuthError(
                f"registry {spec.name!r}: env vars {spec.auth.username_env}/"
                f"{spec.auth.password_env} not set"
            )
    elif spec.auth.method == "kubernetes_secret":
        secret_name = spec.auth.kubernetes_secret_name

    return vms.ensure_container_registry(
        spec.name,
        base_url=spec.base_url,
        tenant_id=tenant_id,
        k8scluster_id=k8scluster_id,
        auth_method=spec.auth.method,
        username=username,
        password=password,
        secret_name=secret_name,
    )

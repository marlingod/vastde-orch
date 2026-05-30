"""Stage A K8s/Zarf bootstrap (PDF p.7-10).

Ordering (one-shot, idempotent):
  1. Check & set inotify limits.
  2. zarf init (skipped if Zarf already installed — detected via the zarf-* namespace).
  3. Create + label namespaces with `zarf.dev/vast=mutate`.
  4. zarf package deploy of the VAST DataEngine package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from vastde_orch.clients.kube import (
    ensure_inotify_limits,
    ensure_vast_namespaces,
    kubectl_namespace_exists,
    zarf_init,
    zarf_package_deploy,
)
from vastde_orch.config.models import KubernetesSpec


@dataclass
class BootstrapReport:
    inotify_changes: dict[str, tuple[int, int]] = field(default_factory=dict)
    zarf_initialized: bool = False
    namespaces_created: list[str] = field(default_factory=list)
    package_deployed: bool = False
    skipped_dry_run: bool = False


def bootstrap_k8s(spec: KubernetesSpec, *, dry_run: bool = False) -> BootstrapReport:
    report = BootstrapReport()

    if dry_run:
        report.skipped_dry_run = True
        return report

    report.inotify_changes = ensure_inotify_limits(
        spec.inotify.instances, spec.inotify.watches
    )

    # Zarf detection: if `zarf` namespace exists, it's already installed.
    if not kubectl_namespace_exists("zarf", spec.kubeconfig) and spec.zarf_init_path:
        zarf_init(Path(spec.zarf_init_path), kubeconfig=spec.kubeconfig)
        report.zarf_initialized = True

    report.namespaces_created = ensure_vast_namespaces(spec.kubeconfig)

    if spec.zarf_package_path:
        zarf_package_deploy(Path(spec.zarf_package_path), kubeconfig=spec.kubeconfig)
        report.package_deployed = True

    return report

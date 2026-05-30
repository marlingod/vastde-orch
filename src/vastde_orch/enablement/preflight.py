"""Pre-flight checks run before any mutation.

Each check raises a PreflightError with a human-friendly message describing
what needs to be fixed. The orchestrator runs all checks and aggregates
failures so the operator sees every problem at once.
"""

from __future__ import annotations

from dataclasses import dataclass

from vastde_orch.clients._shell import ShellError, run
from vastde_orch.clients.docker import docker_version
from vastde_orch.clients.kube import kubectl_ping, zarf_version
from vastde_orch.clients.vms import VmsClient


class PreflightError(RuntimeError):
    """Raised when one or more pre-flight checks fail."""


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def check_vms(vms: VmsClient) -> CheckResult:
    try:
        # Hitting `/versions` is a cheap, read-only probe that works without DE.
        vms.raw.versions.get()
    except Exception as exc:
        return CheckResult("vms", False, f"cannot reach VMS: {exc}")
    return CheckResult("vms", True, "VMS reachable")


def check_vastde() -> CheckResult:
    try:
        v = run(["vastde", "--version"]).stdout.strip()
        return CheckResult("vastde", True, v)
    except ShellError as exc:
        return CheckResult("vastde", False, str(exc))


def check_docker() -> CheckResult:
    try:
        v = docker_version()
        return CheckResult("docker", True, v)
    except ShellError as exc:
        return CheckResult("docker", False, str(exc))


def check_kubectl(kubeconfig=None) -> CheckResult:
    try:
        kubectl_ping(kubeconfig)
        return CheckResult("kubectl", True, "K8s API reachable")
    except ShellError as exc:
        return CheckResult("kubectl", False, str(exc))


def check_zarf() -> CheckResult:
    try:
        v = zarf_version()
        return CheckResult("zarf", True, v)
    except ShellError as exc:
        return CheckResult("zarf", False, str(exc))


def run_preflight(
    vms: VmsClient, *, include_k8s: bool, kubeconfig=None
) -> list[CheckResult]:
    """Run all checks; raise PreflightError summarizing any failures."""
    checks: list[CheckResult] = [check_vms(vms), check_vastde(), check_docker()]
    if include_k8s:
        checks.append(check_kubectl(kubeconfig))
        checks.append(check_zarf())

    failures = [c for c in checks if not c.ok]
    if failures:
        bullets = "\n".join(f"  - {c.name}: {c.detail}" for c in failures)
        raise PreflightError(f"pre-flight checks failed:\n{bullets}")
    return checks

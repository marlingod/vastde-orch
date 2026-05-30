"""kubectl + zarf wrappers used by Stage A enablement.

We treat the K8s bootstrap as an imperative one-shot (per PDF p.7-10):
  1. Check & set sysctl inotify limits.
  2. `zarf init` for the agent.
  3. Create & label namespaces with `zarf.dev/vast=mutate`.
  4. `zarf package deploy` for the VAST DataEngine package.
"""

from __future__ import annotations

import os
from pathlib import Path

from vastde_orch.clients._shell import ShellResult, run, which_or_raise

KUBECTL_BIN = "kubectl"
ZARF_BIN = "zarf"
SYSCTL_BIN = "sysctl"

_VAST_NAMESPACES = ["vast-dataengine", "knative-eventing", "knative-serving"]


def _kube_env(kubeconfig: Path | None) -> dict[str, str]:
    env = os.environ.copy()
    if kubeconfig is not None:
        env["KUBECONFIG"] = str(Path(kubeconfig).expanduser())
    return env


# ── sysctl ────────────────────────────────────────────────────────────────

def sysctl_get(key: str) -> int:
    out = run([SYSCTL_BIN, "-n", key]).stdout.strip()
    return int(out)


def sysctl_set(key: str, value: int) -> None:
    run(["sudo", SYSCTL_BIN, f"{key}={value}"])


def ensure_inotify_limits(instances: int, watches: int) -> dict[str, tuple[int, int]]:
    """Ensure inotify limits meet the minimum required by VAST Zarf package.

    Returns a dict {key: (before, after)}.
    """
    changes: dict[str, tuple[int, int]] = {}
    for key, target in [("fs.inotify.max_user_instances", instances),
                        ("fs.inotify.max_user_watches", watches)]:
        current = sysctl_get(key)
        if current < target:
            sysctl_set(key, target)
            changes[key] = (current, target)
    return changes


# ── kubectl ───────────────────────────────────────────────────────────────

def kubectl_ping(kubeconfig: Path | None = None) -> str:
    """Return server version string. Raises ShellError if unreachable."""
    which_or_raise(KUBECTL_BIN)
    res = run([KUBECTL_BIN, "version", "--output=yaml"], env=_kube_env(kubeconfig))
    return res.stdout


def kubectl_namespace_exists(name: str, kubeconfig: Path | None = None) -> bool:
    res = run(
        [KUBECTL_BIN, "get", "ns", name, "--ignore-not-found", "-o", "name"],
        env=_kube_env(kubeconfig),
        check=False,
    )
    return bool(res.stdout.strip())


def kubectl_create_namespace(name: str, kubeconfig: Path | None = None) -> None:
    if kubectl_namespace_exists(name, kubeconfig):
        return
    run([KUBECTL_BIN, "create", "ns", name], env=_kube_env(kubeconfig))


def kubectl_label_namespace(
    name: str, key: str, value: str, *, overwrite: bool = True,
    kubeconfig: Path | None = None,
) -> None:
    args = [KUBECTL_BIN, "label", "ns", name, f"{key}={value}"]
    if overwrite:
        args.append("--overwrite")
    run(args, env=_kube_env(kubeconfig))


def ensure_vast_namespaces(kubeconfig: Path | None = None) -> list[str]:
    """Create the three VAST namespaces and label them for Zarf mutation."""
    created = []
    for ns in _VAST_NAMESPACES:
        if not kubectl_namespace_exists(ns, kubeconfig):
            run([KUBECTL_BIN, "create", "ns", ns], env=_kube_env(kubeconfig))
            created.append(ns)
        kubectl_label_namespace(ns, "zarf.dev/vast", "mutate", kubeconfig=kubeconfig)
    return created


# ── zarf ──────────────────────────────────────────────────────────────────

def zarf_version() -> str:
    which_or_raise(ZARF_BIN)
    return run([ZARF_BIN, "version"]).stdout.strip()


def zarf_init(
    init_package: Path, *, architecture: str = "amd64", storage_class: str | None = None,
    registry_hpa_auto_size: bool = True, kubeconfig: Path | None = None,
) -> ShellResult:
    args = [
        ZARF_BIN, "init",
        "--architecture", architecture,
        str(init_package),
        "--confirm",
        "--log-level", "debug",
    ]
    if registry_hpa_auto_size:
        args.extend(["--set", "REGISTRY_HPA_AUTO_SIZE=true"])
    if storage_class:
        args.extend(["--storage-class", storage_class])
    return run(args, env=_kube_env(kubeconfig))


def zarf_package_deploy(
    package: Path, *, architecture: str = "amd64", kubeconfig: Path | None = None,
) -> ShellResult:
    return run(
        [
            ZARF_BIN, "package", "deploy",
            "--architecture", architecture,
            str(package),
            "--confirm",
            "--log-level", "debug",
        ],
        env=_kube_env(kubeconfig),
    )

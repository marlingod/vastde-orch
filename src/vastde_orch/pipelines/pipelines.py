"""Pipeline reconciliation: bundles triggers, functions, and flow edges.

The flow is sent as a list of {from, to} edges (already DAG-validated in
PipelineSpec.__validators__). The pipeline body also carries the K8s cluster,
namespace, pipeline-level secrets, env, and per-function deployment configs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from vastde_orch.clients.vastde_cli import VastdeCli
from vastde_orch.config.models import FunctionSpec, PipelineSpec
from vastde_orch.pipelines.functions import ensure_function
from vastde_orch.pipelines.triggers import ensure_trigger

PipelineStatus = Literal["created", "updated", "unchanged", "deployed", "would_create", "would_update"]


@dataclass
class PipelineResult:
    name: str
    status: PipelineStatus
    triggers: list = None
    functions: list = None

    def __post_init__(self) -> None:
        self.triggers = self.triggers or []
        self.functions = self.functions or []


def _function_deployment_body(f: FunctionSpec) -> dict[str, Any]:
    d = f.deployment
    return {
        "function_name": f.name,
        "concurrency": {"min": d.concurrency.min, "max": d.concurrency.max},
        "cpu": {"min": d.cpu.min, "max": d.cpu.max},
        "memory": {"min": d.memory.min, "max": d.memory.max},
        "disk_ephemeral": d.disk_ephemeral,
        "autoscaling_rps_factor": d.autoscaling_rps_factor,
        "timeout_seconds": d.timeout_seconds,
        "retries": d.retries,
        "log_level": d.log_level,
        "method_of_delivery": d.method_of_delivery,
        "secrets": f.secrets,
        "env": f.env,
    }


def _pipeline_body(spec: PipelineSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "description": spec.description or "",
        "k8s_cluster_name": spec.k8s_cluster,
        "namespace": spec.namespace,
        "secrets": spec.secrets,
        "env": spec.env,
        "flow": [{"from": e.from_, "to": e.to} for e in spec.flow],
        "function_deployments": [
            _function_deployment_body(f) for f in spec.functions
        ],
        "trigger_names": [t.name for t in spec.triggers],
    }


def ensure_pipeline(
    cli: VastdeCli,
    spec: PipelineSpec,
    *,
    broker_view_id: int | None = None,
    dry_run: bool = False,
    deploy: bool = True,
) -> PipelineResult:
    result = PipelineResult(name=spec.name, status="unchanged")

    # 1. Ensure all triggers exist.
    for t in spec.triggers:
        result.triggers.append(
            ensure_trigger(cli, t, broker_view_id=broker_view_id, dry_run=dry_run)
        )

    # 2. Ensure all functions exist (build/push images as needed).
    for f in spec.functions:
        result.functions.append(ensure_function(cli, f, dry_run=dry_run))

    # 3. Reconcile the pipeline resource itself.
    body = _pipeline_body(spec)
    existing = cli.pipelines_get(spec.name)

    if existing is None:
        result.status = "would_create" if dry_run else "created"
        if not dry_run:
            cli.pipelines_create(spec.name, body)
    else:
        drift = {k: v for k, v in body.items() if existing.get(k) != v}
        if drift:
            result.status = "would_update" if dry_run else "updated"
            if not dry_run:
                cli.pipelines_update(spec.name, body)

    # 4. Deploy.
    if deploy and not dry_run:
        cli.pipelines_deploy(spec.name)
        result.status = "deployed"

    return result

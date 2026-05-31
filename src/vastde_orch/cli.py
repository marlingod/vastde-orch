"""Click CLI for vastde-orch.

Commands:
  validate   - schema-check the YAML and resolve cross-refs
  enable     - Stage A (one-shot tenant bootstrap)
  apply      - Stage B (reconcile pipelines)
  status     - poll live pipeline status
  destroy    - reverse-order teardown
  function   - subgroup: `build` for the fast inner loop
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import click
import structlog

from vastde_orch.clients.vastde_cli import VastdeCli, VastdeContext
from vastde_orch.clients.vms import VmsClient
from vastde_orch.config.loader import ConfigError, load_any_config, load_config
from vastde_orch.config.models import VastdeConfig
from vastde_orch.config.models_minimal import VastdeMinimalConfig
from vastde_orch.enablement.enable import disable_dataengine, enable_dataengine
from vastde_orch.interactive._tty import require_tty
from vastde_orch.interactive._vms_probe import VmsProbe
from vastde_orch.interactive._yaml_emit import write_yaml_with_backup
from vastde_orch.interactive.confirm import InteractiveSession
from vastde_orch.interactive.orchestrator import run_interactive
from vastde_orch.interactive.wizard import (
    WizardValidationError,
    run_wizard,
)
from vastde_orch.logging import configure as configure_logging
from vastde_orch.pipelines.functions import compute_image_tag, ensure_function
from vastde_orch.pipelines.pipelines import ensure_pipeline


log = structlog.get_logger()


def _load(cfg_path: Path) -> VastdeConfig | VastdeMinimalConfig:
    try:
        return load_any_config(cfg_path)
    except ConfigError as exc:
        click.echo(f"config error: {exc}", err=True)
        sys.exit(2)


def _require_full(cfg: VastdeConfig | VastdeMinimalConfig, command: str) -> VastdeConfig:
    """Reject minimal-schema configs for commands not yet wired for it."""
    if isinstance(cfg, VastdeMinimalConfig):
        click.echo(
            f"'{command}' does not yet support the minimal schema "
            "(sample/vastde.template.yaml).\n"
            "  Use the full schema (sample/demo_tenant.yaml shape) for now, "
            "or run 'vastde-orch wizard' to author one.",
            err=True,
        )
        sys.exit(2)
    return cfg


def _build_vms(cfg: VastdeConfig, dry_run: bool) -> VmsClient:
    return VmsClient(cfg.vms, dry_run=dry_run)


def _build_vastde_cli(cfg: VastdeConfig, dry_run: bool) -> VastdeCli:
    builder_image = os.environ.get("VASTDE_BUILDER_IMAGE", "vastdataorg/vast-builder:latest")
    ctx = VastdeContext(
        vms_url=f"https://{cfg.vms.address}",
        tenant=cfg.vms.tenant,
        username=cfg.vms.user or "",
        password=cfg.vms.password or cfg.vms.token or "",
        builder_image_url=builder_image,
    )
    return VastdeCli(ctx, dry_run=dry_run)


# ── root group ──────────────────────────────────────────────────────────────

@click.group()
@click.option("--log-level", default="INFO",
              type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"]))
@click.version_option()
def main(log_level: str) -> None:
    """vastde-orch: declarative automation for VAST DataEngine."""
    configure_logging(log_level)


# ── validate ────────────────────────────────────────────────────────────────

@main.command()
@click.option("-c", "--config", "cfg_path", required=True, type=click.Path(exists=True, path_type=Path))
def validate(cfg_path: Path) -> None:
    """Schema-check the YAML, including cross-refs (flow edges, k8s_cluster, etc.)."""
    cfg = _load(cfg_path)
    click.echo(f"OK: {cfg_path}")
    if isinstance(cfg, VastdeMinimalConfig):
        click.echo("  - schema: minimal (tenant-scoped)")
        click.echo(f"  - tenant: {cfg.vms.tenant_name}")
        click.echo(f"  - vip pool: {cfg.vip_pool_name}")
        click.echo(f"  - k8s cluster: {cfg.k8s.name} ({cfg.k8s.kube_api_url})")
        click.echo(f"  - registry: {cfg.registry.name} ({cfg.registry.url})")
        click.echo(f"  - broker view: {cfg.broker_view.path} (bucket {cfg.broker_view.bucket})")
        click.echo(f"  - pipelines: {len(cfg.pipelines)}")
    else:
        click.echo("  - schema: full")
        click.echo(f"  - tenant: {cfg.vms.tenant}")
        click.echo(f"  - enablement: {'present' if cfg.enablement else 'absent'}")
        click.echo(f"  - pipelines: {len(cfg.pipelines)}")


# ── enable (Stage A) ────────────────────────────────────────────────────────

@main.command()
@click.option("-c", "--config", "cfg_path", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--plan", "dry_run", is_flag=True, help="Dry-run: print diff, no writes.")
@click.option("--interactive", "-i", is_flag=True,
              help="Prompt before applying each resource type.")
@click.option("--yes-all", "-y", is_flag=True, help="Auto-approve all interactive prompts.")
@click.option("--non-interactive", is_flag=True,
              help="Opt out of interactivity even on a TTY.")
@click.option("--skip-preflight", is_flag=True)
@click.option("--skip-k8s-bootstrap", is_flag=True,
              help="Skip operator-machine kubectl/zarf steps (use when k8s already configured).")
@click.pass_context
def enable(
    ctx: click.Context,
    cfg_path: Path,
    dry_run: bool,
    interactive: bool,
    yes_all: bool,
    non_interactive: bool,
    skip_preflight: bool,
    skip_k8s_bootstrap: bool,
) -> None:
    """Stage A: enable DataEngine on the tenant."""
    cfg = _require_full(_load(cfg_path), "enable")
    if cfg.enablement is None:
        click.echo("config has no `enablement:` section", err=True)
        sys.exit(2)

    if interactive:
        require_tty(
            ctx, command="enable --interactive",
            ci_hint="vastde-orch enable -c <yaml> --yes-all",
            non_interactive_flag=non_interactive,
        )

    if interactive and not (dry_run or yes_all):
        session = InteractiveSession(yes_all=yes_all)
        result = run_interactive(
            plan_fn=lambda: enable_dataengine(
                _build_vms(cfg, dry_run=True), cfg.enablement,
                skip_preflight=skip_preflight, skip_k8s_bootstrap=skip_k8s_bootstrap,
                dry_run=True,
            ),
            apply_fn=lambda: enable_dataengine(
                _build_vms(cfg, dry_run=False), cfg.enablement,
                skip_preflight=skip_preflight, skip_k8s_bootstrap=skip_k8s_bootstrap,
                dry_run=False,
            ),
            session=session,
        )
        if not result.applied and result.rejected_types:
            click.echo(
                f"\nAborted at resource type(s): {', '.join(result.rejected_types)}.\n"
                "Run `vastde-orch enable` again to retry — operations are idempotent.",
                err=True,
            )
            sys.exit(1)
        return

    vms = _build_vms(cfg, dry_run=dry_run)
    plan = enable_dataengine(
        vms, cfg.enablement,
        skip_preflight=skip_preflight,
        skip_k8s_bootstrap=skip_k8s_bootstrap,
        dry_run=dry_run,
    )
    plan.render()


# ── apply (Stage B) ─────────────────────────────────────────────────────────

@main.command()
@click.option("-c", "--config", "cfg_path", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--plan", "dry_run", is_flag=True, help="Dry-run: print diff, no writes.")
@click.option("--interactive", "-i", is_flag=True,
              help="Show planned changes per pipeline and prompt before applying.")
@click.option("--yes-all", "-y", is_flag=True, help="Auto-approve all interactive prompts.")
@click.option("--non-interactive", is_flag=True,
              help="Opt out of interactivity even on a TTY.")
@click.option("--only", "only_names", multiple=True, help="Limit to specific pipeline names.")
@click.option("--no-deploy", is_flag=True, help="Apply changes but skip the final `deploy` step.")
@click.pass_context
def apply(
    ctx: click.Context,
    cfg_path: Path,
    dry_run: bool,
    interactive: bool,
    yes_all: bool,
    non_interactive: bool,
    only_names: tuple[str, ...],
    no_deploy: bool,
) -> None:
    """Stage B: reconcile pipelines."""
    cfg = _require_full(_load(cfg_path), "apply")
    targets = [p for p in cfg.pipelines if not only_names or p.name in only_names]
    if not targets:
        click.echo("no pipelines selected", err=True)
        sys.exit(1)

    if interactive:
        require_tty(
            ctx, command="apply --interactive",
            ci_hint="vastde-orch apply -c <yaml> --yes-all",
            non_interactive_flag=non_interactive,
        )

    session = InteractiveSession(yes_all=yes_all) if interactive else None

    for p in targets:
        click.echo(f"\n=== pipeline: {p.name} ===")
        if interactive and session and not session.yes_all and not session.sticky_yes:
            # Dry-run preview for this pipeline only.
            preview_cli = _build_vastde_cli(cfg, dry_run=True)
            preview = ensure_pipeline(preview_cli, p, dry_run=True, deploy=not no_deploy)
            click.echo(f"  Planned pipeline status: {preview.status}")
            for t in preview.triggers:
                click.echo(f"  - trigger {t.name}: {t.status}")
            for f in preview.functions:
                click.echo(f"  - function {f.name}: {f.de_resource_status} → {f.image}:{f.tag}")
            from vastde_orch.interactive.confirm import ConfirmDecision
            from vastde_orch.clients.vms import DiffResult, EnsureOutcome
            # Fake outcomes for the session prompt (we only need it for the y/n flow).
            decision = session.confirm_type(
                f"pipeline {p.name}",
                [EnsureOutcome(DiffResult.WOULD_UPDATE, "pipeline", p.name, None, {})],
            )
            if decision is ConfirmDecision.NO:
                click.echo(f"  Skipped pipeline {p.name}.", err=True)
                continue

        real_cli = _build_vastde_cli(cfg, dry_run=dry_run)
        result = ensure_pipeline(real_cli, p, dry_run=dry_run, deploy=not no_deploy)
        click.echo(f"  pipeline:  {result.status}")
        for t in result.triggers:
            click.echo(f"  trigger    {t.name}: {t.status}")
        for f in result.functions:
            cached = " (cached)" if f.image_already_in_registry else ""
            click.echo(f"  function   {f.name}: {f.de_resource_status} → {f.image}:{f.tag}{cached}")


# ── status ──────────────────────────────────────────────────────────────────

@main.command()
@click.option("-c", "--config", "cfg_path", required=True, type=click.Path(exists=True, path_type=Path))
def status(cfg_path: Path) -> None:
    """Show live pipeline status from VMS."""
    cfg = _require_full(_load(cfg_path), "status")
    cli = _build_vastde_cli(cfg, dry_run=False)
    for p in cfg.pipelines:
        live = cli.pipelines_get(p.name)
        if live is None:
            click.echo(f"{p.name}: not deployed")
        else:
            click.echo(f"{p.name}: status={live.get('status', '?')}"
                       f" deployed_at={live.get('last_deployed_at', '?')}")


# ── destroy ─────────────────────────────────────────────────────────────────

@main.command()
@click.option("-c", "--config", "cfg_path", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--only", "only_names", multiple=True, help="Limit to specific pipeline names.")
@click.option("--include-enablement", is_flag=True, help="Also disable DataEngine on the tenant.")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
def destroy(
    cfg_path: Path, only_names: tuple[str, ...], include_enablement: bool, yes: bool
) -> None:
    """Tear down pipelines (and optionally the enablement)."""
    cfg = _require_full(_load(cfg_path), "destroy")
    if not yes:
        click.confirm("Really destroy these resources?", abort=True)

    cli = _build_vastde_cli(cfg, dry_run=False)
    targets = [p for p in cfg.pipelines if not only_names or p.name in only_names]

    for p in targets:
        click.echo(f"deleting pipeline {p.name}")
        cli.pipelines_delete(p.name)
        for t in p.triggers:
            cli.triggers_delete(t.name)
        for f in p.functions:
            cli.functions_delete(f.name)

    if include_enablement:
        vms = _build_vms(cfg, dry_run=False)
        click.echo(f"disabling DataEngine on tenant {cfg.vms.tenant}")
        disable_dataengine(vms, cfg.vms.tenant).render()


# ── function subgroup ───────────────────────────────────────────────────────

@main.group()
def function() -> None:
    """Function-level operations (no API calls)."""


@function.command("build")
@click.argument("name")
@click.option("-c", "--config", "cfg_path", required=True, type=click.Path(exists=True, path_type=Path))
def function_build(name: str, cfg_path: Path) -> None:
    """Build and push a single function image (inner-loop)."""
    cfg = _require_full(_load(cfg_path), "function build")
    for pipeline in cfg.pipelines:
        for f in pipeline.functions:
            if f.name == name:
                cli = _build_vastde_cli(cfg, dry_run=False)
                result = ensure_function(cli, f)
                click.echo(f"{f.name}: {result.de_resource_status} → {result.image}:{result.tag}")
                return
    click.echo(f"function {name!r} not found in any pipeline", err=True)
    sys.exit(1)


@function.command("tag")
@click.argument("name")
@click.option("-c", "--config", "cfg_path", required=True, type=click.Path(exists=True, path_type=Path))
def function_tag(name: str, cfg_path: Path) -> None:
    """Print the content-hash tag a function would receive (for CI use)."""
    cfg = _require_full(_load(cfg_path), "function tag")
    for pipeline in cfg.pipelines:
        for f in pipeline.functions:
            if f.name == name:
                click.echo(compute_image_tag(f))
                return
    sys.exit(1)


# ── wizard ──────────────────────────────────────────────────────────────────

@main.command()
@click.option("-o", "--output", "output_path", default="vastde.yaml",
              type=click.Path(path_type=Path),
              help="Where to write the generated config (default: vastde.yaml).")
@click.option("--answers-file", "answers_file", type=click.Path(exists=True, path_type=Path),
              help="Pre-fill all prompts from a YAML file (skips TTY guard, useful for CI/tests).")
@click.option("--non-interactive", is_flag=True,
              help="Require --answers-file; do not prompt.")
@click.option("--vms-address", help="VMS address for live probing (optional).")
@click.option("--vms-token", envvar="VMS_TOKEN",
              help="VMS API token for live probing (optional; defaults to $VMS_TOKEN).")
@click.option("--vms-tenant", default="default",
              help="VMS tenant for live probing (default: default).")
@click.pass_context
def wizard(
    ctx: click.Context,
    output_path: Path,
    answers_file: Path | None,
    non_interactive: bool,
    vms_address: str | None,
    vms_token: str | None,
    vms_tenant: str,
) -> None:
    """Interactively author a vastde.yaml.

    With --answers-file, runs non-interactively from the pre-filled answers.
    Without it, prompts step by step. The output is reviewed by you, then
    applied via `vastde-orch enable` / `apply`.
    """
    import yaml as _yaml
    answers = None
    if answers_file:
        answers = _yaml.safe_load(Path(answers_file).read_text())
        if not isinstance(answers, dict):
            click.echo(f"answers file must contain a YAML mapping at the root", err=True)
            sys.exit(2)
    else:
        require_tty(
            ctx, command="wizard",
            ci_hint="vastde-orch wizard --answers-file answers.yaml",
            non_interactive_flag=non_interactive,
        )

    # Optional live VMS probing (best-effort).
    probe_vms: VmsClient | None = None
    if vms_address and vms_token:
        from vastde_orch.config.models import VmsSpec
        try:
            probe_vms = VmsClient(VmsSpec(
                address=vms_address, token=vms_token, tenant=vms_tenant,
            ))
        except Exception as exc:
            click.echo(f"  Warning: could not connect to VMS for probing ({exc}).", err=True)
    probe = VmsProbe(vms=probe_vms)

    try:
        config = run_wizard(probe, answers=answers)
    except WizardValidationError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(2)

    backup = write_yaml_with_backup(output_path, config)
    if backup:
        click.echo(f"  Backed up previous {output_path} → {backup}")
    click.echo(f"  Wrote {output_path}")
    click.echo("\nNext steps:")
    click.echo(f"  vastde-orch validate -c {output_path}")
    click.echo(f"  vastde-orch enable   -c {output_path} --plan")
    click.echo(f"  vastde-orch apply    -c {output_path} --plan")


if __name__ == "__main__":
    main()

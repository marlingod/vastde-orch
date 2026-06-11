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

from vastde_orch.bootstrap.tenant import (
    create_tenant,
    destroy_tenant,
    load_tenant_config,
)
from vastde_orch.bootstrap.tenant_enable import (
    load_tenant_enable_config,
    tenant_enable as run_tenant_enable,
)
from vastde_orch.clients.vastde_cli import VastdeCli, VastdeContext
from vastde_orch.clients.vms import VmsClient
from vastde_orch.config.loader import ConfigError, load_any_config, load_config
from vastde_orch.config.models import VastdeConfig, VmsSpec
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
@click.option("--plan", is_flag=True, help="Dry-run — print what would be deleted, do not write.")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
def destroy(
    cfg_path: Path, only_names: tuple[str, ...], include_enablement: bool,
    plan: bool, yes: bool,
) -> None:
    """Tear down pipelines (and optionally the enablement)."""
    cfg = _require_full(_load(cfg_path), "destroy")
    if not plan and not yes:
        click.confirm("Really destroy these resources?", abort=True)

    verb = "would delete" if plan else "deleting"
    cli = _build_vastde_cli(cfg, dry_run=plan)
    targets = [p for p in cfg.pipelines if not only_names or p.name in only_names]

    for p in targets:
        click.echo(f"{verb} pipeline {p.name}")
        cli.pipelines_delete(p.name)
        for t in p.triggers:
            click.echo(f"  {verb} trigger {t.name}")
            cli.triggers_delete(t.name)
        for f in p.functions:
            click.echo(f"  {verb} function {f.name}")
            cli.functions_delete(f.name)

    if include_enablement:
        vms = _build_vms(cfg, dry_run=plan)
        click.echo(f"{'would disable' if plan else 'disabling'} "
                   f"DataEngine on tenant {cfg.vms.tenant}")
        disable_dataengine(vms, cfg.vms.tenant).render()

    click.echo(f"\n{'DRY-RUN' if plan else 'DESTROYED'}: "
               f"{len(targets)} pipeline(s){' + enablement' if include_enablement else ''}")


# ── tenant subgroup ─────────────────────────────────────────────────────────
# Cluster-admin tenant bootstrap. The counterpart to `enable` (which runs as
# tenant-admin). Uses its own small YAML schema (sample/tenant-setup.example.yaml)
# — NOT the full VastdeConfig schema — because most of what `enable` needs
# (broker view, K8s registration, etc.) doesn't exist yet when bootstrapping.

def _vms_for_bootstrap(cfg: dict[str, Any], *, dry_run: bool) -> VmsClient:
    """Build a VmsClient from a tenant-setup OR tenant-enable YAML.

    The tenant name lives at `vms.tenant` (enable schema) or at top-level
    `tenant.name` (create schema). Accept either so both subcommands can share
    this helper.
    """
    vms_cfg = cfg["vms"]
    tenant_name = vms_cfg.get("tenant") or (cfg.get("tenant") or {}).get("name")
    if not tenant_name:
        sys.exit("FATAL: config must set vms.tenant or tenant.name")
    return VmsClient(VmsSpec(
        address=vms_cfg["address"],
        user=vms_cfg["user"],
        password=vms_cfg["password"],
        tenant=tenant_name,
    ), dry_run=dry_run)


@main.group()
def tenant() -> None:
    """Cluster-admin tenant bootstrap (tenant + identity + DE policy)."""


@tenant.command("create")
@click.option("-c", "--config", "cfg_path", required=True,
              type=click.Path(exists=True, path_type=Path))
@click.option("--plan", is_flag=True,
              help="Dry-run — print diff, do not write.")
def tenant_create(cfg_path: Path, plan: bool) -> None:
    """Create tenant + identity + role + manager + view policies + DE policy.

    Idempotent; safe to re-run. Steps in order:
      1. tenant  2. group  3. bucket-owner user  4. role  5. manager
      6. (opt) vippool  7. nfs/s3 view policies  8. DE identity policy + bind
      9. (opt) bind to AllowAllTabular
    """
    cfg = load_tenant_config(cfg_path)
    vms = _vms_for_bootstrap(cfg, dry_run=plan)
    sys.exit(create_tenant(cfg, vms))


@tenant.command("destroy")
@click.option("-c", "--config", "cfg_path", required=True,
              type=click.Path(exists=True, path_type=Path))
@click.option("--plan", is_flag=True,
              help="Dry-run — print what would be deleted.")
@click.option("--yes", is_flag=True,
              help="Skip the interactive 'type destroy to confirm' prompt.")
def tenant_destroy(cfg_path: Path, plan: bool, yes: bool) -> None:
    """Strict inverse of `tenant create`. Refuses if a broker view still uses
    the view policies (delete those with `vastde-orch destroy --include-enablement`).
    """
    cfg = load_tenant_config(cfg_path)
    vms = _vms_for_bootstrap(cfg, dry_run=plan)
    sys.exit(destroy_tenant(cfg, vms, yes=yes))


@tenant.command("enable")
@click.option("-c", "--config", "cfg_path", required=True,
              type=click.Path(exists=True, path_type=Path))
@click.option("--plan", is_flag=True,
              help="Dry-run — print discovered state + planned changes, no mutations.")
@click.option("--skip-k8s-bootstrap", is_flag=True, default=True,
              help="Skip operator-machine sysctl/zarf checks (default True — assumes "
                   "K8s is already prepared via the ansible playbook).")
@click.option("--skip-preflight", is_flag=True,
              help="Skip pre-flight checks entirely (kubectl/vastde/zarf).")
def tenant_enable_cmd(
    cfg_path: Path, plan: bool, skip_k8s_bootstrap: bool, skip_preflight: bool,
) -> None:
    """Enable DataEngine using auto-discovery of existing tenant state.

    Loads a MINIMAL config (just vms + kubernetes + container_registry) and
    discovers the rest from VMS — tenant, group, bucket-owner user, view
    policy, and vip_pool are all queried by name/role/local_provider. This
    avoids re-declaring everything `tenant create` already put on the cluster.

    Calls the same `enable_dataengine` flow as `vastde-orch enable` — just
    with the schema constructed in-memory from discovery.
    """
    cfg = load_tenant_enable_config(cfg_path)
    vms = _vms_for_bootstrap(cfg, dry_run=plan)
    sys.exit(run_tenant_enable(
        cfg, vms,
        skip_k8s_bootstrap=skip_k8s_bootstrap,
        skip_preflight=skip_preflight,
    ))


@tenant.command("register-de-resources")
@click.option("-c", "--config", "cfg_path", required=True,
              type=click.Path(exists=True, path_type=Path))
@click.option("--plan", is_flag=True,
              help="Dry-run via `vastde --dry-run` (no resources actually linked).")
def tenant_register_de_resources(cfg_path: Path, plan: bool) -> None:
    """Register K8s cluster + container registry on the tenant via VMS DE-API REST.

    POSTs directly to:
      /api/dataengine/mtls-authentication-credentials/   (3 PEMs, base64'd)
      /api/dataengine/kubernetes-clusters/               (uses the mtls guid)
      /api/dataengine/container-registries/              (uses the cluster VRN)

    Bypasses both vastpy's `/api/latest/` routing (which 404s on these
    endpoints — `tenant enable` correctly skips them with that diagnosis) AND
    the `vastde` CLI's `serverless/kubernetes-clusters/` path (which 422s on
    some VMS versions with "Extra inputs are not permitted"). Sends only the
    documented minimal payload per docs/vms-api-full-catalog.md A.3-A.5.

    All 3 calls are idempotent: look up by name first, return the existing
    guid/vrn if already registered.

    Reuses the same minimal YAML schema as `tenant enable`
    (sample/tenant-enable.example.yaml).

    Runs from anywhere — no `vastde` CLI dependency. Tenant-admin password
    must be in $TENANT_ADMIN_PASSWORD (or whatever tenant_admin.password_env
    points at in the YAML).
    """
    cfg = load_tenant_enable_config(cfg_path)
    tenant_name = cfg["vms"]["tenant"]
    ta = cfg.get("tenant_admin") or {}
    ta_user = ta.get("username") or f"{tenant_name}-admin"
    ta_pwd_env = ta.get("password_env") or "TENANT_ADMIN_PASSWORD"
    ta_pwd = os.environ.get(ta_pwd_env)
    if not ta_pwd:
        click.echo(f"FATAL: env var {ta_pwd_env!r} not set (tenant-admin password)", err=True)
        sys.exit(2)

    vms = _vms_for_bootstrap(cfg, dry_run=plan)

    # 1. mTLS credential (3 PEMs base64'd)
    k8s = cfg["kubernetes"]
    mtls_name = f"{k8s['name']}-credentials"
    namespaces = k8s.get("namespaces") or ["vast-dataengine"]
    click.echo(f"\n── 1. mTLS credential {mtls_name!r} ──")
    mtls_guid = vms.register_de_mtls_credential(
        mtls_name,
        ca_path=Path(k8s["ca_cert_path"]).expanduser(),
        client_cert_path=Path(k8s["client_cert_path"]).expanduser(),
        client_key_path=Path(k8s["client_key_path"]).expanduser(),
        tenant_admin_user=ta_user, tenant_admin_password=ta_pwd,
    )
    click.echo(f"  mtls_credentials_guid: {mtls_guid}")

    # 2. K8s cluster
    click.echo(f"\n── 2. K8s cluster {k8s['name']!r} "
               f"(api_server={k8s['api_server']}, namespaces={namespaces}) ──")
    cluster_vrn = vms.register_de_k8s_cluster(
        k8s["name"],
        kube_api_url=k8s["api_server"],
        mtls_credentials_guid=mtls_guid,
        namespaces=namespaces,
        tenant_admin_user=ta_user, tenant_admin_password=ta_pwd,
    )
    click.echo(f"  vrn: {cluster_vrn}")

    # 3. Container registry (references the cluster above by VRN)
    reg = cfg["container_registry"]
    auth = reg.get("auth") or {"method": "none"}
    click.echo(f"\n── 3. Container registry {reg['name']!r} "
               f"(url={reg['base_url']}, auth={auth['method']}) ──")
    registry_guid = vms.register_de_container_registry(
        reg["name"],
        url=reg["base_url"],
        primary_cluster_vrn=cluster_vrn,
        primary_namespace=namespaces[0],
        auth_type=auth["method"],
        username=os.environ.get(auth.get("username_env", "")) if auth["method"] == "password" else None,
        password=os.environ.get(auth.get("password_env", "")) if auth["method"] == "password" else None,
        secret=auth.get("kubernetes_secret_name") if auth["method"] == "secret" else None,
        tenant_admin_user=ta_user, tenant_admin_password=ta_pwd,
    )
    click.echo(f"  guid: {registry_guid}")


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

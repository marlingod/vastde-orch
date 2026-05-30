"""Top-level wizard: orchestrates sections, validates output, writes vastde.yaml.

Two ways to invoke:
  - Interactive:  run_wizard(probe, answers=None) — uses questionary.
  - Scripted:     run_wizard(probe, answers={...})  — reads from answers dict.

The same code path runs in both modes; the Prompter object decides whether
to ask questionary or look up from `answers`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from vastde_orch.config.models import VastdeConfig
from vastde_orch.interactive._prompts import Prompter
from vastde_orch.interactive._vms_probe import VmsProbe
from vastde_orch.interactive._yaml_emit import write_yaml_with_backup
from vastde_orch.interactive.sections.enablement_section import build_enablement_section
from vastde_orch.interactive.sections.pipelines_section import build_pipelines_section
from vastde_orch.interactive.sections.vms_section import build_vms_section


class WizardValidationError(RuntimeError):
    """Raised when the generated config does not pass VastdeConfig validation."""


def run_wizard(probe: VmsProbe, answers: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run the wizard, return a fully-formed config dict (not yet written).

    Raises WizardValidationError if the result does not validate as VastdeConfig.
    """
    p = Prompter(answers=answers)

    config: dict[str, Any] = {"vms": build_vms_section(probe, p)}

    if p.confirm("generate_enablement", "Generate Stage A (enablement) section?", default=True):
        enablement = build_enablement_section(probe, p)
        config["enablement"] = enablement
        k8s_cluster = enablement["kubernetes"]["name"]
    else:
        k8s_cluster = p.text(
            "pipelines_k8s_cluster",
            "Name of an existing K8s cluster resource on the tenant",
        )

    if p.confirm("generate_pipelines", "Generate Stage B (pipelines) section?", default=True):
        pipelines = build_pipelines_section(probe, p, k8s_cluster_name=k8s_cluster)
        if pipelines:
            config["pipelines"] = pipelines

    # Validate against Pydantic schema before returning. Catches drift between
    # the wizard prompts and the config models.
    try:
        VastdeConfig.model_validate(_substitute_env_for_validation(config))
    except Exception as exc:
        raise WizardValidationError(
            f"wizard produced an invalid config: {exc}"
        ) from exc

    return config


def _substitute_env_for_validation(config: dict[str, Any]) -> dict[str, Any]:
    """Strip ${VAR} placeholders for validation only, leaving them in the saved YAML.

    The real load_config does env interpolation; here we just replace any
    `${VAR}` literal with a non-empty placeholder so Pydantic constraints
    (like "must provide token or user/password") pass.
    """
    import copy
    import re
    pat = re.compile(r"\$\{[A-Z_][A-Z0-9_]*\}")

    def walk(v: Any) -> Any:
        if isinstance(v, str):
            return pat.sub("placeholder", v)
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        return v

    return walk(copy.deepcopy(config))


def run_wizard_to_file(
    probe: VmsProbe,
    output_path: Path,
    *,
    answers: dict[str, Any] | None = None,
    keep_backups: int = 3,
) -> tuple[Path, Path | None]:
    """Run the wizard, write the result to `output_path` (atomic + backup).

    Returns (output_path, backup_path_or_None).
    """
    config = run_wizard(probe, answers=answers)
    backup = write_yaml_with_backup(output_path, config, keep_backups=keep_backups)
    return output_path, backup

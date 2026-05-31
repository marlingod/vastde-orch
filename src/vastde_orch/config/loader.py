"""Load and validate vastde.yaml.

Supports two schemas:
  - VastdeConfig         (full schema in models.py — used by enable/apply CLI)
  - VastdeMinimalConfig  (tenant-scoped schema in models_minimal.py)

Auto-detection: presence of top-level `vip_pool_name` (required in minimal,
absent in full) flags the minimal schema; otherwise full.

Performs ${VAR} interpolation from os.environ (and an optional .env file)
before handing the data to Pydantic.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv

from vastde_orch.config.models import VastdeConfig
from vastde_orch.config.models_minimal import VastdeMinimalConfig

_ENV_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


class ConfigError(Exception):
    """Raised on YAML parse, env interpolation, or validation failure."""


def _interpolate(value: Any) -> Any:
    if isinstance(value, str):
        def repl(m: re.Match[str]) -> str:
            var = m.group(1)
            if var not in os.environ:
                raise ConfigError(f"environment variable {var!r} referenced in config is not set")
            return os.environ[var]
        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    return value


def _read_and_interpolate(path: str | Path, env_file: str | Path | None) -> dict[str, Any]:
    """Read the YAML, run env interpolation, and return the root dict.

    Shared by both load_config and load_minimal_config.
    """
    if env_file is not None and Path(env_file).is_file():
        load_dotenv(env_file)

    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"config file not found: {p}")

    try:
        raw = yaml.safe_load(p.read_text())
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML parse error in {p}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{p} must contain a YAML mapping at the root, got {type(raw).__name__}")

    return _interpolate(raw)


def detect_schema(data: dict[str, Any]) -> Literal["minimal", "full"]:
    """Return which schema applies to the given root dict.

    The marker is the presence of top-level `vip_pool_name` (required in
    minimal, absent in full).
    """
    return "minimal" if "vip_pool_name" in data else "full"


def load_config(path: str | Path, env_file: str | Path | None = ".env") -> VastdeConfig:
    """Read a YAML file, interpolate env vars, and validate against VastdeConfig.

    Args:
        path: Path to vastde.yaml.
        env_file: Optional .env file to load before interpolation. None disables.

    Raises:
        ConfigError: on file read, YAML parse, env interpolation, or validation failure.
    """
    interpolated = _read_and_interpolate(path, env_file)
    try:
        return VastdeConfig.model_validate(interpolated)
    except Exception as exc:
        raise ConfigError(f"validation failed for {path}:\n{exc}") from exc


def load_minimal_config(
    path: str | Path, env_file: str | Path | None = ".env"
) -> VastdeMinimalConfig:
    """Load and validate against the minimal tenant-scoped schema.

    Args:
        path: Path to vastde.yaml.
        env_file: Optional .env file to load before interpolation. None disables.

    Raises:
        ConfigError: on file read, YAML parse, env interpolation, or validation failure.
    """
    interpolated = _read_and_interpolate(path, env_file)
    try:
        return VastdeMinimalConfig.model_validate(interpolated)
    except Exception as exc:
        raise ConfigError(f"validation failed for {path}:\n{exc}") from exc


def load_any_config(
    path: str | Path, env_file: str | Path | None = ".env"
) -> VastdeConfig | VastdeMinimalConfig:
    """Auto-detect schema and load.

    Detection is based on the presence of top-level `vip_pool_name` (minimal)
    vs. its absence (full). Callers route on `isinstance(cfg, VastdeMinimalConfig)`.

    Raises:
        ConfigError: on file read, YAML parse, env interpolation, or validation failure.
    """
    interpolated = _read_and_interpolate(path, env_file)
    schema = detect_schema(interpolated)
    model = VastdeMinimalConfig if schema == "minimal" else VastdeConfig
    try:
        return model.model_validate(interpolated)
    except Exception as exc:
        raise ConfigError(f"validation failed for {path} (detected schema: {schema}):\n{exc}") from exc

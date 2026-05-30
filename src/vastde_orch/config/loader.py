"""Load and validate vastde.yaml.

Performs ${VAR} interpolation from os.environ (and an optional .env file)
before handing the data to Pydantic.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from vastde_orch.config.models import VastdeConfig

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


def load_config(path: str | Path, env_file: str | Path | None = ".env") -> VastdeConfig:
    """Read a YAML file, interpolate env vars, and validate against VastdeConfig.

    Args:
        path: Path to vastde.yaml.
        env_file: Optional .env file to load before interpolation. None disables.

    Raises:
        ConfigError: on file read, YAML parse, env interpolation, or validation failure.
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

    interpolated = _interpolate(raw)

    try:
        return VastdeConfig.model_validate(interpolated)
    except Exception as exc:  # Pydantic ValidationError
        raise ConfigError(f"validation failed for {p}:\n{exc}") from exc

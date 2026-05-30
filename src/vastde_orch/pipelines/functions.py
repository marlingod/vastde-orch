"""Build, push, and register DataEngine functions.

Lifecycle for each function in vastde.yaml:
  1. Compute a content hash of the function source dir → used as the image tag.
  2. If `<image>:<tag>` already exists in the registry → skip docker build/push.
  3. Otherwise: `vastde functions build` → `docker tag` → `docker push`.
  4. If no DataEngine function resource by this name → `vastde functions create`.
  5. If a resource exists but points to a different image → `vastde functions update`
     (which creates a new revision per PDF p.68).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from vastde_orch.clients.docker import docker_manifest_exists, docker_push, docker_tag
from vastde_orch.clients.vastde_cli import VastdeCli
from vastde_orch.config.models import FunctionSpec

FunctionStatus = Literal["created", "updated", "unchanged", "would_create", "would_update"]


@dataclass
class FunctionResult:
    name: str
    image: str
    tag: str
    image_already_in_registry: bool
    de_resource_status: FunctionStatus


def _hash_dir(path: Path) -> str:
    """Stable sha256 of a directory's contents (file paths + bodies).

    The hash is deterministic regardless of mtime/atime: we sort paths and
    hash only the relative path and file content.
    """
    h = hashlib.sha256()
    files = sorted(p for p in path.rglob("*") if p.is_file())
    for f in files:
        rel = f.relative_to(path).as_posix().encode()
        h.update(b"\0" + rel + b"\0")
        h.update(f.read_bytes())
    return h.hexdigest()[:12]


def compute_image_tag(spec: FunctionSpec) -> str:
    """Return the desired image tag for a function (spec.tag if set, else content hash)."""
    if spec.tag:
        return spec.tag
    if not spec.source.is_dir():
        raise FileNotFoundError(
            f"function {spec.name!r}: source directory {spec.source} does not exist"
        )
    return _hash_dir(spec.source)


def ensure_function(
    cli: VastdeCli, spec: FunctionSpec, *, dry_run: bool = False
) -> FunctionResult:
    tag = compute_image_tag(spec)
    full_image = f"{spec.image}:{tag}"

    # Step 2-3: skip docker build/push if the image is already in the registry.
    already_pushed = docker_manifest_exists(full_image)
    if not already_pushed and not dry_run:
        cli.functions_build(spec.name, target=spec.source, image_tag=spec.name)
        docker_tag(f"{spec.name}:latest", full_image)
        docker_push(full_image)

    # Step 4-5: reconcile the DataEngine function resource.
    existing = cli.functions_get(spec.name)
    body = {
        "name": spec.name,
        "description": spec.description or "",
        "revision_alias": spec.revision_alias or tag,
        "container_image": full_image,
    }

    if existing is None:
        status: FunctionStatus = "would_create" if dry_run else "created"
        if not dry_run:
            cli.functions_create(spec.name, body)
        return FunctionResult(spec.name, spec.image, tag, already_pushed, status)

    if existing.get("container_image") != full_image:
        status = "would_update" if dry_run else "updated"
        if not dry_run:
            cli.functions_new_revision(spec.name, body)
        return FunctionResult(spec.name, spec.image, tag, already_pushed, status)

    return FunctionResult(spec.name, spec.image, tag, already_pushed, "unchanged")

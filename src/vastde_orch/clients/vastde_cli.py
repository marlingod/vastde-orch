"""Wrapper around the `vastde` CLI for DataEngine resource CRUD + function packaging.

This is the only sanctioned way to manage triggers, functions, and pipelines
(per PDF p.42: "the same functionality through the DataEngine CLI"). All
commands shell out to `vastde`; we parse JSON output when available.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vastde_orch.clients._shell import ShellResult, run, run_json, which_or_raise

VASTDE_BIN = "vastde"


@dataclass
class VastdeContext:
    """Auth + config used by every `vastde` invocation.

    Mirrors the args to `vastde config init` (PDF p.48-49).
    """

    vms_url: str
    tenant: str
    username: str
    password: str
    builder_image_url: str


class VastdeCli:
    """Thin idempotent layer over the `vastde` CLI."""

    def __init__(self, ctx: VastdeContext, *, dry_run: bool = False) -> None:
        which_or_raise(VASTDE_BIN)
        self._ctx = ctx
        self._dry_run = dry_run
        self._configured = False

    # ── one-shot config bootstrap ───────────────────────────────────────

    def configure(self) -> None:
        """Run `vastde config init` once per process."""
        if self._configured:
            return
        run(
            [
                VASTDE_BIN, "config", "init",
                "--password", self._ctx.password,
                "--tenant", self._ctx.tenant,
                "--username", self._ctx.username,
                "--builder-image-url", self._ctx.builder_image_url,
                "--vms-url", self._ctx.vms_url,
            ],
        )
        self._configured = True

    def version(self) -> str:
        return run([VASTDE_BIN, "--version"]).stdout.strip()

    # ── functions (image packaging) ─────────────────────────────────────

    def functions_init(
        self, template: str, name: str, *, target_dir: Path
    ) -> ShellResult:
        target_dir.mkdir(parents=True, exist_ok=True)
        return run([VASTDE_BIN, "functions", "init", template, name, "-t", str(target_dir)])

    def functions_build(
        self, name: str, *, target: Path, image_tag: str
    ) -> ShellResult:
        if self._dry_run:
            return ShellResult([VASTDE_BIN, "functions", "build", "--dry-run"], 0, "", "")
        return run([
            VASTDE_BIN, "functions", "build", name,
            "-target", str(target),
            "--image-tag", image_tag,
        ])

    # ── triggers ────────────────────────────────────────────────────────

    def triggers_list(self) -> list[dict[str, Any]]:
        self.configure()
        try:
            return run_json([VASTDE_BIN, "triggers", "list", "--output", "json"])
        except Exception:
            return []

    def triggers_get(self, name: str) -> dict[str, Any] | None:
        for t in self.triggers_list():
            if t.get("name") == name:
                return t
        return None

    def triggers_create(self, name: str, body: dict[str, Any]) -> dict[str, Any]:
        if self._dry_run:
            return {"name": name, "dry_run": True}
        return run_json([
            VASTDE_BIN, "triggers", "create", name, "--file-input", "-", "--output", "json",
        ], input_text=json.dumps(body))

    def triggers_update(self, name: str, body: dict[str, Any]) -> dict[str, Any]:
        if self._dry_run:
            return {"name": name, "dry_run": True}
        return run_json([
            VASTDE_BIN, "triggers", "update", name, "--file-input", "-", "--output", "json",
        ], input_text=json.dumps(body))

    def triggers_delete(self, name: str) -> None:
        if self._dry_run:
            return
        run([VASTDE_BIN, "triggers", "delete", name, "--yes"])

    # ── functions (DataEngine resource) ─────────────────────────────────

    def functions_list(self) -> list[dict[str, Any]]:
        self.configure()
        try:
            return run_json([VASTDE_BIN, "functions", "list", "--output", "json"])
        except Exception:
            return []

    def functions_get(self, name: str) -> dict[str, Any] | None:
        for f in self.functions_list():
            if f.get("name") == name:
                return f
        return None

    def functions_create(self, name: str, body: dict[str, Any]) -> dict[str, Any]:
        if self._dry_run:
            return {"name": name, "dry_run": True}
        return run_json([
            VASTDE_BIN, "functions", "create", name, "--file-input", "-", "--output", "json",
        ], input_text=json.dumps(body))

    def functions_new_revision(self, name: str, body: dict[str, Any]) -> dict[str, Any]:
        if self._dry_run:
            return {"name": name, "dry_run": True}
        return run_json([
            VASTDE_BIN, "functions", "update", name, "--file-input", "-", "--output", "json",
        ], input_text=json.dumps(body))

    def functions_delete(self, name: str) -> None:
        if self._dry_run:
            return
        run([VASTDE_BIN, "functions", "delete", name, "--yes"])

    # ── pipelines ───────────────────────────────────────────────────────

    def pipelines_list(self) -> list[dict[str, Any]]:
        self.configure()
        try:
            return run_json([VASTDE_BIN, "pipelines", "list", "--output", "json"])
        except Exception:
            return []

    def pipelines_get(self, name: str) -> dict[str, Any] | None:
        for p in self.pipelines_list():
            if p.get("name") == name:
                return p
        return None

    def pipelines_create(self, name: str, body: dict[str, Any]) -> dict[str, Any]:
        if self._dry_run:
            return {"name": name, "dry_run": True}
        return run_json([
            VASTDE_BIN, "pipelines", "create", name, "--file-input", "-", "--output", "json",
        ], input_text=json.dumps(body))

    def pipelines_update(self, name: str, body: dict[str, Any]) -> dict[str, Any]:
        if self._dry_run:
            return {"name": name, "dry_run": True}
        return run_json([
            VASTDE_BIN, "pipelines", "update", name, "--file-input", "-", "--output", "json",
        ], input_text=json.dumps(body))

    def pipelines_deploy(self, name: str) -> None:
        if self._dry_run:
            return
        run([VASTDE_BIN, "pipelines", "deploy", name])

    def pipelines_delete(self, name: str) -> None:
        if self._dry_run:
            return
        run([VASTDE_BIN, "pipelines", "delete", name, "--yes"])

    # ── compute clusters (K8s) — REST-blind on some VMS versions ────────
    # `POST /api/dataengine/kubernetes-clusters/` returns 404 on this VMS
    # version (verified live var203 2026-06-07). The `vastde` CLI's
    # `compute-clusters link` command is the official fallback per the
    # `tenant enable` skip message we emit. Mirrors the help output of
    # `vastde compute-clusters link --help`.

    def compute_clusters_link(
        self, name: str, *,
        kube_api_url: str,
        namespaces: list[str],
        ca_path: Path,
        client_cert_path: Path,
        client_key_path: Path,
        mtls_credentials_name: str | None = None,
        description: str | None = None,
    ) -> ShellResult:
        """Link a Kubernetes cluster as a DataEngine compute cluster.

        Wraps `vastde compute-clusters link`. The CLI creates an mTLS
        credentials resource from the three PEM files on-the-fly (no
        separate mtls-credentials POST needed).
        """
        self.configure()
        args = [
            VASTDE_BIN, "compute-clusters", "link",
            "--name", name,
            "--kube-api-url", kube_api_url,
            "--namespaces", ",".join(namespaces),
            "--ca-path", str(ca_path),
            "--client-cert-path", str(client_cert_path),
            "--client-key-path", str(client_key_path),
        ]
        if mtls_credentials_name:
            args += ["--mtls-credentials-name", mtls_credentials_name]
        if description:
            args += ["--description", description]
        if self._dry_run:
            args += ["--dry-run"]
        return run(args)

    # ── container registries — also REST-blind ───────────────────────────

    def container_registries_link(
        self, name: str, *,
        url: str,
        auth_type: str,
        primary_cluster: str,
        primary_namespace: str = "vast-dataengine",
        username: str | None = None,
        password: str | None = None,
        email: str | None = None,
        secret: str | None = None,
        description: str | None = None,
    ) -> ShellResult:
        """Link a container registry to DataEngine.

        Wraps `vastde container-registries link`. Requires `--primary-cluster`
        — the compute cluster must be linked first (`compute_clusters_link`).
        `auth_type` is `none` | `password` | `secret`.
        """
        self.configure()
        args = [
            VASTDE_BIN, "container-registries", "link",
            "--name", name,
            "--url", url,
            "--auth-type", auth_type,
            "--primary-cluster", primary_cluster,
            "--primary-namespace", primary_namespace,
        ]
        if auth_type == "password":
            if not (username and password):
                raise ValueError(
                    "container_registries_link: auth_type=password requires username + password"
                )
            args += ["--username", username, "--password", password]
            if email:
                args += ["--email", email]
        elif auth_type == "secret":
            if not secret:
                raise ValueError(
                    "container_registries_link: auth_type=secret requires --secret"
                )
            args += ["--secret", secret]
        if description:
            args += ["--description", description]
        if self._dry_run:
            args += ["--dry-run"]
        return run(args)

"""docker CLI wrapper for function image tagging, pushing, and registry checks."""

from __future__ import annotations

from vastde_orch.clients._shell import run, which_or_raise

DOCKER_BIN = "docker"


def docker_version() -> str:
    which_or_raise(DOCKER_BIN)
    return run([DOCKER_BIN, "--version"]).stdout.strip()


def docker_login(registry: str, *, username: str, password: str) -> None:
    run(
        [DOCKER_BIN, "login", registry, "--username", username, "--password-stdin"],
        input_text=password,
    )


def docker_tag(source: str, target: str) -> None:
    run([DOCKER_BIN, "tag", source, target])


def docker_push(image: str) -> None:
    run([DOCKER_BIN, "push", image])


def docker_manifest_exists(image: str) -> bool:
    """Check if an image:tag exists in a remote registry without pulling.

    Returns True if `docker manifest inspect` succeeds, False if the manifest
    is missing. Any other failure (auth, network) re-raises ShellError.
    """
    res = run([DOCKER_BIN, "manifest", "inspect", image], check=False)
    if res.returncode == 0:
        return True
    if "no such manifest" in res.stderr.lower() or "manifest unknown" in res.stderr.lower():
        return False
    # Some other error — surface it
    from vastde_orch.clients._shell import ShellError
    raise ShellError(res)

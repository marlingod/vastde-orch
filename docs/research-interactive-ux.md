# Research Report: Interactive UX for `vastde-orch`

**Date:** 2026-05-28

## Executive Summary and Concrete Recommendations

**Library:** Use **questionary** as the primary prompt library. Wrap it with a thin adapter so `wizard` and `--interactive` share one import. Keep `click.prompt`/`click.confirm` only for the two or three simplest confirms that are already wired into Click command handlers. Do not add InquirerPy, rich.prompt, or beaupy.

**Wizard pattern:** Wizard generates `vastde.yaml`, the user reviews it, then runs `vastde-orch apply`. Do not apply live from the wizard. This matches the `cookiecutter`/`pulumi new`/`cdktf init` pattern and is the right call for a config that controls infrastructure.

**`--interactive` confirmation granularity:** Prompt per resource type, not per item. Show a summary ("5 users to create, 2 to update") and ask once. Provide `--yes-all` to skip all confirmations.

**Non-TTY behavior:** Detect with `sys.stdin.isatty()` at the top of any interactive entry point. If not TTY and no `--yes-all`, print a single actionable error to stderr and exit 2. Standardize on `VASTDE_NO_INTERACTIVE=1` env var. Accept `--non-interactive` flag as an alias.

**Patterns you may be missing:**
- The `--answers-file` / `--replay-file` pattern (cookiecutter) for scripted wizard runs in tests and CI.
- Numbered backup files before overwriting `vastde.yaml` (e.g., `vastde.yaml.bak.1`).
- Showing existing VMS values as defaults in every prompt (the `aws configure` pattern: `AWS Access Key ID [AKIA***]:`).
- Distinct exit codes: 0 = success, 1 = user abort, 2 = non-TTY / bad invocation, 3 = partial apply.

---

## Section 1: Python CLI Prompt Library Comparison

### `click.prompt` / `click.confirm`
Already a dependency; zero additional install cost. Handles `hide_input=True` for passwords, `type=click.Choice([...])`, `default=`. Testing: `CliRunner(mix_stderr=False).invoke(cmd, input="alice\n10001\nn\n")` works reliably. No multi-select, no fuzzy, no autocomplete. Verdict: fine for two-line confirms; not enough for a multi-step wizard.

### questionary 2.1.1
Built on prompt_toolkit. 2,100 stars. MIT. Python ≥ 3.9. Eight prompt types: text, password, filepath, confirm, select, raw_select, checkbox, autocomplete. `validate=` parameter accepts a lambda or `Validator` subclass. Conditional skipping via `.skip_if()`. Dictionary-based `questionary.prompt([...])` with `when=` lambdas for conditional prompts. Testing: not in CliRunner-compatible way — use the inject-answers pattern instead. Sources: [PyPI](https://pypi.org/project/questionary/), [GitHub](https://github.com/tmbo/questionary), [docs](https://questionary.readthedocs.io/en/stable/pages/advanced.html).

### InquirerPy 0.3.x
461 stars. Stronger than questionary on: fuzzy-search prompt (native), pagination, `when=` on every question type. Better if VMS routinely has 200+ tenants. Otherwise overkill. Sources: [GitHub](https://github.com/kazhala/InquirerPy), [docs](https://inquirerpy.readthedocs.io/en/latest/pages/prompt.html).

### rich.prompt
Already commonly installed. `Prompt.ask`, `Confirm.ask`, `IntPrompt`. No multi-select, no fuzzy. Good for the one or two confirms in `apply`/`enable`. Source: [rich prompt docs](https://rich.readthedocs.io/en/stable/prompt.html).

### prompt_toolkit (direct)
What questionary and InquirerPy wrap. Use directly only for full-screen TUI. Source: [docs](https://python-prompt-toolkit.readthedocs.io/en/stable/pages/asking_for_input.html).

### python-inquirer 3.4.1
Older PyInquirer port. Uses `blessed`. No compelling reason over questionary. Skip.

### typer prompts
Wraps Click. `typer.prompt()` ≡ `click.prompt()`. No richer prompt UX. Skip.

### beaupy 3.12.0
238 stars. rich + yakh. Smaller ecosystem. Skip unless prompt_toolkit problematic in target environments.

### Recommendation
**Primary: questionary.** Cookiecutter-tested (strong signal), clean API for 10–15 question wizards, longer track record than beaupy, no Click conflicts. Use `questionary.prompt([...])` for the wizard flow. Use `click.confirm` for the two interactive confirmations in `apply`/`enable` since they are already Click commands. If VMS tenant lists routinely exceed 50 items, add InquirerPy fuzzy for those specific questions only.

---

## Section 2: UX Patterns from Mature Infra Tools

### terraform apply
Default: runs plan, renders diff (`+` green, `~` yellow, `-` red, `-/+` for replace), prompts `Do you want to perform these actions? Only 'yes' will be accepted to approve.` — requires typing the word `yes`, not just `y`. Non-TTY: `-input=false` fails hard unless `-auto-approve`. The "type 'yes' not 'y'" pattern fits destructive operations (delete tenant). For additive, `Y/n` is fine. Source: [Terraform apply](https://developer.hashicorp.com/terraform/cli/commands/apply).

### terraform init / plan
No wizard. Entirely declarative. Lack of wizard is friction for beginners, not a design goal to copy.

### gh repo create
Excellent wizard. Prompts for name, visibility, description, clone, .gitignore, license. Defaults in `[brackets]`. Non-TTY with no args fails with clear error. `--yes` skips confirmations. Source: [gh repo create](https://cli.github.com/manual/gh_repo_create).

### gcloud init
Multi-stage wizard: account, project, default zone. Non-TTY: use separate non-interactive commands (`gcloud auth activate-service-account`). Source: [gcloud init](https://docs.cloud.google.com/sdk/docs/initializing).

### aws configure
Canonical small wizard. Four sequential prompts with current values shown as defaults in `[brackets]`. Credentials masked, not shown in full. Enter keeps existing value. Source: [aws configure source](https://github.com/aws/aws-cli/blob/develop/awscli/customizations/configure/configure.py).

### pulumi new
Wizard generates scaffolding. `--non-interactive` disables prompts; `--yes` skips optional prompts. Source: [pulumi new](https://www.pulumi.com/docs/iac/cli/commands/pulumi_new/).

### pulumi up
Preview shown by default. Three-option prompt: `yes` / `no` / `details`. The three-option model is compelling — drill into details without aborting. `-y`/`--yes` for auto-approve. `--non-interactive` to disable. Worth copying for `vastde-orch apply --interactive`. Source: [pulumi up](https://www.pulumi.com/docs/iac/cli/commands/pulumi_up/).

### vercel link/deploy
Progressive disclosure. Single confirm gate, then cascading questions, framework detection with auto-detected settings shown. Key pattern: **show auto-detected values as defaults, ask for confirmation rather than blank questions.** Source: [vercel project linking](https://vercel.com/docs/cli/project-linking).

### cookiecutter
Replay: dumps answers to `~/.cookiecutter_replay/<template-name>.json`. `--replay-file=path.json` uses specific file. Closest prior art to wizard-generates-YAML. Source: [cookiecutter replay](https://cookiecutter.readthedocs.io/en/stable/advanced/replay.html).

### databricks configure
Documents non-TTY mode explicitly: reads token from stdin, requires `--host` flag. Source: [docs](https://docs.databricks.com/en/dev-tools/cli/configure-cli.html).

### stripe CLI login
Three modes: TTY (browser OAuth), `--interactive` (paste key), `--non-interactive` (JSON output). Auto-detects TTY to choose mode. Source: [stripe CLI login](https://github.com/stripe/stripe-cli/blob/master/pkg/cmd/login.go).

### ansible-playbook --step
Prompts before each task: `Perform task: <task name> (N)o/(y)es/(c)ontinue:`. The "continue" option exits step mode mid-flow. Implement this for `--interactive apply`.

### helm / kubectl
No wizards. Config-as-interface only. Works for advanced users; not the target for first-time VAST DataEngine setup.

### npm init / create-next-app
Oldest "wizard generates JSON" pattern. `create-next-app` adds checkbox selection for TypeScript, ESLint, Tailwind. Strong prior art for wizard-generates-config.

---

## Section 3: Hybrid Wizard-then-YAML Pattern

No canonical name. Appears as "scaffolding wizard," "init wizard," "interactive config generation." Closest named pattern: **"scaffolding"** (yeoman, cookiecutter) or **"init wizard"** (pulumi new, cdktf init, npm init).

**Prior art:** cookiecutter (wizard → file tree, replay via `--replay-file`), npm init (wizard → package.json, no side effects), pulumi new (wizard → project files + first stack YAML, no cloud touched), cdktf init (wizard → cdktf.json + skeleton), gh repo create (wizard + live operation combined).

**Wizard-generates-YAML pros:** review before any VMS touch; version-controllable artifact; team can PR-review initial configs; wizard bugs caught at review, not after 12 resources created; idiomatic with existing declarative YAML; CI uses generated YAML directly with `apply`.

**Apply-live pros:** fewer commands for simple setups; immediate feedback.

**Apply-live cons:** partial state on mid-wizard failure; less reviewable; harder to test (must mock VMS in integration tests).

**Verdict:** Wizard-generates-YAML is correct. Apply-live is an anti-pattern for infrastructure tools.

---

## Section 4: Interactive Apply UX (Terraform-style)

### Bulk vs. per-item
Prompt **once per resource type with a count summary** — what `pulumi up` and `cdk deploy` do. Per-item prompting (12 confirms for 12 users) is a usability failure. Ansible's per-task `--step` works because tasks are semantically different; for homogeneous "create N users" it's noise.

```
Plan:
  + 3 users to create (alice, bob, carol)
  ~ 1 user to update (dave: quota 100GB → 200GB)
  + 2 views to create (logs, metrics)
  - 1 view to delete (old-archive)

Apply these changes? [yes/no/details] (no):
```

### Diff format
Pulumi's semantic colors: `+` green creates, `~` yellow updates, `-` red deletes, `-/+` magenta replace. Details view shows old → new per field. Honor `NO_COLOR` env var ([no-color.org](https://no-color.org/) — if set and non-empty, strip all ANSI). Check `TERM=dumb` too.

### --yes-all
Implement `--yes-all` (alias `-y`). Match terraform's `-auto-approve` semantics with a name more legible to non-Terraform users. Accept both.

### "Always yes for this type" mid-flow
Implement ansible's third option: per-type prompt has `(c)ontinue` that applies all remaining types without prompting. Lets users who started with `--interactive` exit step-mode when satisfied.

### Cancellation
`ensure_*` calls are idempotent → cancel mid-apply means re-run completes. Document: "Apply cancelled. Run `vastde-orch apply` again to complete remaining changes." Exit 1 on user cancel, 0 on success, 2 on error.

### Accessibility
`+`/`~`/`-` glyphs carry semantic meaning independent of color (essential). `NO_COLOR` removes color but glyphs remain. Never rely on color alone. Use `[+]`, `[~]`, `[-]` prefixes so output is screen-reader and color-blind safe.

---

## Section 5: Live VMS Probing for the Wizard

### Eager vs. lazy
**Lazy probe per section, cached for the session.** Eager has a 1–3 second stall before the first question. Lazy lets wizard start instantly. Cache in `_vms_cache: dict[str, list] = {}` — TTL within a session can be infinite.

### Implementation
```python
def _get_tenants(client) -> list[str]:
    if "tenants" not in _vms_cache:
        _vms_cache["tenants"] = client.list_tenants()
    return _vms_cache["tenants"]
```

### Unreachable
Do not abort. Fall back gracefully:
```
Warning: Could not reach VMS to list tenants (connection refused).
Continuing with manual entry.
```
This is the `gcloud init` pattern. The wizard should still complete and generate valid YAML. Validation against actual VMS state is the job of `apply`, not the wizard.

### Pagination
If VMS returns 50+ items, use InquirerPy's fuzzy prompt for that question only — the one valid layering scenario. Otherwise stick with questionary. Show at most 20 items default; offer search filter for longer.

---

## Section 6: Non-TTY / CI Behavior

The single most common mistake in interactive CLIs.

**1. Detect at every entry point.**
```python
def require_tty(ctx: click.Context) -> None:
    if not sys.stdin.isatty():
        no_interactive = (
            os.environ.get("VASTDE_NO_INTERACTIVE", "").strip() != ""
            or ctx.obj.get("non_interactive", False)
        )
        if no_interactive:
            return
        click.echo(
            "Error: This command requires an interactive terminal.\n"
            "  In CI, use: vastde-orch apply --yes-all\n"
            "  Or set: VASTDE_NO_INTERACTIVE=1",
            err=True,
        )
        ctx.exit(2)
```

**2. Standardize on `VASTDE_NO_INTERACTIVE=1`** — not `CI=true` (not yours to own, false positives).

**3. `--non-interactive` flag** on `wizard`, `enable`, `apply`. Wizard with `--non-interactive` is an error; `apply --non-interactive --yes-all` is valid for CI.

**4. `--answers-file answers.yaml`** on wizard. JSON or YAML of pre-filled answers. cookiecutter `--replay-file` pattern. Makes wizard deterministic in tests without mocking prompt_toolkit. **20 lines to implement, high ROI.**

**5. Stdin pipe (heredoc).** Do not support `yes | vastde-orch wizard` — fragile, unnecessary given `--answers-file`. Reject piped stdin with non-TTY error.

**Reference behavior:** Terraform (`-input=false -auto-approve`), Pulumi (`--non-interactive --yes`), Databricks (token from stdin + `--host`), Stripe (auto-detect TTY mode), gcloud (separate non-interactive subcommands).

**Simplest rule:** if `sys.stdin.isatty()` is False AND `VASTDE_NO_INTERACTIVE` unset AND `--non-interactive` not passed, exit 2 with clear message.

---

## Section 7: Resumability and Persistence

### Atomic write
```python
def write_yaml_atomic(path: pathlib.Path, content: str) -> None:
    dir_ = path.parent
    with tempfile.NamedTemporaryFile("w", dir=dir_, delete=False, suffix=".tmp") as f:
        f.write(content)
        tmp = f.name
    os.replace(tmp, path)
```
`os.replace` atomic on POSIX. Safe enough on Windows. Prevents mid-write corruption.

### Numbered backups
```python
def backup_existing(path: pathlib.Path, keep: int = 3) -> None:
    if not path.exists():
        return
    for i in range(keep - 1, 0, -1):
        src = path.with_suffix(f".yaml.bak.{i}")
        dst = path.with_suffix(f".yaml.bak.{i + 1}")
        if src.exists():
            src.rename(dst)
    path.rename(path.with_suffix(".yaml.bak.1"))
```
Cheap. Saves users from accidental wizard-over-edits. Emacs/vim swap-file pattern.

### Save-and-resume
Cookiecutter doesn't. Pulumi doesn't. npm init doesn't. For a 10–15 question wizard, re-running is acceptable (< 2 min). Do not implement partial state save — complexity not worth it. `--answers-file` is the resume mechanism for power users.

---

## Section 8: Testing Patterns

### Click.prompt path (for confirms in apply/enable)
```python
runner = CliRunner(mix_stderr=False)
result = runner.invoke(cli, ["apply", "--interactive"], input="yes\n")
```
Works for `click.prompt`/`click.confirm`. Does NOT work for questionary (prompt_toolkit's event loop reads from its own stream).

### questionary testing: inject-answers pattern
The most reliable, cheapest pattern is to inject answers directly into the wizard function:
```python
def run_wizard(client, answers: dict | None = None) -> dict:
    def ask(key, prompt_fn):
        if answers is not None:
            return answers[key]
        return prompt_fn()
    tenant = ask("tenant", lambda: questionary.text("Tenant name:").ask())
    ...
    return build_yaml_dict(tenant, ...)
```
In tests:
```python
answers = {"tenant": "acme", "users": ["alice", "bob"], ...}
result = run_wizard(client=mock_client, answers=answers)
assert result["tenant"] == "acme"
```
Django's `startproject` and similar tools use this pattern: generation logic is pure-function-testable; prompt-gathering is a thin wrapper.

### pexpect / subprocess for end-to-end TTY tests
```python
result = subprocess.run(
    ["vastde-orch", "wizard"],
    stdin=subprocess.PIPE,
    capture_output=True,
    text=True,
)
assert result.returncode == 2
assert "VASTDE_NO_INTERACTIVE" in result.stderr
```
`subprocess.run` with `stdin=PIPE` simulates non-TTY (isatty returns False).

### Test pyramid
1. **Unit (fast):** `run_wizard(answers=...)` with pre-filled dict; cover all branches.
2. **Integration (medium):** `CliRunner` with mocked VMS for `--yes-all` flows.
3. **E2E (slow, optional):** `subprocess.run(stdin=PIPE)` for non-TTY exit-code verification; `pexpect` for actual prompt rendering.

Do NOT simulate keystrokes into questionary via CliRunner.input — flaky tests harder to understand than the code they test.

---

## Source Table

| Topic | URL |
|---|---|
| questionary | https://pypi.org/project/questionary/ |
| questionary GitHub | https://github.com/tmbo/questionary |
| questionary docs | https://questionary.readthedocs.io/en/stable/pages/advanced.html |
| InquirerPy | https://github.com/kazhala/InquirerPy |
| InquirerPy docs | https://inquirerpy.readthedocs.io/en/latest/pages/prompt.html |
| prompt_toolkit | https://python-prompt-toolkit.readthedocs.io/en/stable/pages/asking_for_input.html |
| beaupy | https://github.com/petereon/beaupy |
| python-inquirer | https://github.com/magmax/python-inquirer |
| click prompts | https://click.palletsprojects.com/en/8.1.x/api/#click.prompt |
| rich prompts | https://rich.readthedocs.io/en/stable/prompt.html |
| terraform apply | https://developer.hashicorp.com/terraform/cli/commands/apply |
| pulumi new | https://www.pulumi.com/docs/iac/cli/commands/pulumi_new/ |
| pulumi up | https://www.pulumi.com/docs/iac/cli/commands/pulumi_up/ |
| gcloud init | https://docs.cloud.google.com/sdk/docs/initializing |
| aws configure | https://github.com/aws/aws-cli/blob/develop/awscli/customizations/configure/configure.py |
| gh repo create | https://cli.github.com/manual/gh_repo_create |
| vercel link | https://vercel.com/docs/cli/project-linking |
| cookiecutter replay | https://cookiecutter.readthedocs.io/en/stable/advanced/replay.html |
| databricks configure | https://docs.databricks.com/en/dev-tools/cli/configure-cli.html |
| stripe CLI login | https://github.com/stripe/stripe-cli/blob/master/pkg/cmd/login.go |
| NO_COLOR | https://no-color.org/ |

---

## Patterns You May Be Missing

1. **`--answers-file answers.yaml`** on wizard — CI scriptability + trivial test fixtures. ~20 lines.
2. **Show current/auto-detected values as defaults** in every prompt (`Tenant [acme-corp]:`). aws-configure mask pattern for credentials.
3. **Three-option (yes/no/details)** in `--interactive apply` from pulumi up — drill in without aborting.
4. **`(c)ontinue` option** in per-type prompts from ansible `--step` — exit step mode mid-flow.
5. **Numbered YAML backups** before any wizard overwrite. Prevents accidental wipe of hand-edited files.
6. **Distinct exit codes:** 0 success, 1 user abort, 2 invocation error, 3+ VMS errors. Matters for CI `set -e`.
7. **`TERM=dumb`** check alongside `NO_COLOR`. Some CI sets `NO_COLOR`, others set `TERM=dumb`.

# Manual Test Guide

Step-by-step manual walkthrough for `vastde-orch`. Each section marks whether it needs a real VAST cluster or runs entirely offline.

---

## 0. Setup (one time)

```bash
cd /Users/yemalin.godonou/Documents/vast/dataengine
source .venv/bin/activate          # already created by `python3 -m venv .venv && pip install -e ".[dev]"`
vastde-orch --version
vastde-orch --help                 # see all 7 subcommands
```

**Expected**: lists `apply  destroy  enable  function  status  validate  wizard`.

---

## 1. Run the full test suite — offline

```bash
pytest -q                                       # expect: 162 passed
pytest tests/test_interactive_wizard.py -v      # see wizard tests by name
pytest --cov=vastde_orch --cov-report=term-missing tests/ | tail -20
```

**Expected**: 162/162 pass; overall coverage ~79%.

---

## 2. Validate the example YAML — offline

```bash
VMS_ADDRESS=vms.test VMS_TOKEN=t REGISTRY_USER=u REGISTRY_PASSWORD=p \
  S3_KEY=ak S3_SECRET=sk \
  vastde-orch validate -c config/vastde.example.yaml
```

**Expected**:
```
OK: config/vastde.example.yaml
  - tenant: data-platform
  - enablement: present
  - pipelines: 1
```

### 2a. Verify schema validation rejects bad configs

```bash
sed 's|to: parse-pdf|to: ghost-function|' \
  config/vastde.example.yaml > /tmp/bad.yaml

VMS_ADDRESS=x VMS_TOKEN=t REGISTRY_USER=u REGISTRY_PASSWORD=p \
  S3_KEY=k S3_SECRET=s \
  vastde-orch validate -c /tmp/bad.yaml
echo "exit: $?"
```

**Expected**: error mentioning `flow edge to 'ghost-function' must reference a function`, exit code 2.

---

## 3. Wizard via answers file — offline (CI-safe)

```bash
rm -f /tmp/wiz.yaml
vastde-orch wizard \
  --answers-file tests/fixtures/answers_full.yaml \
  -o /tmp/wiz.yaml

head -30 /tmp/wiz.yaml         # inspect generated config
```

**Expected**: prints `Wrote /tmp/wiz.yaml` and "Next steps:" hints.

### 3a. Backup rotation

```bash
vastde-orch wizard --answers-file tests/fixtures/answers_full.yaml -o /tmp/wiz.yaml
ls -la /tmp/wiz.yaml*           # now also see wiz.yaml.bak.1
vastde-orch wizard --answers-file tests/fixtures/answers_full.yaml -o /tmp/wiz.yaml
ls -la /tmp/wiz.yaml*           # bak.1 + bak.2 now exist
```

**Expected**: backup files `.bak.1`, `.bak.2`, capped at `.bak.3`.

### 3b. Validate what the wizard produced

```bash
VMS_TOKEN=fake REGISTRY_USER=u REGISTRY_PASSWORD=p \
  vastde-orch validate -c /tmp/wiz.yaml
```

**Expected**: `OK: /tmp/wiz.yaml`.

---

## 4. Wizard interactively — needs a real terminal

The actual UX with questionary prompts:

```bash
rm -f /tmp/interactive.yaml
vastde-orch wizard -o /tmp/interactive.yaml
```

Walk-through you should see:
1. VMS address (default: `vms.example.com`)
2. Authentication method (token / user_password)
3. Environment variable holding the token (default: `VMS_TOKEN`)
4. Tenant name
5. *Generate Stage A (enablement) section?* → Yes
6. Tenant name + create_if_missing
7. *Is Kubernetes already set up for VAST DataEngine?*
   - **No** → asks for zarf package + init paths
   - **Yes** → skips those
8. K8s cluster name + API server URL
9. Container registry URL + auth method + env var names
10. Event broker type (vast / kafka), then per-kind fields
11. DataEngine group name + GID
12. Loop: "Add a user?" — add as many as you want
13. Loop: "Add a source view?"
14. *Generate Stage B (pipelines) section?* → Yes
15. Loop: pipeline name → triggers loop → functions loop → flow

Ctrl-C aborts cleanly at any prompt.

---

## 5. Non-TTY guard works — offline

```bash
vastde-orch wizard < /dev/null
echo "exit: $?"
```

**Expected**: helpful error message pointing to `--answers-file` and `VASTDE_NO_INTERACTIVE`, exit code 2.

```bash
VASTDE_NO_INTERACTIVE=1 vastde-orch wizard < /dev/null
echo "exit: $?"
```

**Expected**: still errors (wizard requires either TTY or `--answers-file`), exit code 2.

---

## 6. Function tag — offline, no VMS needed

Demonstrates content-hash tagging:

```bash
mkdir -p functions/demo
echo "print('hi')" > functions/demo/main.py
echo "" > functions/demo/requirements.txt

cat > /tmp/funcs.yaml <<'EOF'
vms: { address: x, token: t, tenant: default }
pipelines:
  - name: demo
    k8s_cluster: k
    functions:
      - { name: demo, source: ./functions/demo, image: r/demo }
EOF

vastde-orch function tag demo -c /tmp/funcs.yaml
```

**Expected**: 12-char hex hash (sha256 of source contents, truncated).

```bash
# Change the source — hash must change
echo "print('hi v2')" > functions/demo/main.py
vastde-orch function tag demo -c /tmp/funcs.yaml
```

**Expected**: a different 12-char hash.

---

## 7. Enable / apply — needs a real VAST cluster

These call live VMS endpoints (even with `--plan`, since dry-run still GETs to detect drift).

```bash
export VMS_ADDRESS=<your-vms-host>
export VMS_TOKEN=<your-token>
export REGISTRY_USER=...
export REGISTRY_PASSWORD=...
```

### 7a. Dry-run Stage A

```bash
vastde-orch enable -c config/vastde.example.yaml --plan
```

**Expected**: Terraform-style plan output (`+` create, `~` update, `=` no-op) with summary `N change(s), M unchanged`.

### 7b. Interactive Stage A

```bash
vastde-orch enable -c config/vastde.example.yaml --interactive
```

**Expected**: dry-run plan first, then per-resource-type prompt:
```
viewpolicies: 1 to would_create (dataengine-default)
? Apply these changes?  ❯ yes  no  details  continue (yes to all remaining)
```

Try each option to see behavior:
- `yes` → applies that type, moves to next
- `details` → renders per-resource diff, re-prompts
- `continue` → sticky-yes for all remaining types
- `no` → abort, exit 1

### 7c. Stage B

```bash
vastde-orch apply -c config/vastde.example.yaml --plan
vastde-orch apply -c config/vastde.example.yaml --interactive
vastde-orch apply -c config/vastde.example.yaml --only pdf-ingest --yes-all
```

### 7d. CI / non-interactive

```bash
vastde-orch apply -c config/vastde.example.yaml --yes-all
echo "exit: $?"
```

**Expected**: runs without prompts; exit 0 on success.

### 7e. Status

```bash
vastde-orch status -c config/vastde.example.yaml
```

**Expected**: per pipeline: `status=Running deployed_at=...` or `not deployed`.

---

## 8. Destroy — needs real VAST cluster

```bash
vastde-orch destroy -c config/vastde.example.yaml --only pdf-ingest
# Asks: "Really destroy these resources? [y/N]"
```

```bash
vastde-orch destroy -c config/vastde.example.yaml --include-enablement --yes
# Skips confirmation; tears down pipelines + disables DataEngine on tenant
```

---

## 9. No VMS? Read the test suite

If you don't have a VAST cluster handy, the unit tests cover every code path:

```bash
pytest tests/test_enablement.py -v        # Stage A orchestration
pytest tests/test_pipelines.py -v         # Stage B (functions, triggers, pipelines)
pytest tests/test_clients_vms.py -v       # idempotency / ensure() semantics
pytest tests/test_interactive_wizard.py -v # wizard branches
pytest tests/test_interactive_confirm.py -v # the 4-option prompt
```

Each test name describes one behavior (e.g. `test_creates_when_absent_and_image_missing`).

---

## 10. Read the design docs

```bash
cat /Users/yemalin.godonou/.claude/plans/ultraplan-cannot-launch-remote-virtual-flamingo.md
cat docs/research-interactive-ux.md
cat DECISIONS.md
cat CLAUDE.md
```

- `ultraplan-*.md`: the approved plan for the interactive layer
- `research-interactive-ux.md`: deep research on prompt libraries, terraform/pulumi UX patterns, non-TTY behavior
- `DECISIONS.md`: every non-obvious choice with rationale
- `CLAUDE.md`: project conventions (auto-decide protocol, no clarifying questions)

---

## Cheat-sheet — common idioms

| Goal | Command |
|---|---|
| First-time setup, no VMS | `vastde-orch wizard` → review YAML → commit |
| Validate before applying | `vastde-orch validate -c vastde.yaml` |
| Dry-run | `vastde-orch enable -c vastde.yaml --plan` |
| Apply with per-type prompts | `vastde-orch enable -c vastde.yaml --interactive` |
| CI: full headless apply | `VASTDE_NO_INTERACTIVE=1 vastde-orch apply -c vastde.yaml --yes-all` |
| Build & push one function | `vastde-orch function build my-fn -c vastde.yaml` |
| Just compute the would-be tag | `vastde-orch function tag my-fn -c vastde.yaml` |
| Tear down a single pipeline | `vastde-orch destroy -c vastde.yaml --only my-pipe --yes` |
| Tear down everything | `vastde-orch destroy -c vastde.yaml --include-enablement --yes` |

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `wizard` hangs in CI | non-TTY but no `--answers-file` | Pass `--answers-file` or set `VASTDE_NO_INTERACTIVE=1` and use `apply --yes-all` |
| `config error: environment variable 'X' referenced in config is not set` | `${X}` in YAML, var unset in shell | `export X=...` or `.env` file |
| `vastpy` connection refused | VMS unreachable | check `$VMS_ADDRESS`, network, certs |
| `ShellError: required binary 'vastde' not found on PATH` | `vastde` CLI not installed | install from VAST docs portal (download CLI binary) |
| `manifest unknown` after `function build` | image not pushed | check `docker login`, registry creds |
| `flow edge to X must reference a function` | trigger name on `to:` side | only functions can be flow targets; triggers go on `from:` only |
| `flow contains a cycle` | f1 → f2 → f1 in flow | break the cycle; pipelines must be DAGs |

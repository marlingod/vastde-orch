# vastde-orch

Declarative YAML automation for VAST DataEngine. One config file describes:

- **Stage A — Enablement** of DataEngine on a VAST cluster tenant (K8s + Zarf, container registry, event broker, identity, source views).
- **Stage B — Pipelines**: triggers, functions (built & pushed from source), and pipeline flows.

Both stages are idempotent (`get → post|patch|no-op`) and support a Terraform-style `--plan` dry-run.

## Two paths: wizard or YAML

**Wizard** (recommended first time):
```bash
pip install -e ".[dev]"
cp .env.example .env  # fill in VMS_ADDRESS, VMS_TOKEN, etc.

vastde-orch wizard                              # prompts step-by-step → writes vastde.yaml
vastde-orch validate -c vastde.yaml
vastde-orch enable   -c vastde.yaml --interactive   # prompts before each resource type
vastde-orch apply    -c vastde.yaml --interactive
```

**YAML** (re-runnable, GitOps):
```bash
pip install -e ".[dev]"
cp .env.example .env
cp config/vastde.example.yaml vastde.yaml      # then edit

vastde-orch validate -c vastde.yaml
vastde-orch enable   -c vastde.yaml --plan     # dry-run Stage A
vastde-orch enable   -c vastde.yaml            # apply Stage A
vastde-orch apply    -c vastde.yaml --plan     # dry-run Stage B
vastde-orch apply    -c vastde.yaml            # apply Stage B
vastde-orch status   -c vastde.yaml
```

## Interactive flags

| Flag | Effect |
|---|---|
| `--interactive` / `-i` | Prompt before each resource type with options `yes / no / details / continue` |
| `--yes-all` / `-y` | Auto-approve all prompts (use in CI alongside non-interactive) |
| `--non-interactive` | Opt out of interactivity even on a TTY (errors fast if values missing) |
| `VASTDE_NO_INTERACTIVE=1` (env) | Same as `--non-interactive` |
| `--answers-file <path>` (wizard only) | Pre-fill every wizard prompt from a YAML; CI-safe |

In CI, the canonical idioms are:
```bash
vastde-orch wizard --answers-file answers.yaml -o vastde.yaml
vastde-orch enable -c vastde.yaml --yes-all
vastde-orch apply  -c vastde.yaml --yes-all
```

## Function inner loop

```bash
vastde-orch function build parse-pdf   # build & push image; no API calls
vastde-orch apply -c vastde.yaml --only pdf-ingest
```

## Prerequisites on the operator machine

- Python 3.11+
- `vastde` CLI (download from your VAST cluster docs)
- `docker` for image build/push
- `kubectl` and `zarf` CLIs (only for `enable`)

## Configuration reference

See `config/vastde.example.yaml` for the full annotated schema, or run
`python -c "from vastde_orch.config.models import VastdeConfig; import json; print(json.dumps(VastdeConfig.model_json_schema(), indent=2))"`.

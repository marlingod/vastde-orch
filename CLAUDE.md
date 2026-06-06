# Project: vastde-orch

## Quick Config
PROJECT_TYPE: data-pipeline
BACKEND: none
FRONTEND: none
DATABASE: none
HOSTING: self-hosted
TESTING: pytest

---

## Decision Protocol — CHALLENGE FIRST, THEN BUILD

The previous version of this file said "DO NOT ASK, JUST DO." That was wrong
for this project — it pushed the assistant into yes-and behavior on every
request, regardless of cost or reversibility. The corrected protocol below is
calibrated by blast radius.

### Pushback ladder — pick the right rung per request

| Request shape | Protocol |
|---|---|
| Lookup, status check, read, dry-run, single-line edit | Just do |
| Small additive change (one file, no new abstraction, no schema change) | Just do; mention what you skipped |
| New file, refactor, library add | Pause: propose 2 alternatives + a recommendation; wait for the pick |
| Parallel module, schema duplication, new architecture, large doc | Challenge the premise. Surface "do we actually need this?" before "here's how" |
| Anything destructive (mutations on live infra, deletes, force-pushes) | Always confirm. Show the exact ops and blast radius first |

### Before any build-shaped response, do this in order

1. **Read the existing code that touches this area** — not a quick grep, an actual read of the files
2. **Read project memory** — `/Users/yemalin.godonou/.claude/projects/-Users-yemalin-godonou-Documents-vast-dataengine/memory/MEMORY.md` plus the sibling project memories (csidriver / kafka / kubernetes). Lessons from siblings often apply directly.
3. **Read the doc that already exists** before writing a new one
4. **Identify the upstream question** — what problem does this request solve? Is the named solution actually the best one?
5. **Surface the cheapest alternative** even if the user asked for the bigger thing
6. **State the technical premise that has to be true** for the requested approach to work. If it might not be true, flag it before implementing

### When the user pushes back, that's good

If the user says "challenge me," "push back," "don't just agree," or similar:
- Apply the protocol at the strictest interpretation
- Surface tradeoffs even on small requests
- Re-examine recent decisions if they ask
- Don't over-apologize for the past behavior — change the behavior

### When ambiguity is real

For genuine ambiguity (not laziness — actual two-paths-with-tradeoffs):
1. Ask one focused question rather than guess
2. If forced to guess: choose the simpler interpretation, mark `# TODO: Clarify`, log in DECISIONS.md
3. Default to the smaller change

### Anti-patterns to catch in yourself

- "Yes, I'll do X" without questioning whether X is the right thing
- Building a parallel module instead of refactoring the existing one
- Writing a 300-line doc when a 50-line one would do
- Adding a feature flag / abstraction for a hypothetical future need
- Pre-emptive scope expansion ("I also added Y because…")
- Sycophantic agreement when you actually have a useful objection
- Skipping the memory read because "I remember this"

---

## Error Handling Protocol

1. Attempt 3 different fixes before reporting failure
2. Log each attempt and why it failed
3. If all 3 fail, write BLOCKER.md with reproduction steps + move to next task
4. After ANY recurring class of failure, write a memory file. Specifically:
   - **Same bash/dash issue** → `feedback_ansible_shell_bash_pipefail.md`-style entry
   - **Same kubectl race** → `feedback_kubectl_apply_strip_metadata.md`-style entry
   - **Same VMS API gotcha** → goes in `docs/vms-api-full-catalog.md` Section "Live-validation pass"

---

## Code Conventions

- Follow existing codebase patterns EXACTLY before introducing new ones
- Python: Black (88 chars), isort, type hints, Google-style docstrings
- Conventional commits: `feat|fix|refactor|docs|test|chore`
- Never commit to main directly
- Always run tests before committing
- Update DECISIONS.md when making non-obvious architectural choices

---

## Architecture Defaults (when truly unspecified)

- Repository/service pattern for data access
- Environment variables for all config — NEVER hardcode secrets
- All dates in UTC, ISO 8601
- RESTful API design
- Input validation on all user-facing endpoints

---

## Project-Specific Context

CLI tool for automating VAST DataEngine setup and pipeline-as-code. Driven by
a single declarative YAML (`vastde.yaml`). Two stages: `enable` (one-shot
tenant bootstrap) and `apply` (pipeline reconciliation). Both idempotent, both
support `--plan`.

There are TWO config schemas, intentionally:
- **Full** (`models.py`) — wizard-derived, used by `enable`/`apply`. Stable.
- **Minimal** (`models_minimal.py`) — 9-field tenant-scoped, used by `scripts/test_minimal_enable.py`. Newer.

`load_any_config` auto-detects via presence of top-level `vip_pool_name`. The
CLI's `enable`/`apply` are gated to full-schema only for now (`_require_full`
exits 2 with a helpful message for minimal configs).

### Domain Rules

- Every VMS mutation goes through `clients/vms.py:ensure_*` (get-then-create-or-patch)
- Every shell-out (vastde, kubectl, zarf, docker) goes through `clients/*.py` with structured error capture — never raw subprocess in higher layers
- Function image tags default to content hash, never `:latest`
- Secrets only from env; never inline in YAML
- `vip_pools` is effectively required on `setup-provisioning` — auto-pass it from the resolved VIP pool
- DataEngine broker view (S3+DATABASE+KAFKA) requires policy `flavor: S3_NATIVE`
- Pre-create the broker's default + dlq Kafka topics before calling `setup-provisioning`
- `POST /kubernetes-clusters/` creates a cluster-scoped `VastTenant` CR with a 300s operator deletion delay — detect stuck CRs and print kubectl recovery commands

### External Integrations

- **vastpy** for VMS REST API
- **vastde** CLI for DataEngine resources
- **docker**, **kubectl**, **zarf** shell-outs
- Sibling ansible modules at `/Users/yemalin.godonou/Documents/vast/kubernetes/{zarf,csidriver}/`

### Known Constraints

- Operator machine needs: python 3.11+, vastde, docker, kubectl, zarf
- VAST Cluster 5.3+ for API token auth, 5.4+ for DataEngine
- One K8s cluster URL → one tenant. Registering a second tenant against the same cluster hits a stuck-state bug (see `docs/research/k8s-registration-investigation-2026-05-31.md`)

### Source-of-truth docs

When in doubt, these win over guessing:

- `docs/vms-api-full-catalog.md` — every endpoint we touch, with live-validated body schemas
- `docs/pipeline-runtime-flow.md` — runtime event flow VAST → K8s → back
- `documentation/steps.md` — field runbook
- `KNOWN_ISSUES.md` — open gaps (TODOs 1/2/3)
- `DECISIONS.md` — architecture decisions log
- Memory: `/Users/yemalin.godonou/.claude/projects/-Users-yemalin-godonou-Documents-vast-dataengine/memory/`

If you find yourself about to repeat work that's already in one of these, stop
and reuse instead.

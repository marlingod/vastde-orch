# Project: vastde-orch

## Quick Config
# ─── Fill these 6 fields. Everything else auto-adapts. ───
PROJECT_TYPE: data-pipeline
BACKEND: none
FRONTEND: none
DATABASE: none
HOSTING: self-hosted
TESTING: pytest

---

## Decision Protocol — DO NOT ASK, JUST DO

When you encounter ambiguity, follow these rules.
Never ask for clarification — make the decision and document it in DECISIONS.md.

### When Requirements Are Ambiguous
1. Choose the simpler interpretation
2. Implement the minimum viable version
3. Add: `# TODO: Clarify — assumed [YOUR ASSUMPTION]`
4. Log the decision in DECISIONS.md with rationale

### Error Handling Protocol
1. Attempt 3 different fixes before reporting failure
2. Log each attempt and why it failed
3. If all 3 fail, write BLOCKER.md with reproduction steps and move to next task

### Code Conventions
- Follow the existing codebase patterns EXACTLY
- If no patterns exist, use community standards for the language:
  - Python: Black (88 chars), isort, type hints, Google-style docstrings
  - TypeScript/JS: Prettier, ESLint, strict TypeScript
  - Go: gofmt, golint
  - Rust: rustfmt, clippy
- Conventional commits: feat|fix|refactor|docs|test|chore
- Never commit to main directly
- Always run tests before committing

### Architecture Defaults (when not specified)
- Use repository/service pattern for data access
- Environment variables for all config (never hardcode)
- All dates in UTC, ISO 8601 format
- RESTful API design with OpenAPI/Swagger docs
- JWT auth with httponly cookies (when auth is needed)
- Input validation on all user-facing endpoints

### What NOT to Do
- Never ask which library to use — pick the most popular stable option
- Never ask about file structure — follow existing patterns or framework conventions
- Never ask about naming conventions — follow existing patterns
- Never ask "should I also..." — if it improves the code, just do it
- Never ask for confirmation before running safe commands

---

## Project-Specific Context

CLI tool for automating VAST DataEngine setup and pipeline-as-code. Driven by a single
declarative YAML (`vastde.yaml`). Two stages: `enable` (one-shot tenant bootstrap) and
`apply` (pipeline reconciliation). Both idempotent, both support `--plan`.

### Domain Rules
- Every VMS mutation goes through `clients/vms.py` ensure_* methods (get-then-create-or-patch).
- Every shell-out (vastde, kubectl, zarf, docker) goes through `clients/*.py` with structured error capture — never raw subprocess calls in higher layers.
- Function image tags default to content hash, never `:latest`.
- Secrets only from env; never inline in YAML.

### External Integrations
- **vastpy** for VAST Management Service REST API.
- **vastde** CLI for DataEngine resources (triggers/functions/pipelines).
- **docker**, **kubectl**, **zarf** shell-outs.

### Known Constraints
- Operator machine needs: python 3.11+, vastde, docker, kubectl, zarf.
- VAST Cluster 5.3+ for API token auth, 5.4+ for DataEngine.

---
description: Takes an approved ultraplan and executes it using git worktrees and agent teams. The bridge between Anthropic's cloud planning and our parallel execution engine.
---

Execute this approved ultraplan using worktrees and agent teams. ZERO questions asked.

Plan source: $ARGUMENTS

## Protocol

### Step 1: Locate the Plan
- If $ARGUMENTS is a file path → read that file
- If $ARGUMENTS is empty → check for the most recent plan in docs/plans/
- If $ARGUMENTS is pasted text → parse it directly

### Step 2: Parse the Plan into Phases
Read the ultraplan output and extract:
1. **Implementation phases** — each major section becomes a worktree
2. **File changes** — which files each phase creates or modifies
3. **Dependencies** — which phases depend on others (sequential vs parallel)
4. **Acceptance criteria** — what "done" looks like for each phase

If the ultraplan doesn't have clear phases, split by concern:
- Backend changes → one worktree
- Frontend changes → one worktree
- Infrastructure/config changes → one worktree
- Tests → parallel with implementation

### Step 3: Create Worktrees
```bash
PROJECT=$(basename $(git rev-parse --show-toplevel))
git checkout main && git pull origin main 2>/dev/null || true

# Independent phases → parallel worktrees from main
# Sequential phases → Phase N+1 branches from Phase N
```

### Step 4: Spawn Agent Teams (per worktree)
For each worktree, spawn:

1. **"architect"** — Reads the ultraplan phase, maps it to existing codebase patterns, writes detailed implementation spec. Goes FIRST.
2. **"backend-dev"** — Implements backend: models, services, routes, migrations. Starts after architect.
3. **"frontend-dev"** — Implements UI components using the API contract. Starts after backend-dev defines routes.
4. **"test-engineer"** — Writes tests for all new code. Runs in parallel with frontend-dev.
5. **"reviewer"** — Code review + security audit. Checks compliance rules from CLAUDE.md. Goes LAST.

### Step 5: Quality Gates
Per worktree:
- All tests pass
- Code formatted
- No CRITICAL/HIGH findings from reviewer
- Conventional commits
- Summary written to docs/changelog/

### Step 6: Merge Strategy
- If ultraplan specified a PR workflow → push branches, create PRs
- If not → merge sequentially into main with --no-ff
- Write final report: what was built, what was merged, what needs attention

## Rules
- The ultraplan IS the spec. Don't second-guess it — implement what it says.
- Read CLAUDE.md for project-specific conventions and compliance rules
- If the ultraplan is vague on implementation details, follow existing codebase patterns
- Document any assumptions in DECISIONS.md
- Use delegate mode for team leads

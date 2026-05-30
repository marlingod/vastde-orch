---
description: Takes research output recommendations and auto-implements them. Creates a git worktree per phase, spawns an agent team per worktree, assigns specialized agents to each task. Zero questions.
---

Implement these research recommendations using git worktrees and agent teams. ZERO questions asked.

Research output or recommendations: $ARGUMENTS

## Execution Protocol

### Phase 1: Parse Recommendations
1. If $ARGUMENTS references a file path (e.g., docs/research/*.md), read that file
2. If $ARGUMENTS contains the recommendations directly, parse them inline
3. Extract each distinct phase/recommendation as a separate implementation unit
4. For each phase, identify:
   - **Feature name** (kebab-case, for branch naming)
   - **Scope** (what models, routes, services, UI components are needed)
   - **Dependencies** (does this phase depend on a previous one?)
   - **Acceptance criteria** (how do we know it's done?)

### Phase 2: Create Git Worktrees
For each independent phase (or the first phase if they're sequential):

```bash
# Ensure we're on main and it's clean
git checkout main
git pull origin main 2>/dev/null || true

# Create a worktree per phase
git worktree add ../REPO_NAME-phase-N feature/PHASE-NAME
```

Naming convention:
- Branch: `feature/phase-N-short-description`
- Worktree directory: `../PROJECT-phase-N`

If phases are sequential (Phase 2 depends on Phase 1), only create and implement Phase 1 first. Once complete, create Phase 2's worktree from Phase 1's branch.

If phases are independent, create all worktrees simultaneously.

### Phase 3: Spawn Agent Teams (per worktree)
For EACH worktree, spawn an agent team with these specialists:

**Team for each phase:**

1. **"architect"** — Reads the research output and existing codebase. Produces an implementation plan: file list, data models, API contracts, dependencies needed. Writes plan to `docs/plans/phase-N-plan.md`. Goes FIRST.

2. **"backend-dev"** — Implements models, schemas, services, routes, migrations based on the architect's plan. Follows existing codebase patterns exactly. Starts after architect.

3. **"frontend-dev"** — Builds React/UI components for this phase. Uses the API contract from the architect's plan. Starts after backend-dev defines routes.

4. **"test-engineer"** — Writes unit tests, integration tests, and API tests for all new code. Runs tests and fixes failures. Works in parallel with frontend-dev.

5. **"reviewer"** — Security audit + code review of ALL code from teammates. Checks for:
   - Compliance violations (check CLAUDE.md for domain-specific rules)
   - SQL injection, XSS, auth bypass
   - Missing input validation
   - Proper error handling
   Goes LAST.

### Phase 4: Quality Gates
After each team finishes:
1. All tests must pass
2. Code must be formatted (black/prettier)
3. No CRITICAL or HIGH security findings from reviewer
4. Commit all changes with conventional commit messages
5. Write a summary to `docs/changelog/phase-N-summary.md`

### Phase 5: Integration
After all phases are complete:
1. Merge Phase 1 branch into main (or create PR)
2. If Phase 2 exists and depends on Phase 1, rebase Phase 2 onto updated main
3. Repeat for subsequent phases

## Worktree Commands Reference
```bash
# List active worktrees
git worktree list

# Remove a worktree after merging
git worktree remove ../PROJECT-phase-N

# Clean up stale worktrees
git worktree prune
```

## Rules for ALL agents across ALL worktrees
- Read CLAUDE.md before doing anything
- Follow existing codebase patterns EXACTLY
- NEVER ask clarifying questions — use the research output as the spec
- Document assumptions in DECISIONS.md within the worktree
- Each worktree is an isolated workspace — don't modify files outside it
- Use delegate mode for the team lead
- If blocked, write BLOCKER.md and notify the lead

## Project-Specific Context
Read CLAUDE.md for project-specific rules (compliance requirements, tech stack, conventions). Apply them to ALL phases automatically. Do not hardcode project assumptions here.

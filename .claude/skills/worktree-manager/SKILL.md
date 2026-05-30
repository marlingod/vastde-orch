---
name: worktree-manager
description: Manage git worktrees for parallel feature development. Triggers on worktree, create worktree, parallel branches, isolate feature, merge worktree.
allowed-tools: Bash(git *), Read, Write, Grep, Glob
---

## Git Worktree Management Protocol

Worktrees let you have multiple branches checked out simultaneously in separate directories. Each worktree is a full working copy — agents can work in different worktrees in parallel without conflicts.

### Creating Worktrees

```bash
# Get project name for directory naming
PROJECT=$(basename $(git rev-parse --show-toplevel))

# Ensure main is up to date
git checkout main
git pull origin main 2>/dev/null || true

# Create worktree with a new branch
git worktree add ../${PROJECT}-FEATURE_NAME feature/FEATURE_NAME

# Verify
git worktree list
```

**Naming conventions:**
- Branch: `feature/phase-N-short-description` or `feature/short-description`
- Directory: `../${PROJECT}-phase-N` (sibling to main repo)

### Worktree with Agent Teams

When spawning agent teams in a worktree, tell the lead to work in the worktree directory:

```
Create an agent team to work in the worktree at ../PROJECT-phase-1/

All teammates should:
1. cd to ../PROJECT-phase-1/ before doing any work
2. Read CLAUDE.md in that directory
3. Only modify files within that worktree
4. Commit to the worktree's branch (feature/phase-1-xxx)
```

### Sequential Phases (Phase 2 depends on Phase 1)

```bash
# After Phase 1 is complete and committed:
cd ../${PROJECT}-phase-1
git add -A && git commit -m "feat: phase 1 complete"

# Create Phase 2 branching from Phase 1
git worktree add ../${PROJECT}-phase-2 -b feature/phase-2-xxx feature/phase-1-xxx
```

### Independent Phases (can run in parallel)

```bash
# All branch from main
git worktree add ../${PROJECT}-phase-1 feature/phase-1-xxx
git worktree add ../${PROJECT}-phase-2 feature/phase-2-xxx
git worktree add ../${PROJECT}-phase-3 feature/phase-3-xxx
```

### Merging Back

```bash
# After all work is done in a worktree:
git checkout main

# Merge phase 1
git merge feature/phase-1-xxx --no-ff -m "feat: merge phase 1 — lab order tracking"

# If phase 2 was based on phase 1, rebase onto updated main first
git checkout feature/phase-2-xxx
git rebase main
git checkout main
git merge feature/phase-2-xxx --no-ff -m "feat: merge phase 2 — health gorilla integration"

# Clean up
git worktree remove ../${PROJECT}-phase-1
git worktree remove ../${PROJECT}-phase-2
git branch -d feature/phase-1-xxx
git branch -d feature/phase-2-xxx
```

### Cleanup

```bash
# List all worktrees
git worktree list

# Remove a specific worktree
git worktree remove ../${PROJECT}-phase-N

# Prune stale worktree references
git worktree prune
```

### Decision Rules
- Always branch from main unless a phase explicitly depends on another
- Never modify files in the main repo from a worktree — stay isolated
- Each worktree gets its own DECISIONS.md entries
- Commit frequently within worktrees — small, conventional commits
- Run tests in the worktree before marking phase complete
- If a merge conflict occurs, resolve in favor of the newer phase and document why

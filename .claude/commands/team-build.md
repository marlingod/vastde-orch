---
description: Spawn an agent team for parallel feature development. Requires agent teams enabled.
---

Build this with an agent team, ZERO questions: $ARGUMENTS

## Team Structure

1. **"researcher"**: Research any external APIs, standards, or requirements. Write to docs/research/. Goes FIRST.
2. **"backend-dev"**: Build backend logic (models, services, routes, migrations). Starts after researcher.
3. **"frontend-dev"**: Build frontend components. Starts once backend-dev defines the API contract.
4. **"test-engineer"**: Write comprehensive tests. Runs in parallel with frontend-dev.
5. **"reviewer"**: Security + code quality review of ALL code. Goes LAST.

## Rules for ALL teammates
- Read CLAUDE.md before doing anything
- Follow existing codebase patterns exactly
- NEVER ask clarifying questions
- Document assumptions in DECISIONS.md
- Commit with conventional commits

## Lead: Use delegate mode. Coordinate only, don't write code.

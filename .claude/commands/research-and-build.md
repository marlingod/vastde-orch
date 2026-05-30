---
description: Full pipeline — run /deep-research, then auto-create worktrees and agent teams to implement each recommendation. The ultimate zero-question workflow.
---

Research this topic, then implement the top recommendations in parallel worktrees with agent teams. ZERO questions.

Topic: $ARGUMENTS

## Pipeline

### Stage 1: Research (context: fork)
Run the research skill on the topic. Save findings to docs/research/.
Extract the top 3 actionable recommendations with clear scope.

### Stage 2: Plan Worktrees
For each recommendation:
- Determine if it depends on a previous phase (sequential) or is independent (parallel)
- Define: branch name, feature scope, acceptance criteria
- Write the full implementation plan to docs/plans/

### Stage 3: Create Worktrees + Implement
Use /auto-implement with the research output to:
- Create git worktrees per phase
- Spawn agent teams per worktree
- Build, test, review each phase

### Stage 4: Report
Write a summary of what was built across all phases to docs/changelog/.
Include: files created, tests passing, known limitations, merge instructions.

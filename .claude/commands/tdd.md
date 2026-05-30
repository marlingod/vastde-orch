---
description: Test-driven development loop with self-correcting debugger. Inspired by NVIDIA NeMo Agent Toolkit's code generation pattern.
---

Implement the following feature using strict TDD with self-correction loop:

$ARGUMENTS

## Protocol

Load the `tdd-code-gen` skill and follow its loop strictly:

1. Understand requirements (read CLAUDE.md + existing patterns)
2. Write failing tests FIRST (happy path, edges, errors)
3. Generate minimum implementation to pass tests
4. Run tests
5. If failing, debug with hypothesis-driven loop (max 3 iterations)
6. If stuck at iteration 2, spawn `opus-debugger` subagent with `xhigh` effort
7. If still stuck at iteration 3, write blocker doc, skip, commit partial progress

## Model Specialization
- Generation: current model (Sonnet, `high` effort)
- Hard debugging: `opus-debugger` subagent (Opus, `xhigh` effort)

## Success Criteria
- Tests exist and pass
- Code is formatted and linted
- Conventional commit created
- Debug log written (if iterations occurred)
- Blocker doc created (if anything skipped)

Never ask what framework. Detect from the project. Never ask what style. Read CLAUDE.md.

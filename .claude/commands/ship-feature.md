---
description: End-to-end autonomous feature build. Auto-detects project type. Zero questions.
---

Build this feature autonomously with ZERO questions: $ARGUMENTS

## Protocol
1. Read CLAUDE.md for project type and stack
2. Scan codebase for existing patterns (auto-detect language, framework, conventions)
3. Write a 10-line plan to docs/plans/
4. Build following detected patterns (models → logic → API → UI → tests)
5. Run all tests and fix failures (3 attempts max per failure)
6. Format code (auto-detect formatter)
7. Commit with conventional commit message
8. Write summary to docs/changelog/

## Rules
- NEVER ask a clarifying question
- Follow existing codebase patterns EXACTLY
- Document every assumption in DECISIONS.md
- If blocked after 3 fix attempts, write BLOCKER.md and move on

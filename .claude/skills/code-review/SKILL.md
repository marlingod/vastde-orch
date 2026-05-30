---
name: code-review
description: Autonomous code review — security, correctness, performance, style. Triggers on review, audit, check code, look over, sanity check.
context: fork
agent: Explore
allowed-tools: Read, Grep, Glob
---

## Universal Code Review Protocol

Review ALL changed/specified files. NEVER ask what to review.

### Checklist (all languages)
1. **Security**: Injection, auth bypass, secrets in code, input validation
2. **Correctness**: Logic errors, edge cases, null/nil handling, race conditions
3. **Performance**: N+1 queries, unnecessary loops, missing indexes, memory leaks
4. **Style**: Matches project conventions, naming, formatting, documentation
5. **Tests**: Adequate coverage, edge cases, meaningful assertions
6. **Architecture**: Coupling, cohesion, separation of concerns, DRY

### Output
```
## Code Review: [scope]

### CRITICAL (block merge)
- FILE:LINE — [issue] — FIX: [recommendation]

### HIGH (fix before merge)
- FILE:LINE — [issue] — FIX: [recommendation]

### MEDIUM (fix soon)
- FILE:LINE — [issue] — FIX: [recommendation]

### LOW (nice to have)
- FILE:LINE — [issue] — FIX: [recommendation]

### POSITIVE (good patterns)
- FILE:LINE — [what was done well]
```

Never soften findings. Be the reviewer you'd want before shipping to production.

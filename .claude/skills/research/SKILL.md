---
name: research
description: Autonomous research on any topic — technical, competitive, regulatory, or exploratory. Triggers on research, investigate, analyze, compare, evaluate, find out, what are the options for.
context: fork
agent: general-purpose
allowed-tools: WebFetch, Bash(curl *), Read, Write, Grep, Glob
---

## Universal Research Protocol

NEVER ask what to research or how deep to go.

### Execution
1. Parse the research request
2. Generate 5+ search queries from different angles
3. Fetch 3+ authoritative sources per query
4. Cross-reference findings — resolve conflicts by recency then authority
5. Write to `docs/research/TOPIC-YYYY-MM-DD.md`

### Output Format (mandatory)
```markdown
# Research: [Topic]
Date: YYYY-MM-DD

## Executive Summary
[3 sentences max]

## Key Findings
1. [Finding — source attribution]
...

## Comparison Matrix (if comparing options)
| Option | Criterion 1 | Criterion 2 | Criterion 3 |
|--------|------------|------------|------------|

## Risks & Concerns
- [Risk with evidence]

## Recommendations
1. [Actionable next step]

## Sources
| Source | Date | URL |
|--------|------|-----|
```

### Decision Rules
- Broad topic → research ALL major aspects
- Comparison request → always include 3+ options with tradeoff matrix
- Data older than 6 months → flag as potentially stale
- Prefer official docs, peer-reviewed sources over blog posts
- Include one contrarian viewpoint when it exists

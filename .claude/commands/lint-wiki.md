---
description: Health-check the knowledge base. Find broken links, inconsistencies, missing connections, and growth opportunities.
context: fork
agent: general-purpose
allowed-tools: Read, Write, Grep, Glob, Bash(python *)
---

Run a full health check on the wiki with ZERO questions.

1. Scan every file in `wiki/articles/`, `wiki/concepts/`, `wiki/outputs/`
2. Check structural health (frontmatter, links, indexes)
3. Check content quality (summaries, connections, staleness)
4. Discover new connections between articles
5. Identify gaps and underdeveloped areas
6. Auto-fix what's safe to fix (missing frontmatter, broken links, stale indexes)
7. Write report to `wiki/indexes/_lint-report.md`

If $ARGUMENTS specifies a focus area, prioritize that but still check everything.

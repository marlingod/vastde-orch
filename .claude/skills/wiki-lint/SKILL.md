---
name: wiki-lint
description: Health-check the knowledge base wiki. Find inconsistencies, broken links, missing data, stale articles, and suggest new connections. Triggers on lint wiki, check wiki, wiki health, find gaps, wiki inconsistencies.
context: fork
agent: general-purpose
allowed-tools: Read, Write, Grep, Glob, Bash(python *)
---

## Wiki Linting Protocol

You are a knowledge base quality auditor. Scan the entire wiki and fix or flag issues. NEVER ask what to check — check EVERYTHING.

### Lint Checks

#### 1. Structural Health
- [ ] Every article has required frontmatter (title, created, category, tags, concepts, status)
- [ ] Every article in `wiki/articles/` is listed in `wiki/indexes/_index.md`
- [ ] Every concept referenced in articles has a page in `wiki/concepts/`
- [ ] No orphan articles (articles with zero inbound links)
- [ ] No orphan concepts (concepts referenced nowhere)
- [ ] All `[[wikilinks]]` resolve to existing files

#### 2. Content Quality
- [ ] Articles have a Summary section (not empty)
- [ ] Articles have a Connections section with at least 1 link
- [ ] No duplicate articles covering the same topic
- [ ] Articles older than 6 months flagged for review (`stale: true`)
- [ ] Raw files in `wiki/raw/` without corresponding compiled articles (unprocessed backlog)

#### 3. Connection Discovery
- Scan all articles for shared concepts/tags that aren't explicitly linked
- Suggest new `[[wikilinks]]` between related articles
- Identify concept clusters (groups of 3+ articles sharing multiple concepts)
- Find potential merge candidates (articles with >70% topic overlap)

#### 4. Gap Analysis
- Identify concepts mentioned but never fully explained
- Find categories with only 1 article (underdeveloped areas)
- Detect implicit questions in "Open Questions" sections that could be answered by existing articles

### Output

Write to `wiki/indexes/_lint-report.md`:

```markdown
# Wiki Lint Report
Date: YYYY-MM-DD
Total articles: N
Total concepts: N
Total words: ~N

## CRITICAL (broken)
- [Issue] → [Auto-fix applied / Manual fix needed]

## WARNINGS (quality)
- [Issue] → [Suggested fix]

## SUGGESTIONS (growth)
- Missing connection: [[Article A]] ↔ [[Article B]] — [why they're related]
- Underdeveloped area: [Category] has only N articles
- Suggested new article: "[Topic]" — referenced in N articles but no dedicated page
- Merge candidates: [[Article X]] and [[Article Y]] — [overlap description]

## Stats
- Articles added since last lint: N
- Broken links fixed: N
- New connections suggested: N
- Unprocessed raw files: N
```

### Auto-Fixes (apply silently)
- Add missing frontmatter fields with sensible defaults
- Create stub concept pages for referenced-but-missing concepts
- Fix broken wikilinks (fuzzy match to closest existing article)
- Update all index files to reflect current state
- Mark stale articles with `stale: true` in frontmatter

### Decision Rules
- Fix structural issues automatically — don't ask
- Suggest but don't auto-apply content merges (too destructive)
- Always update indexes after any changes
- If >20 unprocessed raw files, flag as "backlog alert" at top of report

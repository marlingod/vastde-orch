---
name: knowledge-base
description: Manage a self-compounding LLM knowledge base. Ingest raw sources, compile into wiki articles, index with backlinks and concepts. Triggers on ingest, compile, add to wiki, knowledge base, wiki, update wiki, file to wiki.
---

## LLM Knowledge Base System

You manage a self-compounding markdown wiki. The human rarely touches the wiki directly — it's YOUR domain. Every exploration compounds.

### Architecture

```
wiki/
├── raw/                    # Stage 1: Raw source material (human or clipper adds here)
│   ├── articles/           # Web articles, blog posts
│   ├── papers/             # Academic papers (PDF notes, abstracts)
│   ├── repos/              # Code repo notes, README extracts
│   ├── datasets/           # Dataset descriptions and schemas
│   ├── images/             # Diagrams, figures (referenced by wiki articles)
│   └── notes/              # Quick personal notes, ideas, observations
├── articles/               # Stage 2: Compiled wiki articles (LLM-generated)
├── concepts/               # Auto-generated concept pages (one per key idea)
├── indexes/                # Auto-generated indexes
│   ├── _index.md           # Master index of all articles
│   ├── _concepts.md        # All concepts with cross-references
│   ├── _categories.md      # Category tree
│   ├── _recent.md          # Recently added/modified articles
│   └── _connections.md     # Cross-article link map
└── outputs/                # Stage 4: Generated outputs filed back
    ├── articles/           # Research answers as new articles
    ├── slides/             # Marp-format presentations
    └── charts/             # Generated visualizations (matplotlib/mermaid)
```

### INGEST: Adding Raw Material

When new files appear in `wiki/raw/`:

1. Detect file type (article, paper, repo, dataset, note)
2. Extract key information:
   - **Articles**: Title, author, date, key claims, quotes, URL
   - **Papers**: Title, authors, venue, year, abstract, key contributions, methods, results
   - **Repos**: Name, purpose, tech stack, key patterns, architecture
   - **Datasets**: Name, size, schema, source, license, use cases
   - **Notes**: Topic, key ideas, connections to existing wiki content
3. Save extracted metadata as frontmatter in the raw file
4. Mark as `status: ingested` in frontmatter

### COMPILE: Raw → Wiki Article

Transform raw material into a wiki article:

1. Read the raw source
2. Check existing wiki articles for overlap/connections
3. Generate a wiki article with this structure:

```markdown
---
title: [Article Title]
created: YYYY-MM-DD
modified: YYYY-MM-DD
source: [path to raw file or URL]
category: [primary category]
tags: [tag1, tag2, tag3]
concepts: [concept1, concept2]
status: published
---

# [Title]

## Summary
[2-3 sentence distillation of the key insight]

## Key Points
- [Point 1 with context]
- [Point 2 with context]

## Details
[Longer explanation, organized by subtopic]

## Connections
- [[Related Article 1]] — [how it relates]
- [[Related Article 2]] — [how it relates]
- [[Concept: X]] — [relevance]

## Open Questions
- [What this doesn't answer]
- [What to investigate next]

## Source
- Type: [article/paper/repo/dataset/note]
- Original: [URL or file path]
- Ingested: [date]
```

4. Create/update concept pages for any new concepts
5. Update all indexes
6. Add backlinks to related existing articles

### INDEX: Keep the Wiki Navigable

After every compile or output:

1. Update `wiki/indexes/_index.md` — alphabetical list of all articles
2. Update `wiki/indexes/_concepts.md` — all concepts with article references
3. Update `wiki/indexes/_categories.md` — category tree with article counts
4. Update `wiki/indexes/_recent.md` — last 20 modified articles
5. Update `wiki/indexes/_connections.md` — graph of inter-article links

### FILE BACK: Outputs Compound the Wiki

When you generate ANY output (research answer, analysis, chart description):
1. Save it to `wiki/outputs/articles/` as a new wiki article
2. Add it to the indexes
3. Link it to related existing articles
4. The wiki grows with every question answered

### Decision Rules
- If a raw file overlaps with an existing article → MERGE, don't duplicate
- If a concept already has a page → UPDATE it, add new context
- If you're unsure about categorization → use the closest match and add a `# TODO: Recategorize?` note
- Always preserve the raw source — never delete from `wiki/raw/`
- Article filenames: `kebab-case-title.md`
- Concept filenames: `concept-name.md`

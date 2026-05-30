---
description: Ingest and compile raw sources into wiki articles. Processes everything in wiki/raw/ that hasn't been compiled yet, or a specific file/URL.
---

Ingest and compile into the knowledge base with ZERO questions: $ARGUMENTS

## Protocol

### If $ARGUMENTS is a URL:
1. Fetch the URL content
2. Save to `wiki/raw/articles/` with metadata
3. Compile into a wiki article
4. Update indexes and add connections

### If $ARGUMENTS is a file path:
1. Read the file
2. Classify (article, paper, repo, dataset, note)
3. Compile into a wiki article
4. Update indexes and add connections

### If $ARGUMENTS is empty or "all":
1. Scan `wiki/raw/` for all unprocessed files (missing `status: compiled` in frontmatter)
2. Compile each into wiki articles
3. Update all indexes
4. Generate a batch report

### If $ARGUMENTS is a topic/description:
1. Create a note in `wiki/raw/notes/` with the provided information
2. Compile into a wiki article
3. Update indexes and connections

## After Every Ingest:
- Update `wiki/indexes/_index.md`
- Update `wiki/indexes/_concepts.md`
- Update `wiki/indexes/_recent.md`
- Add backlinks to related existing articles
- Create concept pages for any new concepts

## Key Rule: Raw sources are never deleted. The wiki only grows.

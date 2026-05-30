---
name: build-feature
description: Build a complete feature from a one-line description. Auto-detects project type and conventions. Triggers on build, implement, create, add feature, ship, make.
---

## Universal Feature Build Protocol

NEVER ask clarifying questions. Detect project type and conventions automatically.

### Phase 1: Detect Project (DO NOT ASK — SCAN)
1. Read CLAUDE.md for project type and tech stack
2. Detect language/framework from existing files:
   - !`ls *.py pyproject.toml setup.py requirements.txt 2>/dev/null | head -5`
   - !`ls *.ts *.tsx package.json tsconfig.json next.config.* 2>/dev/null | head -5`
   - !`ls go.mod Cargo.toml 2>/dev/null | head -5`
3. Scan for project structure patterns:
   - !`find . -maxdepth 3 -type d -not -path '*/node_modules/*' -not -path '*/.git/*' -not -path '*/__pycache__/*' | head -20`
4. Find the closest existing feature to use as a template:
   - !`find . -name "*.py" -path "*/routes/*" -o -name "*.py" -path "*/api/*" -o -name "*.ts" -path "*/api/*" 2>/dev/null | head -5`

### Phase 2: Plan (brief — not a dissertation)
Write a 10-line plan to `docs/plans/FEATURE-NAME.md`:
- What files will be created/modified
- What the feature does
- Any dependencies needed

### Phase 3: Build (follow detected patterns)

#### For Python/FastAPI:
Models/Schemas → Repository → Service → Routes → Tests

#### For Python/Django:
Models → Serializers → Views → URLs → Tests

#### For Node/Express:
Models → Controllers → Routes → Middleware → Tests

#### For React/Next.js:
Components → Hooks → API Routes → Pages → Tests

#### For Go:
Models → Handlers → Routes → Middleware → Tests

#### For Rust:
Types → Handlers → Routes → Tests

#### For any framework:
Follow the existing pattern in the codebase. If no pattern → use framework conventions.

### Phase 4: Verify
1. Run the full test suite (auto-detect: pytest / npm test / go test / cargo test)
2. Run linter/formatter (auto-detect: black / prettier / gofmt / rustfmt)
3. Fix failures (up to 3 attempts per failure)
4. Commit with conventional commit message

### Decision Rules
- Follow existing codebase patterns EXACTLY — consistency > personal preference
- If no pattern exists, use the framework's official convention
- If a dependency is needed, pick the most popular stable option
- If scope is unclear, build the MINIMUM viable version
- Document every assumption in DECISIONS.md
- If you break something, fix it before moving on

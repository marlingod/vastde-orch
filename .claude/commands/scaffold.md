---
description: Bootstrap a new project from scratch. Auto-detects stack from CLAUDE.md Quick Config. Zero questions.
---

Scaffold a new project with ZERO questions: $ARGUMENTS

## Protocol
1. Read CLAUDE.md Quick Config for PROJECT_TYPE, BACKEND, FRONTEND, DATABASE, HOSTING, TESTING
2. Initialize the project structure following framework best practices:
   - Create directory structure
   - Initialize package manager (pip/npm/cargo/go mod)
   - Set up linting and formatting configs
   - Create initial models/schemas
   - Set up database connection and migrations
   - Create health check endpoint
   - Write initial tests
   - Create .gitignore, .env.example, README.md
   - Initialize git repo

3. Generate a docker-compose.yml if DATABASE is not "none"
4. Generate CI/CD config (GitHub Actions) matching the HOSTING target
5. Run all tests to verify the scaffold works
6. Commit: "feat: initial project scaffold"

## If $ARGUMENTS specifies features, build them into the scaffold.
## If $ARGUMENTS is empty, create a minimal working project with health check + auth.

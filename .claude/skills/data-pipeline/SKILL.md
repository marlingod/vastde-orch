---
name: data-pipeline
description: Build data pipelines, migrations, ETL, schema changes, seeding. Triggers on migrate, schema, database, ETL, seed data, data pipeline, create table.
---

## Universal Data Pipeline Protocol

NEVER ask about schema details. Infer from models and existing migrations.

### Auto-Detection
1. Read CLAUDE.md for database type
2. Detect ORM/migration tool:
   - !`ls alembic.ini alembic/ 2>/dev/null` → Alembic (SQLAlchemy)
   - !`ls prisma/ 2>/dev/null` → Prisma
   - !`find . -path "*/migrations/*.py" -not -path "*/node_modules/*" 2>/dev/null | head -3` → Django
   - !`ls drizzle.config.* 2>/dev/null` → Drizzle
   - !`ls knexfile.* 2>/dev/null` → Knex
3. Follow the existing migration pattern EXACTLY

### For Schema Changes
1. Compare models/types to current schema
2. Generate migration (auto-generate when possible)
3. Review migration for destructive operations
4. Apply migration
5. Verify with a simple query

### For Seed Data
1. Create seed script matching existing patterns
2. Use realistic but fake data (Faker library or equivalent)
3. Include relationships between entities
4. Make idempotent (safe to run multiple times)

### Decision Rules
- Never drop tables/columns without creating a backup migration
- Always add indexes on foreign keys
- Use transactions for multi-step migrations
- If uncertain about data loss, create a reversible migration

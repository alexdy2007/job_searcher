---
name: db-migrate
description: Change the job_searcher database schema with Alembic. Use when adding or altering a column, index, constraint, or table, or when alembic autogenerate produces a migration that needs review. Covers the Postgres traps autogenerate silently misses.
---

# Database migrations

Autogenerate produces a first draft. It is never the final answer — read
every generated file before applying it.

## The loop

```powershell
docker compose up -d db
uv run alembic revision --autogenerate -m "short description"
```

Then **open the new file in `migrations/versions/` and read it.** Check it
against the list below. Only then:

```powershell
uv run alembic upgrade head
uv run pytest tests/test_migrations.py
```

That test compares live schema against `Base.metadata` and is what catches
"changed the model, forgot the migration".

Finally, prove it is reversible — a migration you cannot roll back is a
one-way door:

```powershell
uv run alembic downgrade -1
uv run alembic upgrade head
```

CI enforces this on every push (`.github/workflows/ci.yml`): it runs a full
`downgrade base`, then `scripts/check_no_leftover_enums.py`, then
`upgrade head`. If you add a new native enum, add its name to `NATIVE_ENUMS`
in that script or the check will not cover it.

## What autogenerate never emits

Add these by hand, every time:

**Extensions.** `op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")`.

**Native enum type drops.** `op.drop_table` leaves the enum TYPE behind, so a
downgrade followed by an upgrade fails with *type already exists*. Any
downgrade dropping a table that uses `job_status` or `source_kind` must end
with:
```python
op.execute("DROP TYPE IF EXISTS job_status")
op.execute("DROP TYPE IF EXISTS source_kind")
```

**Computed column changes.** Alembic cannot compare a generated expression
and emits a warning instead of a diff. Changing the `search_vector`
expression needs a hand-written drop-and-add pair.

**Partial index predicates** and **CHECK constraint edits.**

## Enum policy

Only `job_status` and `source_kind` are native Postgres enums, because they
are stable. Everything churn-prone — `seniority`, `remote_policy`,
`salary_period`, `extraction_method` — is `text` plus a CHECK constraint.

The reason is that `ALTER TYPE ... ADD VALUE` cannot run inside a transaction
block, which makes native enums effectively un-downgradable. Adding a new
seniority level should be a one-line CHECK replacement, not a data migration.
Keep new churn-prone vocabularies on the text + CHECK side.

## Naming

Constraint names in the model carry **no table prefix** — the naming
convention in `db/base.py` adds it. `name="seniority"` on a `jobs` constraint
produces `ck_jobs_seniority`. Passing `name="jobs_seniority"` produces the
doubled `ck_jobs_jobs_seniority`.

## Gotchas

- `tsvector` has a 1MB ceiling. Any expression over `description_text` must
  keep the `left(description_text, 100000)` guard, or long career pages fail
  on INSERT.
- Never drop a column holding scraped history without flagging it. The job
  history and `first_seen_at` cannot be re-scraped.
- Never `docker compose down -v` casually — that deletes the volume and every
  listing's history with it.

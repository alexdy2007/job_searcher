"""Assert that a full downgrade left no native enum types behind.

Run this against an empty (fully downgraded) database.

Dropping a table does not drop the Postgres enum TYPEs its columns used. If a
downgrade forgets to drop them, the schema looks clean but the next
``alembic upgrade head`` dies with "type already exists" -- and it only shows
up when someone tries to roll back, which is the worst possible moment to
discover it.

Uses SQLAlchemy rather than psql so it depends on nothing beyond the project's
own requirements.
"""

import sys

from sqlalchemy import create_engine, text

from job_searcher.config import settings

# Every native enum in the schema. Churn-prone vocabularies use text + CHECK
# instead and never appear here; add a name to this list only when a new
# native enum is introduced.
NATIVE_ENUMS = ("job_status", "source_kind")


def main() -> int:
    engine = create_engine(settings.database_url)
    with engine.connect() as conn:
        # Guard against being run at the wrong point. On a migrated database
        # these types exist legitimately, and reporting that as a failure
        # would send someone chasing a bug that is not there.
        still_migrated = conn.execute(
            text("SELECT to_regclass('public.jobs') IS NOT NULL")
        ).scalar()
        if still_migrated:
            print(
                "FAIL: 'jobs' table still exists, so this database is not "
                "downgraded.\nRun 'alembic downgrade base' before this check.",
                file=sys.stderr,
            )
            return 2

        leftover = sorted(
            row[0]
            for row in conn.execute(
                text("SELECT typname FROM pg_type WHERE typname = ANY(:names)"),
                {"names": list(NATIVE_ENUMS)},
            )
        )

    if leftover:
        print(
            f"FAIL: enum type(s) survived downgrade: {', '.join(leftover)}\n"
            "Add to the migration's downgrade():\n"
            + "\n".join(f'    op.execute("DROP TYPE IF EXISTS {n}")' for n in leftover),
            file=sys.stderr,
        )
        return 1

    print(f"OK: no leftover enum types ({', '.join(NATIVE_ENUMS)} all dropped)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

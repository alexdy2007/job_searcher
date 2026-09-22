"""The single test that catches "changed the model, forgot the migration".

That drift is the most common greenfield bug in a SQLAlchemy + Alembic
project, and it is invisible until a query fails in production.
"""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from job_searcher.db import models  # noqa: F401  -- registers tables
from job_searcher.db.base import Base

pytestmark = pytest.mark.db


# Alembic reports these as diffs against a live database even when nothing has
# drifted: it cannot introspect a Computed expression well enough to compare
# it, and CHECK constraints are not part of its comparison set at all.
_IGNORED_DIFF_KINDS = {"add_constraint", "remove_constraint"}


def _meaningful(diffs):
    out = []
    for diff in diffs:
        # A diff is either a tuple or a list of tuples (for multi-part changes).
        entries = diff if isinstance(diff, list) else [diff]
        for entry in entries:
            if entry[0] not in _IGNORED_DIFF_KINDS:
                out.append(entry)
    return out


def test_models_match_migrations(db_engine):
    """After `alembic upgrade head`, the DB must match Base.metadata exactly."""
    with db_engine.connect() as conn:
        context = MigrationContext.configure(
            conn,
            opts={"compare_type": True, "compare_server_default": True},
        )
        diffs = _meaningful(compare_metadata(context, Base.metadata))

    assert not diffs, (
        "Model and migrations have drifted. Run:\n"
        "  uv run alembic revision --autogenerate -m 'describe change'\n"
        f"Unexpected differences: {diffs}"
    )

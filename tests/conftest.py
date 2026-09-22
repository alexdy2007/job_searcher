import pytest
from sqlalchemy import create_engine, text

from job_searcher.config import settings


def pytest_addoption(parser):
    parser.addoption(
        "--llm",
        action="store_true",
        default=False,
        help="run tests that call the real Anthropic API (costs money)",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--llm"):
        return
    skip = pytest.mark.skip(reason="needs --llm (hits the real API)")
    for item in items:
        if "llm" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def db_engine():
    """Engine against the local Postgres, skipping cleanly when it is down.

    A missing database should report as 'skipped', not as a wall of connection
    errors that hides real failures.
    """
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("select 1"))
    except Exception as exc:  # pragma: no cover - environment, not logic
        pytest.skip(
            f"Postgres unavailable ({exc.__class__.__name__}). "
            "Run: docker compose up -d db"
        )
    return engine


@pytest.fixture
def db_session(db_engine):
    """Each test runs in a transaction that is always rolled back, so tests
    share one migrated database with no cross-talk."""
    from sqlalchemy.orm import Session

    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()

"""Engine and session factory.

Collection is async (httpx), but persistence is sync: writes happen once per
source after its listings are gathered, so an async driver would add
complexity without buying throughput. Streamlit is sync too.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from job_searcher.config import settings

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            future=True,
            # Without an explicit timeout, connecting to a host that is not
            # listening blocks for minutes on Windows rather than failing.
            # That turns "Postgres isn't running" into an app that appears to
            # hang, in the UI and in the CLI alike, instead of one that says
            # so. Five seconds is far longer than a healthy local connect.
            connect_args={"connect_timeout": 5},
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionFactory


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commits on success, rolls back on any exception."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

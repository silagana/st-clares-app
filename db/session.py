import os
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "data" / "instituto.db"

_DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH}")

# Railway provides postgres:// — SQLAlchemy requires postgresql://
if _DATABASE_URL.startswith("postgres://"):
    _DATABASE_URL = _DATABASE_URL.replace("postgres://", "postgresql://", 1)

_is_sqlite = _DATABASE_URL.startswith("sqlite")
_connect_args = {"check_same_thread": False} if _is_sqlite else {}

engine = create_engine(_DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_engine(db_url: str | None = None):
    if db_url is None:
        return engine
    url = db_url
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=args)


@contextmanager
def get_session():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

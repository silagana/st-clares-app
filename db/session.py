from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "data" / "instituto.db"


def get_engine(db_url: str | None = None):
    url = db_url or f"sqlite:///{DB_PATH}"
    return create_engine(url, connect_args={"check_same_thread": False})


engine = get_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


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

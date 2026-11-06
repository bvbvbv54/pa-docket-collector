from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from pa_docket.config import Settings
from pa_docket.db.models import Base


def make_engine(database_url: str):
    kwargs = {"pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    if database_url.startswith("postgresql"):
        kwargs.update(pool_size=5, max_overflow=10)
    return create_engine(database_url, **kwargs)


def make_session_factory(settings: Settings):
    engine = make_engine(settings.database_url)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine) -> None:
    Base.metadata.create_all(engine)

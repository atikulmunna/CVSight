from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from shelfsight_api.config import ConfigurationError, get_database_url


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_engine(get_database_url(), pool_pre_ping=True)


def database_is_ready() -> bool:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except (ConfigurationError, SQLAlchemyError):
        return False
    return True

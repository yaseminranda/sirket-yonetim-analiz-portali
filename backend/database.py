"""SQLAlchemy engine setup and query helper functions for PostgreSQL access."""
from typing import Optional, Tuple

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from config import settings

engine: Engine = create_engine(settings.database_url, pool_pre_ping=True)


def run_query(query, params: Optional[dict] = None) -> pd.DataFrame:
    """Runs a SELECT query and returns the result as a DataFrame."""
    with engine.connect() as conn:
        if isinstance(query, str):
            query = text(query)
        return pd.read_sql(query, conn, params=params)


def execute_query(query, params: Optional[dict] = None) -> Tuple[bool, Optional[str]]:
    """Runs and commits an INSERT/UPDATE/DELETE query, returning (success, error_message)."""
    try:
        with engine.begin() as conn:
            if isinstance(query, str):
                query = text(query)
            conn.execute(query, params or {})
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)

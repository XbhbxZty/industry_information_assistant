"""Explicitly create the optional Text2SQL demonstration tables and rows.

Run manually from ``backend/`` only after the application schema is at Alembic
head: ``python app/scripts/seed_demo_data.py``.  This command never runs at service
startup and refuses to touch a database where any demo table already exists.
"""
from __future__ import annotations

from pathlib import Path
import sys

# Support the documented direct invocation from backend/ without requiring
# callers to alter PYTHONPATH.
APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from sqlalchemy import text
from sqlalchemy.engine import Engine

from core.database_url import resolve_database_urls
from core.schema_head_guard import assert_database_schema_at_head


DEMO_TABLES = (
    "restaurants",
    "restaurant_orders",
    "stocks",
    "stock_daily",
    "legal_cases",
    "vehicles",
    "transport_records",
)
DEMO_SQL_RESOURCE = Path(__file__).resolve().parents[1] / "data" / "text2sql_demo_seed.sql"


class DemoSeedError(RuntimeError):
    """The optional demo seed cannot safely be applied."""


def _existing_demo_tables(connection) -> tuple[str, ...]:
    rows = connection.execute(text("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_type = 'BASE TABLE'
          AND table_name = ANY(:table_names)
        ORDER BY table_name
    """), {"table_names": list(DEMO_TABLES)}).scalars().all()
    return tuple(rows)


def seed_demo_data(engine: Engine) -> None:
    """Create all seven demo tables and rows in one transaction, or do nothing."""
    sql = DEMO_SQL_RESOURCE.read_text(encoding="utf-8")
    with engine.begin() as connection:
        connection.exec_driver_sql("SET LOCAL search_path TO public")
        assert_database_schema_at_head(connection)
        existing = _existing_demo_tables(connection)
        if existing:
            raise DemoSeedError(
                "refusing to seed Text2SQL demo data because existing demo table(s) were found: "
                + ", ".join(existing)
            )
        connection.exec_driver_sql(sql)


def main() -> int:
    from dotenv import load_dotenv
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    engine = None
    try:
        load_dotenv(APP_DIR.parent / ".env")
        engine = create_engine(resolve_database_urls().sqlalchemy_url, poolclass=NullPool)
        seed_demo_data(engine)
        print("Text2SQL demo data seeded successfully.")
        return 0
    except Exception as exc:
        # DBAPI messages may expose target details; retain only a stable failure type.
        print(f"Text2SQL demo seed failed: {type(exc).__name__}", file=sys.stderr)
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())

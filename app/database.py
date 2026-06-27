from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base
from .config import settings

url = settings.DATABASE_URL
connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
engine = create_engine(url, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _sql_type(dialect: str, type_name: str) -> str:
    """Return simple SQL column type that works on Postgres and SQLite."""
    if type_name == "int":
        return "INTEGER"
    if type_name == "money":
        return "NUMERIC(12, 2)" if dialect != "sqlite" else "NUMERIC"
    if type_name == "bool":
        return "BOOLEAN" if dialect != "sqlite" else "BOOLEAN"
    if type_name == "datetime":
        return "TIMESTAMP" if dialect != "sqlite" else "DATETIME"
    if type_name == "text":
        return "TEXT"
    return type_name


def _add_column_if_missing(conn, inspector, table: str, column: str, type_name: str, default_sql: str | None = None):
    if not inspector.has_table(table):
        return
    cols = {c["name"] for c in inspector.get_columns(table)}
    if column in cols:
        return
    dialect = conn.engine.dialect.name
    col_type = _sql_type(dialect, type_name)
    sql = f'ALTER TABLE {table} ADD COLUMN {column} {col_type}'
    if default_sql is not None:
        sql += f' DEFAULT {default_sql}'
    conn.execute(text(sql))


def migrate_db():
    """Lightweight auto-migration for Railway/Postgres.

    SQLAlchemy create_all() creates missing tables but does not add new columns
    to existing tables. During fast iterations on Railway, old databases can be
    missing columns such as members.default_amount. This function safely adds
    columns that the current code expects, so we do not need to delete the DB
    every time the schema changes.
    """
    with engine.begin() as conn:
        inspector = inspect(conn)
        # members
        _add_column_if_missing(conn, inspector, "members", "default_amount", "money", "0")
        _add_column_if_missing(conn, inspector, "members", "line_user_id", "VARCHAR(80)")
        _add_column_if_missing(conn, inspector, "members", "active", "bool", "TRUE")
        _add_column_if_missing(conn, inspector, "members", "created_at", "datetime")

        # rounds
        _add_column_if_missing(conn, inspector, "rounds", "carry_over", "money", "0")
        _add_column_if_missing(conn, inspector, "rounds", "is_open", "bool", "TRUE")
        _add_column_if_missing(conn, inspector, "rounds", "created_at", "datetime")

        # payments
        _add_column_if_missing(conn, inspector, "payments", "due_amount", "money", "0")
        _add_column_if_missing(conn, inspector, "payments", "paid_amount", "money", "0")
        _add_column_if_missing(conn, inspector, "payments", "status", "VARCHAR(20)", "'unpaid'")
        _add_column_if_missing(conn, inspector, "payments", "paid_at", "datetime")
        _add_column_if_missing(conn, inspector, "payments", "slip_message_id", "VARCHAR(80)")
        _add_column_if_missing(conn, inspector, "payments", "note", "text")

        # expenses
        _add_column_if_missing(conn, inspector, "expenses", "created_at", "datetime")
        _add_column_if_missing(conn, inspector, "expenses", "note", "text")

        # group_states
        _add_column_if_missing(conn, inspector, "group_states", "active_round_id", "int")


def init_db():
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    migrate_db()

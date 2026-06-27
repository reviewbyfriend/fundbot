from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker
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

def _exec(conn, sql: str):
    conn.execute(text(sql))

def _dialect() -> str:
    return engine.dialect.name

def _has_table(conn, name: str) -> bool:
    return inspect(conn).has_table(name)

def _cols(conn, table: str) -> set[str]:
    if not _has_table(conn, table):
        return set()
    return {c["name"] for c in inspect(conn).get_columns(table)}

def _add_col(conn, table: str, col: str, coltype: str, default: str | None = None):
    if not _has_table(conn, table):
        return
    if col in _cols(conn, table):
        return
    sql = f"ALTER TABLE {table} ADD COLUMN {col} {coltype}"
    if default is not None:
        sql += f" DEFAULT {default}"
    _exec(conn, sql)

def migrate_db():
    d = _dialect()
    money = "NUMERIC(12,2)" if d != "sqlite" else "NUMERIC"
    bool_t = "BOOLEAN"
    ts = "TIMESTAMP" if d != "sqlite" else "DATETIME"
    with engine.begin() as conn:
        # Ensure missing columns if DB was created by older versions.
        _add_col(conn, "members", "name", "VARCHAR(120)")
        _add_col(conn, "members", "display_name", "VARCHAR(120)")
        _add_col(conn, "members", "default_amount", money, "0")
        _add_col(conn, "members", "amount", money, "0")
        _add_col(conn, "members", "line_user_id", "VARCHAR(120)")
        _add_col(conn, "members", "active", bool_t, "TRUE" if d != "sqlite" else "1")
        _add_col(conn, "members", "created_at", ts)
        if _has_table(conn, "members"):
            cols = _cols(conn, "members")
            if "name" in cols and "display_name" in cols:
                _exec(conn, "UPDATE members SET name = COALESCE(NULLIF(name, ''), display_name) WHERE name IS NULL OR name = ''")
                _exec(conn, "UPDATE members SET display_name = COALESCE(NULLIF(display_name, ''), name) WHERE display_name IS NULL OR display_name = ''")
            if "default_amount" in cols and "amount" in cols:
                _exec(conn, "UPDATE members SET default_amount = COALESCE(NULLIF(default_amount, 0), amount, 0)")
        _add_col(conn, "rounds", "title", "VARCHAR(120)")
        _add_col(conn, "rounds", "carry_over", money, "0")
        _add_col(conn, "rounds", "brought_forward", money, "0")
        _add_col(conn, "rounds", "is_open", bool_t, "TRUE" if d != "sqlite" else "1")
        _add_col(conn, "rounds", "status", "VARCHAR(20)")
        _add_col(conn, "rounds", "created_at", ts)
        if _has_table(conn, "rounds"):
            cols = _cols(conn, "rounds")
            if "is_open" in cols and "status" in cols:
                _exec(conn, "UPDATE rounds SET is_open = CASE WHEN status = 'open' OR status IS NULL THEN TRUE ELSE FALSE END WHERE is_open IS NULL")
        _add_col(conn, "payments", "due_amount", money, "0")
        _add_col(conn, "payments", "paid_amount", money, "0")
        _add_col(conn, "payments", "amount", money, "0")
        _add_col(conn, "payments", "status", "VARCHAR(20)", "'unpaid'")
        _add_col(conn, "payments", "paid_at", ts)
        _add_col(conn, "payments", "slip_message_id", "VARCHAR(120)")
        _add_col(conn, "payments", "slip_path", "TEXT")
        _add_col(conn, "payments", "note", "TEXT")
        if _has_table(conn, "payments"):
            cols = _cols(conn, "payments")
            if "amount" in cols and "paid_amount" in cols:
                _exec(conn, "UPDATE payments SET paid_amount = COALESCE(NULLIF(paid_amount, 0), amount, 0)")
            if "status" in cols:
                _exec(conn, "UPDATE payments SET status = COALESCE(status, 'unpaid')")
        _add_col(conn, "expenses", "created_at", ts)
        _add_col(conn, "expenses", "note", "TEXT")

def init_db():
    from . import models  # noqa
    Base.metadata.create_all(bind=engine)
    migrate_db()

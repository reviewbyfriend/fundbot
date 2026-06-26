import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH = Path("fundbot.db")


def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with conn() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS groups (
            group_id TEXT PRIMARY KEY,
            name TEXT,
            promptpay_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS admins (
            group_id TEXT,
            user_id TEXT,
            PRIMARY KEY(group_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            amount REAL NOT NULL DEFAULT 0,
            line_user_id TEXT,
            active INTEGER DEFAULT 1,
            UNIQUE(group_id, display_name)
        );
        CREATE TABLE IF NOT EXISTS rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL,
            title TEXT NOT NULL,
            brought_forward REAL DEFAULT 0,
            status TEXT DEFAULT 'open',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(group_id, title)
        );
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id INTEGER NOT NULL,
            member_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            status TEXT DEFAULT 'paid',
            slip_message_id TEXT,
            paid_at TEXT DEFAULT CURRENT_TIMESTAMP,
            note TEXT,
            UNIQUE(round_id, member_id)
        );
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id INTEGER NOT NULL,
            item_date TEXT,
            title TEXT NOT NULL,
            amount REAL NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        ''')

def ensure_group(group_id, name=None, promptpay_id=None):
    with conn() as c:
        c.execute("INSERT OR IGNORE INTO groups(group_id,name,promptpay_id) VALUES(?,?,?)", (group_id, name or group_id, promptpay_id))
        if promptpay_id:
            c.execute("UPDATE groups SET promptpay_id=? WHERE group_id=?", (promptpay_id, group_id))

def set_admin(group_id, user_id):
    ensure_group(group_id)
    with conn() as c:
        c.execute("INSERT OR IGNORE INTO admins(group_id,user_id) VALUES(?,?)", (group_id, user_id))

def is_admin(group_id, user_id):
    with conn() as c:
        r = c.execute("SELECT 1 FROM admins WHERE group_id=? AND user_id=?", (group_id, user_id)).fetchone()
        return bool(r)

def upsert_member(group_id, name, amount=0, line_user_id=None):
    ensure_group(group_id)
    name = " ".join(str(name).strip().split())
    with conn() as c:
        c.execute("""
        INSERT INTO members(group_id,display_name,amount,line_user_id) VALUES(?,?,?,?)
        ON CONFLICT(group_id, display_name) DO UPDATE SET amount=excluded.amount,
            line_user_id=COALESCE(excluded.line_user_id, members.line_user_id), active=1
        """, (group_id, name, float(amount or 0), line_user_id))

def link_member(group_id, user_id, name):
    with conn() as c:
        row = c.execute("SELECT id FROM members WHERE group_id=? AND display_name LIKE ?", (group_id, f"%{name.strip()}%")).fetchone()
        if not row:
            return False
        c.execute("UPDATE members SET line_user_id=? WHERE id=?", (user_id, row['id']))
        return True

def list_members(group_id):
    with conn() as c:
        return c.execute("SELECT * FROM members WHERE group_id=? AND active=1 ORDER BY id", (group_id,)).fetchall()

def open_round(group_id, title, brought_forward=0):
    ensure_group(group_id)
    with conn() as c:
        c.execute("INSERT OR IGNORE INTO rounds(group_id,title,brought_forward) VALUES(?,?,?)", (group_id, title, brought_forward))
        return current_round(group_id)

def current_round(group_id):
    with conn() as c:
        return c.execute("SELECT * FROM rounds WHERE group_id=? AND status='open' ORDER BY id DESC LIMIT 1", (group_id,)).fetchone()

def record_payment(group_id, member_name_or_id, amount, slip_message_id=None, note=None):
    r = current_round(group_id)
    if not r:
        raise ValueError("ยังไม่มีรอบเดือนที่เปิดอยู่")
    with conn() as c:
        if isinstance(member_name_or_id, int) or str(member_name_or_id).isdigit():
            m = c.execute("SELECT * FROM members WHERE id=? AND group_id=?", (int(member_name_or_id), group_id)).fetchone()
        else:
            m = c.execute("SELECT * FROM members WHERE group_id=? AND display_name LIKE ?", (group_id, f"%{member_name_or_id.strip()}%")).fetchone()
        if not m:
            raise ValueError("ไม่พบชื่อสมาชิก")
        c.execute("""
            INSERT INTO payments(round_id,member_id,amount,slip_message_id,note) VALUES(?,?,?,?,?)
            ON CONFLICT(round_id, member_id) DO UPDATE SET amount=excluded.amount, slip_message_id=excluded.slip_message_id, paid_at=CURRENT_TIMESTAMP, note=excluded.note
        """, (r['id'], m['id'], float(amount), slip_message_id, note))
        return m

def record_payment_by_user(group_id, user_id, amount, slip_message_id=None):
    r = current_round(group_id)
    if not r:
        raise ValueError("ยังไม่มีรอบเดือนที่เปิดอยู่")
    with conn() as c:
        m = c.execute("SELECT * FROM members WHERE group_id=? AND line_user_id=?", (group_id, user_id)).fetchone()
        if not m:
            raise ValueError("ยังไม่ทราบว่าคุณคือใคร ให้พิมพ์: ลงทะเบียน ชื่อของคุณ")
    return record_payment(group_id, m['id'], amount, slip_message_id)

def add_expense(group_id, title, amount, item_date=None):
    r = current_round(group_id)
    if not r:
        raise ValueError("ยังไม่มีรอบเดือนที่เปิดอยู่")
    with conn() as c:
        c.execute("INSERT INTO expenses(round_id,item_date,title,amount) VALUES(?,?,?,?)", (r['id'], item_date, title, float(amount)))

def summary(group_id):
    r = current_round(group_id)
    if not r:
        return None
    with conn() as c:
        rows = c.execute('''
        SELECT m.id, m.display_name, m.amount AS due_amount, COALESCE(p.amount,0) AS paid_amount,
               CASE WHEN p.id IS NULL THEN 'unpaid' WHEN p.amount < m.amount THEN 'partial' ELSE 'paid' END AS pay_status
        FROM members m
        LEFT JOIN payments p ON p.member_id=m.id AND p.round_id=?
        WHERE m.group_id=? AND m.active=1
        ORDER BY m.id
        ''', (r['id'], group_id)).fetchall()
        ex = c.execute("SELECT * FROM expenses WHERE round_id=? ORDER BY id", (r['id'],)).fetchall()
        return r, rows, ex

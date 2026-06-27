from datetime import datetime
from decimal import Decimal
from sqlalchemy import desc
from sqlalchemy.orm import Session
from .models import Member, Round, Payment, Expense

PRESET_MEMBERS = [
    ("ท่านฝ่ายฯ", Decimal("2500.00")),
    ("ท่านพุฒิพงค์", Decimal("800.00")),
    ("ท่านรักษิน", Decimal("500.00")),
    ("ท่านธนิดา", Decimal("500.00")),
    ("ท่านสิริกาญจน์", Decimal("500.00")),
    ("ท่านมณฑล", Decimal("300.00")),
    ("ท่านดวงเดือน", Decimal("1000.00")),
]

def money(x) -> str:
    return f"{Decimal(x or 0):,.2f}"

def active_round(db: Session):
    return db.query(Round).filter(Round.is_open == True).order_by(desc(Round.id)).first()

def add_member(db: Session, name: str, amount: Decimal):
    name = " ".join(str(name).strip().split())
    m = db.query(Member).filter(Member.name == name).first()
    if not m:
        m = Member(name=name, default_amount=amount, active=True)
        db.add(m)
    else:
        m.default_amount = amount
        m.active = True
    db.commit()
    db.refresh(m)
    r = active_round(db)
    if r:
        ensure_payment(db, r, m)
    return m

def seed_members(db: Session):
    for name, amount in PRESET_MEMBERS:
        add_member(db, name, amount)

def open_round(db: Session, title: str, carry_over: Decimal = Decimal("0")):
    title = title.strip() or f"เดือน {datetime.now().month}/{datetime.now().year + 543}"
    for old in db.query(Round).filter(Round.is_open == True).all():
        old.is_open = False
    r = db.query(Round).filter(Round.title == title).first()
    if not r:
        r = Round(title=title, carry_over=carry_over, is_open=True)
        db.add(r)
    else:
        r.is_open = True
        r.carry_over = carry_over
    db.commit()
    db.refresh(r)
    for m in db.query(Member).filter(Member.active == True).order_by(Member.id).all():
        ensure_payment(db, r, m)
    return r

def ensure_payment(db: Session, round_: Round, member: Member):
    p = db.query(Payment).filter(Payment.round_id == round_.id, Payment.member_id == member.id).first()
    if not p:
        p = Payment(round_id=round_.id, member_id=member.id, due_amount=member.default_amount, paid_amount=0, status="unpaid")
        db.add(p)
        db.commit()
        db.refresh(p)
    return p

def get_payments(db: Session, round_=None):
    r = round_ or active_round(db)
    if not r:
        return []
    return db.query(Payment).join(Member).filter(Payment.round_id == r.id).order_by(Member.id).all()

def member_by_id(db: Session, member_id: int):
    return db.query(Member).filter(Member.id == member_id).first()

def mark_member_paid(db: Session, member_id: int, amount=None, note: str = "", slip_path: str | None = None):
    r = active_round(db)
    if not r:
        return False, "ยังไม่มีรอบเดือนที่เปิดอยู่"
    m = member_by_id(db, member_id)
    if not m:
        return False, "ไม่พบสมาชิก"
    p = ensure_payment(db, r, m)
    amt = Decimal(amount if amount is not None else p.due_amount or 0)
    p.paid_amount = amt
    p.paid_at = datetime.utcnow()
    p.note = note
    p.slip_path = slip_path
    if amt >= Decimal(p.due_amount or 0):
        p.status = "paid"
    elif amt > 0:
        p.status = "partial"
    else:
        p.status = "unpaid"
    db.commit()
    return True, f"✅ บันทึกชำระแล้ว\n{m.name} {money(amt)} บาท"

def status_totals(db: Session):
    r = active_round(db)
    if not r:
        return {"round": None, "due": Decimal(0), "paid": Decimal(0), "unpaid": Decimal(0), "paid_count": 0, "unpaid_count": 0, "count": 0}
    pays = get_payments(db, r)
    due = sum(Decimal(p.due_amount or 0) for p in pays)
    paid = sum(Decimal(p.paid_amount or 0) for p in pays)
    paid_count = len([p for p in pays if p.status == "paid"])
    return {"round": r, "due": due, "paid": paid, "unpaid": max(due - paid, Decimal(0)), "paid_count": paid_count, "unpaid_count": len(pays) - paid_count, "count": len(pays)}

def summary_text(db: Session) -> str:
    t = status_totals(db)
    if not t["round"]:
        return "ยังไม่มีรอบเดือน"
    return f"💰 {t['round'].title}\nทั้งหมด {money(t['due'])} บาท\nชำระแล้ว {t['paid_count']} คน\nยังไม่ได้ชำระ {t['unpaid_count']} คน"

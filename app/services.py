from decimal import Decimal
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import desc
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

def active_round(db: Session) -> Round | None:
    return db.query(Round).filter(Round.is_open == True).order_by(desc(Round.id)).first()

def seed_members(db: Session):
    for name, amt in PRESET_MEMBERS:
        add_member(db, name, amt)

def add_member(db: Session, name: str, amount: Decimal) -> Member:
    m = db.query(Member).filter(Member.name == name).first()
    if not m:
        m = Member(name=name, default_amount=amount)
        db.add(m)
    else:
        m.default_amount = amount
        m.active = True
    db.commit(); db.refresh(m)
    r = active_round(db)
    if r:
        ensure_payment(db, r, m)
    return m

def ensure_payment(db: Session, round_: Round, member: Member) -> Payment:
    p = db.query(Payment).filter(Payment.round_id == round_.id, Payment.member_id == member.id).first()
    if not p:
        p = Payment(round_id=round_.id, member_id=member.id, due_amount=member.default_amount, paid_amount=0, status="unpaid")
        db.add(p); db.commit(); db.refresh(p)
    return p

def open_round(db: Session, title: str, carry_over: Decimal = Decimal("0")) -> Round:
    for r in db.query(Round).filter(Round.is_open == True).all():
        r.is_open = False
    r = db.query(Round).filter(Round.title == title).first()
    if not r:
        r = Round(title=title, carry_over=carry_over, is_open=True)
        db.add(r); db.commit(); db.refresh(r)
    else:
        r.is_open = True; r.carry_over = carry_over
        db.commit(); db.refresh(r)
    for m in db.query(Member).filter(Member.active == True).order_by(Member.id).all():
        ensure_payment(db, r, m)
    return r

def get_payments(db: Session, round_: Round | None = None):
    r = round_ or active_round(db)
    if not r:
        return []
    return db.query(Payment).join(Member).filter(Payment.round_id == r.id).order_by(Member.id).all()

def member_by_id(db: Session, member_id: int) -> Member | None:
    return db.query(Member).filter(Member.id == member_id).first()

def register_line(db: Session, line_user_id: str, name: str) -> tuple[bool, str]:
    m = db.query(Member).filter(Member.name == name).first()
    if not m:
        return False, f"ยังไม่มีชื่อ {name} ในระบบ"
    m.line_user_id = line_user_id
    db.commit()
    return True, f"✅ ลงทะเบียนสำเร็จ\n{name} ผูกกับ LINE นี้แล้ว"

def mark_member_paid(db: Session, member_id: int, amount: Decimal | None = None, note: str = "") -> tuple[bool, str]:
    r = active_round(db)
    if not r:
        return False, "ยังไม่มีรอบเดือนที่เปิดอยู่"
    m = member_by_id(db, member_id)
    if not m:
        return False, "ไม่พบสมาชิก"
    p = ensure_payment(db, r, m)
    amt = amount if amount is not None else Decimal(p.due_amount or 0)
    p.paid_amount = amt
    p.paid_at = datetime.utcnow()
    p.note = note
    if Decimal(p.paid_amount or 0) >= Decimal(p.due_amount or 0):
        p.status = "paid"
    elif Decimal(p.paid_amount or 0) > 0:
        p.status = "partial"
    else:
        p.status = "unpaid"
    db.commit()
    return True, f"✅ บันทึกชำระแล้ว\n{m.name} {money(amt)} บาท"

def pay_for_user(db: Session, line_user_id: str, amount: Decimal | None = None, note: str = "") -> tuple[bool, str]:
    r = active_round(db)
    if not r:
        return False, "ยังไม่มีรอบเดือนที่เปิดอยู่"
    m = db.query(Member).filter(Member.line_user_id == line_user_id).first()
    if not m:
        return False, "ยังไม่รู้ว่าคุณคือใคร\nกรุณาเลือกชื่อในหน้าชำระเงิน หรือพิมพ์: ลงทะเบียน ชื่อ"
    p = ensure_payment(db, r, m)
    amt = amount if amount is not None else Decimal(p.due_amount or 0)
    p.paid_amount = amt
    p.paid_at = datetime.utcnow()
    p.note = note
    p.status = "paid" if Decimal(p.paid_amount or 0) >= Decimal(p.due_amount or 0) else "partial"
    db.commit()
    return True, f"✅ บันทึกชำระแล้ว\n{m.name} {money(amt)} บาท"

def add_expense(db: Session, title: str, amount: Decimal) -> tuple[bool, str]:
    r = active_round(db)
    if not r:
        return False, "ยังไม่มีรอบเดือนที่เปิดอยู่"
    e = Expense(round_id=r.id, title=title, amount=amount)
    db.add(e); db.commit()
    return True, f"✅ เพิ่มรายจ่ายแล้ว\n{title} {money(amount)} บาท"

def status_totals(db: Session):
    r = active_round(db)
    if not r:
        return {"round": None, "due": Decimal(0), "paid": Decimal(0), "unpaid": Decimal(0), "paid_count": 0, "unpaid_count": 0, "count": 0}
    pays = get_payments(db, r)
    due = sum(Decimal(p.due_amount or 0) for p in pays)
    paid = sum(Decimal(p.paid_amount or 0) for p in pays)
    paid_count = len([p for p in pays if p.status == "paid"])
    return {"round": r, "due": due, "paid": paid, "unpaid": max(due-paid, Decimal(0)), "paid_count": paid_count, "unpaid_count": len(pays)-paid_count, "count": len(pays)}

def line_list_text(db: Session) -> str:
    r = active_round(db)
    if not r:
        return "ยังไม่มีรอบเดือน กรุณาเปิดรอบในหลังบ้านก่อน"
    lines = [f"💰 เงินกองสำนักงาน {r.title}", ""]
    for p in get_payments(db, r):
        status = "✅ ชำระแล้ว" if p.status == "paid" else "🔴 ยังไม่ได้ชำระ"
        lines.append(f"{p.member.name}  {money(p.due_amount)}  {status}")
    lines.append("\nกดชำระเงินจากปุ่มด้านล่าง")
    return "\n".join(lines)

def summary_text(db: Session) -> str:
    t = status_totals(db)
    if not t["round"]:
        return "ยังไม่มีรอบเดือน"
    return f"📊 {t['round'].title}\nทั้งหมด {money(t['due'])} บาท\nชำระแล้ว {t['paid_count']} คน\nยังไม่ได้ชำระ {t['unpaid_count']} คน"

def my_status_text(db: Session, line_user_id: str) -> str:
    r = active_round(db)
    if not r:
        return "ยังไม่มีรอบที่เปิดอยู่"
    m = db.query(Member).filter(Member.line_user_id == line_user_id).first()
    if not m:
        return "ยังไม่ได้ลงทะเบียน\nพิมพ์: ลงทะเบียน ชื่อของคุณ"
    p = ensure_payment(db, r, m)
    return f"👤 {m.name}\nรอบ: {r.title}\nยอด: {money(p.due_amount)} บาท\nสถานะ: {'✅ ชำระแล้ว' if p.status=='paid' else '🔴 ยังไม่ได้ชำระ'}"

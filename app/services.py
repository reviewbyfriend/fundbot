from decimal import Decimal
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import desc
from .models import Member, Round, Payment, Expense, GroupState

def money(x) -> str:
    return f"{Decimal(x or 0):,.2f}"

def active_round(db: Session) -> Round | None:
    return db.query(Round).filter(Round.is_open == True).order_by(desc(Round.id)).first()

def add_member(db: Session, name: str, amount: Decimal) -> Member:
    m = db.query(Member).filter(Member.name == name).first()
    if not m:
        m = Member(name=name, default_amount=amount)
        db.add(m)
    else:
        m.default_amount = amount
        m.active = True
    db.commit(); db.refresh(m)
    # ถ้ามีรอบเปิดอยู่ ให้เพิ่มยอดให้สมาชิกด้วย
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
    # ปิดรอบเดิมก่อน
    for r in db.query(Round).filter(Round.is_open == True).all():
        r.is_open = False
    r = db.query(Round).filter(Round.title == title).first()
    if not r:
        r = Round(title=title, carry_over=carry_over, is_open=True)
        db.add(r); db.commit(); db.refresh(r)
    else:
        r.is_open = True; r.carry_over = carry_over
        db.commit(); db.refresh(r)
    for m in db.query(Member).filter(Member.active == True).all():
        ensure_payment(db, r, m)
    return r

def register_line(db: Session, line_user_id: str, name: str) -> tuple[bool, str]:
    m = db.query(Member).filter(Member.name == name).first()
    if not m:
        return False, f"ยังไม่มีชื่อ {name} ในระบบ ให้แอดมินพิมพ์: เพิ่มสมาชิก {name} 500"
    m.line_user_id = line_user_id
    db.commit()
    return True, f"✅ ลงทะเบียนสำเร็จ\n{name} ผูกกับ LINE นี้แล้ว"

def pay_for_user(db: Session, line_user_id: str, amount: Decimal | None = None, note: str = "") -> tuple[bool, str]:
    r = active_round(db)
    if not r:
        return False, "ยังไม่มีรอบเดือนที่เปิดอยู่"
    m = db.query(Member).filter(Member.line_user_id == line_user_id).first()
    if not m:
        return False, "ยังไม่รู้ว่าคุณคือใคร\nพิมพ์: ลงทะเบียน ชื่อของคุณ\nเช่น ลงทะเบียน รักษิน"
    p = ensure_payment(db, r, m)
    amt = amount if amount is not None else Decimal(p.due_amount) - Decimal(p.paid_amount)
    p.paid_amount = Decimal(p.paid_amount or 0) + Decimal(amt)
    p.paid_at = datetime.utcnow()
    p.note = note
    if p.paid_amount >= p.due_amount:
        p.status = "paid"
    elif p.paid_amount > 0:
        p.status = "partial"
    else:
        p.status = "unpaid"
    db.commit()
    return True, f"✅ บันทึกการชำระแล้ว\n{m.name}\nรอบ: {r.title}\nยอดชำระ: {money(amt)} บาท\nสถานะ: {'จ่ายครบแล้ว' if p.status=='paid' else 'จ่ายบางส่วน'}"

def add_expense(db: Session, title: str, amount: Decimal) -> tuple[bool, str]:
    r = active_round(db)
    if not r:
        return False, "ยังไม่มีรอบเดือนที่เปิดอยู่"
    e = Expense(round_id=r.id, title=title, amount=amount)
    db.add(e); db.commit()
    return True, f"✅ เพิ่มรายจ่ายแล้ว\n{title} {money(amount)} บาท"

def summary_text(db: Session) -> str:
    r = active_round(db)
    if not r:
        return "ยังไม่มีรอบที่เปิดอยู่\nพิมพ์: เปิดรอบ กรกฎาคม 2569 ยกมา 0"
    pays = db.query(Payment).filter(Payment.round_id == r.id).all()
    exps = db.query(Expense).filter(Expense.round_id == r.id).all()
    due = sum(Decimal(p.due_amount or 0) for p in pays)
    paid = sum(Decimal(p.paid_amount or 0) for p in pays)
    exp_total = sum(Decimal(e.amount or 0) for e in exps)
    balance = Decimal(r.carry_over or 0) + paid - exp_total
    unpaid = [p for p in pays if p.status != "paid"]
    lines = [
        f"📊 สรุปกองกลาง {r.title}",
        f"ยอดยกมา: {money(r.carry_over)} บาท",
        f"ต้องเก็บรวม: {money(due)} บาท",
        f"รับแล้ว: {money(paid)} บาท",
        f"รายจ่าย: {money(exp_total)} บาท",
        f"คงเหลือ: {money(balance)} บาท",
        "",
        f"✅ จ่ายครบ {len([p for p in pays if p.status == 'paid'])} คน",
        f"🔴 ยังไม่ครบ {len(unpaid)} คน",
    ]
    if unpaid:
        lines.append("\nคนที่ยังไม่ครบ:")
        for p in unpaid[:30]:
            remain = Decimal(p.due_amount or 0) - Decimal(p.paid_amount or 0)
            lines.append(f"- {p.member.name}: ค้าง {money(remain)}")
    return "\n".join(lines)

def my_status_text(db: Session, line_user_id: str) -> str:
    r = active_round(db)
    if not r:
        return "ยังไม่มีรอบที่เปิดอยู่"
    m = db.query(Member).filter(Member.line_user_id == line_user_id).first()
    if not m:
        return "ยังไม่ได้ลงทะเบียน\nพิมพ์: ลงทะเบียน ชื่อของคุณ"
    p = ensure_payment(db, r, m)
    remain = Decimal(p.due_amount or 0) - Decimal(p.paid_amount or 0)
    return f"👤 {m.name}\nรอบ: {r.title}\nยอดที่ต้องจ่าย: {money(p.due_amount)} บาท\nจ่ายแล้ว: {money(p.paid_amount)} บาท\nค้าง: {money(max(remain, Decimal('0')))} บาท\nสถานะ: {'✅ จ่ายแล้ว' if p.status=='paid' else '🔴 ยังไม่ครบ'}"

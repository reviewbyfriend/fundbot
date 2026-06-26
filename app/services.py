from datetime import datetime
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from .models import Group, Member, FundRound, PaymentDue, Expense, AuditLog, SlipRecord


def money(v) -> float:
    return float(v or 0)


def get_or_create_group(db: Session, line_group_id: str) -> Group:
    group = db.scalar(select(Group).where(Group.line_group_id == line_group_id))
    if not group:
        group = Group(line_group_id=line_group_id, name="LINE Group")
        db.add(group); db.commit(); db.refresh(group)
    return group


def is_admin(user_id: str | None, admin_ids: set[str]) -> bool:
    return bool(user_id and user_id in admin_ids)


def add_log(db: Session, group_id: int | None, user_id: str | None, action: str, detail: str = ""):
    db.add(AuditLog(group_id=group_id, user_id=user_id, action=action, detail=detail)); db.commit()


def add_member(db: Session, group: Group, name: str, amount: float) -> Member:
    m = db.scalar(select(Member).where(Member.group_id == group.id, Member.display_name == name))
    if m:
        m.monthly_amount = amount; m.active = True
    else:
        m = Member(group_id=group.id, display_name=name, monthly_amount=amount)
        db.add(m)
    db.commit(); db.refresh(m)
    return m


def find_member(db: Session, group: Group, keyword: str) -> Member | None:
    keyword = (keyword or "").strip()
    if not keyword: return None
    exact = db.scalar(select(Member).where(Member.group_id == group.id, Member.display_name == keyword, Member.active == True))
    if exact: return exact
    return db.scalar(select(Member).where(Member.group_id == group.id, Member.display_name.ilike(f"%{keyword}%"), Member.active == True).limit(1))


def bind_member(db: Session, group: Group, user_id: str, keyword: str) -> Member | None:
    m = find_member(db, group, keyword)
    if m:
        m.line_user_id = user_id
        db.commit(); db.refresh(m)
    return m


def current_round(db: Session, group: Group) -> FundRound | None:
    return db.scalar(select(FundRound).where(FundRound.group_id == group.id, FundRound.is_open == True).order_by(FundRound.created_at.desc()).limit(1))


def open_round(db: Session, group: Group, month_label: str, opening_balance: float = 0) -> FundRound:
    r = db.scalar(select(FundRound).where(FundRound.group_id == group.id, FundRound.month_label == month_label))
    if not r:
        r = FundRound(group_id=group.id, month_label=month_label, opening_balance=opening_balance)
        db.add(r); db.flush()
    r.is_open = True
    # close other rounds
    for other in db.scalars(select(FundRound).where(FundRound.group_id == group.id, FundRound.id != r.id, FundRound.is_open == True)):
        other.is_open = False
    db.flush()
    members = db.scalars(select(Member).where(Member.group_id == group.id, Member.active == True)).all()
    for m in members:
        due = db.scalar(select(PaymentDue).where(PaymentDue.round_id == r.id, PaymentDue.member_id == m.id))
        if not due:
            db.add(PaymentDue(round_id=r.id, member_id=m.id, amount_due=m.monthly_amount, amount_paid=0, status="ค้างชำระ"))
    db.commit(); db.refresh(r)
    return r


def mark_paid(db: Session, group: Group, user_id: str | None, amount: float, member_keyword: str | None = None, slip_message_id: str | None = None) -> tuple[bool, str]:
    r = current_round(db, group)
    if not r:
        return False, "ยังไม่มีรอบเดือนที่เปิดอยู่\nให้แอดมินพิมพ์: เปิดรอบ กรกฎาคม 2569"
    if member_keyword:
        m = find_member(db, group, member_keyword)
    else:
        m = db.scalar(select(Member).where(Member.group_id == group.id, Member.line_user_id == user_id, Member.active == True)) if user_id else None
    if not m:
        return False, "ยังไม่รู้ว่าเป็นของใคร\nให้พิมพ์: ลงทะเบียน ชื่อของคุณ\nหรือแอดมินพิมพ์: รับเงิน ชื่อ จำนวนเงิน"
    due = db.scalar(select(PaymentDue).where(PaymentDue.round_id == r.id, PaymentDue.member_id == m.id))
    if not due:
        due = PaymentDue(round_id=r.id, member_id=m.id, amount_due=m.monthly_amount, amount_paid=0, status="ค้างชำระ")
        db.add(due); db.flush()
    due.amount_paid = Decimal(str(money(due.amount_paid) + float(amount)))
    due.slip_message_id = slip_message_id or due.slip_message_id
    if money(due.amount_paid) >= money(due.amount_due):
        due.status = "จ่ายแล้ว"
        due.paid_at = datetime.utcnow()
    elif money(due.amount_paid) > 0:
        due.status = "ชำระบางส่วน"
    else:
        due.status = "ค้างชำระ"
    db.commit()
    remain = max(0, money(due.amount_due) - money(due.amount_paid))
    if remain:
        return True, f"✅ บันทึกแล้ว\n{m.display_name} ชำระ {float(amount):,.2f} บาท\nสถานะ: ชำระบางส่วน\nค้างอีก {remain:,.2f} บาท"
    return True, f"✅ รับชำระแล้ว\n{m.display_name}\nจำนวนรวม {money(due.amount_paid):,.2f} บาท\nสถานะ: จ่ายแล้ว"


def add_expense(db: Session, group: Group, description: str, amount: float, item_date: str | None = None) -> tuple[bool, str]:
    r = current_round(db, group)
    if not r:
        return False, "ยังไม่มีรอบเดือนที่เปิดอยู่"
    db.add(Expense(round_id=r.id, description=description, amount=amount, item_date=item_date))
    db.commit()
    return True, f"✅ เพิ่มรายจ่ายแล้ว\n{description}\n{amount:,.2f} บาท"


def summary_text(db: Session, group: Group) -> str:
    r = current_round(db, group)
    if not r:
        return "ยังไม่มีรอบเดือนที่เปิดอยู่"
    dues = db.scalars(select(PaymentDue).where(PaymentDue.round_id == r.id)).all()
    expenses = db.scalars(select(Expense).where(Expense.round_id == r.id)).all()
    total_due = sum(money(d.amount_due) for d in dues)
    total_paid = sum(money(d.amount_paid) for d in dues)
    total_exp = sum(money(e.amount) for e in expenses)
    balance = money(r.opening_balance) + total_paid - total_exp
    paid_count = sum(1 for d in dues if d.status == "จ่ายแล้ว")
    pending = [d for d in dues if d.status != "จ่ายแล้ว"]
    lines = [
        f"📊 สรุป{r.month_label}",
        f"ยอดยกมา {money(r.opening_balance):,.2f} บาท",
        f"รับแล้ว {total_paid:,.2f} / {total_due:,.2f} บาท",
        f"รายจ่าย {total_exp:,.2f} บาท",
        f"คงเหลือ {balance:,.2f} บาท",
        f"จ่ายแล้ว {paid_count} คน / ค้าง {len(pending)} คน",
    ]
    if pending:
        lines.append("\nคนที่ยังไม่ครบ:")
        for d in pending[:30]:
            lines.append(f"- {d.member.display_name}: ค้าง {max(0, money(d.amount_due)-money(d.amount_paid)):,.2f}")
    return "\n".join(lines)


def my_due(db: Session, group: Group, user_id: str | None) -> PaymentDue | None:
    r = current_round(db, group)
    if not r or not user_id: return None
    m = db.scalar(select(Member).where(Member.group_id == group.id, Member.line_user_id == user_id, Member.active == True))
    if not m: return None
    return db.scalar(select(PaymentDue).where(PaymentDue.round_id == r.id, PaymentDue.member_id == m.id))


def remind_text(db: Session, group: Group) -> str:
    r = current_round(db, group)
    if not r: return "ยังไม่มีรอบเดือนที่เปิดอยู่"
    pending = db.scalars(select(PaymentDue).where(PaymentDue.round_id == r.id, PaymentDue.status != "จ่ายแล้ว")).all()
    if not pending:
        return f"✅ {r.month_label} ทุกคนชำระครบแล้ว"
    lines = [f"📢 แจ้งเตือนชำระ{r.month_label}", "ผู้ที่ยังชำระไม่ครบ:"]
    for d in pending[:50]:
        lines.append(f"- {d.member.display_name}: {max(0, money(d.amount_due)-money(d.amount_paid)):,.2f} บาท")
    lines.append("\nพิมพ์ ‘ชำระเงิน’ เพื่อรับลิงก์ QR พร้อมเพย์")
    return "\n".join(lines)


def process_slip_payment(db: Session, group: Group, user_id: str | None, line_message_id: str, amount: float | None, reference_no: str | None, raw_text: str, receiver_checked: bool = True) -> tuple[bool, str]:
    """Record OCR slip and mark member paid when safe enough."""
    r = current_round(db, group)
    if not r:
        rec = SlipRecord(group_id=group.id, line_message_id=line_message_id, amount=amount, reference_no=reference_no, raw_text=raw_text, status="ไม่มีรอบเดือน")
        db.add(rec); db.commit()
        return False, "📎 อ่านสลิปได้แล้ว แต่ยังไม่มีรอบเดือนที่เปิดอยู่\nให้แอดมินพิมพ์: เปิดรอบ กรกฎาคม 2569"

    # Duplicate reference guard
    if reference_no:
        old = db.scalar(select(SlipRecord).where(SlipRecord.group_id == group.id, SlipRecord.reference_no == reference_no))
        if old:
            return False, f"⚠️ สลิปนี้เคยถูกส่งแล้ว\nเลขอ้างอิง: {reference_no}\nยังไม่ตัดยอดซ้ำ"

    m = db.scalar(select(Member).where(Member.group_id == group.id, Member.line_user_id == user_id, Member.active == True)) if user_id else None
    if not m:
        rec = SlipRecord(group_id=group.id, round_id=r.id, line_message_id=line_message_id, amount=amount, reference_no=reference_no, raw_text=raw_text, status="ไม่ทราบสมาชิก")
        db.add(rec); db.commit()
        amt_txt = f"\nยอดที่อ่านได้ {amount:,.2f} บาท" if amount else ""
        return False, "📎 อ่านสลิปแล้ว แต่ยังไม่รู้ว่าเป็นของใคร" + amt_txt + "\nให้พิมพ์: ลงทะเบียน ชื่อของคุณ\nหรือแอดมินพิมพ์: รับเงิน ชื่อ จำนวนเงิน"

    if not receiver_checked:
        rec = SlipRecord(group_id=group.id, round_id=r.id, member_id=m.id, line_message_id=line_message_id, amount=amount, reference_no=reference_no, raw_text=raw_text, status="ผู้รับไม่ตรง")
        db.add(rec); db.commit()
        return False, "⚠️ อ่านสลิปแล้ว แต่ชื่อ/บัญชีผู้รับไม่ตรงกับที่ตั้งไว้\nระบบยังไม่ตัดยอด ให้แอดมินตรวจสอบก่อน"

    if amount is None:
        rec = SlipRecord(group_id=group.id, round_id=r.id, member_id=m.id, line_message_id=line_message_id, amount=None, reference_no=reference_no, raw_text=raw_text, status="อ่านยอดไม่ได้")
        db.add(rec); db.commit()
        return False, f"📎 รับสลิปของ {m.display_name} แล้ว แต่อ่านยอดเงินไม่ได้ชัดเจน\nให้พิมพ์ยืนยัน: จ่ายแล้ว 500"

    ok, msg = mark_paid(db, group, user_id, amount, slip_message_id=line_message_id)
    rec = SlipRecord(group_id=group.id, round_id=r.id, member_id=m.id, line_message_id=line_message_id, amount=amount, reference_no=reference_no, raw_text=raw_text, status="บันทึกแล้ว" if ok else "ไม่สำเร็จ")
    db.add(rec); db.commit()
    extra = ""
    if reference_no:
        extra += f"\nเลขอ้างอิง: {reference_no}"
    return ok, "📎 OCR สลิปสำเร็จ\n" + msg + extra

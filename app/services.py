from sqlalchemy.orm import Session
from sqlalchemy import func
from .models import Group, Member, Round, Payment, Expense

def money(x: float | int | None) -> str:
    return f"{float(x or 0):,.2f}"

def get_group(db: Session, line_group_id: str, title: str = "LINE Group") -> Group:
    g = db.query(Group).filter_by(line_group_id=line_group_id).first()
    if not g:
        g = Group(line_group_id=line_group_id, title=title)
        db.add(g); db.commit(); db.refresh(g)
    return g

def current_round(db: Session, group: Group) -> Round | None:
    return db.query(Round).filter_by(group_id=group.id, status="open").order_by(Round.id.desc()).first()

def add_member(db: Session, group: Group, name: str, amount: float) -> Member:
    m = db.query(Member).filter_by(group_id=group.id, name=name).first()
    if not m:
        m = Member(group_id=group.id, name=name, monthly_amount=amount)
        db.add(m)
    else:
        m.monthly_amount = amount; m.active = 1
    db.commit(); db.refresh(m)
    return m

def find_member(db: Session, group: Group, name: str) -> Member | None:
    return db.query(Member).filter(Member.group_id == group.id, Member.name.ilike(f"%{name}%"), Member.active == 1).first()

def member_by_user(db: Session, group: Group, user_id: str) -> Member | None:
    return db.query(Member).filter_by(group_id=group.id, line_user_id=user_id, active=1).first()

def register_user(db: Session, group: Group, user_id: str, name: str) -> Member | None:
    m = find_member(db, group, name)
    if not m:
        return None
    m.line_user_id = user_id
    db.commit(); db.refresh(m)
    return m

def open_round(db: Session, group: Group, title: str, brought_forward: float) -> Round:
    existing = db.query(Round).filter_by(group_id=group.id, title=title).first()
    if existing:
        existing.status = "open"; existing.brought_forward = brought_forward
        db.commit(); db.refresh(existing)
        return existing
    r = Round(group_id=group.id, title=title, brought_forward=brought_forward)
    db.add(r); db.commit(); db.refresh(r)
    return r

def record_payment(db: Session, rnd: Round, member: Member, amount: float, source="manual", slip_ref=None, note=None) -> Payment:
    p = db.query(Payment).filter_by(round_id=rnd.id, member_id=member.id).first()
    if not p:
        p = Payment(round_id=rnd.id, member_id=member.id, amount=amount, source=source, slip_ref=slip_ref, note=note)
        db.add(p)
    else:
        p.amount = amount; p.source = source; p.slip_ref = slip_ref or p.slip_ref; p.note = note or p.note
    db.commit(); db.refresh(p)
    return p

def add_expense(db: Session, rnd: Round, title: str, amount: float) -> Expense:
    e = Expense(round_id=rnd.id, title=title, amount=amount)
    db.add(e); db.commit(); db.refresh(e)
    return e

def round_summary(db: Session, rnd: Round) -> dict:
    members = db.query(Member).filter_by(group_id=rnd.group_id, active=1).order_by(Member.name).all()
    payments = {p.member_id: p for p in db.query(Payment).filter_by(round_id=rnd.id).all()}
    expenses = db.query(Expense).filter_by(round_id=rnd.id).all()
    expected = sum(m.monthly_amount for m in members)
    paid = sum(p.amount for p in payments.values())
    expense = sum(e.amount for e in expenses)
    unpaid = [m for m in members if m.id not in payments]
    balance = rnd.brought_forward + paid - expense
    return {"members": members, "payments": payments, "expenses": expenses, "expected": expected, "paid": paid, "expense": expense, "unpaid": unpaid, "balance": balance}

def is_admin(user_id: str, admin_ids: set[str]) -> bool:
    return not admin_ids or user_id in admin_ids

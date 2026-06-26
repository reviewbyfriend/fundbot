from datetime import datetime
from sqlalchemy import String, Integer, Numeric, DateTime, ForeignKey, UniqueConstraint, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base

class Group(Base):
    __tablename__ = "groups"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    line_group_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), default="LINE Group")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Member(Base):
    __tablename__ = "members"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(255), index=True)
    monthly_amount: Mapped[float] = mapped_column(Numeric(12,2), default=0)
    line_user_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    group = relationship("Group")
    __table_args__ = (UniqueConstraint("group_id", "display_name", name="uq_member_name_per_group"),)

class FundRound(Base):
    __tablename__ = "fund_rounds"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    month_label: Mapped[str] = mapped_column(String(100), index=True)  # เช่น กรกฎาคม 2569
    opening_balance: Mapped[float] = mapped_column(Numeric(12,2), default=0)
    is_open: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    group = relationship("Group")
    __table_args__ = (UniqueConstraint("group_id", "month_label", name="uq_round_per_group_month"),)

class PaymentDue(Base):
    __tablename__ = "payment_dues"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("fund_rounds.id"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    amount_due: Mapped[float] = mapped_column(Numeric(12,2), default=0)
    amount_paid: Mapped[float] = mapped_column(Numeric(12,2), default=0)
    status: Mapped[str] = mapped_column(String(30), default="ค้างชำระ")
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    slip_message_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    member = relationship("Member")
    round = relationship("FundRound")
    __table_args__ = (UniqueConstraint("round_id", "member_id", name="uq_due_per_round_member"),)

class Expense(Base):
    __tablename__ = "expenses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("fund_rounds.id"), index=True)
    item_date: Mapped[str | None] = mapped_column(String(50), nullable=True)
    description: Mapped[str] = mapped_column(String(255))
    amount: Mapped[float] = mapped_column(Numeric(12,2))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    round = relationship("FundRound")

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    action: Mapped[str] = mapped_column(String(80))
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class SlipRecord(Base):
    __tablename__ = "slip_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    round_id: Mapped[int | None] = mapped_column(ForeignKey("fund_rounds.id"), nullable=True, index=True)
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id"), nullable=True, index=True)
    line_message_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    amount: Mapped[float | None] = mapped_column(Numeric(12,2), nullable=True)
    reference_no: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="รอตรวจสอบ")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    group = relationship("Group")
    round = relationship("FundRound")
    member = relationship("Member")
    __table_args__ = (UniqueConstraint("group_id", "reference_no", name="uq_slip_ref_per_group"),)

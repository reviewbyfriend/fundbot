from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from .database import Base

class Member(Base):
    __tablename__ = "members"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), unique=True, index=True, nullable=False)
    default_amount = Column(Numeric(12, 2), default=0)
    line_user_id = Column(String(120), unique=True, nullable=True)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    payments = relationship("Payment", back_populates="member")

class Round(Base):
    __tablename__ = "rounds"
    id = Column(Integer, primary_key=True)
    title = Column(String(120), unique=True, index=True, nullable=False)
    carry_over = Column(Numeric(12, 2), default=0)
    is_open = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    payments = relationship("Payment", back_populates="round")
    expenses = relationship("Expense", back_populates="round")

class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True)
    round_id = Column(Integer, ForeignKey("rounds.id"), nullable=False)
    member_id = Column(Integer, ForeignKey("members.id"), nullable=False)
    due_amount = Column(Numeric(12, 2), default=0)
    paid_amount = Column(Numeric(12, 2), default=0)
    status = Column(String(20), default="unpaid")
    paid_at = Column(DateTime, nullable=True)
    slip_message_id = Column(String(120), nullable=True)
    slip_path = Column(Text, nullable=True)
    receipt_path = Column(Text, nullable=True)
    payment_type = Column(String(20), nullable=True)  # transfer / cash
    rejection_reason = Column(Text, nullable=True)
    note = Column(Text, nullable=True)
    round = relationship("Round", back_populates="payments")
    member = relationship("Member", back_populates="payments")
    __table_args__ = (UniqueConstraint("round_id", "member_id", name="uix_round_member"),)

class Expense(Base):
    __tablename__ = "expenses"
    id = Column(Integer, primary_key=True)
    round_id = Column(Integer, ForeignKey("rounds.id"), nullable=False)
    title = Column(String(200), nullable=False)
    amount = Column(Numeric(12, 2), default=0)
    category = Column(String(120), nullable=True)
    expense_date = Column(DateTime, default=datetime.utcnow)
    receipt_path = Column(Text, nullable=True)
    created_by = Column(String(120), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    note = Column(Text, nullable=True)
    round = relationship("Round", back_populates="expenses")


class BotState(Base):
    __tablename__ = "bot_state"
    key = Column(String(120), primary_key=True)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AdminUser(Base):
    __tablename__ = "admin_users"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), unique=True, index=True, nullable=False)
    role = Column(String(20), default="approver")  # owner / approver / viewer
    code_hash = Column(String(128), nullable=False)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login_at = Column(DateTime, nullable=True)
    line_user_id = Column(String(120), unique=True, nullable=True)


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"
    id = Column(Integer, primary_key=True)
    admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    admin_name = Column(String(120), nullable=False)
    action = Column(String(40), nullable=False)
    payment_id = Column(Integer, ForeignKey("payments.id"), nullable=True)
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

from datetime import datetime
from sqlalchemy import String, Integer, Float, DateTime, ForeignKey, UniqueConstraint, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base

class Group(Base):
    __tablename__ = "groups"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    line_group_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255), default="LINE Group")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Member(Base):
    __tablename__ = "members"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    monthly_amount: Mapped[float] = mapped_column(Float, default=0)
    line_user_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    active: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    group = relationship("Group")
    __table_args__ = (UniqueConstraint("group_id", "name", name="uq_member_group_name"),)

class Round(Base):
    __tablename__ = "rounds"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    brought_forward: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(32), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    group = relationship("Group")
    __table_args__ = (UniqueConstraint("group_id", "title", name="uq_round_group_title"),)

class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    amount: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(50), default="manual")
    slip_ref: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    round = relationship("Round")
    member = relationship("Member")
    __table_args__ = (UniqueConstraint("round_id", "member_id", name="uq_payment_round_member"),)

class Expense(Base):
    __tablename__ = "expenses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    amount: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    round = relationship("Round")

from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Date, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from backend.database import Base


class ReceivablesSnapshot(Base):
    """One uploaded Excel file = one snapshot."""
    __tablename__ = "receivables_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    file_name = Column(String, nullable=False)
    upload_date = Column(DateTime(timezone=True), server_default=func.now())
    report_date = Column(Date)  # Date extracted from filename or file content

    # Footer totals from the Excel
    total_unpaid = Column(Float, default=0)
    total_prepayments = Column(Float, default=0)
    total_balance = Column(Float, default=0)
    total_overdue = Column(Float, default=0)

    total_buyers = Column(Integer, default=0)
    total_invoices = Column(Integer, default=0)
    overdue_buyers = Column(Integer, default=0)
    overdue_invoices = Column(Integer, default=0)

    ai_analysis = Column(Text)  # AI-generated insights text

    buyers = relationship("ReceivablesBuyer", back_populates="snapshot", cascade="all, delete-orphan")


class ReceivablesBuyer(Base):
    """One buyer entry per snapshot."""
    __tablename__ = "receivables_buyers"

    id = Column(Integer, primary_key=True, index=True)
    snapshot_id = Column(Integer, ForeignKey("receivables_snapshots.id"), nullable=False)

    buyer_code = Column(String, index=True)  # e.g. "110011925"
    buyer_name = Column(String, nullable=False)
    balance = Column(Float, default=0)
    overdue_amount = Column(Float, default=0)
    has_overdue = Column(Boolean, default=False)
    has_prepayment = Column(Boolean, default=False)

    snapshot = relationship("ReceivablesSnapshot", back_populates="buyers")
    invoices = relationship("ReceivablesInvoice", back_populates="buyer", cascade="all, delete-orphan")


class ReceivablesInvoice(Base):
    """One invoice row per buyer per snapshot."""
    __tablename__ = "receivables_invoices"

    id = Column(Integer, primary_key=True, index=True)
    buyer_id = Column(Integer, ForeignKey("receivables_buyers.id"), nullable=False)

    invoice_number = Column(String)
    invoice_datetime = Column(String)  # stored as string to preserve original format
    payment_date = Column(Date)
    payment_term_days = Column(Integer)
    amount = Column(Float, default=0)
    days_remaining = Column(Integer)  # negative = overdue
    is_overdue = Column(Boolean, default=False)
    is_prepayment = Column(Boolean, default=False)

    buyer = relationship("ReceivablesBuyer", back_populates="invoices")

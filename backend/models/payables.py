from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Date, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from backend.database import Base


class PayablesSnapshot(Base):
    """One uploaded Excel file = one supplier debts snapshot."""
    __tablename__ = "payables_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    file_name = Column(String, nullable=False)
    upload_date = Column(DateTime(timezone=True), server_default=func.now())
    report_date = Column(Date)

    # Footer totals (stored as positive amounts for clarity)
    total_unpaid = Column(Float, default=0)       # Iš viso neapmokėta (abs)
    total_prepayments = Column(Float, default=0)  # Iš viso išankstinių mokėjimų
    total_balance = Column(Float, default=0)      # Bendras balansas (abs)
    total_overdue = Column(Float, default=0)      # Iš viso uždelsta (abs)

    total_suppliers = Column(Integer, default=0)
    total_invoices = Column(Integer, default=0)
    overdue_suppliers = Column(Integer, default=0)
    overdue_invoices = Column(Integer, default=0)

    ai_analysis = Column(Text)

    suppliers = relationship("PayablesSupplier", back_populates="snapshot", cascade="all, delete-orphan")


class PayablesSupplier(Base):
    """One supplier entry per snapshot."""
    __tablename__ = "payables_suppliers"

    id = Column(Integer, primary_key=True, index=True)
    snapshot_id = Column(Integer, ForeignKey("payables_snapshots.id"), nullable=False)

    supplier_code = Column(String, index=True)
    supplier_name = Column(String, nullable=False)
    balance = Column(Float, default=0)        # abs value (what we owe)
    overdue_amount = Column(Float, default=0) # abs value
    has_overdue = Column(Boolean, default=False)
    has_prepayment = Column(Boolean, default=False)

    snapshot = relationship("PayablesSnapshot", back_populates="suppliers")
    invoices = relationship("PayablesInvoice", back_populates="supplier", cascade="all, delete-orphan")


class PayablesInvoice(Base):
    """One invoice row per supplier per snapshot."""
    __tablename__ = "payables_invoices"

    id = Column(Integer, primary_key=True, index=True)
    supplier_id = Column(Integer, ForeignKey("payables_suppliers.id"), nullable=False)

    invoice_number = Column(String)           # our internal number
    supplier_invoice_number = Column(String)  # supplier's own invoice number
    invoice_datetime = Column(String)
    payment_date = Column(Date)
    payment_term = Column(String)             # can be "30", "KR", "0", etc.
    amount = Column(Float, default=0)         # abs value
    days_remaining = Column(Integer)          # negative = overdue
    is_overdue = Column(Boolean, default=False)
    is_prepayment = Column(Boolean, default=False)

    supplier = relationship("PayablesSupplier", back_populates="invoices")

from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Date, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from backend.database import Base

TEAM_DISPLAY = {
    "SAVI": "SAVI",
    "ROMBAS": "ROMBAS",
    "SOSTINES": "SOSTINĖ",
    "UZ_VILNIAUS": "UZ LIETUVĄ",
}

KNOWN_TEAMS = set(TEAM_DISPLAY.keys())


class SalesClientSnapshot(Base):
    """Uploaded client list – refreshed when team assignments change."""
    __tablename__ = "sales_client_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    file_name = Column(String, nullable=False)
    upload_date = Column(DateTime(timezone=True), server_default=func.now())
    total_clients = Column(Integer, default=0)   # unique base codes
    total_assigned = Column(Integer, default=0)  # with known team
    is_active = Column(Boolean, default=True)    # only one active at a time

    clients = relationship("SalesClient", back_populates="snapshot", cascade="all, delete-orphan")


class SalesClient(Base):
    """One client per base code per client snapshot."""
    __tablename__ = "sales_clients"

    id = Column(Integer, primary_key=True, index=True)
    snapshot_id = Column(Integer, ForeignKey("sales_client_snapshots.id"), nullable=False)

    client_code = Column(String, index=True, nullable=False)  # base code (no / suffix)
    client_name = Column(String, nullable=False)
    team = Column(String)        # SAVI | ROMBAS | SOSTINES | UZ_VILNIAUS
    salesperson = Column(String) # JURATE_C, EDITA, ROMUALDA, etc.

    snapshot = relationship("SalesClientSnapshot", back_populates="clients")


class SalesMonthSnapshot(Base):
    """One uploaded monthly sales file."""
    __tablename__ = "sales_month_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    file_name = Column(String, nullable=False)
    upload_date = Column(DateTime(timezone=True), server_default=func.now())
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)  # 1-12

    total_invoices = Column(Integer, default=0)
    total_suma = Column(Float, default=0)       # excl. VAT
    total_bp = Column(Float, default=0)         # gross profit
    total_bp_pct = Column(Float, default=0)     # avg GP%

    ai_analysis = Column(Text)          # bendra situacija + prioritetai
    ai_savi       = Column(Text)        # SAVI komandos analizė
    ai_rombas     = Column(Text)        # ROMBAS
    ai_sostines   = Column(Text)        # SOSTINĖ
    ai_uz_vilniaus= Column(Text)        # UZ LIETUVĄ

    __table_args__ = (UniqueConstraint("year", "month", name="uq_sales_year_month"),)

    invoices = relationship("SalesInvoice", back_populates="month_snapshot", cascade="all, delete-orphan")
    team_stats = relationship("SalesTeamStat", back_populates="month_snapshot", cascade="all, delete-orphan")


class SalesInvoice(Base):
    """Individual invoice row from the monthly sales file."""
    __tablename__ = "sales_invoices"

    id = Column(Integer, primary_key=True, index=True)
    month_snapshot_id = Column(Integer, ForeignKey("sales_month_snapshots.id"), nullable=False)

    invoice_number = Column(String)
    invoice_date = Column(Date)
    client_code = Column(String, index=True)   # base code
    client_name = Column(String)
    team = Column(String, index=True)          # from client snapshot lookup
    salesperson = Column(String)
    apmok_term = Column(Integer)

    suma = Column(Float, default=0)            # excl. VAT
    pvm = Column(Float, default=0)
    suma_su_pvm = Column(Float, default=0)
    bendrasis_pelnas = Column(Float, default=0)
    bp_pct = Column(Float, default=0)

    month_snapshot = relationship("SalesMonthSnapshot", back_populates="invoices")


class SalesTeamStat(Base):
    """Aggregated stats per team per month (computed at upload time)."""
    __tablename__ = "sales_team_stats"

    id = Column(Integer, primary_key=True, index=True)
    month_snapshot_id = Column(Integer, ForeignKey("sales_month_snapshots.id"), nullable=False)
    team = Column(String, nullable=False)

    total_suma = Column(Float, default=0)
    total_bp = Column(Float, default=0)
    avg_bp_pct = Column(Float, default=0)
    invoice_count = Column(Integer, default=0)
    active_clients = Column(Integer, default=0)  # unique clients who bought

    month_snapshot = relationship("SalesMonthSnapshot", back_populates="team_stats")

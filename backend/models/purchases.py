from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Date, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from backend.database import Base


class PurchaseCategory(Base):
    """Prekių klasių hierarchija (ikeliama kartą, atnaujinama pagal poreikį)."""
    __tablename__ = "purchase_categories"

    id          = Column(Integer, primary_key=True, index=True)
    code        = Column(String, unique=True, index=True, nullable=False)  # KODAS
    name        = Column(String, nullable=False)                           # PAVADINIMAS
    parent_code = Column(String, ForeignKey("purchase_categories.code"), nullable=True)  # PAGRINDINIS
    level       = Column(Integer, default=0)  # 0=root, 1, 2, 3
    upload_date = Column(DateTime(timezone=True), server_default=func.now())


class WarehouseSnapshot(Base):
    """Sandėlio kiekio įkėlimas (naujas kiekvieną savaitę)."""
    __tablename__ = "warehouse_snapshots"

    id          = Column(Integer, primary_key=True, index=True)
    file_name   = Column(String, nullable=False)
    snap_date   = Column(Date, nullable=False)   # data iš failo pavadinimo / parinkta
    upload_date = Column(DateTime(timezone=True), server_default=func.now())
    total_items = Column(Integer, default=0)
    total_value = Column(Float, default=0.0)     # bendra sandėlio vertė (suma)
    is_latest   = Column(Boolean, default=True)  # ar tai paskutinis

    items = relationship("WarehouseItem", back_populates="snapshot", cascade="all, delete-orphan")


class WarehouseItem(Base):
    """Viena prekė sandėlio kiekio snapshot'e."""
    __tablename__ = "warehouse_items"

    id          = Column(Integer, primary_key=True, index=True)
    snapshot_id = Column(Integer, ForeignKey("warehouse_snapshots.id"), nullable=False, index=True)
    product_code          = Column(String, nullable=False, index=True)  # Prekė
    category_code         = Column(String, nullable=True, index=True)   # Klasė (gali nebūti naujame formate)
    product_name          = Column(String, nullable=False)               # Pavadinimas
    quantity              = Column(Float, default=0.0)                   # Kiekis
    unit_cost             = Column(Float, default=0.0)                   # Savikaina
    total_value           = Column(Float, default=0.0)                   # Suma
    product_group_manager = Column(String, nullable=True)                # Produktų grupės vadovas
    product_category      = Column(String, nullable=True)                # Prekės kategorija

    snapshot = relationship("WarehouseSnapshot", back_populates="items")


class SalesWeekSnapshot(Base):
    """Savaitinės pardavimų statistikos įkėlimas."""
    __tablename__ = "sales_week_snapshots"

    id           = Column(Integer, primary_key=True, index=True)
    file_name    = Column(String, nullable=False)
    period_from  = Column(Date, nullable=False)
    period_to    = Column(Date, nullable=False)
    upload_date  = Column(DateTime(timezone=True), server_default=func.now())
    total_items  = Column(Integer, default=0)   # unikalių prekių sk.
    total_qty    = Column(Float, default=0.0)   # bendra kiekio suma

    __table_args__ = (
        UniqueConstraint("period_from", "period_to", name="uq_sales_week_period"),
    )

    items = relationship("SalesWeekItem", back_populates="snapshot", cascade="all, delete-orphan")


class SalesWeekItem(Base):
    """Viena prekė savaitės pardavimų statistikoje."""
    __tablename__ = "sales_week_items"

    id          = Column(Integer, primary_key=True, index=True)
    snapshot_id = Column(Integer, ForeignKey("sales_week_snapshots.id"), nullable=False, index=True)
    product_code = Column(String, nullable=False, index=True)
    product_name = Column(String, nullable=False)
    quantity     = Column(Float, default=0.0)

    snapshot = relationship("SalesWeekSnapshot", back_populates="items")


class PurchaseAnalysis(Base):
    """AI analizė – susieta su sandėlio snapshot ir naudotomis pardavimų savaitėmis."""
    __tablename__ = "purchase_analyses"

    id              = Column(Integer, primary_key=True, index=True)
    warehouse_snap_id = Column(Integer, ForeignKey("warehouse_snapshots.id"), nullable=False)
    generated_at    = Column(DateTime(timezone=True), server_default=func.now())
    weeks_used      = Column(Integer, default=0)   # kiek savaičių duomenų naudota
    # AI tekstai
    ai_overall      = Column(Text)                 # bendra situacija
    ai_illiquid     = Column(Text)                 # nelikvidžios prekės
    ai_overstock    = Column(Text)                 # per didelį likutį turinčios prekės
    ai_reorder      = Column(Text)                 # rekomendacijos užsakymui


class ClearanceItem(Base):
    """Išpardavimų sąrašas — prekių kodai, įkelti iš xlsb failo."""
    __tablename__ = "clearance_items"

    id           = Column(Integer, primary_key=True, index=True)
    product_code = Column(String, nullable=False, unique=True, index=True)
    uploaded_at  = Column(DateTime(timezone=True), server_default=func.now())

from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.sql import func
from backend.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    full_name = Column(String)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Module access flags
    # KAIP PRIDĖTI NAUJĄ MODULĮ:
    #  1. Čia pridėti: can_view_[modulis] = Column(Boolean, default=False)
    #  2. backend/auth.py — pridėti require_[modulis]() funkciją
    #  3. backend/routers/auth.py — UserCreate, UserUpdate ir _user_dict() papildyti nauju lauku
    #  4. frontend/js/app.js — ADMIN_MODULES masyve pridėti { key: 'can_view_[modulis]', label: '...' }
    #  5. frontend/js/app.js showApp() — pridėti if (mods.[modulis]) eilutę
    #  6. frontend/index.html — pridėti <li class="nav-item disabled" data-module="[modulis]">
    can_view_receivables = Column(Boolean, default=False)
    can_view_payables = Column(Boolean, default=False)
    can_view_sales = Column(Boolean, default=False)
    can_view_purchases = Column(Boolean, default=False)
    is_admin = Column(Boolean, default=False)

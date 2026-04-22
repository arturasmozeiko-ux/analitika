from dotenv import load_dotenv
import pathlib
load_dotenv(pathlib.Path(__file__).parent.parent / ".env", override=True)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

from backend.database import engine, Base
from backend.models import user, receivables, payables, sales, purchases, comment  # noqa: F401 – register models
from backend.routers import auth, receivables as receivables_router, payables as payables_router, sales as sales_router, purchases as purchases_router, comments as comments_router
from backend.auth import hash_password
from backend.database import SessionLocal
from backend.models.user import User

app = FastAPI(title="Analitika", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create tables
Base.metadata.create_all(bind=engine)

# Create default admin user if no users exist
def create_default_admin():
    db = SessionLocal()
    try:
        if db.query(User).count() == 0:
            admin = User(
                username="admin",
                password_hash=hash_password("admin123"),
                full_name="Administratorius",
                is_admin=True,
                can_view_receivables=True,
                can_view_payables=True,
                can_view_sales=True,
                can_view_purchases=True,
            )
            db.add(admin)
            db.commit()
            print("✓ Sukurtas numatytasis admin vartotojas (admin / admin123)")
    finally:
        db.close()

create_default_admin()


def migrate_db():
    """Papildo esamas lenteles naujais stulpeliais (jei jų dar nėra)."""
    from sqlalchemy import text, inspect as sa_inspect
    insp = sa_inspect(engine)
    with engine.connect() as conn:
        # warehouse_items: product_group_manager, product_category
        if "warehouse_items" in insp.get_table_names():
            existing = [c["name"] for c in insp.get_columns("warehouse_items")]
            if "product_group_manager" not in existing:
                conn.execute(text("ALTER TABLE warehouse_items ADD COLUMN product_group_manager VARCHAR"))
                print("✓ Pridėtas stulpelis: warehouse_items.product_group_manager")
            if "product_category" not in existing:
                conn.execute(text("ALTER TABLE warehouse_items ADD COLUMN product_category VARCHAR"))
                print("✓ Pridėtas stulpelis: warehouse_items.product_category")
            conn.commit()

migrate_db()

# Routers
app.include_router(auth.router)
app.include_router(receivables_router.router)
app.include_router(payables_router.router)
app.include_router(sales_router.router)
app.include_router(purchases_router.router)
app.include_router(comments_router.router)

# Serve frontend
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.exists(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/")
    def serve_index():
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

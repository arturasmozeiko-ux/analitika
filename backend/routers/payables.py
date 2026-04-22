import os
import shutil
from datetime import date
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks
from sqlalchemy import func
from sqlalchemy.orm import Session
from backend.database import get_db, SessionLocal
from backend.auth import require_payables
from backend.models.user import User
from backend.models.payables import PayablesSnapshot, PayablesSupplier, PayablesInvoice
from backend.services.payables_parser import parse_payables_excel
from backend.services.ai_analysis import analyze_payables

router = APIRouter(prefix="/api/payables", tags=["payables"])

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


def _snapshot_to_ai_dict(snapshot: PayablesSnapshot, db: Session) -> dict:
    suppliers = db.query(PayablesSupplier).filter(
        PayablesSupplier.snapshot_id == snapshot.id
    ).all()
    return {
        "snapshot_date": str(snapshot.report_date or snapshot.upload_date.date()),
        "total_balance": snapshot.total_balance,
        "total_overdue": snapshot.total_overdue,
        "total_suppliers": snapshot.total_suppliers,
        "overdue_suppliers": snapshot.overdue_suppliers,
        "overdue_invoices": snapshot.overdue_invoices,
        "suppliers": [
            {
                "supplier_code": s.supplier_code,
                "supplier_name": s.supplier_name,
                "balance": s.balance,
                "overdue_amount": s.overdue_amount,
                "has_overdue": s.has_overdue,
            }
            for s in suppliers
        ],
    }


def _run_ai_analysis(snapshot_id: int, db: Session):
    snapshot = db.query(PayablesSnapshot).filter(PayablesSnapshot.id == snapshot_id).first()
    if not snapshot:
        return

    current_dict = _snapshot_to_ai_dict(snapshot, db)

    previous_snapshot = (
        db.query(PayablesSnapshot)
        .filter(PayablesSnapshot.id < snapshot_id)
        .order_by(PayablesSnapshot.id.desc())
        .first()
    )
    previous_dict = _snapshot_to_ai_dict(previous_snapshot, db) if previous_snapshot else None

    try:
        analysis = analyze_payables(current_dict, previous_dict)
        snapshot.ai_analysis = analysis
        db.commit()
    except Exception as e:
        snapshot.ai_analysis = f"Klaida generuojant analizę: {str(e)}"
        db.commit()


@router.post("/upload")
async def upload_snapshot(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    report_date: Optional[str] = None,
    current_user: User = Depends(require_payables),
    db: Session = Depends(get_db),
):
    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Tik Excel failai (.xlsx, .xls)")

    import uuid
    ext = os.path.splitext(file.filename)[1].lower()
    file_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4()}{ext}")
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        parsed = parse_payables_excel(file_path)
    except Exception as e:
        os.remove(file_path)
        raise HTTPException(status_code=422, detail=f"Nepavyko perskaityti Excel failo: {str(e)}")

    footer = parsed["footer"]
    stats = parsed["stats"]

    parsed_report_date = None
    if report_date:
        try:
            parsed_report_date = date.fromisoformat(report_date)
        except ValueError:
            pass

    snapshot = PayablesSnapshot(
        file_name=file.filename,
        report_date=parsed_report_date,
        total_unpaid=footer["total_unpaid"],
        total_prepayments=footer["total_prepayments"],
        total_balance=footer["total_balance"],
        total_overdue=footer["total_overdue"],
        total_suppliers=stats["total_suppliers"],
        total_invoices=stats["total_invoices"],
        overdue_suppliers=stats["overdue_suppliers"],
        overdue_invoices=stats["overdue_invoices"],
    )
    db.add(snapshot)
    db.flush()

    for sup_data in parsed["suppliers"]:
        supplier = PayablesSupplier(
            snapshot_id=snapshot.id,
            supplier_code=sup_data["supplier_code"],
            supplier_name=sup_data["supplier_name"],
            balance=sup_data["balance"],
            overdue_amount=sup_data["overdue_amount"],
            has_overdue=sup_data["has_overdue"],
            has_prepayment=sup_data["has_prepayment"],
        )
        db.add(supplier)
        db.flush()

        for inv_data in sup_data["invoices"]:
            invoice = PayablesInvoice(
                supplier_id=supplier.id,
                invoice_number=inv_data["invoice_number"],
                supplier_invoice_number=inv_data["supplier_invoice_number"],
                invoice_datetime=inv_data["invoice_datetime"],
                payment_date=inv_data["payment_date"],
                payment_term=inv_data["payment_term"],
                amount=inv_data["amount"],
                days_remaining=inv_data["days_remaining"],
                is_overdue=inv_data["is_overdue"],
                is_prepayment=inv_data["is_prepayment"],
            )
            db.add(invoice)

    db.commit()
    db.refresh(snapshot)

    snapshot_id = snapshot.id
    background_tasks.add_task(_run_ai_analysis, snapshot_id, SessionLocal())

    return {
        "snapshot_id": snapshot.id,
        "file_name": snapshot.file_name,
        "total_balance": snapshot.total_balance,
        "total_overdue": snapshot.total_overdue,
        "total_suppliers": snapshot.total_suppliers,
        "total_invoices": snapshot.total_invoices,
        "overdue_suppliers": snapshot.overdue_suppliers,
        "message": "Failas įkeltas sėkmingai. AI analizė vykdoma...",
    }


@router.get("/snapshots")
def list_snapshots(
    current_user: User = Depends(require_payables),
    db: Session = Depends(get_db),
):
    snapshots = db.query(PayablesSnapshot).order_by(PayablesSnapshot.id.desc()).all()
    return [
        {
            "id": s.id,
            "file_name": s.file_name,
            "upload_date": s.upload_date,
            "report_date": s.report_date,
            "total_balance": s.total_balance,
            "total_overdue": s.total_overdue,
            "total_suppliers": s.total_suppliers,
            "overdue_suppliers": s.overdue_suppliers,
            "has_analysis": bool(s.ai_analysis),
        }
        for s in snapshots
    ]


@router.get("/snapshots/{snapshot_id}")
def get_snapshot(
    snapshot_id: int,
    current_user: User = Depends(require_payables),
    db: Session = Depends(get_db),
):
    snapshot = db.query(PayablesSnapshot).filter(PayablesSnapshot.id == snapshot_id).first()
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot nerastas")

    overdue_days_subq = (
        db.query(
            PayablesInvoice.supplier_id,
            func.min(PayablesInvoice.days_remaining).label("min_days")
        )
        .filter(PayablesInvoice.is_overdue == True)
        .group_by(PayablesInvoice.supplier_id)
        .subquery()
    )

    suppliers = (
        db.query(PayablesSupplier, overdue_days_subq.c.min_days)
        .outerjoin(overdue_days_subq, PayablesSupplier.id == overdue_days_subq.c.supplier_id)
        .filter(PayablesSupplier.snapshot_id == snapshot_id)
        .order_by(PayablesSupplier.balance.desc())
        .all()
    )

    return {
        "id": snapshot.id,
        "file_name": snapshot.file_name,
        "upload_date": snapshot.upload_date,
        "report_date": snapshot.report_date,
        "total_unpaid": snapshot.total_unpaid,
        "total_prepayments": snapshot.total_prepayments,
        "total_balance": snapshot.total_balance,
        "total_overdue": snapshot.total_overdue,
        "total_suppliers": snapshot.total_suppliers,
        "total_invoices": snapshot.total_invoices,
        "overdue_suppliers": snapshot.overdue_suppliers,
        "overdue_invoices": snapshot.overdue_invoices,
        "ai_analysis": snapshot.ai_analysis,
        "suppliers": [
            {
                "id": s.id,
                "supplier_code": s.supplier_code,
                "supplier_name": s.supplier_name,
                "balance": s.balance,
                "overdue_amount": s.overdue_amount,
                "has_overdue": s.has_overdue,
                "has_prepayment": s.has_prepayment,
                "max_overdue_days": abs(min_days) if min_days is not None else None,
            }
            for s, min_days in suppliers
        ],
    }


@router.get("/snapshots/{snapshot_id}/supplier/{supplier_id}")
def get_supplier_invoices(
    snapshot_id: int,
    supplier_id: int,
    current_user: User = Depends(require_payables),
    db: Session = Depends(get_db),
):
    supplier = db.query(PayablesSupplier).filter(
        PayablesSupplier.id == supplier_id,
        PayablesSupplier.snapshot_id == snapshot_id,
    ).first()
    if not supplier:
        raise HTTPException(status_code=404, detail="Tiekėjas nerastas")

    invoices = db.query(PayablesInvoice).filter(
        PayablesInvoice.supplier_id == supplier_id
    ).all()

    return {
        "supplier_code": supplier.supplier_code,
        "supplier_name": supplier.supplier_name,
        "balance": supplier.balance,
        "overdue_amount": supplier.overdue_amount,
        "invoices": [
            {
                "invoice_number": i.invoice_number,
                "supplier_invoice_number": i.supplier_invoice_number,
                "invoice_datetime": i.invoice_datetime,
                "payment_date": i.payment_date,
                "payment_term": i.payment_term,
                "amount": i.amount,
                "days_remaining": i.days_remaining,
                "is_overdue": i.is_overdue,
                "is_prepayment": i.is_prepayment,
            }
            for i in invoices
        ],
    }


@router.get("/snapshots/{snapshot_id}/analysis")
def get_analysis(
    snapshot_id: int,
    current_user: User = Depends(require_payables),
    db: Session = Depends(get_db),
):
    snapshot = db.query(PayablesSnapshot).filter(PayablesSnapshot.id == snapshot_id).first()
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot nerastas")
    return {"analysis": snapshot.ai_analysis, "ready": bool(snapshot.ai_analysis)}

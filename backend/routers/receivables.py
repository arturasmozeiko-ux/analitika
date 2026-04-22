import os
import shutil
from datetime import date
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks
from sqlalchemy import func
from sqlalchemy.orm import Session
from backend.database import get_db
from backend.auth import require_receivables
from backend.models.user import User
from backend.models.receivables import ReceivablesSnapshot, ReceivablesBuyer, ReceivablesInvoice
from backend.services.excel_parser import parse_receivables_excel
from backend.services.ai_analysis import analyze_receivables

router = APIRouter(prefix="/api/receivables", tags=["receivables"])

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


def _snapshot_to_ai_dict(snapshot: ReceivablesSnapshot, db: Session) -> dict:
    buyers = db.query(ReceivablesBuyer).filter(
        ReceivablesBuyer.snapshot_id == snapshot.id
    ).all()
    return {
        "snapshot_date": str(snapshot.report_date or snapshot.upload_date.date()),
        "total_balance": snapshot.total_balance,
        "total_overdue": snapshot.total_overdue,
        "total_buyers": snapshot.total_buyers,
        "overdue_buyers": snapshot.overdue_buyers,
        "overdue_invoices": snapshot.overdue_invoices,
        "buyers": [
            {
                "buyer_code": b.buyer_code,
                "buyer_name": b.buyer_name,
                "balance": b.balance,
                "overdue_amount": b.overdue_amount,
                "has_overdue": b.has_overdue,
                "invoices": [],
            }
            for b in buyers
        ],
    }


def _run_ai_analysis(snapshot_id: int, db: Session):
    snapshot = db.query(ReceivablesSnapshot).filter(ReceivablesSnapshot.id == snapshot_id).first()
    if not snapshot:
        return

    current_dict = _snapshot_to_ai_dict(snapshot, db)

    # Find the previous snapshot
    previous_snapshot = (
        db.query(ReceivablesSnapshot)
        .filter(ReceivablesSnapshot.id < snapshot_id)
        .order_by(ReceivablesSnapshot.id.desc())
        .first()
    )
    previous_dict = _snapshot_to_ai_dict(previous_snapshot, db) if previous_snapshot else None

    try:
        analysis = analyze_receivables(current_dict, previous_dict)
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
    current_user: User = Depends(require_receivables),
    db: Session = Depends(get_db),
):
    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Tik Excel failai (.xlsx, .xls)")

    # Save uploaded file (UUID vardas — apsauga nuo path traversal)
    import uuid
    ext = os.path.splitext(file.filename)[1].lower()
    file_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4()}{ext}")
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # Parse Excel
    try:
        parsed = parse_receivables_excel(file_path)
    except Exception as e:
        os.remove(file_path)
        raise HTTPException(status_code=422, detail=f"Nepavyko perskaityti Excel failo: {str(e)}")

    footer = parsed["footer"]
    stats = parsed["stats"]

    # Parse report_date
    parsed_report_date = None
    if report_date:
        try:
            parsed_report_date = date.fromisoformat(report_date)
        except ValueError:
            pass

    # Create snapshot
    snapshot = ReceivablesSnapshot(
        file_name=file.filename,
        report_date=parsed_report_date,
        total_unpaid=footer["total_unpaid"],
        total_prepayments=footer["total_prepayments"],
        total_balance=footer["total_balance"],
        total_overdue=footer["total_overdue"],
        total_buyers=stats["total_buyers"],
        total_invoices=stats["total_invoices"],
        overdue_buyers=stats["overdue_buyers"],
        overdue_invoices=stats["overdue_invoices"],
    )
    db.add(snapshot)
    db.flush()

    # Save buyers and invoices
    for buyer_data in parsed["buyers"]:
        buyer = ReceivablesBuyer(
            snapshot_id=snapshot.id,
            buyer_code=buyer_data["buyer_code"],
            buyer_name=buyer_data["buyer_name"],
            balance=buyer_data["balance"],
            overdue_amount=buyer_data["overdue_amount"],
            has_overdue=buyer_data["has_overdue"],
            has_prepayment=buyer_data["has_prepayment"],
        )
        db.add(buyer)
        db.flush()

        for inv_data in buyer_data["invoices"]:
            invoice = ReceivablesInvoice(
                buyer_id=buyer.id,
                invoice_number=inv_data["invoice_number"],
                invoice_datetime=inv_data["invoice_datetime"],
                payment_date=inv_data["payment_date"],
                payment_term_days=inv_data["payment_term_days"],
                amount=inv_data["amount"],
                days_remaining=inv_data["days_remaining"],
                is_overdue=inv_data["is_overdue"],
                is_prepayment=inv_data["is_prepayment"],
            )
            db.add(invoice)

    db.commit()
    db.refresh(snapshot)

    # Trigger AI analysis in background
    snapshot_id = snapshot.id
    background_tasks.add_task(_run_ai_analysis, snapshot_id, SessionLocal())

    return {
        "snapshot_id": snapshot.id,
        "file_name": snapshot.file_name,
        "total_balance": snapshot.total_balance,
        "total_overdue": snapshot.total_overdue,
        "total_buyers": snapshot.total_buyers,
        "total_invoices": snapshot.total_invoices,
        "overdue_buyers": snapshot.overdue_buyers,
        "message": "Failas įkeltas sėkmingai. AI analizė vykdoma...",
    }


@router.get("/snapshots")
def list_snapshots(
    current_user: User = Depends(require_receivables),
    db: Session = Depends(get_db),
):
    snapshots = db.query(ReceivablesSnapshot).order_by(ReceivablesSnapshot.id.desc()).all()
    return [
        {
            "id": s.id,
            "file_name": s.file_name,
            "upload_date": s.upload_date,
            "report_date": s.report_date,
            "total_balance": s.total_balance,
            "total_overdue": s.total_overdue,
            "total_buyers": s.total_buyers,
            "overdue_buyers": s.overdue_buyers,
            "has_analysis": bool(s.ai_analysis),
        }
        for s in snapshots
    ]


@router.get("/snapshots/{snapshot_id}")
def get_snapshot(
    snapshot_id: int,
    current_user: User = Depends(require_receivables),
    db: Session = Depends(get_db),
):
    snapshot = db.query(ReceivablesSnapshot).filter(ReceivablesSnapshot.id == snapshot_id).first()
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot nerastas")

    # Subquery: min days_remaining per buyer (most negative = oldest overdue invoice)
    overdue_days_subq = (
        db.query(
            ReceivablesInvoice.buyer_id,
            func.min(ReceivablesInvoice.days_remaining).label("min_days")
        )
        .filter(ReceivablesInvoice.is_overdue == True)
        .group_by(ReceivablesInvoice.buyer_id)
        .subquery()
    )

    buyers = (
        db.query(ReceivablesBuyer, overdue_days_subq.c.min_days)
        .outerjoin(overdue_days_subq, ReceivablesBuyer.id == overdue_days_subq.c.buyer_id)
        .filter(ReceivablesBuyer.snapshot_id == snapshot_id)
        .order_by(ReceivablesBuyer.balance.desc())
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
        "total_buyers": snapshot.total_buyers,
        "total_invoices": snapshot.total_invoices,
        "overdue_buyers": snapshot.overdue_buyers,
        "overdue_invoices": snapshot.overdue_invoices,
        "ai_analysis": snapshot.ai_analysis,
        "buyers": [
            {
                "id": b.id,
                "buyer_code": b.buyer_code,
                "buyer_name": b.buyer_name,
                "balance": b.balance,
                "overdue_amount": b.overdue_amount,
                "has_overdue": b.has_overdue,
                "has_prepayment": b.has_prepayment,
                "max_overdue_days": abs(min_days) if min_days is not None else None,
            }
            for b, min_days in buyers
        ],
    }


@router.get("/snapshots/{snapshot_id}/buyer/{buyer_id}")
def get_buyer_invoices(
    snapshot_id: int,
    buyer_id: int,
    current_user: User = Depends(require_receivables),
    db: Session = Depends(get_db),
):
    buyer = db.query(ReceivablesBuyer).filter(
        ReceivablesBuyer.id == buyer_id,
        ReceivablesBuyer.snapshot_id == snapshot_id,
    ).first()
    if not buyer:
        raise HTTPException(status_code=404, detail="Pirkėjas nerastas")

    invoices = db.query(ReceivablesInvoice).filter(
        ReceivablesInvoice.buyer_id == buyer_id
    ).all()

    return {
        "buyer_code": buyer.buyer_code,
        "buyer_name": buyer.buyer_name,
        "balance": buyer.balance,
        "overdue_amount": buyer.overdue_amount,
        "invoices": [
            {
                "invoice_number": i.invoice_number,
                "invoice_datetime": i.invoice_datetime,
                "payment_date": i.payment_date,
                "payment_term_days": i.payment_term_days,
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
    current_user: User = Depends(require_receivables),
    db: Session = Depends(get_db),
):
    snapshot = db.query(ReceivablesSnapshot).filter(ReceivablesSnapshot.id == snapshot_id).first()
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot nerastas")
    return {"analysis": snapshot.ai_analysis, "ready": bool(snapshot.ai_analysis)}


# Fix: import SessionLocal for background tasks
from backend.database import SessionLocal

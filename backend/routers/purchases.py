import os
from collections import defaultdict
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks, Body, Query
from sqlalchemy.orm import Session
from backend.database import get_db, SessionLocal
from backend.auth import require_purchases
from backend.models.user import User
from backend.models.purchases import (
    PurchaseCategory, WarehouseSnapshot, WarehouseItem,
    SalesWeekSnapshot, SalesWeekItem, PurchaseAnalysis, ClearanceItem,
)
from backend.services.purchases_parser import (
    parse_categories_excel, parse_warehouse_excel, parse_sales_week_excel, parse_annual_sales_excel,
    parse_clearance_file,
)
from backend.services.purchases_analysis import analyze_overall, analyze_illiquid, analyze_overstock, analyze_reorder

router = APIRouter(prefix="/api/purchases", tags=["purchases"])
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ── HELPERS ────────────────────────────────────────────────────────────────────

def _build_category_map(db: Session) -> dict:
    """Returns {code: name} for all categories."""
    cats = db.query(PurchaseCategory).all()
    return {c.code: c.name for c in cats}


def _months_back(d: date, n: int) -> date:
    """Grąžina datą lygiai n mėnesiais atgal."""
    import calendar
    month = d.month - n
    year  = d.year
    while month <= 0:
        month += 12
        year  -= 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last_day))


def _three_months_back(d: date) -> date:
    return _months_back(d, 3)


def _twelve_months_back(d: date) -> date:
    return _months_back(d, 12)


def _get_new_product_codes(snap_date: date, db: Session) -> set:
    """Grąžina prekių kodus, kurie laikomi NAUJOMIS prekėmis ir NETURI patekti į
    'Visiškai nejuda' sąrašą.

    Prekė laikoma NAUJA jei tenkina ABU kriterijus vienu metu:
      1. Ji NEATSIRADO nė viename ankstesniame warehouse snapshot'e
         (t.y. pirmą kartą matoma dabartiniu snapshot'u)
      2. Ji NIEKADA nebuvo parduota jokiame pardavimų periode

    Išimtis: jei tai PIRMASIS snapshot'as (nėra ankstesnių) — nefiltruojama,
    nes neturime pakankamai istorijos nustatyti kurios prekės yra naujos.
    """
    # Patikrinti ar egzistuoja bent vienas ankstesnis snapshot'as
    has_prev = db.query(WarehouseSnapshot).filter(
        WarehouseSnapshot.snap_date < snap_date
    ).first() is not None

    if not has_prev:
        # Pirmasis snapshot'as — negalime nustatyti kurios prekės naujos,
        # todėl rodome visas su 0 pardavimų per 12 mėn.
        return set()

    # Prekės, kurios egzistavo bent viename ANKSTESNIAME snapshot'e
    in_prev_snap = {
        r.product_code
        for r in (
            db.query(WarehouseItem.product_code)
            .join(WarehouseSnapshot, WarehouseItem.snapshot_id == WarehouseSnapshot.id)
            .filter(WarehouseSnapshot.snap_date < snap_date)
            .distinct()
            .all()
        )
    }

    # Prekės, kurios turi bent vieną pardavimą istorijoje (bet kuriame periode)
    ever_sold = {
        r.product_code
        for r in db.query(SalesWeekItem.product_code).distinct().all()
    }

    # Prekės, kurios yra TIKTAI dabartiniame snapshot'e IR niekada neparduotos
    current_codes = {
        r.product_code
        for r in (
            db.query(WarehouseItem.product_code)
            .join(WarehouseSnapshot, WarehouseItem.snapshot_id == WarehouseSnapshot.id)
            .filter(WarehouseSnapshot.snap_date == snap_date)
            .distinct()
            .all()
        )
    }

    return current_codes - in_prev_snap - ever_sold


def _calc_total_days(week_snap_ids: list[int], db: Session) -> float:
    """Skaičiuoja faktinių dienų skaičių pagal visų periodų datas."""
    if not week_snap_ids:
        return 0.0
    snaps = db.query(SalesWeekSnapshot).filter(SalesWeekSnapshot.id.in_(week_snap_ids)).all()
    from datetime import timedelta
    total = sum((s.period_to - s.period_from).days + 1 for s in snaps)
    return float(total)


def _build_analysis_data(warehouse_snap: WarehouseSnapshot, week_snap_ids: list[int], db: Session) -> dict:
    """Build the data dict for AI analysis."""
    items = db.query(WarehouseItem).filter(WarehouseItem.snapshot_id == warehouse_snap.id).all()
    cat_map = _build_category_map(db)

    # Praėjusio snapshoto duomenys savaitiniam palyginimui
    prev_snap = db.query(WarehouseSnapshot).filter(
        WarehouseSnapshot.snap_date < warehouse_snap.snap_date
    ).order_by(WarehouseSnapshot.snap_date.desc()).first()

    prev_qty: dict[str, float] = {}
    prev_value: dict[str, float] = {}
    if prev_snap:
        prev_items = db.query(WarehouseItem).filter(WarehouseItem.snapshot_id == prev_snap.id).all()
        prev_qty   = {pi.product_code: pi.quantity    for pi in prev_items}
        prev_value = {pi.product_code: pi.total_value for pi in prev_items}

    # 3 mėnesių pardavimai — greičio skaičiavimui
    total_days = _calc_total_days(week_snap_ids, db)
    sales_qty: dict[str, float] = defaultdict(float)
    if week_snap_ids:
        week_items = db.query(SalesWeekItem).filter(SalesWeekItem.snapshot_id.in_(week_snap_ids)).all()
        for wi in week_items:
            sales_qty[wi.product_code] += wi.quantity

    # 12 mėnesių pardavimai — nelikvidumui nustatyti
    cutoff_12m = _twelve_months_back(warehouse_snap.snap_date)
    snaps_12m = db.query(SalesWeekSnapshot).filter(
        SalesWeekSnapshot.period_from >= cutoff_12m,
        SalesWeekSnapshot.period_to   <= warehouse_snap.snap_date,
    ).all()
    ids_12m = [s.id for s in snaps_12m]
    sales_qty_12m: dict[str, float] = defaultdict(float)
    if ids_12m:
        items_12m = db.query(SalesWeekItem).filter(SalesWeekItem.snapshot_id.in_(ids_12m)).all()
        for wi in items_12m:
            sales_qty_12m[wi.product_code] += wi.quantity

    # Prekių, kurios egzistuoja sandėlyje daugiau nei 12 mėn.
    new_codes = _get_new_product_codes(warehouse_snap.snap_date, db)

    # Per-product analysis
    illiquid_none    = []  # 0 pardavimų per 12m
    illiquid_partial = []  # ≤20% likučio parduota per 12m
    overstock = []
    low_stock = []
    cat_agg: dict[str, dict] = defaultdict(lambda: {"value": 0.0, "qty": 0.0, "sold": 0.0})

    for it in items:
        daily_avg = (sales_qty.get(it.product_code, 0.0) / total_days) if total_days > 0 else 0.0
        days_stock = (it.quantity / daily_avg) if daily_avg > 0 else float("inf")

        cat_name = cat_map.get(it.category_code, it.category_code)
        cat_agg[it.category_code]["value"] += it.total_value
        cat_agg[it.category_code]["qty"]   += it.quantity
        cat_agg[it.category_code]["sold"]  += sales_qty.get(it.product_code, 0.0)

        sold_12m = sales_qty_12m.get(it.product_code, 0.0)

        qty_delta   = round(it.quantity - prev_qty[it.product_code], 2) if it.product_code in prev_qty else None
        value_delta = round(it.total_value - prev_value[it.product_code], 2) if it.product_code in prev_value else None

        row = {
            "code":        it.product_code,
            "name":        it.product_name,
            "category":    cat_name,
            "qty":         round(it.quantity, 2),
            "value":       round(it.total_value, 2),
            "days_stock":  round(days_stock, 0) if days_stock != float("inf") else 9999,
            "daily_avg":   round(daily_avg, 3),
            "sold_12m":    round(sold_12m, 2),
            "qty_delta":   qty_delta,
            "value_delta": value_delta,
        }

        if it.quantity > 0:
            if sold_12m == 0 and it.product_code not in new_codes:
                illiquid_none.append(row)
            elif sold_12m > 0 and sold_12m / it.quantity <= 0.20:
                row["sold_pct"] = round(sold_12m / it.quantity * 100, 1)
                illiquid_partial.append(row)
            elif total_days > 0 and daily_avg > 0 and days_stock > 84:
                overstock.append(row)
            elif total_days > 0 and daily_avg > 0 and days_stock < 14:
                low_stock.append(row)
        elif total_days > 0:
            if daily_avg > 0 and days_stock > 84:
                overstock.append(row)
            elif daily_avg > 0 and days_stock < 14:
                low_stock.append(row)

    # Sort
    illiquid_none.sort(key=lambda x: -x["value"])
    illiquid_partial.sort(key=lambda x: -x["value"])
    overstock.sort(key=lambda x: -x["value"])
    low_stock.sort(key=lambda x: x["days_stock"])

    # Top categories by value
    categories = []
    for code, agg in sorted(cat_agg.items(), key=lambda x: -x[1]["value"]):
        daily_cat_avg = (agg["sold"] / total_days) if total_days > 0 else 0
        days_stock_cat = round(agg["qty"] / daily_cat_avg, 0) if daily_cat_avg > 0 else 9999
        categories.append({
            "code":        code,
            "name":        cat_map.get(code, code),
            "value":       round(agg["value"], 2),
            "qty":         round(agg["qty"], 2),
            "days_stock":  days_stock_cat,
        })

    # Savaitinio pokyčio suvestinė
    weekly_delta = None
    if prev_snap:
        cur_codes  = {it.product_code for it in items}
        prev_codes = set(prev_qty.keys())

        grown    = [(it.product_code, it.product_name, cat_map.get(it.category_code, it.category_code),
                     round(it.quantity - prev_qty[it.product_code], 2),
                     round(it.total_value - prev_value.get(it.product_code, 0), 2))
                    for it in items
                    if it.product_code in prev_qty and it.quantity > prev_qty[it.product_code]]
        shrunk   = [(it.product_code, it.product_name, cat_map.get(it.category_code, it.category_code),
                     round(it.quantity - prev_qty[it.product_code], 2),
                     round(it.total_value - prev_value.get(it.product_code, 0), 2))
                    for it in items
                    if it.product_code in prev_qty and it.quantity < prev_qty[it.product_code]]
        new_items = [(it.product_code, it.product_name, cat_map.get(it.category_code, it.category_code),
                      round(it.quantity, 2), round(it.total_value, 2))
                     for it in items if it.product_code not in prev_codes]
        gone      = [(c, prev_qty[c]) for c in prev_codes - cur_codes]

        grown.sort(key=lambda x: -x[4])    # pagal vertės augimą
        shrunk.sort(key=lambda x: x[4])    # pagal didžiausią vertės kritimą

        weekly_delta = {
            "prev_date":       str(prev_snap.snap_date),
            "value_change":    round(warehouse_snap.total_value - prev_snap.total_value, 2),
            "items_change":    warehouse_snap.total_items - prev_snap.total_items,
            "grown_count":     len(grown),
            "shrunk_count":    len(shrunk),
            "new_count":       len(new_items),
            "gone_count":      len(gone),
            "top_grown":       [{"code": r[0], "name": r[1], "category": r[2],
                                 "qty_delta": r[3], "value_delta": r[4]} for r in grown[:20]],
            "top_shrunk":      [{"code": r[0], "name": r[1], "category": r[2],
                                 "qty_delta": r[3], "value_delta": r[4]} for r in shrunk[:20]],
            "new_items":       [{"code": r[0], "name": r[1], "category": r[2],
                                 "qty": r[3], "value": r[4]} for r in new_items[:20]],
            "gone_items":      [{"code": r[0], "qty_was": r[1]} for r in gone[:20]],
        }

    return {
        "snap_date":        str(warehouse_snap.snap_date),
        "total_days":       total_days,
        "weeks_used":       round(total_days / 7, 1),
        "total_value":      warehouse_snap.total_value,
        "total_items":      warehouse_snap.total_items,
        "categories":       categories[:50],
        "illiquid_none":    illiquid_none,
        "illiquid_partial": illiquid_partial,
        "overstock":        overstock,
        "low_stock":        low_stock,
        "weekly_delta":     weekly_delta,
    }


def _run_ai_analysis(analysis_id: int):
    import pathlib
    from dotenv import load_dotenv
    load_dotenv(pathlib.Path(__file__).parent.parent.parent / ".env", override=True)
    db = SessionLocal()
    try:
        analysis = db.query(PurchaseAnalysis).filter(PurchaseAnalysis.id == analysis_id).first()
        if not analysis:
            return
        snap = db.query(WarehouseSnapshot).filter(WarehouseSnapshot.id == analysis.warehouse_snap_id).first()
        if not snap:
            return

        # Naudoti tik paskutinius 3 mėnesius prieš sandėlio datą
        cutoff = _three_months_back(snap.snap_date)
        week_snaps = db.query(SalesWeekSnapshot).filter(
            SalesWeekSnapshot.period_from >= cutoff,
            SalesWeekSnapshot.period_to   <= snap.snap_date,
        ).order_by(SalesWeekSnapshot.period_from).all()
        week_snap_ids = [s.id for s in week_snaps]

        data = _build_analysis_data(snap, week_snap_ids, db)

        analysis.ai_overall = analyze_overall(data)
        db.commit()

        analysis.ai_illiquid = analyze_illiquid(data)
        db.commit()

        analysis.ai_overstock = analyze_overstock(data)
        db.commit()

        analysis.ai_reorder = analyze_reorder(data)
        analysis.weeks_used = len(week_snap_ids)
        db.commit()

    except Exception as e:
        analysis = db.query(PurchaseAnalysis).filter(PurchaseAnalysis.id == analysis_id).first()
        if analysis:
            analysis.ai_overall = (analysis.ai_overall or "") + f"\n\nKlaida: {str(e)}"
            db.commit()
    finally:
        db.close()


# ── CATEGORIES ─────────────────────────────────────────────────────────────────

@router.post("/categories/upload")
async def upload_categories(
    file: UploadFile = File(...),
    current_user: User = Depends(require_purchases),
    db: Session = Depends(get_db),
):
    path = os.path.join(UPLOAD_DIR, f"cat_{file.filename}")
    with open(path, "wb") as f:
        f.write(await file.read())

    try:
        rows = parse_categories_excel(path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Klaida skaitant failą: {e}")

    # Replace all categories
    db.query(PurchaseCategory).delete()
    for r in rows:
        db.add(PurchaseCategory(**r))
    db.commit()

    return {"message": f"Įkeltos {len(rows)} kategorijos", "total": len(rows)}


@router.get("/categories")
def get_categories(
    current_user: User = Depends(require_purchases),
    db: Session = Depends(get_db),
):
    cats = db.query(PurchaseCategory).order_by(PurchaseCategory.code).all()
    return [{"code": c.code, "name": c.name, "parent_code": c.parent_code, "level": c.level} for c in cats]


# ── WAREHOUSE ──────────────────────────────────────────────────────────────────

@router.post("/warehouse/upload")
async def upload_warehouse(
    file: UploadFile = File(...),
    snap_date: str = Query(..., description="Sandėlio data (YYYY-MM-DD)"),
    current_user: User = Depends(require_purchases),
    db: Session = Depends(get_db),
):
    try:
        d = date.fromisoformat(snap_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Neteisinga data (formatas: YYYY-MM-DD)")

    path = os.path.join(UPLOAD_DIR, f"wh_{file.filename}")
    with open(path, "wb") as f:
        f.write(await file.read())

    try:
        rows = parse_warehouse_excel(path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Klaida skaitant failą: {e}")

    # Mark previous as not latest
    db.query(WarehouseSnapshot).update({"is_latest": False})

    total_value = sum(r["total_value"] for r in rows)
    snap = WarehouseSnapshot(
        file_name=file.filename,
        snap_date=d,
        total_items=len(rows),
        total_value=round(total_value, 2),
        is_latest=True,
    )
    db.add(snap)
    db.flush()

    for r in rows:
        db.add(WarehouseItem(snapshot_id=snap.id, **r))
    db.commit()

    return {
        "snapshot_id": snap.id,
        "snap_date": str(d),
        "total_items": len(rows),
        "total_value": round(total_value, 2),
    }


@router.get("/warehouse/snapshots")
def list_warehouse_snapshots(
    current_user: User = Depends(require_purchases),
    db: Session = Depends(get_db),
):
    snaps = db.query(WarehouseSnapshot).order_by(WarehouseSnapshot.snap_date.desc()).all()
    result = []
    for s in snaps:
        analysis = db.query(PurchaseAnalysis).filter(
            PurchaseAnalysis.warehouse_snap_id == s.id
        ).order_by(PurchaseAnalysis.id.desc()).first()
        has_analysis = bool(analysis and analysis.ai_overall)
        all_done = bool(analysis and analysis.ai_overall and analysis.ai_illiquid
                        and analysis.ai_overstock and analysis.ai_reorder)
        result.append({
            "id": s.id,
            "snap_date": str(s.snap_date),
            "file_name": s.file_name,
            "total_items": s.total_items,
            "total_value": s.total_value,
            "is_latest": s.is_latest,
            "has_analysis": has_analysis,
            "analysis_complete": all_done,
            "analysis_id": analysis.id if analysis else None,
        })
    return result


@router.get("/warehouse/illiquid-history")
def get_illiquid_history(
    current_user: User = Depends(require_purchases),
    db: Session = Depends(get_db),
):
    """Grąžina visų sandėlio snapshot'ų nelikvidžių prekių skaičių ir vertę."""
    snaps = db.query(WarehouseSnapshot).order_by(WarehouseSnapshot.snap_date.desc()).all()
    result = []
    for snap in snaps:
        # 12 mėnesių pardavimai šio snapshot'o atžvilgiu
        cutoff_12m = _twelve_months_back(snap.snap_date)
        snaps_12m = db.query(SalesWeekSnapshot).filter(
            SalesWeekSnapshot.period_from >= cutoff_12m,
            SalesWeekSnapshot.period_to   <= snap.snap_date,
        ).all()
        ids_12m = [s.id for s in snaps_12m]
        sales_qty_12m: dict[str, float] = defaultdict(float)
        if ids_12m:
            items_12m = db.query(SalesWeekItem).filter(SalesWeekItem.snapshot_id.in_(ids_12m)).all()
            for wi in items_12m:
                sales_qty_12m[wi.product_code] += wi.quantity

        items = db.query(WarehouseItem).filter(WarehouseItem.snapshot_id == snap.id).all()
        new_codes = _get_new_product_codes(snap.snap_date, db)

        none_count = 0;    none_value = 0.0
        partial_count = 0; partial_value = 0.0
        for it in items:
            if it.quantity <= 0:
                continue
            sold_12m = sales_qty_12m.get(it.product_code, 0.0)
            if sold_12m == 0 and it.product_code not in new_codes:
                none_count += 1
                none_value += it.total_value
            elif sold_12m > 0 and sold_12m / it.quantity <= 0.20:
                partial_count += 1
                partial_value += it.total_value

        result.append({
            "snap_id":       snap.id,
            "snap_date":     str(snap.snap_date),
            "none_count":    none_count,
            "none_value":    round(none_value, 2),
            "partial_count": partial_count,
            "partial_value": round(partial_value, 2),
        })
    return result


@router.get("/clearance/status")
def get_clearance_status(
    current_user: User = Depends(require_purchases),
    db: Session = Depends(get_db),
):
    """Grąžina išpardavimų sąrašo būseną."""
    count = db.query(ClearanceItem).count()
    if count == 0:
        return {"count": 0, "uploaded_at": None}
    last = db.query(ClearanceItem).order_by(ClearanceItem.id.desc()).first()
    return {"count": count, "uploaded_at": last.uploaded_at.isoformat() if last.uploaded_at else None}


@router.post("/clearance/upload")
async def upload_clearance(
    file: UploadFile = File(...),
    current_user: User = Depends(require_purchases),
    db: Session = Depends(get_db),
):
    """Įkelia išpardavimų sąrašą (.xlsb arba .xlsx). Pakeičia visą esamą sąrašą."""
    suffix = "." + file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ".xlsb"
    import tempfile, shutil
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name
    try:
        codes = parse_clearance_file(tmp_path)
    except Exception as e:
        import os; os.unlink(tmp_path)
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        import os
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    # Replace all existing clearance items
    db.query(ClearanceItem).delete()
    for code in set(codes):
        db.add(ClearanceItem(product_code=code))
    db.commit()
    return {"count": len(set(codes)), "message": f"Įkelta {len(set(codes))} išpardavimų prekių"}


@router.get("/warehouse/product-illiquid-history")
def get_product_illiquid_history(
    product_code: str = Query(...),
    current_user: User = Depends(require_purchases),
    db: Session = Depends(get_db),
):
    """Grąžina vienos prekės nelikvidumo istoriją per visus snapshot'us.

    Kiekvienam snapshot'ui grąžina: datą, tipą (none/partial/null) ir kiekį.
    Tai leidžia frontend'e parodyti kada ir kiek laiko prekė buvo nejudanti.
    """
    # 1. Visi snapshot'ai chronologine tvarka
    snaps = db.query(WarehouseSnapshot).order_by(WarehouseSnapshot.snap_date.asc()).all()
    if not snaps:
        return []

    # 2. Visos šios prekės eilutės visuose snapshot'uose (vienas query)
    all_items: dict[int, WarehouseItem] = {
        wi.snapshot_id: wi
        for wi in db.query(WarehouseItem)
        .filter(WarehouseItem.product_code == product_code)
        .all()
    }
    if not all_items:
        return []

    # 3. Visi šios prekės pardavimai istorijoje (vienas query)
    all_sales = (
        db.query(SalesWeekItem, SalesWeekSnapshot.period_from, SalesWeekSnapshot.period_to)
        .join(SalesWeekSnapshot, SalesWeekItem.snapshot_id == SalesWeekSnapshot.id)
        .filter(SalesWeekItem.product_code == product_code)
        .all()
    )
    ever_sold = len(all_sales) > 0

    # 4. Snapshot'ai, kuriuose prekė egzistavo (chronologine tvarka)
    snap_ids_with_item = sorted(all_items.keys())
    first_snap_id = snap_ids_with_item[0] if snap_ids_with_item else None

    result = []
    for snap in snaps:
        wi = all_items.get(snap.id)
        if not wi or wi.quantity <= 0:
            continue

        # Ar prekė "nauja" šiame snapshot'e?
        # Nauja = pirmą kartą matoma IR niekada neparduota
        is_new_in_this_snap = (snap.id == first_snap_id and not ever_sold)

        # 12 mėn. pardavimai iki šio snapshot'o datos
        cutoff = _twelve_months_back(snap.snap_date)
        sold_12m = sum(
            si.quantity
            for si, period_from, period_to in all_sales
            if period_from >= cutoff and period_to <= snap.snap_date
        )

        # Nelikvidumo tipas
        if sold_12m == 0 and not is_new_in_this_snap:
            illiquid_type = "none"
        elif sold_12m > 0 and sold_12m / wi.quantity <= 0.20:
            illiquid_type = "partial"
        else:
            illiquid_type = None

        result.append({
            "snap_date":    str(snap.snap_date),
            "illiquid_type": illiquid_type,
            "quantity":     wi.quantity,
            "total_value":  round(wi.total_value, 2),
            "sold_12m":     round(sold_12m, 2),
        })

    return result


@router.get("/warehouse/snapshots/{snapshot_id}/items")
def get_warehouse_items(
    snapshot_id: int,
    category: str = Query(None),
    search: str = Query(None),
    sort: str = Query("value_desc"),
    limit: int = Query(200),
    offset: int = Query(0),
    current_user: User = Depends(require_purchases),
    db: Session = Depends(get_db),
):
    snap = db.query(WarehouseSnapshot).filter(WarehouseSnapshot.id == snapshot_id).first()
    if not snap:
        raise HTTPException(status_code=404, detail="Sandėlio snapshot nerastas")

    items = db.query(WarehouseItem).filter(WarehouseItem.snapshot_id == snapshot_id)
    if category:
        items = items.filter(WarehouseItem.category_code.like(f"{category}%"))
    items = items.all()

    # 3 mėnesių pardavimai — greičio skaičiavimui
    cutoff = _three_months_back(snap.snap_date)
    week_snaps = db.query(SalesWeekSnapshot).filter(
        SalesWeekSnapshot.period_from >= cutoff,
        SalesWeekSnapshot.period_to   <= snap.snap_date,
    ).all()
    week_snap_ids = [s.id for s in week_snaps]
    total_days = _calc_total_days(week_snap_ids, db)

    sales_qty: dict[str, float] = defaultdict(float)
    if week_snap_ids:
        week_items = db.query(SalesWeekItem).filter(SalesWeekItem.snapshot_id.in_(week_snap_ids)).all()
        for wi in week_items:
            sales_qty[wi.product_code] += wi.quantity

    # 12 mėnesių pardavimai — nelikvidumui
    cutoff_12m = _twelve_months_back(snap.snap_date)
    snaps_12m = db.query(SalesWeekSnapshot).filter(
        SalesWeekSnapshot.period_from >= cutoff_12m,
        SalesWeekSnapshot.period_to   <= snap.snap_date,
    ).all()
    ids_12m = [s.id for s in snaps_12m]
    sales_qty_12m: dict[str, float] = defaultdict(float)
    if ids_12m:
        items_12m = db.query(SalesWeekItem).filter(SalesWeekItem.snapshot_id.in_(ids_12m)).all()
        for wi in items_12m:
            sales_qty_12m[wi.product_code] += wi.quantity

    clearance_codes: set[str] = {ci.product_code for ci in db.query(ClearanceItem.product_code).all()}
    cat_map = _build_category_map(db)

    # Prekių, kurios egzistuoja sandėlyje daugiau nei 12 mėn. (naujoms nepriskiriamas "none")
    new_codes = _get_new_product_codes(snap.snap_date, db)

    # Praeitos savaitės snapshot — ankstesnis pagal datą
    prev_snap = db.query(WarehouseSnapshot).filter(
        WarehouseSnapshot.snap_date < snap.snap_date
    ).order_by(WarehouseSnapshot.snap_date.desc()).first()

    prev_qty: dict[str, float] = {}
    if prev_snap:
        prev_items = db.query(WarehouseItem).filter(WarehouseItem.snapshot_id == prev_snap.id).all()
        prev_qty = {pi.product_code: pi.quantity for pi in prev_items}

    rows = []
    for it in items:
        if search and search.lower() not in it.product_name.lower() and search.lower() not in it.product_code.lower():
            continue
        daily_avg = (sales_qty.get(it.product_code, 0.0) / total_days) if total_days > 0 else 0.0
        days_stock = round(it.quantity / daily_avg, 0) if daily_avg > 0 else None
        sold_12m = sales_qty_12m.get(it.product_code, 0.0)

        if it.quantity > 0:
            if sold_12m == 0 and it.product_code not in new_codes:
                illiquid_type = "none"
            elif sold_12m > 0 and sold_12m / it.quantity <= 0.20:
                illiquid_type = "partial"
            else:
                illiquid_type = None
        else:
            illiquid_type = None

        if it.product_code in prev_qty:
            qty_delta = round(it.quantity - prev_qty[it.product_code], 2)
        elif prev_snap:
            qty_delta = round(it.quantity, 2)  # nauja prekė — visa suma kaip prieaugis
        else:
            qty_delta = None  # nėra ankstesnio snapshoto

        rows.append({
            "product_code":         it.product_code,
            "category_code":        it.category_code or "",
            "category_name":        cat_map.get(it.category_code or "", it.category_code or ""),
            "product_name":         it.product_name,
            "quantity":             it.quantity,
            "unit_cost":            it.unit_cost,
            "total_value":          it.total_value,
            "daily_avg":            round(daily_avg, 3),
            "days_stock":           days_stock,
            "total_sold":           round(sales_qty.get(it.product_code, 0.0), 2),
            "sales_12m":            round(sold_12m, 2),
            "illiquid_type":        illiquid_type,
            "qty_delta":            qty_delta,
            "prev_snap_date":       str(prev_snap.snap_date) if prev_snap else None,
            "product_group_manager": it.product_group_manager or "",
            "product_category":      it.product_category or "",
            "is_clearance":          it.product_code in clearance_codes,
        })

    # Sort
    sort_map = {
        "value_desc":  (lambda x: -x["total_value"]),
        "value_asc":   (lambda x:  x["total_value"]),
        "weeks_asc":   (lambda x:  x["days_stock"] if x["days_stock"] is not None else 9999),
        "weeks_desc":  (lambda x: -(x["days_stock"] if x["days_stock"] is not None else 0)),
        "sold_desc":   (lambda x: -x["total_sold"]),
        "name_asc":    (lambda x:  x["product_name"]),
    }
    rows.sort(key=sort_map.get(sort, sort_map["value_desc"]))

    total = len(rows)
    return {"total": total, "total_days": total_days, "items": rows[offset:offset+limit]}


@router.get("/warehouse/snapshots/{snapshot_id}/filter-options")
def get_filter_options(
    snapshot_id: int,
    current_user: User = Depends(require_purchases),
    db: Session = Depends(get_db),
):
    """Grąžina unikalias filtrų reikšmes (vadovai, kategorijos) pasirinktam snapshot'ui."""
    items = db.query(WarehouseItem).filter(WarehouseItem.snapshot_id == snapshot_id).all()
    managers   = sorted({i.product_group_manager for i in items if i.product_group_manager})
    categories = sorted({i.product_category      for i in items if i.product_category})
    return {"managers": managers, "categories": categories}


# ── SALES WEEKS ────────────────────────────────────────────────────────────────

@router.post("/sales-weeks/upload")
async def upload_sales_week(
    file: UploadFile = File(...),
    period_from: str = Query(..., description="Periodo pradžia (YYYY-MM-DD)"),
    period_to: str = Query(..., description="Periodo pabaiga (YYYY-MM-DD)"),
    current_user: User = Depends(require_purchases),
    db: Session = Depends(get_db),
):
    try:
        d_from = date.fromisoformat(period_from)
        d_to   = date.fromisoformat(period_to)
    except ValueError:
        raise HTTPException(status_code=400, detail="Neteisinga data (formatas: YYYY-MM-DD)")

    path = os.path.join(UPLOAD_DIR, f"sw_{file.filename}")
    with open(path, "wb") as f:
        f.write(await file.read())

    try:
        rows = parse_sales_week_excel(path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Klaida skaitant failą: {e}")

    # If this period already exists — replace
    existing = db.query(SalesWeekSnapshot).filter(
        SalesWeekSnapshot.period_from == d_from,
        SalesWeekSnapshot.period_to   == d_to,
    ).first()
    if existing:
        db.query(SalesWeekItem).filter(SalesWeekItem.snapshot_id == existing.id).delete()
        db.delete(existing)
        db.flush()

    total_qty = sum(r["quantity"] for r in rows)
    snap = SalesWeekSnapshot(
        file_name=file.filename,
        period_from=d_from,
        period_to=d_to,
        total_items=len(rows),
        total_qty=round(total_qty, 2),
    )
    db.add(snap)
    db.flush()

    for r in rows:
        db.add(SalesWeekItem(snapshot_id=snap.id, **r))
    db.commit()

    return {
        "snapshot_id": snap.id,
        "period_from": str(d_from),
        "period_to": str(d_to),
        "total_items": len(rows),
        "total_qty": round(total_qty, 2),
    }


@router.get("/sales-weeks")
def list_sales_weeks(
    current_user: User = Depends(require_purchases),
    db: Session = Depends(get_db),
):
    snaps = db.query(SalesWeekSnapshot).order_by(SalesWeekSnapshot.period_from.desc()).all()
    return [{"id": s.id, "period_from": str(s.period_from), "period_to": str(s.period_to),
             "file_name": s.file_name, "total_items": s.total_items, "total_qty": s.total_qty} for s in snaps]


@router.post("/sales-weeks/upload-annual")
async def upload_annual_sales(
    file: UploadFile = File(...),
    year: int = Query(..., description="Metai (pvz. 2025)"),
    current_user: User = Depends(require_purchases),
    db: Session = Depends(get_db),
):
    """Įkelia metinę pardavimų statistiką — visi mėnesiai iš vieno failo."""
    path = os.path.join(UPLOAD_DIR, f"annual_{year}_{file.filename}")
    with open(path, "wb") as f:
        f.write(await file.read())

    try:
        by_month = parse_annual_sales_excel(path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Klaida skaitant failą: {e}")

    from calendar import monthrange
    from datetime import date

    MONTHS_LT = ["","Sausis","Vasaris","Kovas","Balandis","Gegužė",
                 "Birželis","Liepa","Rugpjūtis","Rugsėjis","Spalis","Lapkritis","Gruodis"]

    imported = []
    skipped  = []

    for month_num, items in by_month.items():
        if not items:
            skipped.append(month_num)
            continue

        d_from = date(year, month_num, 1)
        last_day = monthrange(year, month_num)[1]
        d_to = date(year, month_num, last_day)

        # Replace if exists
        existing = db.query(SalesWeekSnapshot).filter(
            SalesWeekSnapshot.period_from == d_from,
            SalesWeekSnapshot.period_to   == d_to,
        ).first()
        if existing:
            db.query(SalesWeekItem).filter(SalesWeekItem.snapshot_id == existing.id).delete()
            db.delete(existing)
            db.flush()

        total_qty = sum(i["quantity"] for i in items)
        snap = SalesWeekSnapshot(
            file_name=file.filename,
            period_from=d_from,
            period_to=d_to,
            total_items=len(items),
            total_qty=round(total_qty, 2),
        )
        db.add(snap)
        db.flush()

        for r in items:
            db.add(SalesWeekItem(snapshot_id=snap.id, **r))

        imported.append({"month": month_num, "name": MONTHS_LT[month_num],
                         "items": len(items), "qty": round(total_qty, 2)})

    db.commit()
    return {
        "year": year,
        "imported_months": len(imported),
        "skipped_months": len(skipped),
        "months": imported,
    }


@router.delete("/warehouse/snapshots/{snapshot_id}")
def delete_warehouse_snapshot(
    snapshot_id: int,
    current_user: User = Depends(require_purchases),
    db: Session = Depends(get_db),
):
    snap = db.query(WarehouseSnapshot).filter(WarehouseSnapshot.id == snapshot_id).first()
    if not snap:
        raise HTTPException(status_code=404, detail="Snapshot nerastas")
    # Delete related analysis
    db.query(PurchaseAnalysis).filter(PurchaseAnalysis.warehouse_snap_id == snapshot_id).delete()
    db.delete(snap)
    db.commit()
    # If deleted was latest, mark newest remaining as latest
    remaining = db.query(WarehouseSnapshot).order_by(WarehouseSnapshot.snap_date.desc()).first()
    if remaining and not remaining.is_latest:
        remaining.is_latest = True
        db.commit()
    return {"message": "Sandėlio snapshot ištrintas"}


@router.delete("/sales-weeks/{snapshot_id}")
def delete_sales_week(
    snapshot_id: int,
    current_user: User = Depends(require_purchases),
    db: Session = Depends(get_db),
):
    snap = db.query(SalesWeekSnapshot).filter(SalesWeekSnapshot.id == snapshot_id).first()
    if not snap:
        raise HTTPException(status_code=404, detail="Periodas nerastas")
    db.delete(snap)
    db.commit()
    return {"message": "Periodas ištrintas"}


# ── ANALYSIS ───────────────────────────────────────────────────────────────────

@router.post("/warehouse/snapshots/{snapshot_id}/analyze")
def run_analysis(
    snapshot_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_purchases),
    db: Session = Depends(get_db),
):
    snap = db.query(WarehouseSnapshot).filter(WarehouseSnapshot.id == snapshot_id).first()
    if not snap:
        raise HTTPException(status_code=404, detail="Sandėlio snapshot nerastas")

    # Reset existing analysis for this snapshot
    existing = db.query(PurchaseAnalysis).filter(
        PurchaseAnalysis.warehouse_snap_id == snapshot_id
    ).first()
    if existing:
        existing.ai_overall = None
        existing.ai_illiquid = None
        existing.ai_overstock = None
        existing.ai_reorder = None
        db.commit()
        analysis_id = existing.id
    else:
        analysis = PurchaseAnalysis(warehouse_snap_id=snapshot_id)
        db.add(analysis)
        db.commit()
        analysis_id = analysis.id

    background_tasks.add_task(_run_ai_analysis, analysis_id)
    return {"message": "AI analizė paleista", "analysis_id": analysis_id}


@router.get("/warehouse/snapshots/{snapshot_id}/analysis")
def get_analysis(
    snapshot_id: int,
    current_user: User = Depends(require_purchases),
    db: Session = Depends(get_db),
):
    analysis = db.query(PurchaseAnalysis).filter(
        PurchaseAnalysis.warehouse_snap_id == snapshot_id
    ).order_by(PurchaseAnalysis.id.desc()).first()

    if not analysis:
        return {"ready": False, "progress": {}}

    progress = {
        "overall":   bool(analysis.ai_overall),
        "illiquid":  bool(analysis.ai_illiquid),
        "overstock": bool(analysis.ai_overstock),
        "reorder":   bool(analysis.ai_reorder),
    }
    ready = all(progress.values())
    return {
        "ready": ready,
        "progress": progress,
        "weeks_used": analysis.weeks_used,
        "ai_overall":   analysis.ai_overall or "",
        "ai_illiquid":  analysis.ai_illiquid or "",
        "ai_overstock": analysis.ai_overstock or "",
        "ai_reorder":   analysis.ai_reorder or "",
    }

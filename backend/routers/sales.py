import os
import shutil
from typing import Optional
from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks, Query, Body
from sqlalchemy import func
from sqlalchemy.orm import Session
from backend.database import get_db, SessionLocal
from backend.auth import require_sales
from backend.models.user import User
from backend.models.sales import (
    SalesClientSnapshot, SalesClient, SalesMonthSnapshot,
    SalesInvoice, SalesTeamStat, KNOWN_TEAMS, TEAM_DISPLAY
)
from backend.services.sales_clients_parser import parse_clients_excel
from backend.services.sales_parser import parse_sales_excel
from backend.services.sales_analysis import analyze_sales_month, analyze_overall, analyze_team

router = APIRouter(prefix="/api/sales", tags=["sales"])
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _get_active_client_map(db: Session) -> dict:
    """Return {base_code: {team, salesperson, client_name}} from active client snapshot."""
    snap = db.query(SalesClientSnapshot).filter(SalesClientSnapshot.is_active == True).first()
    if not snap:
        return {}
    clients = db.query(SalesClient).filter(SalesClient.snapshot_id == snap.id).all()
    return {c.client_code: {"team": c.team, "salesperson": c.salesperson, "client_name": c.client_name} for c in clients}


def _build_ai_dict(snapshot: SalesMonthSnapshot, db: Session) -> dict:
    """Build the data dict for AI analysis."""
    client_map = _get_active_client_map(db)
    team_clients_total = defaultdict(int)
    for info in client_map.values():
        if info["team"] in KNOWN_TEAMS:
            team_clients_total[info["team"]] += 1

    invoices = db.query(SalesInvoice).filter(SalesInvoice.month_snapshot_id == snapshot.id).all()

    # Group by team → client
    team_data = defaultdict(lambda: defaultdict(lambda: {"suma": 0.0, "bp": 0.0, "name": ""}))
    team_totals = defaultdict(lambda: {"suma": 0.0, "bp": 0.0, "count": 0})

    for inv in invoices:
        team = inv.team or "NEPRISKIRTAS"
        team_data[team][inv.client_code]["suma"] += inv.suma
        team_data[team][inv.client_code]["bp"] += inv.bendrasis_pelnas
        team_data[team][inv.client_code]["name"] = inv.client_name
        team_totals[team]["suma"] += inv.suma
        team_totals[team]["bp"] += inv.bendrasis_pelnas
        team_totals[team]["count"] += 1

    teams_out = {}
    for team in KNOWN_TEAMS:
        clients_this = team_data.get(team, {})
        sorted_clients = sorted(clients_this.items(), key=lambda x: -x[1]["suma"])
        active = len(clients_this)
        total = team_clients_total.get(team, 0)
        tp = team_totals[team]
        bp_pct = (tp["bp"] / tp["suma"] * 100) if tp["suma"] > 0 else 0

        teams_out[team] = {
            "suma": round(tp["suma"], 2),
            "bp": round(tp["bp"], 2),
            "bp_pct": round(bp_pct, 1),
            "invoice_count": tp["count"],
            "active_clients": active,
            "total_clients": total,
            "inactive_count": max(0, total - active),
            "top_clients": [{"code": c, "name": d["name"], "suma": round(d["suma"], 2), "bp": round(d["bp"], 2)} for c, d in sorted_clients[:10]],
            "bottom_clients": [{"code": c, "name": d["name"], "suma": round(d["suma"], 2), "bp": round(d["bp"], 2)} for c, d in sorted_clients if d["suma"] > 0][-5:],
        }

    return {
        "year": snapshot.year, "month": snapshot.month,
        "total_suma": snapshot.total_suma, "total_bp": snapshot.total_bp,
        "total_bp_pct": snapshot.total_bp_pct, "total_invoices": snapshot.total_invoices,
        "teams": teams_out,
    }


def _team_client_map_for_snaps(snap_ids: list, db: Session) -> dict:
    """Returns {team: {client_code: {name, bp, suma}}} aggregated across multiple snapshots."""
    if not snap_ids:
        return {}
    invs = db.query(SalesInvoice).filter(SalesInvoice.month_snapshot_id.in_(snap_ids)).all()
    tm = defaultdict(lambda: defaultdict(lambda: {"name": "", "suma": 0.0, "bp": 0.0}))
    for inv in invs:
        t = inv.team or "NEPRISKIRTAS"
        tm[t][inv.client_code]["name"] = inv.client_name
        tm[t][inv.client_code]["suma"] += inv.suma
        tm[t][inv.client_code]["bp"] += inv.bendrasis_pelnas
    return tm


def _enrich_with_comparisons(current_dict: dict, current_snap_id: int,
                              prev_snap: Optional[SalesMonthSnapshot],
                              yago_snap: Optional[SalesMonthSnapshot],
                              ytd_snap_ids: list,   # 2026 mėnesiai prieš dabartinį
                              y2025_snap_ids: list, # visi 2025 mėnesiai
                              db: Session) -> dict:
    """Add lost/new client lists to each team with 3 comparison dimensions."""

    curr_map  = _team_client_map_for_snaps([current_snap_id], db)
    prev_map  = _team_client_map_for_snaps([prev_snap.id] if prev_snap else [], db)
    yago_map  = _team_client_map_for_snaps([yago_snap.id] if yago_snap else [], db)
    ytd_map   = _team_client_map_for_snaps(ytd_snap_ids, db)   # sausis+vasaris 2026
    y2025_map = _team_client_map_for_snaps(y2025_snap_ids, db) # visi 2025

    for team, td in current_dict["teams"].items():
        curr_team = curr_map.get(team, {})
        curr_set  = set(curr_team.keys())

        def _lost(source_team, bp_key, min_bp=30):
            lst = []
            for code, info in source_team.items():
                if code not in curr_set and info["bp"] >= min_bp:
                    lst.append({"code": code, "name": info["name"],
                                "bp": round(info["bp"], 2), "suma": round(info["suma"], 2)})
            return sorted(lst, key=lambda x: -x["bp"])[:12]

        def _new(source_team, min_bp=0):
            lst = []
            for code, info in curr_team.items():
                if code not in source_team and info["bp"] >= min_bp:
                    lst.append({"code": code, "name": info["name"],
                                "bp": round(info["bp"], 2), "suma": round(info["suma"], 2)})
            return sorted(lst, key=lambda x: -x["bp"])[:12]

        prev_team  = prev_map.get(team, {})
        yago_team  = yago_map.get(team, {})
        ytd_team   = ytd_map.get(team, {})
        y2025_team = y2025_map.get(team, {})

        # 1. Prarasti vs praėjęs mėnuo
        td["lost_vs_prev"]  = _lost(prev_team, "bp")
        # 2. Nauji vs praėjęs mėnuo
        td["new_vs_prev"]   = _new(prev_team)
        # 3. Prarasti vs 2026 YTD (pirko sausį/vasarį, nepirko kovą)
        td["lost_vs_ytd"]   = _lost(ytd_team, "bp")
        # 4. Prarasti vs praėjusių metų tas pats mėnuo
        td["lost_vs_yago"]  = _lost(yago_team, "bp")
        # 5. Prarasti vs visas 2025 (pirko 2025, dar nepirko 2026)
        # Reikia žiūrėti: pirko 2025, bet NIEKADA nepirko 2026 (nei sausį, nei vasarį, nei kovą)
        all_2026_set = set(curr_set)
        for c in ytd_team:
            all_2026_set.add(c)
        td["lost_vs_2025"]  = _lost(
            {c: i for c, i in y2025_team.items() if c not in all_2026_set}, "bp"
        )

        # Top clients su BP
        top_full = sorted(curr_team.items(), key=lambda x: -x[1]["bp"])
        td["top_by_bp"] = [
            {"code": c, "name": i["name"], "bp": round(i["bp"], 2), "suma": round(i["suma"], 2)}
            for c, i in top_full[:10]
        ]

    return current_dict


_AI_TEAM_ORDER = ["SAVI", "ROMBAS", "SOSTINES", "UZ_VILNIAUS"]
_AI_TEAM_COL   = {
    "SAVI":        "ai_savi",
    "ROMBAS":      "ai_rombas",
    "SOSTINES":    "ai_sostines",
    "UZ_VILNIAUS": "ai_uz_vilniaus",
}


def _run_ai_analysis(snapshot_id: int):
    import pathlib
    from dotenv import load_dotenv
    load_dotenv(pathlib.Path(__file__).parent.parent.parent / ".env", override=True)
    db = SessionLocal()
    try:
        snap = db.query(SalesMonthSnapshot).filter(SalesMonthSnapshot.id == snapshot_id).first()
        if not snap:
            return
        if snap.year < 2026:
            return  # 2025 ir ankstesni – tik palyginimui, analizės nereikia

        current = _build_ai_dict(snap, db)

        # Praėjęs mėnuo (chronologiškai prieš šį)
        prev_snap = db.query(SalesMonthSnapshot).filter(
            (SalesMonthSnapshot.year < snap.year) |
            ((SalesMonthSnapshot.year == snap.year) & (SalesMonthSnapshot.month < snap.month))
        ).order_by(SalesMonthSnapshot.year.desc(), SalesMonthSnapshot.month.desc()).first()

        # Tas pats mėnuo praėjusiais metais
        yago_snap = db.query(SalesMonthSnapshot).filter(
            SalesMonthSnapshot.year == snap.year - 1,
            SalesMonthSnapshot.month == snap.month,
        ).first()

        # 2026 YTD: mėnesiai prieš dabartinį tais pačiais metais
        ytd_snaps = db.query(SalesMonthSnapshot).filter(
            SalesMonthSnapshot.year == snap.year,
            SalesMonthSnapshot.month < snap.month,
        ).all()
        ytd_snap_ids = [s.id for s in ytd_snaps]

        # Visi 2025 mėnesiai
        y2025_snaps = db.query(SalesMonthSnapshot).filter(
            SalesMonthSnapshot.year == snap.year - 1,
        ).all()
        y2025_snap_ids = [s.id for s in y2025_snaps]

        current = _enrich_with_comparisons(
            current, snap.id, prev_snap, yago_snap,
            ytd_snap_ids, y2025_snap_ids, db
        )
        prev_dict = _build_ai_dict(prev_snap, db) if prev_snap else None
        yago_dict = _build_ai_dict(yago_snap, db) if yago_snap else None

        prev_label = f"{prev_dict['year']}-{prev_dict['month']:02d}" if prev_dict else "—"
        yago_label = f"{yago_dict['year']}-{yago_dict['month']:02d}" if yago_dict else "—"

        # 1. Bendra situacija
        snap.ai_analysis = analyze_overall(current, prev_dict, yago_dict)
        db.commit()

        # 2–5. Kiekviena komanda atskirai – išsaugome iškart po kiekvieno API kvietimo
        for team_code in _AI_TEAM_ORDER:
            td = current["teams"].get(team_code)
            if not td:
                continue
            prev_td = prev_dict["teams"].get(team_code) if prev_dict else None
            yago_td = yago_dict["teams"].get(team_code) if yago_dict else None
            text = analyze_team(team_code, td, prev_td, yago_td, prev_label, yago_label)
            setattr(snap, _AI_TEAM_COL[team_code], text)
            db.commit()

    except Exception as e:
        snap = db.query(SalesMonthSnapshot).filter(SalesMonthSnapshot.id == snapshot_id).first()
        if snap:
            snap.ai_analysis = (snap.ai_analysis or "") + f"\n\nKlaida generuojant analizę: {str(e)}"
            db.commit()
    finally:
        db.close()


# ── ROUTES ────────────────────────────────────────────────────────────────────

@router.post("/clients/upload")
async def upload_clients(
    file: UploadFile = File(...),
    current_user: User = Depends(require_sales),
    db: Session = Depends(get_db),
):
    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Tik Excel failai")

    import uuid
    ext = os.path.splitext(file.filename)[1].lower()
    file_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4()}{ext}")
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        parsed = parse_clients_excel(file_path)
    except Exception as e:
        os.remove(file_path)
        raise HTTPException(status_code=422, detail=str(e))

    # Deactivate old snapshots
    db.query(SalesClientSnapshot).update({"is_active": False})

    snap = SalesClientSnapshot(
        file_name=file.filename,
        total_clients=parsed["stats"]["total_clients"],
        total_assigned=parsed["stats"]["total_assigned"],
        is_active=True,
    )
    db.add(snap)
    db.flush()

    for c in parsed["clients"]:
        db.add(SalesClient(
            snapshot_id=snap.id,
            client_code=c["client_code"],
            client_name=c["client_name"],
            team=c["team"],
            salesperson=c["salesperson"],
        ))

    db.commit()
    return {
        "snapshot_id": snap.id,
        "total_clients": snap.total_clients,
        "total_assigned": snap.total_assigned,
        "message": "Klientų sąrašas įkeltas sėkmingai",
    }


@router.post("/sales/upload")
async def upload_sales_month(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    year: int = Query(...),
    month: int = Query(...),
    current_user: User = Depends(require_sales),
    db: Session = Depends(get_db),
):
    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Tik Excel failai")
    if not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="Mėnuo turi būti 1-12")

    # Check duplicate
    existing = db.query(SalesMonthSnapshot).filter(
        SalesMonthSnapshot.year == year, SalesMonthSnapshot.month == month
    ).first()
    if existing:
        # Delete and replace
        db.delete(existing)
        db.commit()

    import uuid
    ext = os.path.splitext(file.filename)[1].lower()
    file_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4()}{ext}")
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    client_map = _get_active_client_map(db)
    try:
        parsed = parse_sales_excel(file_path, client_map)
    except Exception as e:
        os.remove(file_path)
        raise HTTPException(status_code=422, detail=str(e))

    stats = parsed["stats"]
    snap = SalesMonthSnapshot(
        file_name=file.filename,
        year=year, month=month,
        total_invoices=stats["total_invoices"],
        total_suma=stats["total_suma"],
        total_bp=stats["total_bp"],
        total_bp_pct=stats["total_bp_pct"],
    )
    db.add(snap)
    db.flush()

    # Save invoices
    for inv in parsed["invoices"]:
        db.add(SalesInvoice(
            month_snapshot_id=snap.id,
            invoice_number=inv["invoice_number"],
            invoice_date=inv["invoice_date"],
            client_code=inv["client_code"],
            client_name=inv["client_name"],
            team=inv["team"],
            salesperson=inv["salesperson"],
            apmok_term=inv["apmok_term"],
            suma=inv["suma"],
            pvm=inv["pvm"],
            suma_su_pvm=inv["suma_su_pvm"],
            bendrasis_pelnas=inv["bendrasis_pelnas"],
            bp_pct=inv["bp_pct"],
        ))

    # Save team stats
    team_agg = defaultdict(lambda: {"suma": 0.0, "bp": 0.0, "count": 0, "clients": set()})
    for inv in parsed["invoices"]:
        t = inv["team"] or "NEPRISKIRTAS"
        team_agg[t]["suma"] += inv["suma"]
        team_agg[t]["bp"] += inv["bendrasis_pelnas"]
        team_agg[t]["count"] += 1
        team_agg[t]["clients"].add(inv["client_code"])

    for team, agg in team_agg.items():
        bp_pct = (agg["bp"] / agg["suma"] * 100) if agg["suma"] > 0 else 0
        db.add(SalesTeamStat(
            month_snapshot_id=snap.id,
            team=team,
            total_suma=round(agg["suma"], 2),
            total_bp=round(agg["bp"], 2),
            avg_bp_pct=round(bp_pct, 2),
            invoice_count=agg["count"],
            active_clients=len(agg["clients"]),
        ))

    db.commit()

    return {
        "snapshot_id": snap.id,
        "year": year, "month": month,
        "total_invoices": snap.total_invoices,
        "total_suma": snap.total_suma,
        "total_bp": snap.total_bp,
        "message": "Pardavimai įkelti sėkmingai.",
    }


@router.get("/months")
def list_months(
    current_user: User = Depends(require_sales),
    db: Session = Depends(get_db),
):
    snaps = db.query(SalesMonthSnapshot).order_by(
        SalesMonthSnapshot.year.desc(), SalesMonthSnapshot.month.desc()
    ).all()
    months_lt = ["","Sausis","Vasaris","Kovas","Balandis","Gegužė","Birželis",
                 "Liepa","Rugpjūtis","Rugsėjis","Spalis","Lapkritis","Gruodis"]
    return [
        {
            "id": s.id, "year": s.year, "month": s.month,
            "month_name": months_lt[s.month],
            "label": f"{months_lt[s.month]} {s.year}",
            "total_suma": s.total_suma, "total_bp": s.total_bp,
            "total_bp_pct": s.total_bp_pct, "total_invoices": s.total_invoices,
            "has_analysis": bool(s.ai_analysis),
        }
        for s in snaps
    ]


@router.get("/months/{snapshot_id}")
def get_month(
    snapshot_id: int,
    current_user: User = Depends(require_sales),
    db: Session = Depends(get_db),
):
    snap = db.query(SalesMonthSnapshot).filter(SalesMonthSnapshot.id == snapshot_id).first()
    if not snap:
        raise HTTPException(status_code=404, detail="Mėnuo nerastas")

    team_stats = db.query(SalesTeamStat).filter(SalesTeamStat.month_snapshot_id == snapshot_id).all()

    # Active client snapshot info
    client_snap = db.query(SalesClientSnapshot).filter(SalesClientSnapshot.is_active == True).first()
    client_snap_info = None
    if client_snap:
        from sqlalchemy import func as sqlfunc
        team_counts = dict(db.query(SalesClient.team, sqlfunc.count()).filter(
            SalesClient.snapshot_id == client_snap.id,
            SalesClient.team.isnot(None)
        ).group_by(SalesClient.team).all())
        client_snap_info = {"total_clients": client_snap.total_clients, "team_counts": team_counts}

    teams_out = {}
    for ts in team_stats:
        if ts.team not in KNOWN_TEAMS:
            continue
        total_cl = client_snap_info["team_counts"].get(ts.team, 0) if client_snap_info else 0

        # Top clients for this team this month
        top = db.query(
            SalesInvoice.client_code,
            SalesInvoice.client_name,
            func.sum(SalesInvoice.suma).label("total_suma"),
            func.sum(SalesInvoice.bendrasis_pelnas).label("total_bp"),
        ).filter(
            SalesInvoice.month_snapshot_id == snapshot_id,
            SalesInvoice.team == ts.team,
        ).group_by(SalesInvoice.client_code, SalesInvoice.client_name
        ).order_by(func.sum(SalesInvoice.suma).desc()).limit(15).all()

        teams_out[ts.team] = {
            "team_display": TEAM_DISPLAY.get(ts.team, ts.team),
            "suma": ts.total_suma, "bp": ts.total_bp, "bp_pct": ts.avg_bp_pct,
            "invoice_count": ts.invoice_count, "active_clients": ts.active_clients,
            "total_clients": total_cl,
            "inactive_clients": max(0, total_cl - ts.active_clients),
            "top_clients": [
                {"code": r.client_code, "name": r.client_name,
                 "suma": round(r.total_suma, 2), "bp": round(r.total_bp, 2)}
                for r in top
            ],
        }

    # Per-team AI texts
    all_team_ai = {
        "SAVI":        snap.ai_savi or "",
        "ROMBAS":      snap.ai_rombas or "",
        "SOSTINES":    snap.ai_sostines or "",
        "UZ_VILNIAUS": snap.ai_uz_vilniaus or "",
    }
    has_analysis = bool(snap.ai_analysis or any(all_team_ai.values()))

    return {
        "id": snap.id, "year": snap.year, "month": snap.month,
        "total_suma": snap.total_suma, "total_bp": snap.total_bp,
        "total_bp_pct": snap.total_bp_pct, "total_invoices": snap.total_invoices,
        "ai_analysis": snap.ai_analysis,
        "ai_teams": all_team_ai,
        "has_analysis": has_analysis,
        "teams": teams_out,
        "client_snapshot": client_snap_info,
    }


@router.get("/months/{snapshot_id}/team/{team}/clients")
def get_team_clients(
    snapshot_id: int,
    team: str,
    current_user: User = Depends(require_sales),
    db: Session = Depends(get_db),
):
    rows = db.query(
        SalesInvoice.client_code,
        SalesInvoice.client_name,
        SalesInvoice.salesperson,
        func.sum(SalesInvoice.suma).label("total_suma"),
        func.sum(SalesInvoice.bendrasis_pelnas).label("total_bp"),
        func.count(SalesInvoice.id).label("inv_count"),
    ).filter(
        SalesInvoice.month_snapshot_id == snapshot_id,
        SalesInvoice.team == team,
    ).group_by(
        SalesInvoice.client_code, SalesInvoice.client_name, SalesInvoice.salesperson
    ).order_by(func.sum(SalesInvoice.suma).desc()).all()

    return [
        {
            "client_code": r.client_code,
            "client_name": r.client_name,
            "salesperson": r.salesperson,
            "suma": round(r.total_suma, 2),
            "bp": round(r.total_bp, 2),
            "bp_pct": round(r.total_bp / r.total_suma * 100, 1) if r.total_suma else 0,
            "invoice_count": r.inv_count,
        }
        for r in rows
    ]


@router.get("/months/{snapshot_id}/analysis")
def get_analysis(
    snapshot_id: int,
    current_user: User = Depends(require_sales),
    db: Session = Depends(get_db),
):
    snap = db.query(SalesMonthSnapshot).filter(SalesMonthSnapshot.id == snapshot_id).first()
    if not snap:
        raise HTTPException(status_code=404, detail="Mėnuo nerastas")
    progress = {
        "overall":    bool(snap.ai_analysis),
        "SAVI":       bool(snap.ai_savi),
        "ROMBAS":     bool(snap.ai_rombas),
        "SOSTINES":   bool(snap.ai_sostines),
        "UZ_VILNIAUS":bool(snap.ai_uz_vilniaus),
    }
    ready = all(progress.values())
    return {
        "ready": ready,
        "progress": progress,
        "analysis": snap.ai_analysis,
        "ai_teams": {
            "SAVI":        snap.ai_savi or "",
            "ROMBAS":      snap.ai_rombas or "",
            "SOSTINES":    snap.ai_sostines or "",
            "UZ_VILNIAUS": snap.ai_uz_vilniaus or "",
        }
    }


@router.post("/months/{snapshot_id}/chat")
async def chat(
    snapshot_id: int,
    request: dict = Body(...),
    current_user: User = Depends(require_sales),
    db: Session = Depends(get_db),
):
    import pathlib
    from dotenv import load_dotenv
    load_dotenv(pathlib.Path(__file__).parent.parent.parent / ".env", override=True)

    snap = db.query(SalesMonthSnapshot).filter(SalesMonthSnapshot.id == snapshot_id).first()
    if not snap:
        raise HTTPException(status_code=404, detail="Mėnuo nerastas")

    question = (request.get("question") or "").strip()
    history  = request.get("history") or []   # [{"role": "user"|"assistant", "content": str}]
    if not question:
        raise HTTPException(status_code=400, detail="Klausimas tuščias")

    months_lt = ["","Sausis","Vasaris","Kovas","Balandis","Gegužė","Birželis",
                 "Liepa","Rugpjūtis","Rugsėjis","Spalis","Lapkritis","Gruodis"]
    label = f"{months_lt[snap.month]} {snap.year}"

    def _agg_clients(invoices):
        """Aggregate invoices → {team: {code: {name, suma, bp}}}"""
        tc = defaultdict(lambda: defaultdict(lambda: {"name": "", "suma": 0.0, "bp": 0.0}))
        for inv in invoices:
            t = inv.team or "NEPRISKIRTAS"
            tc[t][inv.client_code]["name"] = inv.client_name
            tc[t][inv.client_code]["suma"] += inv.suma
            tc[t][inv.client_code]["bp"] += inv.bendrasis_pelnas
        return tc

    def _team_rows(tc, max_clients=100):
        rows = []
        for team in KNOWN_TEAMS:
            tname = TEAM_DISPLAY.get(team, team)
            clients = tc.get(team, {})
            if not clients:
                continue
            sorted_c = sorted(clients.values(), key=lambda x: -x["bp"])
            team_bp = sum(c["bp"] for c in clients.values())
            rows.append(f"  {tname} ({len(clients)} klientų, BP {team_bp:,.2f} €):")
            for c in sorted_c[:max_clients]:
                rows.append(f"    • {c['name']}: BP {c['bp']:,.2f} €  Apyvarta {c['suma']:,.2f} €")
        return rows

    # ── Vienu užklausu paimti VISUS mėnesius ir VISUS invoice ──────────────────
    all_months = db.query(SalesMonthSnapshot).order_by(
        SalesMonthSnapshot.year, SalesMonthSnapshot.month
    ).all()
    all_ids = [m.id for m in all_months]
    month_by_id = {m.id: m for m in all_months}

    all_invoices = db.query(SalesInvoice).filter(
        SalesInvoice.month_snapshot_id.in_(all_ids)
    ).all()

    # Grupuoti: mid → team → client_code → {name, bp, suma}
    mtc = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {"name": "", "bp": 0.0, "suma": 0.0})))
    for inv in all_invoices:
        team = inv.team or "NEPRISKIRTAS"
        mtc[inv.month_snapshot_id][team][inv.client_code]["name"] = inv.client_name
        mtc[inv.month_snapshot_id][team][inv.client_code]["bp"]   += inv.bendrasis_pelnas
        mtc[inv.month_snapshot_id][team][inv.client_code]["suma"] += inv.suma

    # ── SLUOKSNIS 1: Kompaktiška mėnesių suvestinė (visi mėnesiai) ─────────────
    hdr = f"{'Mėnuo':<8} | {'Apyvarta€':>10} | {'BP€':>9} | {'Mrž%':>5} | {'SAVI BP':>9} | {'ROMBAS BP':>10} | {'SOSTINĖS BP':>12} | {'UZ BP':>8}"
    sep = "-" * len(hdr)
    tbl_rows = [hdr, sep]
    for m in all_months:
        tc = mtc.get(m.id, {})
        team_bp = {t: sum(c["bp"] for c in tc.get(t, {}).values()) for t in KNOWN_TEAMS}
        mk = f"{m.year}-{m.month:02d}"
        tbl_rows.append(
            f"{mk:<8} | {m.total_suma:>10,.0f} | {m.total_bp:>9,.0f} | {m.total_bp_pct:>4.1f}% |"
            f" {team_bp.get('SAVI',0):>9,.0f} | {team_bp.get('ROMBAS',0):>10,.0f} |"
            f" {team_bp.get('SOSTINES',0):>12,.0f} | {team_bp.get('UZ_VILNIAUS',0):>8,.0f}"
        )

    # ── SLUOKSNIS 2: Klientų sąrašai visiems mėnesiams (top 60 pagal BP) ───────
    client_rows = []
    for m in all_months:
        mk = f"{m.year}-{m.month:02d}"
        tc = mtc.get(m.id, {})
        for team in KNOWN_TEAMS:
            clients = tc.get(team, {})
            if not clients:
                continue
            sorted_c = sorted(clients.values(), key=lambda x: -x["bp"])
            total_clients = len(sorted_c)
            top = sorted_c[:60]
            names = ", ".join(f"{c['name']}({int(c['bp'])})" for c in top)
            suffix = f" [+{total_clients-60} klientų]" if total_clients > 60 else ""
            client_rows.append(f"{mk} {team}({total_clients}): {names}{suffix}")

    # ── SLUOKSNIS 3: Dabartinio mėnesio pilna detalė + AI analizė ──────────────
    cur_detail = [f"\n=== PASIRINKTAS MĖNUO: {label} ===",
                  f"Apyvarta: {snap.total_suma:,.2f} €  BP: {snap.total_bp:,.2f} €  "
                  f"Marža: {snap.total_bp_pct:.1f}%  Sąskaitos: {snap.total_invoices}"]
    for title, text in [
        ("BENDRA SITUACIJA", snap.ai_analysis),
        ("KOMANDA SAVI",     snap.ai_savi),
        ("KOMANDA ROMBAS",   snap.ai_rombas),
        ("KOMANDA SOSTINĖ",  snap.ai_sostines),
        ("KOMANDA UZ LIETUVĄ", snap.ai_uz_vilniaus),
    ]:
        if text and not text.startswith("Klaida"):
            cur_detail.append(f"\n--- {title} ---\n{text}")

    cur_tc = mtc.get(snap.id, {})
    cur_detail.append(f"\n--- {label} KLIENTAI PAGAL KOMANDAS ---")
    for team in KNOWN_TEAMS:
        tname = TEAM_DISPLAY.get(team, team)
        clients = cur_tc.get(team, {})
        if not clients:
            continue
        sorted_c = sorted(clients.values(), key=lambda x: -x["bp"])
        team_bp = sum(c["bp"] for c in clients.values())
        cur_detail.append(f"  {tname} ({len(clients)} klientų, BP {team_bp:,.2f} €):")
        for c in sorted_c[:60]:
            cur_detail.append(f"    • {c['name']}: BP {c['bp']:,.2f} €  Apyvarta {c['suma']:,.2f} €")

    context = "\n".join([
        "=== MĖNESIŲ SUVESTINĖ (visi turimi duomenys) ===",
        *tbl_rows,
        "\n=== KLIENTŲ SĄRAŠAI PAGAL MĖNESIUS IR KOMANDAS (BP €) ===",
        *client_rows,
        *cur_detail,
    ])

    system_msg = f"""Tu esi pardavimų analitikas. Atsakyk LIETUVIŲ KALBA, glaustai ir konkrečiai.
Pagrindinis rodiklis – BENDRASIS PELNAS (BP). Naudok TIK skaičius iš konteksto.

SVARBIOS TAISYKLĖS darbui su duomenimis:
1. KLIENTŲ SĄRAŠAI yra sugrupuoti pagal KONKRETŲ MĖNESĮ. Eilutė "2025-01 SAVI: ..." reiškia klientus, kurie pirko TIKTAI 2025 sausį. Eilutė "2025-07 SAVI: ..." – tiktai 2025 liepą. Nemaišyk mėnesių.
2. Kai lyginai laikotarpius (pvz. Q1 2025 vs Q1 2026), naudok TIK tuos mėnesius:
   - Q1 2025 = 2025-01 + 2025-02 + 2025-03
   - Q1 2026 = 2026-01 + 2026-02 + 2026-03
   Klientai iš 2025-04..2025-12 į Q1 palyginimą NEPRIKLAUSO.
3. "Kurie nepirko šiemet" = klientų nėra NE VIENAME 2026 mėnesio sąraše.
4. "Grįžęs klientas" = buvo pirmame periode IR yra antrame periode.
5. "Prarastas klientas" = buvo pirmame periode IR NĖRA antrame periode.
6. Visada tikrink kliento buvimą kiekviename mėnesyje atskirai – nesudėk visų 2025 mėnesių į vieną.

PARDAVIMŲ DUOMENYS ({label}):
{context}"""

    messages = []
    for h in history[-8:]:  # paskutiniai 8 žinučių - kontekstui
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": question})

    import os, anthropic
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2048,
        system=system_msg,
        messages=messages,
    )
    return {"answer": resp.content[0].text}


@router.post("/months/{snapshot_id}/rerun-analysis")
def rerun_analysis(
    snapshot_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_sales),
    db: Session = Depends(get_db),
):
    snap = db.query(SalesMonthSnapshot).filter(SalesMonthSnapshot.id == snapshot_id).first()
    if not snap:
        raise HTTPException(status_code=404, detail="Mėnuo nerastas")
    snap.ai_analysis   = None
    snap.ai_savi        = None
    snap.ai_rombas      = None
    snap.ai_sostines    = None
    snap.ai_uz_vilniaus = None
    db.commit()
    background_tasks.add_task(_run_ai_analysis, snapshot_id)
    return {"message": "AI analizė paleista iš naujo"}


@router.get("/clients/status")
def clients_status(
    current_user: User = Depends(require_sales),
    db: Session = Depends(get_db),
):
    snap = db.query(SalesClientSnapshot).filter(SalesClientSnapshot.is_active == True).first()
    if not snap:
        return {"has_clients": False}
    return {
        "has_clients": True,
        "file_name": snap.file_name,
        "upload_date": snap.upload_date,
        "total_clients": snap.total_clients,
        "total_assigned": snap.total_assigned,
    }

"""
Parser for Directo monthly sales (arved) Excel export.

Header row contains: Numeris, Data, Pirkėjas, Pavadinimas, (empty),
                     Objektas:pardavėjas, Apmok. term., (empty), (empty),
                     Bendrasis pelnas, BP %%, Suma, PVM, su PVM

Rules:
  - Skip FIZINIS pirkėjas codes
  - Client code: strip /suffix (divisions → base code)
  - Team/salesperson: looked up from client map (by base code)
  - Skip rows where Numeris is not numeric
  - Skip rows where Suma is not numeric (footer/summary rows)
"""

import re
from datetime import date, datetime
from typing import Optional, Dict
import pandas as pd

# ── Filtravimo konstantos ──────────────────────────────────────────────────────
# Kliento kodai, kurių sąskaitos NESKAITOMOS (fiziniai asmenys ir pan.)
EXCLUDED_CLIENT_PREFIXES: set[str] = {"FIZINIS"}

# Konkretūs kliento kodai, kurie visada praleidžiami (kaip purchases 001-0071)
EXCLUDED_CLIENT_CODES: set[str] = set()


def _to_float(val) -> Optional[float]:
    if val is None or str(val).strip() in ("", "nan"):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _to_int(val) -> Optional[int]:
    f = _to_float(val)
    return int(f) if f is not None else None


def _parse_date(val) -> Optional[date]:
    if not val or str(val).strip() in ("", "nan"):
        return None
    s = str(val).strip()
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:len(fmt.replace('%d','00').replace('%m','00').replace('%Y','0000').replace('%H','00').replace('%M','00').replace('%S','00'))], fmt).date()
        except Exception:
            pass
    # Try pandas
    try:
        return pd.to_datetime(s, dayfirst=True).date()
    except Exception:
        return None


def _clean_client_code(raw: str) -> str:
    """Strip /suffix from division codes, strip _ artifacts."""
    s = str(raw).strip().strip("_")
    return s.split("/")[0].strip("_")


def parse_sales_excel(file_path: str, client_map: Dict[str, dict]) -> dict:
    """
    Parse Directo arved Excel file.

    client_map: {base_code: {"team": ..., "salesperson": ..., "client_name": ...}}

    Returns dict with invoices list and summary stats.
    """
    df = pd.read_excel(file_path, sheet_name=0, header=None, dtype=str)
    df = df.fillna("")

    # Find the header row (contains "Numeris" in col 0)
    header_row = None
    for i, row in df.iterrows():
        if str(row.iloc[0]).strip() == "Numeris":
            header_row = i
            break

    if header_row is None:
        raise ValueError("Nepavyko rasti antraštės eilutės su 'Numeris'")

    data = df.iloc[header_row + 1:].copy()

    invoices = []
    total_suma = 0.0
    total_bp = 0.0

    for _, row in data.iterrows():
        cols = row.tolist()

        numeris = str(cols[0]).strip()
        if not numeris.isdigit():
            continue

        suma = _to_float(cols[11])
        if suma is None:
            continue

        raw_code = str(cols[2]).strip()

        # Skip pagal prefiksą (FIZINIS ir pan.)
        if any(raw_code.upper().startswith(p) for p in EXCLUDED_CLIENT_PREFIXES):
            continue

        base_code = _clean_client_code(raw_code)

        # Skip pagal konkretų kodą
        if base_code in EXCLUDED_CLIENT_CODES:
            continue
        client_name = str(cols[3]).strip()

        # Lookup team/salesperson from client map
        client_info = client_map.get(base_code, {})
        team = client_info.get("team")
        salesperson = client_info.get("salesperson")
        # Use stored name if available
        if client_info.get("client_name"):
            client_name = client_info["client_name"]

        bp = _to_float(cols[9]) or 0.0
        bp_pct = _to_float(cols[10]) or 0.0
        pvm = _to_float(cols[12]) or 0.0
        suma_su_pvm = _to_float(cols[13]) or 0.0

        invoices.append({
            "invoice_number": numeris,
            "invoice_date": _parse_date(cols[1]),
            "client_code": base_code,
            "client_name": client_name,
            "team": team,
            "salesperson": salesperson,
            "apmok_term": _to_int(cols[6]),
            "suma": suma,
            "pvm": pvm,
            "suma_su_pvm": suma_su_pvm,
            "bendrasis_pelnas": bp,
            "bp_pct": bp_pct,
        })

        total_suma += suma
        total_bp += bp

    avg_bp_pct = (total_bp / total_suma * 100) if total_suma > 0 else 0.0

    return {
        "invoices": invoices,
        "stats": {
            "total_invoices": len(invoices),
            "total_suma": round(total_suma, 2),
            "total_bp": round(total_bp, 2),
            "total_bp_pct": round(avg_bp_pct, 2),
        },
    }

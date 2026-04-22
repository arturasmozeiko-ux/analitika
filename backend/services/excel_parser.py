"""
Parser for Directo accounts receivable (reskontro) Excel export.

File structure per buyer:
  Row 1: "Pirkėjas {CODE} {NAME}"
  Row 2: Column headers (Sąskaitos numeris | Sąskaitos laikas | Apmok. data | Apmokėjimo terminas | Mokėti | Dienos)
  Row N: invoice rows  OR  "Išankstinis apmokėjimas:" row (prepayment)
  Last:  "Pirkėjo balansas"  (col 4 = balance)
  Opt:   "Pradelsta"         (col 4 = overdue amount)
  Sep:   empty row

Footer (last rows of file):
  "Iš viso neapmokėta"               → col 3
  "Bendra išankstinių apmokėjimų suma" → col 3
  "Bendras balansas"                  → col 3
  "Pradelstų apmokėti sąskaitų suma" → col 3
"""

import re
from datetime import date, datetime
from typing import Optional
import pandas as pd


def _parse_date(val) -> Optional[date]:
    if pd.isna(val) or val is None:
        return None
    s = str(val).strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def _to_float(val) -> Optional[float]:
    if pd.isna(val) or val is None or str(val).strip() == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _to_int(val) -> Optional[int]:
    f = _to_float(val)
    return int(f) if f is not None else None


def parse_receivables_excel(file_path: str) -> dict:
    """
    Parse a Directo reskontro Excel file.
    Returns a dict with buyers list and footer totals.
    """
    df = pd.read_excel(file_path, sheet_name=0, header=None, dtype=str)
    df = df.fillna("")

    buyers = []
    current_buyer = None
    current_invoices = []
    current_overdue = 0.0
    current_balance = 0.0
    current_has_overdue = False
    current_has_prepayment = False

    footer = {
        "total_unpaid": 0.0,
        "total_prepayments": 0.0,
        "total_balance": 0.0,
        "total_overdue": 0.0,
    }

    def flush_buyer():
        nonlocal current_buyer, current_invoices, current_overdue, current_balance
        nonlocal current_has_overdue, current_has_prepayment
        if current_buyer:
            current_buyer["balance"] = current_balance
            current_buyer["overdue_amount"] = current_overdue
            current_buyer["has_overdue"] = current_has_overdue
            current_buyer["has_prepayment"] = current_has_prepayment
            current_buyer["invoices"] = current_invoices
            buyers.append(current_buyer)
        current_buyer = None
        current_invoices = []
        current_overdue = 0.0
        current_balance = 0.0
        current_has_overdue = False
        current_has_prepayment = False

    for _, row in df.iterrows():
        col0 = str(row.iloc[0]).strip()
        col1 = str(row.iloc[1]).strip()
        col2 = str(row.iloc[2]).strip()
        col3 = str(row.iloc[3]).strip()
        col4 = str(row.iloc[4]).strip()
        col5 = str(row.iloc[5]).strip() if len(row) > 5 else ""

        # --- Footer lines ---
        if col1 == "Iš viso neapmokėta":
            footer["total_unpaid"] = _to_float(col3) or 0.0
            continue
        if col1 == "Bendra išankstinių apmokėjimų suma":
            footer["total_prepayments"] = _to_float(col3) or 0.0
            continue
        if col1 == "Bendras balansas":
            footer["total_balance"] = _to_float(col3) or 0.0
            continue
        if col1 == "Pradelstų apmokėti sąskaitų suma":
            footer["total_overdue"] = _to_float(col3) or 0.0
            continue

        # --- Buyer header ---
        if col0.startswith("Pirkėjas "):
            flush_buyer()
            # Extract code and name: "Pirkėjas 110011925 Eltel Networks, UAB"
            match = re.match(r"Pirkėjas\s+(\S+)\s+(.*)", col0)
            if match:
                buyer_code = match.group(1)
                buyer_name = match.group(2).strip()
            else:
                buyer_code = ""
                buyer_name = col0.replace("Pirkėjas ", "").strip()
            current_buyer = {"buyer_code": buyer_code, "buyer_name": buyer_name}
            continue

        # --- Column headers row (skip) ---
        if col0 == "Sąskaitos numeris":
            continue

        # --- Buyer balance ---
        if col0 == "Pirkėjo balansas":
            current_balance = _to_float(col4) or 0.0
            continue

        # --- Overdue marker ---
        if col0 == "Pradelsta":
            current_overdue = _to_float(col4) or 0.0
            current_has_overdue = True
            continue

        # --- Prepayment row ---
        if col0 == "Išankstinis apmokėjimas:":
            amount = _to_float(col4)
            if amount is not None:
                current_has_prepayment = True
                current_invoices.append({
                    "invoice_number": None,
                    "invoice_datetime": None,
                    "payment_date": None,
                    "payment_term_days": None,
                    "amount": amount,
                    "days_remaining": None,
                    "is_overdue": False,
                    "is_prepayment": True,
                })
            continue

        # --- Empty row (separator) ---
        if not col0 and not col1 and not col2 and not col4:
            continue

        # --- Invoice row: col0 is numeric invoice number ---
        if col0.isdigit() and current_buyer is not None:
            days = _to_int(col5)
            amount = _to_float(col4)
            current_invoices.append({
                "invoice_number": col0,
                "invoice_datetime": col1 if col1 else None,
                "payment_date": _parse_date(col2),
                "payment_term_days": _to_int(col3),
                "amount": amount or 0.0,
                "days_remaining": days,
                "is_overdue": (days is not None and days < 0),
                "is_prepayment": False,
            })

    flush_buyer()  # flush last buyer

    total_invoices = sum(len(b["invoices"]) for b in buyers)
    overdue_buyers = sum(1 for b in buyers if b["has_overdue"])
    overdue_invoices = sum(
        1 for b in buyers for inv in b["invoices"]
        if inv["is_overdue"]
    )

    return {
        "buyers": buyers,
        "footer": footer,
        "stats": {
            "total_buyers": len(buyers),
            "total_invoices": total_invoices,
            "overdue_buyers": overdue_buyers,
            "overdue_invoices": overdue_invoices,
        },
    }

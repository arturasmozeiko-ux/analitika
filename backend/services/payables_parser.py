"""
Parser for Directo accounts payable (tiekėjų reskontro) Excel export.

File structure per supplier:
  Row 1: "Tiekėjas: {CODE} {NAME}"
  Row 2: Column headers (Sąsk. nr. | Tiekėjo s-f nr. | Sąskaitos laikas | Apmok. data | Apmok. terminas | Mokėti | Dienos)
  Row N: invoice rows  OR  "Išankstinis apmokėjimas :" row
  Last:  "Skola tiekėjui"  (col 5 = balance, negative = we owe)
  Opt:   "Pradelsta"       (col 5 = overdue amount, negative)
  Sep:   empty row

Footer:
  "Iš viso neapmokėta"           → col 3
  "Iš viso išankstinių mokėjimų" → col 3
  "Bendras balansas"             → col 3
  "Iš viso uždelsta apmokėti"    → col 3
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
    if pd.isna(val) or val is None or str(val).strip() in ("", "nan"):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _to_int(val) -> Optional[int]:
    f = _to_float(val)
    return int(f) if f is not None else None


def parse_payables_excel(file_path: str) -> dict:
    """
    Parse a Directo tiekėjų reskontro Excel file.
    Returns dict with suppliers list and footer totals.
    Amounts are stored as ABSOLUTE values (positive numbers).
    """
    df = pd.read_excel(file_path, sheet_name=0, header=None, dtype=str)
    df = df.fillna("")

    suppliers = []
    current_supplier = None
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

    def flush_supplier():
        nonlocal current_supplier, current_invoices, current_overdue, current_balance
        nonlocal current_has_overdue, current_has_prepayment
        if current_supplier:
            current_supplier["balance"] = current_balance
            current_supplier["overdue_amount"] = current_overdue
            current_supplier["has_overdue"] = current_has_overdue
            current_supplier["has_prepayment"] = current_has_prepayment
            current_supplier["invoices"] = current_invoices
            suppliers.append(current_supplier)
        current_supplier = None
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
        col6 = str(row.iloc[6]).strip() if len(row) > 6 else ""

        # --- Footer lines ---
        if col1 == "Iš viso neapmokėta":
            val = _to_float(col3)
            footer["total_unpaid"] = abs(val) if val is not None else 0.0
            continue
        if col1 == "Iš viso išankstinių mokėjimų":
            val = _to_float(col3)
            footer["total_prepayments"] = abs(val) if val is not None else 0.0
            continue
        if col1 == "Bendras balansas":
            val = _to_float(col3)
            footer["total_balance"] = abs(val) if val is not None else 0.0
            continue
        if col1 == "Iš viso uždelsta apmokėti":
            val = _to_float(col3)
            footer["total_overdue"] = abs(val) if val is not None else 0.0
            continue

        # --- Supplier header: "Tiekėjas: {CODE} {NAME}" ---
        if col0.startswith("Tiekėjas:"):
            flush_supplier()
            match = re.match(r"Tiekėjas:\s+(\S+)\s+(.*)", col0)
            if match:
                supplier_code = match.group(1)
                supplier_name = match.group(2).strip()
            else:
                supplier_code = ""
                supplier_name = col0.replace("Tiekėjas:", "").strip()
            current_supplier = {"supplier_code": supplier_code, "supplier_name": supplier_name}
            continue

        # --- Column headers row (skip) ---
        if col0 == "Sąsk. nr.":
            continue

        # --- Supplier balance ---
        if col0 == "Skola tiekėjui":
            val = _to_float(col5)
            current_balance = abs(val) if val is not None else 0.0
            continue

        # --- Overdue marker ---
        if col0 == "Pradelsta":
            val = _to_float(col5)
            current_overdue = abs(val) if val is not None else 0.0
            current_has_overdue = True
            continue

        # --- Prepayment row ---
        if col0.startswith("Išankstinis apmokėjimas"):
            val = _to_float(col5)
            if val is not None:
                current_has_prepayment = True
                current_invoices.append({
                    "invoice_number": None,
                    "supplier_invoice_number": None,
                    "invoice_datetime": None,
                    "payment_date": None,
                    "payment_term": None,
                    "amount": abs(val),
                    "days_remaining": None,
                    "is_overdue": False,
                    "is_prepayment": True,
                })
            continue

        # --- Empty row ---
        if not col0 and not col1 and not col5:
            continue

        # --- Invoice row: col0 is numeric ---
        if col0.isdigit() and current_supplier is not None:
            days = _to_int(col6)
            amount = _to_float(col5)
            current_invoices.append({
                "invoice_number": col0,
                "supplier_invoice_number": col1 if col1 else None,
                "invoice_datetime": col2 if col2 else None,
                "payment_date": _parse_date(col3),
                "payment_term": col4 if col4 else None,
                "amount": abs(amount) if amount is not None else 0.0,
                "days_remaining": days,
                "is_overdue": (days is not None and days < 0),
                "is_prepayment": False,
            })

    flush_supplier()

    total_invoices = sum(len(s["invoices"]) for s in suppliers)
    overdue_suppliers = sum(1 for s in suppliers if s["has_overdue"])
    overdue_invoices = sum(1 for s in suppliers for inv in s["invoices"] if inv["is_overdue"])

    return {
        "suppliers": suppliers,
        "footer": footer,
        "stats": {
            "total_suppliers": len(suppliers),
            "total_invoices": total_invoices,
            "overdue_suppliers": overdue_suppliers,
            "overdue_invoices": overdue_invoices,
        },
    }

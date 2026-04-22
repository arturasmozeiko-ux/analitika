"""
Parser for Directo client list (kliendid) Excel export.

Structure (from row 13):
  Col 0: Kodas        – client code (may have _ prefix/suffix, / for divisions)
  Col 1: Pavadinimas  – client name
  Col 2: Klasė        – class (always PIRKEJAI here)
  Col 3: Objektas     – comma-separated tags, e.g. "VILNIUS,PRIVATUS,SAVI,JURATE_C"

Rules:
  - Codes with "/" → strip suffix, group as one client (base code)
  - Codes starting with "FIZINIS" → skip
  - Objektas: ignore VILNIUS, PRIVATUS, VALSTYBINIS and internal tags
  - Known teams: SAVI, ROMBAS, SOSTINES, UZ_VILNIAUS
  - Salesperson = element right after the team name
"""

import re
import pandas as pd

KNOWN_TEAMS = {"SAVI", "ROMBAS", "SOSTINES", "UZ_VILNIAUS"}

IGNORE_TAGS = {
    "VILNIUS", "PRIVATUS", "VALSTYBINIS", "LAISVI_KLIENTAI", "LAISVI_KLIENTAI_A",
    "NEPRISKIRTAS", "DRAUGAI", "DRAUGAI_A", "ADMINISTRACIJA", "B_ADMINISTRACIJA",
    "DARBUOTOJAI", "MARKETINGAS", "PIRKIMAI", "PIRKIMU", "SK_PIRKIMAI",
    "KAUNAS", "KLAIPEDA", "SIAULIAI", "PANEVEZYS", "VIP",
}


def _clean_code(raw: str) -> str:
    """Strip _ prefix/suffix, take base before /."""
    s = str(raw).strip().strip("_")
    return s.split("/")[0].strip("_")


def _parse_objektas(raw: str):
    """Extract (team, salesperson) from Objektas string."""
    if not raw or str(raw).strip() in ("nan", ""):
        return None, None
    cleaned = str(raw).replace("\xa0", "").strip()
    parts = [p.strip() for p in cleaned.split(",") if p.strip()]
    team = None
    salesperson = None
    for i, part in enumerate(parts):
        if part in KNOWN_TEAMS:
            team = part
            if i + 1 < len(parts):
                nxt = parts[i + 1]
                if nxt not in IGNORE_TAGS and nxt not in KNOWN_TEAMS:
                    salesperson = nxt
            break
    return team, salesperson


def parse_clients_excel(file_path: str) -> dict:
    """
    Parse Directo kliendid Excel.
    Returns dict with clients list (deduplicated by base code).
    """
    df = pd.read_excel(file_path, sheet_name=0, header=None, dtype=str)
    df = df.fillna("")

    # Find header row (contains "Kodas")
    header_row = None
    for i, row in df.iterrows():
        if str(row.iloc[0]).strip() == "Kodas":
            header_row = i
            break

    if header_row is None:
        raise ValueError("Nepavyko rasti antraštės eilutės su 'Kodas'")

    data = df.iloc[header_row + 1:].copy()
    data.columns = range(len(data.columns))

    seen_codes = {}  # base_code → client dict

    for _, row in data.iterrows():
        raw_code = str(row.iloc[0]).strip()
        name = str(row.iloc[1]).strip()
        objektas = str(row.iloc[3]).strip()

        if not raw_code or raw_code in ("nan", "", "Kodas"):
            continue

        # Skip FIZINIS
        if raw_code.upper().startswith("FIZINIS"):
            continue

        # Skip header repetitions
        if name in ("Pavadinimas", "nan", ""):
            continue

        base_code = _clean_code(raw_code)
        if not base_code or base_code in ("nan", ""):
            continue

        team, salesperson = _parse_objektas(objektas)

        # First occurrence wins (or update if no team yet)
        if base_code not in seen_codes:
            seen_codes[base_code] = {
                "client_code": base_code,
                "client_name": name,
                "team": team,
                "salesperson": salesperson,
            }
        elif seen_codes[base_code]["team"] is None and team is not None:
            seen_codes[base_code]["team"] = team
            seen_codes[base_code]["salesperson"] = salesperson

    clients = list(seen_codes.values())
    assigned = sum(1 for c in clients if c["team"] is not None)

    return {
        "clients": clients,
        "stats": {
            "total_clients": len(clients),
            "total_assigned": assigned,
        },
    }

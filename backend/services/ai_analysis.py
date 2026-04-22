"""
AI analysis service using Claude API.
Generates insights for receivables snapshots, including week-over-week comparisons.
"""

import os
from typing import Optional
import anthropic

MODEL = "claude-opus-4-6"


def _get_client():
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError("ANTHROPIC_API_KEY nėra nustatytas .env faile")
    return anthropic.Anthropic(api_key=key)


def _format_currency(val: float) -> str:
    return f"{val:,.2f} €"


def analyze_receivables(current: dict, previous: Optional[dict] = None) -> str:
    """
    Generate AI insights for a receivables snapshot.

    current / previous structure:
    {
        "snapshot_date": "2026-04-09",
        "total_balance": float,
        "total_overdue": float,
        "total_buyers": int,
        "overdue_buyers": int,
        "overdue_invoices": int,
        "buyers": [
            {
                "buyer_code": str,
                "buyer_name": str,
                "balance": float,
                "overdue_amount": float,
                "has_overdue": bool,
                "invoices": [...]
            }
        ]
    }
    """

    # Build context for current snapshot
    top_debtors = sorted(current["buyers"], key=lambda b: b["balance"], reverse=True)[:10]
    top_overdue = sorted(
        [b for b in current["buyers"] if b["has_overdue"]],
        key=lambda b: b["overdue_amount"],
        reverse=True
    )[:10]

    current_summary = f"""
DABARTINIS SNAPSHOT ({current.get('snapshot_date', 'nežinoma data')}):
- Bendras balansas: {_format_currency(current['total_balance'])}
- Iš viso pradelsta: {_format_currency(current['total_overdue'])}
- Pirkėjų su skola: {current['total_buyers']}
- Pirkėjų su pradelstosiomis: {current['overdue_buyers']}
- Pradelstų sąskaitų: {current['overdue_invoices']}

TOP 10 DIDŽIAUSIŲ SKOLININKŲ:
{chr(10).join(f"  {i+1}. {b['buyer_name']}: {_format_currency(b['balance'])}" for i, b in enumerate(top_debtors))}

TOP 10 PRADELSTŲ:
{chr(10).join(f"  {i+1}. {b['buyer_name']}: {_format_currency(b['overdue_amount'])} pradelsta" for i, b in enumerate(top_overdue)) if top_overdue else "  Nėra pradelstų"}
"""

    comparison_text = ""
    if previous:
        # Build buyer lookup by code for comparison
        prev_by_code = {b["buyer_code"]: b for b in previous["buyers"]}
        curr_by_code = {b["buyer_code"]: b for b in current["buyers"]}

        # Paid off significant debts (appeared in previous, gone or reduced now)
        paid_off = []
        for code, prev_b in prev_by_code.items():
            curr_b = curr_by_code.get(code)
            if curr_b is None and prev_b["balance"] > 100:
                paid_off.append((prev_b["buyer_name"], prev_b["balance"]))
            elif curr_b and prev_b["balance"] > 0:
                reduction = prev_b["balance"] - curr_b["balance"]
                if reduction > 200:
                    paid_off.append((prev_b["buyer_name"], reduction))
        paid_off.sort(key=lambda x: x[1], reverse=True)

        # New or increased overdue
        new_overdue = []
        for code, curr_b in curr_by_code.items():
            prev_b = prev_by_code.get(code)
            if curr_b["has_overdue"]:
                prev_overdue = prev_b["overdue_amount"] if prev_b else 0
                increase = curr_b["overdue_amount"] - prev_overdue
                if increase > 100:
                    new_overdue.append((curr_b["buyer_name"], curr_b["overdue_amount"], increase))
        new_overdue.sort(key=lambda x: x[2], reverse=True)

        # New buyers
        new_buyers = [b for code, b in curr_by_code.items() if code not in prev_by_code]
        # Gone buyers
        gone_buyers = [b for code, b in prev_by_code.items() if code not in curr_by_code]

        balance_change = current["total_balance"] - previous["total_balance"]
        overdue_change = current["total_overdue"] - previous["total_overdue"]

        comparison_text = f"""
PALYGINIMAS SU ANKSTESNIU SNAPSHOT ({previous.get('snapshot_date', 'nežinoma data')}):
- Bendras balansas: {_format_currency(previous['total_balance'])} → {_format_currency(current['total_balance'])} ({'+' if balance_change >= 0 else ''}{_format_currency(balance_change)})
- Pradelsta: {_format_currency(previous['total_overdue'])} → {_format_currency(current['total_overdue'])} ({'+' if overdue_change >= 0 else ''}{_format_currency(overdue_change)})

APMOKĖJO / SUMAŽINO SKOLĄ:
{chr(10).join(f"  - {name}: {_format_currency(amount)}" for name, amount in paid_off[:10]) if paid_off else "  (nėra reikšmingų apmokėjimų)"}

NAUJA / PADIDĖJUSI PRADELSTA:
{chr(10).join(f"  - {name}: {_format_currency(total)} iš viso (padidėjo {_format_currency(inc)})" for name, total, inc in new_overdue[:10]) if new_overdue else "  (nėra naujų pradelstų)"}

NAUJI PIRKĖJAI ({len(new_buyers)}): {', '.join(b['buyer_name'] for b in new_buyers[:5]) or 'nėra'}
DINGO PIRKĖJAI ({len(gone_buyers)}): {', '.join(b['buyer_name'] for b in gone_buyers[:5]) or 'nėra'}
"""

    prompt = f"""Tu esi finansų analitikas. Analizuok šiuos gautinų sumų (accounts receivable) duomenis ir pateik įžvalgas lietuvių kalba.

{current_summary}
{comparison_text}

Pateik struktūruotą analizę su šiomis dalimis:
1. **Bendra situacija** – trumpas situacijos apibūdinimas
2. **Rizikos zona** – kurie klientai kelia didžiausią susirūpinimą ir kodėl
3. **Teigiami pokyčiai** – kas apmokėjo, kas sumažino skolą (tik jei yra palyginimas)
4. **Dėmesio reikalaujantys klientai** – konkrečios rekomendacijos
5. **Rekomendacijos** – ką daryti artimiausiomis dienomis

Būk konkretus, naudok skaičius. Nekartok visų duomenų – pabrėžk svarbius dalykus.
"""

    response = _get_client().messages.create(
        model=MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )

    return response.content[0].text


def analyze_payables(current: dict, previous: Optional[dict] = None) -> str:
    """Generate AI insights for a payables (supplier debts) snapshot."""

    top_creditors = sorted(current["suppliers"], key=lambda s: s["balance"], reverse=True)[:10]
    top_overdue = sorted(
        [s for s in current["suppliers"] if s["has_overdue"]],
        key=lambda s: s["overdue_amount"],
        reverse=True
    )[:10]

    current_summary = f"""
DABARTINIS SNAPSHOT ({current.get('snapshot_date', 'nežinoma data')}):
- Bendras balansas (skolos tiekėjams): {_format_currency(current['total_balance'])}
- Iš viso pradelsta: {_format_currency(current['total_overdue'])}
- Tiekėjų su skola: {current['total_suppliers']}
- Tiekėjų su pradelstomis mokėjimais: {current['overdue_suppliers']}
- Pradelstų sąskaitų: {current['overdue_invoices']}

TOP 10 DIDŽIAUSIŲ KREDITORIŲ:
{chr(10).join(f"  {i+1}. {s['supplier_name']}: {_format_currency(s['balance'])}" for i, s in enumerate(top_creditors))}

TOP 10 PRADELSTŲ MOKĖJIMŲ:
{chr(10).join(f"  {i+1}. {s['supplier_name']}: {_format_currency(s['overdue_amount'])} pradelsta" for i, s in enumerate(top_overdue)) if top_overdue else "  Nėra pradelstų"}
"""

    comparison_text = ""
    if previous:
        prev_by_code = {s["supplier_code"]: s for s in previous["suppliers"]}
        curr_by_code = {s["supplier_code"]: s for s in current["suppliers"]}

        # Paid off / reduced
        paid_off = []
        for code, prev_s in prev_by_code.items():
            curr_s = curr_by_code.get(code)
            if curr_s is None and prev_s["balance"] > 100:
                paid_off.append((prev_s["supplier_name"], prev_s["balance"]))
            elif curr_s and prev_s["balance"] > 0:
                reduction = prev_s["balance"] - curr_s["balance"]
                if reduction > 200:
                    paid_off.append((prev_s["supplier_name"], reduction))
        paid_off.sort(key=lambda x: x[1], reverse=True)

        # New/increased overdue
        new_overdue = []
        for code, curr_s in curr_by_code.items():
            prev_s = prev_by_code.get(code)
            if curr_s["has_overdue"]:
                prev_overdue = prev_s["overdue_amount"] if prev_s else 0
                increase = curr_s["overdue_amount"] - prev_overdue
                if increase > 100:
                    new_overdue.append((curr_s["supplier_name"], curr_s["overdue_amount"], increase))
        new_overdue.sort(key=lambda x: x[2], reverse=True)

        balance_change = current["total_balance"] - previous["total_balance"]
        overdue_change = current["total_overdue"] - previous["total_overdue"]

        comparison_text = f"""
PALYGINIMAS SU ANKSTESNIU SNAPSHOT ({previous.get('snapshot_date', 'nežinoma data')}):
- Bendras balansas: {_format_currency(previous['total_balance'])} → {_format_currency(current['total_balance'])} ({'+' if balance_change >= 0 else ''}{_format_currency(balance_change)})
- Pradelsta: {_format_currency(previous['total_overdue'])} → {_format_currency(current['total_overdue'])} ({'+' if overdue_change >= 0 else ''}{_format_currency(overdue_change)})

SUMOKĖTA / SUMAŽINTA SKOLA TIEKĖJAMS:
{chr(10).join(f"  - {name}: {_format_currency(amount)}" for name, amount in paid_off[:10]) if paid_off else "  (nėra reikšmingų apmokėjimų)"}

NAUJA / PADIDĖJUSI PRADELSTA:
{chr(10).join(f"  - {name}: {_format_currency(total)} iš viso (padidėjo {_format_currency(inc)})" for name, total, inc in new_overdue[:10]) if new_overdue else "  (nėra naujų pradelstų)"}
"""

    prompt = f"""Tu esi finansų analitikas. Analizuok šiuos mokėtinų sumų tiekėjams (accounts payable) duomenis ir pateik įžvalgas lietuvių kalba.

{current_summary}
{comparison_text}

Pateik struktūruotą analizę:
1. **Bendra situacija** – trumpas situacijos apibūdinimas
2. **Kritiniai pradelsti mokėjimai** – kuriems tiekėjams labiausiai vėluojama ir rizika santykiams
3. **Teigiami pokyčiai** – kas sumokėta (tik jei yra palyginimas)
4. **Prioritetiniai mokėjimai** – ką reikia sumokėti artimiausiu metu
5. **Rekomendacijos** – likvidumo valdymo pasiūlymai

Būk konkretus, naudok skaičius. Pabrėžk, kurie vėlavimai kelia didžiausią riziką santykiams su tiekėjais.
"""

    response = _get_client().messages.create(
        model=MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )

    return response.content[0].text

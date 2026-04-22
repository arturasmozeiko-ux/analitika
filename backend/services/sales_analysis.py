"""AI analysis for sales – one API call per team, focused on BP."""
import os
from typing import Optional
import anthropic

TEAM_DISPLAY = {
    "SAVI":        "SAVI",
    "ROMBAS":      "ROMBAS",
    "SOSTINES":    "SOSTINĖ",
    "UZ_VILNIAUS": "UZ LIETUVĄ",
}
TEAM_ORDER = ["SAVI", "ROMBAS", "SOSTINES", "UZ_VILNIAUS"]
MODEL = "claude-opus-4-6"


def _get_client():
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError("ANTHROPIC_API_KEY nėra nustatytas")
    return anthropic.Anthropic(api_key=key)


def _bp(v: float) -> str:
    return f"{v:,.2f} €"


def _delta(now: float, prev: float) -> str:
    d = now - prev
    sign = "+" if d >= 0 else ""
    pct = d / prev * 100 if prev else 0
    return f"{sign}{d:,.2f} € ({sign}{pct:.1f}%)"


def _client_rows(clients: list, bp_key: str = "bp", prev_key: str = "") -> str:
    if not clients:
        return "  (nėra)\n"
    out = ""
    for c in clients[:12]:
        line = f"  • {c['name'][:52]}: BP {_bp(c[bp_key])}"
        if prev_key and prev_key in c:
            line += f"  (buvo {_bp(c[prev_key])})"
        out += line + "\n"
    return out


def analyze_overall(current: dict, previous: Optional[dict], year_ago: Optional[dict]) -> str:
    """Short overall summary + priorities — one API call."""
    ml   = f"{current['year']}-{current['month']:02d}"
    pl   = f"{previous['year']}-{previous['month']:02d}" if previous else "—"
    yl   = f"{year_ago['year']}-{year_ago['month']:02d}" if year_ago else "—"

    bp_now  = current["total_bp"]
    bp_prev = previous["total_bp"] if previous else None
    bp_yago = year_ago["total_bp"] if year_ago else None

    summary = f"Mėnuo: {ml}\nBP: {_bp(bp_now)}  Marža: {current['total_bp_pct']:.1f}%  Sąskaitos: {current['total_invoices']}\n"
    if bp_prev: summary += f"vs {pl}: {_delta(bp_now, bp_prev)}\n"
    if bp_yago: summary += f"vs {yl}: {_delta(bp_now, bp_yago)}\n"

    team_lines = ""
    for tc in TEAM_ORDER:
        td = current["teams"].get(tc)
        if not td:
            continue
        tn = TEAM_DISPLAY[tc]
        pt = previous["teams"].get(tc) if previous else None
        yt = year_ago["teams"].get(tc) if year_ago else None
        team_lines += f"\n{tn}: BP {_bp(td['bp'])}  Marža {td['bp_pct']:.1f}%  Aktyvūs {td['active_clients']}/{td['total_clients']}"
        if pt: team_lines += f"  vs {pl}: {_delta(td['bp'], pt['bp'])}"
        if yt: team_lines += f"  vs {yl}: {_delta(td['bp'], yt['bp'])}"
        team_lines += "\n"

    prompt = f"""Tu esi pardavimų analitikas. Atsakyk LIETUVIŲ KALBA. Pagrindinis rodiklis – BENDRASIS PELNAS (BP).

DUOMENYS:
{summary}{team_lines}

Pateik:

## BENDRA SITUACIJA
2–4 sakiniai: kaip sekėsi {ml}, tendencija vs {pl} ir {yl}, svarbiausia išvada.

## PRIORITETINIAI VEIKSMAI
Konkretūs 4–5 veiksmai visam pardavimų skyriui šį mėnesį. Tik veiksmai, ne statistika."""

    r = _get_client().messages.create(
        model=MODEL, max_tokens=1024,
        messages=[{"role": "user", "content": prompt}]
    )
    return r.content[0].text


def analyze_team(team_code: str, td: dict,
                 prev_td: Optional[dict], yago_td: Optional[dict],
                 prev_label: str, yago_label: str) -> str:
    """Full analysis for one team — separate API call."""
    name = TEAM_DISPLAY.get(team_code, team_code)
    bp_now  = td["bp"]
    bp_prev = prev_td["bp"] if prev_td else None
    bp_yago = yago_td["bp"] if yago_td else None
    inactive = td.get("inactive_clients", td.get("inactive_count", 0))

    stats = (
        f"BP: {_bp(bp_now)}  Marža: {td['bp_pct']:.1f}%  Sąskaitos: {td['invoice_count']}\n"
        f"Aktyvūs klientai: {td['active_clients']} iš {td['total_clients']}  Neperkantys: {inactive}\n"
    )
    if bp_prev: stats += f"vs {prev_label}: {_delta(bp_now, bp_prev)}\n"
    if bp_yago: stats += f"vs {yago_label}: {_delta(bp_now, bp_yago)}\n"

    def section(title, clients, bp_key="bp", prev_key=""):
        if not clients:
            return ""
        return f"\n{title}:\n{_client_rows(clients, bp_key, prev_key)}"

    data = stats
    data += section(f"Top klientai pagal BP", td.get("top_by_bp", []))
    data += section(f"Nustojo pirkti vs {prev_label}", td.get("lost_vs_prev", []), "bp", "bp")
    data += section(f"Nauji / grįžo vs {prev_label}", td.get("new_vs_prev", []))
    data += section(f"Pirko 2026 anksčiau, nepirko dabar", td.get("lost_vs_ytd", []), "bp", "bp")
    data += section(f"Pirko {yago_label}, šiemet nepirko", td.get("lost_vs_yago", []), "bp", "bp")
    data += section(f"Pirko 2025, dar negrįžo 2026", td.get("lost_vs_2025", []), "bp", "bp")

    prompt = f"""Tu esi pardavimų analitikas. Atsakyk LIETUVIŲ KALBA. Pagrindinis rodiklis – BENDRASIS PELNAS (BP).

KOMANDA: {name}
{data}

Pateik išsamią analizę:

### Rezultatai
Trumpas vertinimas – kaip sekėsi lyginant su {prev_label} ir {yago_label}.

### Stipriausi klientai (BP)
Pakomentuok top klientus – kas pirkdavo reguliariai, kas naujas, kas padidino pirkimus.

### Prarasti klientai – prioritetai susigrąžinti
Kuriuos klientus svarbiausia susigrąžinti ir kodėl (pagal BP dydį ir praradimo tipą).

### Rekomendacijos
3–4 konkretūs veiksmai šiai komandai ateinančiam mėnesiui. Vardink klientus vardais."""

    r = _get_client().messages.create(
        model=MODEL, max_tokens=2048,
        messages=[{"role": "user", "content": prompt}]
    )
    return r.content[0].text


def analyze_sales_month(current: dict, previous: Optional[dict], year_ago: Optional[dict]) -> dict:
    """
    Returns dict: {
        "overall": str,
        "SAVI": str, "ROMBAS": str, "SOSTINES": str, "UZ_VILNIAUS": str
    }
    Each generated via separate API call.
    """
    prev_label = f"{previous['year']}-{previous['month']:02d}" if previous else "—"
    yago_label = f"{year_ago['year']}-{year_ago['month']:02d}" if year_ago else "—"

    result = {}
    result["overall"] = analyze_overall(current, previous, year_ago)

    for team_code in TEAM_ORDER:
        td = current["teams"].get(team_code)
        if not td:
            result[team_code] = ""
            continue
        prev_td = previous["teams"].get(team_code) if previous else None
        yago_td = year_ago["teams"].get(team_code) if year_ago else None
        result[team_code] = analyze_team(team_code, td, prev_td, yago_td, prev_label, yago_label)

    return result

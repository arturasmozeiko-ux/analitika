"""
AI analizė pirkimų moduliui.
"""
import os
import anthropic


def _client():
    return anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def _call(system: str, user_msg: str, max_tokens: int = 2048) -> str:
    resp = _client().messages.create(
        model="claude-opus-4-6",
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )
    return resp.content[0].text


def _fmt_list(items, fields, max_n=30):
    lines = []
    for it in items[:max_n]:
        parts = [f"{k}: {v}" for k, v in it.items() if k in fields]
        lines.append("  • " + "  |  ".join(parts))
    return "\n".join(lines) if lines else "  (nėra)"


SYSTEM = """Tu esi pirkimų ir sandėlio analitikas. Atsakyk LIETUVIŲ KALBA.
Pagrindinis tikslas – identifikuoti problemas sandėlyje ir pateikti konkrečias rekomendacijas.
Naudok skaičius ir faktinius duomenis. Būk konkretus ir glaustas."""


def analyze_overall(data: dict) -> str:
    snap_date = data["snap_date"]
    tot_val   = data["total_value"]
    tot_items = data["total_items"]
    wd        = data.get("weekly_delta")

    if wd:
        prev_date    = wd["prev_date"]
        val_chg      = wd["value_change"]
        val_chg_pct  = (val_chg / (tot_val - val_chg) * 100) if (tot_val - val_chg) != 0 else 0
        items_chg    = wd["items_change"]

        def _fmt_delta(rows, fields):
            lines = []
            for r in rows:
                parts = [f"{k}: {v}" for k, v in r.items() if k in fields]
                lines.append("  • " + "  |  ".join(parts))
            return "\n".join(lines) if lines else "  (nėra)"

        grown_text  = _fmt_delta(wd["top_grown"],  ["name", "category", "qty_delta", "value_delta"])
        shrunk_text = _fmt_delta(wd["top_shrunk"], ["name", "category", "qty_delta", "value_delta"])
        new_text    = _fmt_delta(wd["new_items"],  ["name", "category", "qty", "value"])

        prompt = f"""Sandėlio savaitinis pokytis: {prev_date} → {snap_date}

BENDRA SUVESTINĖ:
  Sandėlio vertė: {tot_val:,.2f} € (pokytis: {val_chg:+,.2f} €, {val_chg_pct:+.1f}%)
  Unikalių prekių: {tot_items} (pokytis: {items_chg:+d})
  Padidėjo kiekis: {wd['grown_count']} prekių
  Sumažėjo kiekis: {wd['shrunk_count']} prekių
  Naujos prekės sandėlyje: {wd['new_count']}
  Dingo iš sandėlio: {wd['gone_count']} prekių

TOP PADIDĖJUSIOS (pagal vertę, vnt. delta ir vertės delta):
{grown_text}

TOP SUMAŽĖJUSIOS (didžiausias vertės kritimas):
{shrunk_text}

NAUJOS PREKĖS:
{new_text}

Pateik savaitinio pokyčio analizę (4-6 sakiniai):
1. Kokia bendra tendencija — sandėlis auga ar mažėja ir kodėl?
2. Kurios kategorijos/prekės labiausiai keitėsi?
3. Ar yra nerimą keliančių signalų (staigus augimas, dingusios prekės)?"""
    else:
        cat_text = _fmt_list(data["categories"], ["name", "value", "days_stock"], 20)
        prompt = f"""Sandėlio situacija {snap_date}. Bendra vertė: {tot_val:,.2f} €. Prekių: {tot_items}.

TOP kategorijos:
{cat_text}

Parašyk bendrą sandėlio situacijos apžvalgą (4-6 sakiniai)."""

    return _call(SYSTEM, prompt, 1024)


def analyze_illiquid(data: dict) -> str:
    snap_date = data["snap_date"]

    none_text    = _fmt_list(data["illiquid_none"],    ["name", "category", "qty", "value"], 30)
    partial_text = _fmt_list(data["illiquid_partial"], ["name", "category", "qty", "value", "sold_12m", "sold_pct"], 30)

    none_val    = sum(i["value"] for i in data["illiquid_none"])
    partial_val = sum(i["value"] for i in data["illiquid_partial"])

    prompt = f"""Sandėlio data: {snap_date}.

VISIŠKAI NEJUDANČIOS PREKĖS (0 pardavimų per paskutinius 12 mėnesių) — {len(data['illiquid_none'])} vnt., vertė: {none_val:,.2f} €:
{none_text}

DALINAI NEJUDANČIOS PREKĖS (per 12 mėn. parduota ≤20% dabartinio likučio) — {len(data['illiquid_partial'])} vnt., vertė: {partial_val:,.2f} €:
{partial_text}

Pateik analizę:
1. Kokie produktų tipai/kategorijos dominuoja kiekvienoje grupėje?
2. Kokia bendra finansinė rizika (įšaldytas kapitalas) — atskirai abiem grupėms?
3. Konkrečios rekomendacijos: ką daryti su visiškai nejudančiomis ir su dalinai nejudančiomis prekėmis (akcijos, grąžinti tiekėjui, nurašyti, sumažinti pirkimus)?"""
    return _call(SYSTEM, prompt, 2048)


def analyze_overstock(data: dict) -> str:
    snap_date  = data["snap_date"]
    total_days = data["total_days"]

    over_text = _fmt_list(data["overstock"], ["name", "category", "qty", "value", "days_stock", "daily_avg"], 40)
    prompt = f"""Sandėlio data: {snap_date}. Analizuota {total_days:.0f} dienų.

Prekės su per dideliu likučiu (daugiau nei 84 dienų atsarga):
{over_text}

Pateik analizę:
1. Kurios prekės/kategorijos reikalauja skubaus dėmesio (didžiausia vertė sandėlyje)?
2. Kur buvo per daug užsakyta?
3. Konkrečios rekomendacijos – sustabdyti užsakymus, mažinti likučius."""
    return _call(SYSTEM, prompt, 2048)


def analyze_reorder(data: dict) -> str:
    snap_date  = data["snap_date"]
    total_days = data["total_days"]

    low_text = _fmt_list(data["low_stock"], ["name", "category", "qty", "value", "days_stock", "daily_avg"], 30)
    prompt = f"""Sandėlio data: {snap_date}. Analizuota {total_days:.0f} dienų.

Prekės, kurių greitai nebeliks (<14 dienų):
{low_text}

Pateik rekomendacijas:
1. Kurias prekes reikia užsakyti SKUBIAI (šią savaitę)?
2. Rekomenduojamas užsakymo kiekis (orientuotis į 4-8 savaičių atsargą)?
3. Ar yra pastebimų tendencijų – kokios kategorijos sistemingai baigiasi?"""
    return _call(SYSTEM, prompt, 2048)

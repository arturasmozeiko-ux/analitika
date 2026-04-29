# Analitika — projekto instrukcijos Claude'ui

## Svarbiausia taisyklė
**Kiekvieną kartą pridėjus naują funkciją, patobulinimą ar architektūrinį sprendimą —
atnaujink šį failą ir/arba palik komentarą atitinkamame kodo faile.**
Tai vienintelis būdas išlaikyti žinias tarp sesijų.

---

## Deploy workflow — PRIVALOMA po kiekvieno pakeitimo

**Kai šiame susirašinėjime atliekami kodo pakeitimai — Claude PRIVALO automatiškai:**

1. Sukurti git commit ir push į GitHub:
```bash
cd /Users/arturas/Documents/analitika
git add .
git commit -m "Trumpas pakeitimo aprašymas"
git push
```

2. Atnaujinti serverį:
```bash
ssh root@uzpm.l.dedikuoti.lt "cd /opt/analitika && git pull && systemctl restart analitika"
```

**Serverio duomenys:**
- Host: `uzpm.l.dedikuoti.lt` (IP: 62.77.152.172)
- Aplankas: `/opt/analitika`
- Servisas: `analitika.service`
- Portas: `8004`
- Domain: `analitika.mgrupe.lt`
- GitHub: `https://github.com/arturasmozeiko-ux/analitika.git`

**Išimtys — NEKELIA į serverį automatiškai:**
- Jei pakeitimas tik lokalus testavimui
- Jei vartotojas aiškiai sako „dar nekelt"
- Jei keičiamas tik CLAUDE.md

---

## Projekto struktūra

```
analitika/
├── CLAUDE.md                  ← šis failas (instrukcijos Claude'ui)
├── run.py                     ← paleidimas: uvicorn ant 0.0.0.0:8004
├── backend/
│   ├── main.py                ← FastAPI app, routerių registracija, default admin kūrimas
│   ├── auth.py                ← JWT, bcrypt, require_*() dependency funkcijos
│   ├── database.py            ← SQLAlchemy + SQLite
│   ├── models/
│   │   ├── user.py            ← User modelis su modulių teisėmis
│   │   ├── comment.py         ← Comment modelis (atsekamumui)
│   │   ├── receivables.py
│   │   ├── payables.py
│   │   ├── sales.py
│   │   └── purchases.py       ← PurchaseCategory, WarehouseSnapshot/Item, SalesWeekSnapshot/Item,
│   │                             PurchaseAnalysis, ClearanceItem
│   ├── routers/
│   │   ├── auth.py            ← /api/auth/* (login, users CRUD, /me/password)
│   │   ├── comments.py        ← /api/comments/* (CRUD)
│   │   ├── receivables.py
│   │   ├── payables.py
│   │   ├── sales.py
│   │   └── purchases.py       ← visa pirkimų logika (~1000 eilučių)
│   └── services/
│       ├── purchases_parser.py   ← Excel parseriai (warehouse, sales, categories, clearance)
│       └── purchases_analysis.py ← AI analizė (analyze_overall/illiquid/overstock/reorder)
└── frontend/
    ├── index.html             ← vieno puslapio aplikacija
    ├── js/app.js              ← visas frontend JS (~2700+ eilučių)
    └── css/style.css
```

---

## Autentifikacija ir teisės

- **JWT** su bcrypt, tokenas saugomas `localStorage`
- **Numatytasis admin**: `admin / admin123` (sukuriamas automatiškai jei DB tuščia)
- Kiekvienas vartotojas turi laukus: `is_admin`, `is_active`, `can_view_*`

### Kaip pridėti naują modulį

1. **`backend/models/user.py`** → `can_view_[modulis] = Column(Boolean, default=False)`
2. **`backend/auth.py`** → nauja `require_[modulis]()` funkcija
3. **`backend/routers/auth.py`** → `UserCreate`, `UserUpdate` ir `_user_dict()` papildyti nauju lauku
4. **`frontend/js/app.js`** → `ADMIN_MODULES` masyve: `{ key: 'can_view_[modulis]', label: '...' }`
5. **`frontend/js/app.js`** `showApp()` → `if (mods.[modulis]) document.getElementById('nav-[modulis]').classList.remove('disabled');`
6. **`frontend/index.html`** → `<li class="nav-item disabled" data-module="[modulis]" id="nav-[modulis]">` navigacijoje

Admin UI checkbox'as atsiras automatiškai iš `ADMIN_MODULES` masyvo.

---

## Pirkimų modulio logika (purchases)

### Sandėlio snapshot'ai
- Kiekvienas įkėlimas sukuria `WarehouseSnapshot` su data
- `WarehouseItem.qty_delta` = skirtumas nuo ankstesnio snapshot'o (skaičiuojamas API atsakyme, ne DB)
- **SVARBU — trynimo RAM problema (ištaisyta 2026-04):** `cascade="all, delete-orphan"` ant
  `WarehouseSnapshot.items` įkelia VISAS eilutes į Python atmintį prieš trindamas. 1 GB serveriui
  tai reiškia OOM. **Sprendimas:** delete endpoint'e naudoti `db.query(WarehouseItem).filter(...).delete(synchronize_session=False)` PRIEŠ `db.delete(snap)`. Ta pati logika `delete_sales_week`.

### WarehouseItem laukai (nuo 2026-04-22)
- `product_code`, `category_code` (gali būti tuščia), `product_name`
- `quantity`, `unit_cost`, `total_value`
- `product_group_manager` — "Produktų grupės vadovas" (AZUSINAITE, GSTALIAUSKE ir kt.)
- `product_category` — "Prekės kategorija" (ASORTIMENTAS, UŽSAKOMA, IŠPARDAVIMAS ir kt.)

### Excel formato pastabos (ladu_aru_seis formatas)
- Antraštė 20-oje eilutėje: Prekė, Pavadinimas, Sandėlis, Lentyna, Kiekis, Savikaina, Suma, Produktų grupės vadovas, Prekės kategorija
- Nėra "Klasė" stulpelio naujame formate (category_code bus tuščias)
- Footer eilutės su ":" simboliu kode filtruojamos automatiškai
- Filter options endpoint: `GET /api/purchases/warehouse/snapshots/{id}/filter-options`

### Pardavimų periodo skaičiavimas
- **Pardavimams (12 mėn. nelikvidumui):** `_twelve_months_back(snap_date)` — 12 kalendorinių mėnesių atgal nuo sandėlio snapshot datos, **NE nuo šiandien**
- **Pardavimams (3 mėn. greičiui):** `_three_months_back(snap_date)` — 3 kalendoriniai mėnesiai atgal
- `total_days` skaičiuojamas iš faktinių periodų datų, ne dienų kiekiu
- Pardavimų periodai, patenkantys į langą: `period_from >= cutoff AND period_to <= snap_date`

### Nejudančių prekių klasifikacija (`_get_young_product_codes`)

**SVARBU:** funkcija seniau vadinosi `_get_new_product_codes` — dabar `_get_young_product_codes`.
Logika pasikeitė 2026-04: naujų prekių lengvata pratęsta iki **12 MĖNESIŲ** (buvo 1 savaitė).

Prekė laikoma **NAUJA** (nepateks į „Visiškai nejuda" sąrašą) jei tenkina **VISUS 3 kriterijus**:
1. Pirmą kartą pasirodė sandėlyje **PO** pirmojo snapshot'o (ne baziniam inventoriuje)
2. Pirmą kartą pasirodė per **paskutinius 12 mėnesių** nuo dabartinio snap datos
3. **Niekada nebuvo parduota** (jei buvo parduota — nebelaikoma nauja)

```python
# backend/routers/purchases.py — _get_young_product_codes()
return {
    row.product_code
    for row in rows
    if row.first_date > first_snapshot.snap_date   # ne bazinis inventorius
    and row.first_date > cutoff_12m                # 12 mėn. lengvata dar galioja
    and row.product_code not in ever_sold          # tikrai niekada neparduota
}
```

**Kodėl 1-as kriterijus svarbus:** sistema veikia tik ~3 savaitės, visi produktai yra per
12 mėnesių nuo pradžios. Be šio kriterijaus visi produktai būtų "nauji" ir "Visiškai nejuda"
sąrašas būtų tuščias.

### Nelikvidumas — skaičiavimas

```python
if sold_12m == 0 and product_code not in new_codes:
    illiquid_type = "none"      # Visiškai nejuda
elif sold_12m > 0 and sold_12m / quantity <= 0.20:
    illiquid_type = "partial"   # Dalinai nejuda
# sold_12m > 0 būtina sąlyga — 0-pardavimų prekės NEpateks į "dalinai"
```

### Langų logika
| Langas | Sąlyga |
|--------|--------|
| Visiškai nejuda | `sold_12m == 0` ir ne nauja prekė (ne `_get_young_product_codes`) |
| Dalinai nejuda | `sold_12m > 0` ir `sold_12m / qty ≤ 20%` |
| Per daug sandėlyje | `days_stock > 84` (>12 savaičių), `daily_avg` iš 3 mėn. |
| Greitai baigsis | `days_stock < 14` (<2 savaitės) |

### `is_newly_illiquid` laukas (nuo 2026-04)

API atsakyme `get_warehouse_items()` yra laukas `is_newly_illiquid`:
```python
is_newly_illiquid = (
    illiquid_type == "none"
    and prev_snap is not None
    and (it.product_code not in prev_qty   # nebuvo ankstesniame snapshot'e
         or it.product_code in prev_new_codes)  # arba buvo "nauja" ankstesniame
)
```
Frontend naudoja `purNewBadge(i)` — rodo raudoną `● NAUJA` badge'ą jei `i.is_newly_illiquid`.

### Spalvų logika strelytėms
- **Įprastuose languose**: ▲ žalia (padaugėjo), ▼ raudona (sumažėjo) — `purQtyDelta()`
- **Nejudančių languose**: ▲ raudona (blogai — dar daugiau), ▼ žalia (gerai — parduota) — `purQtyDeltaIlliq()`

### NAUJA žyma (`purNewBadge`)

```javascript
function purNewBadge(i) {
  if (i.is_newly_illiquid) {
    return '<span style="color:#dc2626;...">● NAUJA</span>';
  }
  // Legacy: jei nėra is_newly_illiquid, tikrina qty_delta ≈ quantity
  if (!i.prev_snap_date || ...) return '';
  if (i.quantity > 0 && Math.abs(i.qty_delta - i.quantity) < 0.001) {
    return '<span ...>● NAUJA</span>';
  }
  return '';
}
```

---

## API limitai ir paginacija

- **Sandėlio items API:** `GET /api/purchases/warehouse/snapshots/{id}/items?limit=10000&offset=0`
- Frontend visada krauna `limit: 10000` (pakankamai visiems produktams)
- Filtravimas pagal manager/prodcat — **kliento pusėje** (visi items jau pakrauti)
- Filtravimas pagal category/search — **serverio pusėje** (query parametrai)
- **Specialių tab'ų (Visiškai nejuda, Dalinai nejuda, Per daug sandėlyje) render'inimas:**
  visada `sorted.map(...)` be `.slice()` — rodo visus elementus, ne tik pirmus 300

---

## Frontend architektūra

### Navigacija
- Moduliai slepiami/rodomi pagal `currentUser.modules.*`
- Admin mato papildomą **⚙ Administravimas** punktą
- Vartotojo vardas (sidebar) → paspaudus atsidaro slaptažodžio keitimo modalas

### Admin modulis (js/app.js)
- `ADMIN_MODULES` masyvas — vienintelė vieta kur aprašomi moduliai frontend'e
- `adminLoad()` → `adminRenderUsers()` — vartotojų sąrašas
- `adminOpenCreate()` / `adminOpenEdit(id)` — modalinis langas
- `adminSaveUser()` — POST arba PATCH priklausomai nuo `adminEditingId`
- `adminDeleteUser(id)` — DELETE su confirm dialogo

### Pirkimų filtrai — visi tab'ai (nuo 2026-04-29)

Visi 4 tab'ai turi tuos pačius filtrus:
| Tab'as | Search | Klasė | Vadovas | Kategorija | Sort |
|--------|--------|-------|---------|------------|------|
| Sandėlio likučiai | ✓ (serverio) | ✓ (serverio) | ✓ (kliento) | ✓ (kliento) | ✓ |
| Visiškai nejuda | ✓ (kliento) | ✓ (kliento) | ✓ (kliento) | ✓ (kliento) | ✓ |
| Dalinai nejuda | ✓ (kliento) | ✓ (kliento) | ✓ (kliento) | ✓ (kliento) | ✓ |
| Per daug sandėlyje | ✓ (kliento) | ✓ (kliento) | ✓ (kliento) | ✓ (kliento) | ✓ |

**HTML ID konvencija:**
- `pur-none-search`, `pur-none-cat-filter`, `pur-none-manager-filter`, `pur-none-prodcat-filter`, `pur-none-sort-select`
- `pur-partial-*`, `pur-over-*` — ta pati schema

**JS funkcijos:**
- `purRenderNone()` — skaito visus 5 filtrus, filtruoja `purIlliquidNoneItems`
- `purRenderPartial()` — skaito visus 5 filtrus, filtruoja `purIlliquidPartialItems`
- `purRenderOverstock()` — skaito visus 5 filtrus, filtruoja `purOverstockItems`
- `purLoadFilterOptions(snapId)` — užpildo manager/prodcat/klasė dropdown'us visuose tab'uose
- `purSetupFilters()` — registruoja event listeners (search su 250ms debounce, select iš karto)

**Klasės dropdown'as** pildomas iš `purCatMap` (top-level kategorijos, `level <= 1`), **ne** iš serverio filter-options endpoint'o (kuris grąžina tik manager/categories).

### Event listener kaupimassi — DĖMESIO
`purSetupFilters()` kviečiamas **tik vieną kartą** (modulio init metu). Jei reikia dynamiškai
perkrauti filtrų dropdown'us — naudoti `purLoadFilterOptions()`, **ne** naują `addEventListener`.
`sel.onchange = ...` vietoj `sel.addEventListener('change', ...)` — neleidžia kaupimuisi.

### Komentarai (Visiškai nejuda + Dalinai nejuda)
- `Comment` modelis: `module='purchases'`, `entity_type='product'`, `entity_id=product_code`
- UI įdiegtas **2026-04-21**: stulpelis "Komentaras" abiuose nejudančių prekių lentelėse
- **Ląstelė**: komentaro peržiūra (pirmos 38 simboliai) + 💬 mygtukas, eilutės aukštis nesikeičia
- **Modalas** (`#pur-comment-modal`): visa istorija + redagavimas + trynimas + naujo rašymas
- **JS state**: `purCommentsCache = {}` (produkto kodas → komentarų masyvas), `purCommentModalCode`
- **Funkcijos**: `purLoadComments()`, `purCommentCellInner(code)`, `purRefreshCommentCells(code)`,
  `purOpenCommentModal(code, e)`, `purRenderCommentHistory()`, `purSaveComment()`,
  `purDeleteComment(id)`, `purStartEditComment(id)`, `purSaveEditComment(id)`, `purCancelEditComment()`
- `purLoadComments()` kviečiamas `purLoadInventory()` viduje po `purRenderSpecialTabs()`
- Enter = siųsti, Shift+Enter = nauja eilutė (textarea keydown listener `purSetupFilters()` viduje)
- Redaguoti/trinti gali tik savus komentarus (arba admin visus)

**Komentarų modalas — dvigubo atidarymo fix (2026-04):**
- `onclick` yra ant `<div class="pur-comment-inner">` (ne ant `<button>`) — abu elementai
  (preview tekstas ir 💬 mygtukas) naudoja tą patį handler'į
- `purOpenCommentModal(code, e)` — `e.stopPropagation()` sustabdo event bubbling
- Guard: jei modalas jau atidarytas tam pačiam product_code — ignoruoja antrą kvietimą

### Produkto nelikvidumo istorija (`purOpenCommentModal`)
Komentarų modalo viršuje rodoma produkto istorija iš `/api/purchases/warehouse/product-illiquid-history`.
Kiekvienam snapshot'ui rodoma:
- data, tipas (Visiškai nejuda / Dalinai nejuda / —), kiekis, pardavimai 12m

**`get_product_illiquid_history` endpoint'e** naudojama ta pati `is_young` logika kaip
`_get_young_product_codes` (3 kriterijai). Jei naudoji seną logiką — visas produktas bus
klasifikuojamas kaip `is_young=True` ir istorija bus tuščia.

### Išpardavimų sąrašas (nuo 2026-04-22)

- **Modelis**: `ClearanceItem` lentelė `clearance_items` — tik `product_code` (unique) + `uploaded_at`
- **Failas**: `.xlsb` (arba `.xlsx`) — 1-a eilutė header, 1-as stulpelis = prekės kodas
- **Parseris**: `parse_clearance_file()` — naudoja `pyxlsb` xlsb formatui, `openpyxl` xlsx formatui
- **Įkėlimas**: `POST /api/purchases/clearance/upload` — ištrina visus senus, įrašo naujus
- **Statusas**: `GET /api/purchases/clearance/status` — grąžina `{count, uploaded_at}`
- **`is_clearance`**: laukas `get_warehouse_items()` atsakyme — `true` jei `product_code` yra `clearance_items`
- **UI**: upload blokas pirkimų juostoje, stulpelis **Išpardavimas** visuose 4 tab'uose — rodo **TAIP** badge arba `—`
- **Priklausomybė**: `pyxlsb==1.0.10` įtraukta į `requirements.txt`

---

## API endpointai (svarbiausi)

| Endpointas | Metodas | Apsauga | Pastabos |
|-----------|---------|---------|---------|
| `/api/auth/login` | POST | — | |
| `/api/auth/me` | GET | JWT | |
| `/api/auth/me/password` | POST | JWT | senas slaptažodis reikalingas |
| `/api/auth/users` | GET, POST | admin | |
| `/api/auth/users/{id}` | PATCH, DELETE | admin | |
| `/api/comments` | GET, POST | JWT | |
| `/api/comments/{id}` | PATCH, DELETE | JWT | tik savus |
| `/api/purchases/categories` | GET | purchases | |
| `/api/purchases/categories/upload` | POST | purchases | |
| `/api/purchases/warehouse` | GET | purchases | |
| `/api/purchases/warehouse/upload` | POST | purchases | query: `snap_date` |
| `/api/purchases/warehouse/snapshots` | GET | purchases | sąrašas su analysis info |
| `/api/purchases/warehouse/snapshots/{id}` | DELETE | purchases | explicit SQL delete (ne ORM cascade) |
| `/api/purchases/warehouse/snapshots/{id}/items` | GET | purchases | `limit`, `offset`, `category`, `search`, `sort` |
| `/api/purchases/warehouse/snapshots/{id}/filter-options` | GET | purchases | managers + categories |
| `/api/purchases/warehouse/snapshots/{id}/analyze` | POST | purchases | paleisti AI analizę background'e |
| `/api/purchases/warehouse/snapshots/{id}/analysis` | GET | purchases | AI analizės rezultatai |
| `/api/purchases/warehouse/illiquid-history` | GET | purchases | visų snapshot'ų nelikvidumas |
| `/api/purchases/warehouse/product-illiquid-history` | GET | purchases | `?product_code=XXX` |
| `/api/purchases/sales-weeks` | GET | purchases | |
| `/api/purchases/sales-weeks/upload` | POST | purchases | `period_from`, `period_to` |
| `/api/purchases/sales-weeks/upload-annual` | POST | purchases | `year` query param |
| `/api/purchases/sales-weeks/{id}` | DELETE | purchases | explicit SQL delete |
| `/api/purchases/clearance/status` | GET | purchases | |
| `/api/purchases/clearance/upload` | POST | purchases | |

---

## Žinomos spąstai ir taisymai

### 1. ORM cascade = RAM išeikvojimas (ištaisyta)
`cascade="all, delete-orphan"` ant ryšio įkelia VISAS child eilutes į Python atmintį.
Su 50k+ eilučių = OOM ant 1 GB serverio.
**Sprendimas visada:** `db.query(ChildModel).filter(...).delete(synchronize_session=False)` prieš `db.delete(parent)`.

### 2. Visi produktai "nauji" su nauja logika
Jei sistema veikia trumpai (pvz. tik 3 savaitės), visi produktai atsiranda "per paskutinius 12 mėn."
→ visi laikomi "naujais" → "Visiškai nejuda" sąrašas tuščias.
**Sprendimas:** pirmasis kriterijus — `first_date > first_snapshot.snap_date` (ne bazinis inventorius).

### 3. Produktas neatsirandantis "Visiškai nejuda" (per didelis sąrašas)
Du galimi priežasčiai:
- API limit per mažas (default buvo 200, dabar 10000)
- Frontend `.slice(0, 300)` (pašalinta — dabar rodo viską)

### 4. Komentarų modalas atsidaro du kartus
Priežastis: `addEventListener('change', fn)` kaupiasi kiekvieną kartą, kai persikraunamas snapshot'ų sąrašas.
**Sprendimas:** `sel.onchange = fn` (ne addEventListener) + modalui guard'as + onclick ant `<div>` ne `<button>`.

### 5. Produkto istorija tuščia
`get_product_illiquid_history` naudojo seną `is_young` logiką → visi buvo `is_young=True` → tipas `None` → tuščia istorija.
**Sprendimas:** sinchronizuoti su `_get_young_product_codes` 3-kriterijų logika.

---

## Stiliaus konvencijos

- **CSS klasės**: `pur-*` pirkimams, `admin-*` administravimui
- **JS funkcijos**: `pur*()` pirkimams, `admin*()` administravimui, `profile*()` profiliui
- **Modalai**: `.modal` + `.modal-box-sm` (formos) arba `.modal-box-lg` (lentelės)
- **Klaidų atvaizdavimas**: `.admin-form-error` (raudona), `.admin-form-success` (žalia)
- **Filtrai**: `pur-filters` div su `pur-search-input`, `pur-cat-select`, `pur-sort-select` klasėmis

---

## Idėjos / galimi tobulinimai (neimplementuoti)

- Greitai baigsis (low stock) tab'o filtrai — dar neturi pilno filtravimo kaip kiti 4 tab'ai
- Eksportas į Excel — vartotojas gali norėti eksportuoti filtruotus sąrašus
- Email pranešimai apie naujai patekusias prekes į "Visiškai nejuda"
- Prekių palyginimas tarp dviejų snapshot'ų datos

# Analitika — projekto instrukcijos Claude'ui

## Svarbiausia taisyklė
**Kiekvieną kartą pridėjus naują funkciją, patobulinimą ar architektūrinį sprendimą —
atnaujink šį failą ir/arba palik komentarą atitinkamame kodo faile.**
Tai vienintelis būdas išlaikyti žinias tarp sesijų.

---

## Projekto struktūra

```
analitika/
├── CLAUDE.md                  ← šis failas (instrukcijos Claude'ui)
├── run.py                     ← paleidimas: uvicorn ant 0.0.0.0:8003
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
│   │   └── purchases.py
│   ├── routers/
│   │   ├── auth.py            ← /api/auth/* (login, users CRUD, /me/password)
│   │   ├── comments.py        ← /api/comments/* (CRUD)
│   │   ├── receivables.py
│   │   ├── payables.py
│   │   ├── sales.py
│   │   └── purchases.py
│   └── services/              ← Excel parseriai, AI analizė
└── frontend/
    ├── index.html             ← vieno puslapio aplikacija
    ├── js/app.js              ← visas frontend JS (~2500+ eilučių)
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
- `WarehouseItem.qty_delta` = skirtumas nuo ankstesnio snapshot'o

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

### Nejudančių prekių klasifikacija

```python
# _get_new_product_codes(snap_date, db) — grąžina "naujų" prekių kodus
# Nauja = tik šiame snapshot'e IR niekada neparduota
# Pirmas snapshot → grąžina set() (jokio filtravimo)

if sold_12m == 0 and product_code not in new_codes:
    illiquid_type = "none"      # Visiškai nejuda
elif sold_12m > 0 and sold_12m / quantity <= 0.20:
    illiquid_type = "partial"   # Dalinai nejuda
# sold_12m > 0 būtina sąlyga — neleidžia 0-pardavimų prekėms patekti į "dalinai"
```

### Langų logika
| Langas | Sąlyga |
|--------|--------|
| Visiškai nejuda | `sold_12m == 0` ir ne nauja prekė |
| Dalinai nejuda | `sold_12m > 0` ir `sold_12m / qty ≤ 20%` |
| Per daug sandėlyje | `days_stock > 84` (>12 savaičių) |
| Greitai baigsis | `days_stock < 14` (<2 savaitės) |

### Spalvų logika strelytėms
- **Įprastuose languose**: ▲ žalia (padaugėjo), ▼ raudona (sumažėjo) — `purQtyDelta()`
- **Nejudančių languose**: ▲ raudona (blogai — dar daugiau), ▼ žalia (gerai — parduota) — `purQtyDeltaIlliq()`

### NAUJA žyma
- `purNewBadge(i)`: raudona `● NAUJA` jei `qty_delta ≈ quantity` (prekė neegzistavo ankstesniame snapshot'e)
- Stulpelis `is_new` yra rikiuojamas kaip ir kiti stulpeliai (ne checkbox filtras)

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

### Išpardavimų sąrašas (nuo 2026-04-22)

- **Modelis**: `ClearanceItem` lentelė `clearance_items` — tik `product_code` (unique) + `uploaded_at`
- **Failas**: `.xlsb` (arba `.xlsx`) — 1-a eilutė header, 1-as stulpelis = prekės kodas
- **Parseris**: `parse_clearance_file()` — naudoja `pyxlsb` xlsb formatui, `openpyxl` xlsx formatui
- **Įkėlimas**: `POST /api/purchases/clearance/upload` — ištrina visus senus, įrašo naujus
- **Statusas**: `GET /api/purchases/clearance/status` — grąžina `{count, uploaded_at}`
- **`is_clearance`**: laukas `get_warehouse_items()` atsakyme — `true` jei `product_code` yra `clearance_items`
- **UI**: upload blokas pirkimų juostoje, stulpelis **Išpardavimas** 4 tab'uose (Sandėlio likučiai, Visiškai nejuda, Dalinai nejuda, Per daug sandėlyje) — rodo **TAIP** badge arba `—`
- **Priklausomybė**: `pyxlsb==1.0.10` įtraukta į `requirements.txt`

---

## API endpointai (svarbiausi)

| Endpointas | Metodas | Apsauga |
|-----------|---------|---------|
| `/api/auth/login` | POST | — |
| `/api/auth/me` | GET | JWT |
| `/api/auth/me/password` | POST | JWT (senas slaptažodis reikalingas) |
| `/api/auth/users` | GET, POST | admin |
| `/api/auth/users/{id}` | PATCH, DELETE | admin |
| `/api/comments` | GET, POST | JWT |
| `/api/comments/{id}` | PATCH, DELETE | JWT (tik savus) |
| `/api/purchases/warehouse` | GET | purchases |
| `/api/purchases/warehouse/illiquid-history` | GET | purchases |
| `/api/purchases/clearance/status` | GET | purchases |
| `/api/purchases/clearance/upload` | POST | purchases |
| `/api/purchases/warehouse/product-illiquid-history` | GET | purchases |

---

## Stiliaus konvencijos

- **CSS klasės**: `pur-*` pirkimams, `admin-*` administravimui
- **JS funkcijos**: `pur*()` pirkimams, `admin*()` administravimui, `profile*()` profiliui
- **Modalai**: `.modal` + `.modal-box-sm` (formos) arba `.modal-box-lg` (lentelės)
- **Klaidų atvaizdavimas**: `.admin-form-error` (raudona), `.admin-form-success` (žalia)

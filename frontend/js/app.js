const API = '';
let token = localStorage.getItem('token');
let currentUser = null;
let currentSnapshotId = null;
let allBuyers = [];
let sortCol = 'balance';
let sortDir = 'desc';

// ── AUTH ──────────────────────────────────────────────────────────────────────

async function apiFetch(path, opts = {}) {
  const res = await fetch(API + path, {
    ...opts,
    headers: {
      ...(token ? { Authorization: 'Bearer ' + token } : {}),
      ...(opts.headers || {}),
    },
  });
  if (res.status === 401) { logout(); return null; }
  return res;
}

document.getElementById('login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const errEl = document.getElementById('login-error');
  errEl.classList.add('hidden');

  const body = new URLSearchParams({
    username: document.getElementById('username').value,
    password: document.getElementById('password').value,
  });

  const res = await fetch(API + '/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  });

  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    errEl.textContent = data.detail || 'Prisijungimo klaida';
    errEl.classList.remove('hidden');
    return;
  }

  const data = await res.json();
  token = data.access_token;
  localStorage.setItem('token', token);
  currentUser = data.user;
  showApp();
});

document.getElementById('btn-logout').addEventListener('click', logout);

function logout() {
  token = null;
  currentUser = null;
  localStorage.removeItem('token');
  document.getElementById('app').classList.add('hidden');
  document.getElementById('login-screen').classList.remove('hidden');
}

async function checkAuth() {
  if (!token) return;
  const res = await apiFetch('/api/auth/me');
  if (!res || !res.ok) { token = null; localStorage.removeItem('token'); return; }
  currentUser = await res.json();
  showApp();
}

function showApp() {
  document.getElementById('login-screen').classList.add('hidden');
  document.getElementById('app').classList.remove('hidden');
  document.getElementById('nav-username').textContent = currentUser.full_name || currentUser.username;

  // Enable only permitted modules
  const mods = currentUser.modules;
  if (mods.receivables) document.getElementById('nav-receivables').classList.remove('disabled');
  if (mods.payables)    document.getElementById('nav-payables').classList.remove('disabled');
  if (mods.sales)       document.getElementById('nav-sales').classList.remove('disabled');
  if (mods.purchases)   document.getElementById('nav-purchases').classList.remove('disabled');

  // Show admin nav only for admins
  if (currentUser.is_admin) {
    const navAdmin = document.getElementById('nav-admin');
    navAdmin.classList.remove('hidden');
    navAdmin.classList.remove('disabled');
  }

  // Activate first permitted module
  const order = ['receivables', 'payables', 'sales', 'purchases', 'admin'];
  const first = order.find(m => {
    if (m === 'admin') return currentUser.is_admin;
    return mods[m];
  });
  if (first) {
    document.querySelectorAll('.module').forEach(m => m.classList.add('hidden'));
    document.getElementById(`nav-${first}`).classList.add('active');
    document.getElementById(`module-${first}`).classList.remove('hidden');
  }

  if (mods.receivables) loadSnapshots();
}

// ── NAVIGATION ────────────────────────────────────────────────────────────────

document.querySelectorAll('.nav-item').forEach(item => {
  item.addEventListener('click', () => {
    if (item.classList.contains('disabled') || item.classList.contains('hidden')) return;
    if (item.dataset.module === 'admin' && (!currentUser || !currentUser.is_admin)) return;
    document.querySelectorAll('.nav-item').forEach(i => i.classList.remove('active'));
    document.querySelectorAll('.module').forEach(m => m.classList.add('hidden'));
    item.classList.add('active');
    document.getElementById('module-' + item.dataset.module).classList.remove('hidden');
    if (item.dataset.module === 'admin') adminLoad();
  });
});

// ── UPLOAD ────────────────────────────────────────────────────────────────────

document.getElementById('btn-upload').addEventListener('click', () => {
  document.getElementById('upload-panel').classList.toggle('hidden');
});

document.getElementById('btn-cancel-upload').addEventListener('click', () => {
  document.getElementById('upload-panel').classList.add('hidden');
});

document.getElementById('btn-do-upload').addEventListener('click', async () => {
  const fileInput = document.getElementById('file-input');
  const statusEl = document.getElementById('upload-status');

  if (!fileInput.files.length) {
    showStatus(statusEl, 'Pasirinkite Excel failą', 'err');
    return;
  }

  const formData = new FormData();
  formData.append('file', fileInput.files[0]);
  const reportDate = document.getElementById('report-date').value;
  if (reportDate) formData.append('report_date', reportDate);

  showStatus(statusEl, 'Įkeliama ir analizuojama...', 'info');
  document.getElementById('btn-do-upload').disabled = true;

  const res = await apiFetch('/api/receivables/upload', { method: 'POST', body: formData });

  document.getElementById('btn-do-upload').disabled = false;

  if (!res || !res.ok) {
    const err = await res?.json().catch(() => ({}));
    showStatus(statusEl, err.detail || 'Klaida įkeliant failą', 'err');
    return;
  }

  const data = await res.json();
  showStatus(statusEl, `✓ Įkelta: ${data.total_buyers} pirkėjų, balansas ${fmt(data.total_balance)} €. AI analizė vykdoma fone.`, 'ok');

  setTimeout(() => {
    document.getElementById('upload-panel').classList.add('hidden');
    statusEl.classList.add('hidden');
    fileInput.value = '';
    loadSnapshots();
    loadSnapshot(data.snapshot_id);
  }, 2500);
});

function showStatus(el, msg, type) {
  el.textContent = msg;
  el.className = 'upload-status ' + type;
  el.classList.remove('hidden');
}

// ── SNAPSHOTS LIST ────────────────────────────────────────────────────────────

async function loadSnapshots() {
  const res = await apiFetch('/api/receivables/snapshots');
  if (!res || !res.ok) return;
  const snapshots = await res.json();

  const container = document.getElementById('snapshots-list');
  if (!snapshots.length) {
    container.innerHTML = '<div class="loading">Nėra įkeltų ataskaitų. Įkelkite pirmą Excel failą.</div>';
    return;
  }

  container.innerHTML = snapshots.map(s => `
    <div class="snapshot-row" data-id="${s.id}" onclick="loadSnapshot(${s.id})">
      <div class="snap-info">
        <span class="snap-name">${s.file_name}
          ${s.has_analysis ? '<span class="snap-badge badge-ai">🤖 AI</span>' : ''}
        </span>
        <span class="snap-meta">
          Įkelta: ${formatDate(s.upload_date)}
          ${s.report_date ? ' · Ataskaita: ' + s.report_date : ''}
          · ${s.total_buyers} pirkėjų
        </span>
      </div>
      <div class="snap-amounts">
        <div class="snap-balance">${fmt(s.total_balance)} €</div>
        ${s.total_overdue > 0 ? `<div class="snap-overdue">⚠ Pradelsta: ${fmt(s.total_overdue)} €</div>` : '<div style="font-size:12px;color:#10b981">✓ Nėra pradelstų</div>'}
      </div>
    </div>
  `).join('');

  // Auto-load first snapshot
  if (snapshots.length && !currentSnapshotId) {
    loadSnapshot(snapshots[0].id);
  }
}

// ── SNAPSHOT DETAIL ───────────────────────────────────────────────────────────

async function loadSnapshot(id) {
  currentSnapshotId = id;

  // Highlight active row
  document.querySelectorAll('.snapshot-row').forEach(r => r.classList.remove('active'));
  const row = document.querySelector(`.snapshot-row[data-id="${id}"]`);
  if (row) row.classList.add('active');

  document.getElementById('snapshot-detail').classList.remove('hidden');

  const res = await apiFetch(`/api/receivables/snapshots/${id}`);
  if (!res || !res.ok) return;
  const data = await res.json();

  renderStats(data);
  renderBuyers(data.buyers);
  renderAI(data);

  allBuyers = data.buyers;
}

function renderStats(data) {
  const overduePercent = data.total_balance > 0
    ? ((data.total_overdue / data.total_balance) * 100).toFixed(1)
    : '0.0';

  document.getElementById('stats-cards').innerHTML = `
    <div class="stat-box">
      <div class="stat-value col-primary">${fmt(data.total_balance)} €</div>
      <div class="stat-label">Bendras balansas</div>
      <div class="stat-sub" style="color:#64748b">Neapmokėta: ${fmt(data.total_unpaid)} €</div>
    </div>
    <div class="stat-box">
      <div class="stat-value ${data.total_overdue > 0 ? 'col-danger' : 'col-success'}">${fmt(data.total_overdue)} €</div>
      <div class="stat-label">Pradelsta</div>
      <div class="stat-sub ${data.total_overdue > 0 ? 'col-danger' : ''}">${overduePercent}% nuo balanso</div>
    </div>
    <div class="stat-box">
      <div class="stat-value">${data.total_buyers}</div>
      <div class="stat-label">Pirkėjų</div>
      <div class="stat-sub" style="color:#64748b">${data.total_invoices} sąskaitos</div>
    </div>
    <div class="stat-box">
      <div class="stat-value ${data.overdue_buyers > 0 ? 'col-warning' : 'col-success'}">${data.overdue_buyers}</div>
      <div class="stat-label">Su pradelstosiomis</div>
      <div class="stat-sub">${data.overdue_invoices} pradelstų sąskaitų</div>
    </div>
  `;
}

function renderBuyers(buyers) {
  allBuyers = buyers;
  applyBuyerFilters();
}

function setSort(col) {
  if (sortCol === col) {
    sortDir = sortDir === 'desc' ? 'asc' : 'desc';
  } else {
    sortCol = col;
    sortDir = 'desc';
  }
  // Update header indicators
  ['balance', 'overdue_amount', 'max_overdue_days'].forEach(c => {
    const thId = c === 'balance' ? 'th-balance' : c === 'overdue_amount' ? 'th-overdue' : 'th-overdue-days';
    const th = document.getElementById(thId);
    th.classList.remove('sort-asc', 'sort-desc');
    th.querySelector('.sort-icon').textContent = '↕';
  });
  const activeThId = col === 'balance' ? 'th-balance' : col === 'overdue_amount' ? 'th-overdue' : 'th-overdue-days';
  const activeTh = document.getElementById(activeThId);
  activeTh.classList.add(sortDir === 'asc' ? 'sort-asc' : 'sort-desc');
  activeTh.querySelector('.sort-icon').textContent = '';
  applyBuyerFilters();
}

function applyBuyerFilters() {
  const search = document.getElementById('buyer-search').value.toLowerCase();
  const overdueOnly = document.getElementById('filter-overdue').checked;
  const balanceMin = parseFloat(document.getElementById('filter-balance-min').value) || 0;
  const balanceMax = parseFloat(document.getElementById('filter-balance-max').value) || Infinity;
  const overdueMin = parseFloat(document.getElementById('filter-overdue-min').value) || 0;
  const overdueMax = parseFloat(document.getElementById('filter-overdue-max').value) || Infinity;

  let filtered = [...allBuyers];
  if (overdueOnly) filtered = filtered.filter(b => b.has_overdue);
  if (search) filtered = filtered.filter(b => b.buyer_name.toLowerCase().includes(search) || b.buyer_code.includes(search));
  if (balanceMin > 0 || balanceMax < Infinity)
    filtered = filtered.filter(b => b.balance >= balanceMin && b.balance <= balanceMax);
  if (overdueMin > 0 || overdueMax < Infinity)
    filtered = filtered.filter(b => b.overdue_amount >= overdueMin && b.overdue_amount <= overdueMax);

  filtered.sort((a, b) => {
    const av = a[sortCol] ?? -1;
    const bv = b[sortCol] ?? -1;
    return sortDir === 'asc' ? av - bv : bv - av;
  });

  const tbody = document.getElementById('buyers-tbody');
  tbody.innerHTML = filtered.map(b => `
    <tr class="${b.has_overdue ? 'overdue-row' : ''}">
      <td>
        <a class="buyer-link" onclick="openBuyerModal(${b.id})">${b.buyer_name}</a>
        <span style="color:#94a3b8;font-size:11px;margin-left:6px">${b.buyer_code}</span>
      </td>
      <td class="num">${fmt(b.balance)}</td>
      <td class="num">${b.overdue_amount > 0 ? fmt(b.overdue_amount) : '—'}</td>
      <td class="num">${b.max_overdue_days != null
        ? `<span style="color:${b.max_overdue_days > 60 ? 'var(--danger)' : b.max_overdue_days > 30 ? 'var(--warning)' : 'inherit'};font-weight:${b.max_overdue_days > 30 ? '600' : '400'}">${b.max_overdue_days} d.</span>`
        : '—'}</td>
      <td class="num">
        ${b.has_overdue
          ? `<span class="badge-overdue">Pradelsta</span>`
          : b.has_prepayment
            ? `<span class="badge-ok-small">Išankstinis</span>`
            : `<span class="badge-ok-small">Tvarkinga</span>`
        }
      </td>
    </tr>
  `).join('');

  // Footer totals
  const totalBalance = filtered.reduce((s, b) => s + b.balance, 0);
  const totalOverdue = filtered.reduce((s, b) => s + b.overdue_amount, 0);
  const isFiltered = filtered.length !== allBuyers.length;
  document.getElementById('buyers-tfoot').innerHTML = `
    <tr>
      <td><span class="tfoot-label">Viso ${isFiltered ? '(filtruota)' : ''}</span> <span class="tfoot-count">${filtered.length} pirkėjų</span></td>
      <td class="num">${fmt(totalBalance)} €</td>
      <td class="num">${totalOverdue > 0 ? fmt(totalOverdue) + ' €' : '—'}</td>
      <td></td>
      <td></td>
    </tr>
  `;
}

document.getElementById('buyer-search').addEventListener('input', applyBuyerFilters);
document.getElementById('filter-overdue').addEventListener('change', applyBuyerFilters);
document.getElementById('filter-balance-min').addEventListener('input', applyBuyerFilters);
document.getElementById('filter-balance-max').addEventListener('input', applyBuyerFilters);
document.getElementById('filter-overdue-min').addEventListener('input', applyBuyerFilters);
document.getElementById('filter-overdue-max').addEventListener('input', applyBuyerFilters);
document.getElementById('btn-clear-filters').addEventListener('click', () => {
  document.getElementById('buyer-search').value = '';
  document.getElementById('filter-overdue').checked = false;
  document.getElementById('filter-balance-min').value = '';
  document.getElementById('filter-balance-max').value = '';
  document.getElementById('filter-overdue-min').value = '';
  document.getElementById('filter-overdue-max').value = '';
  applyBuyerFilters();
});

// ── AI ANALYSIS ───────────────────────────────────────────────────────────────

function renderAI(data) {
  const el = document.getElementById('ai-content');
  if (data.ai_analysis) {
    el.innerHTML = formatMarkdown(data.ai_analysis);
  } else {
    el.innerHTML = '<div class="loading">AI analizė dar vykdoma... Perkraukite po kelių sekundžių.</div>';
    // Poll for analysis
    pollAnalysis(data.id);
  }
}

async function pollAnalysis(snapshotId) {
  const poll = async () => {
    const res = await apiFetch(`/api/receivables/snapshots/${snapshotId}/analysis`);
    if (!res || !res.ok) return;
    const data = await res.json();
    if (data.ready) {
      document.getElementById('ai-content').innerHTML = formatMarkdown(data.analysis);
      // Refresh snapshots list to show AI badge
      loadSnapshots();
    } else {
      setTimeout(poll, 4000);
    }
  };
  setTimeout(poll, 4000);
}

// ── BUYER MODAL ───────────────────────────────────────────────────────────────

async function openBuyerModal(buyerId) {
  const modal = document.getElementById('invoices-modal');
  modal.classList.remove('hidden');
  document.getElementById('invoices-tbody').innerHTML = '<tr><td colspan="6" class="loading">Kraunama...</td></tr>';

  const res = await apiFetch(`/api/receivables/snapshots/${currentSnapshotId}/buyer/${buyerId}`);
  if (!res || !res.ok) return;
  const data = await res.json();

  document.getElementById('modal-buyer-name').textContent = `${data.buyer_name} — skola: ${fmt(data.balance)} €`;

  document.getElementById('invoices-tbody').innerHTML = data.invoices.map(inv => `
    <tr class="${inv.is_overdue ? 'overdue-row' : ''}">
      <td>${inv.is_prepayment ? '<em>Išankstinis</em>' : (inv.invoice_number || '—')}</td>
      <td>${inv.invoice_datetime ? inv.invoice_datetime.substring(0, 10) : '—'}</td>
      <td>${inv.payment_date || '—'}</td>
      <td class="num">${inv.payment_term_days ?? '—'}</td>
      <td class="num">${fmt(inv.amount)}</td>
      <td class="num ${inv.is_overdue ? 'col-danger' : inv.days_remaining !== null && inv.days_remaining <= 5 ? 'col-warning' : ''}">
        ${inv.days_remaining !== null
          ? (inv.is_overdue
              ? `${Math.abs(inv.days_remaining)} d. vėluoja`
              : `${inv.days_remaining} d.`)
          : '—'}
      </td>
    </tr>
  `).join('');
}

document.getElementById('modal-close').addEventListener('click', () => {
  document.getElementById('invoices-modal').classList.add('hidden');
});
document.getElementById('invoices-modal').addEventListener('click', (e) => {
  if (e.target === document.getElementById('invoices-modal')) {
    document.getElementById('invoices-modal').classList.add('hidden');
  }
});

// ── HELPERS ───────────────────────────────────────────────────────────────────

function fmt(val) {
  if (val === null || val === undefined) return '—';
  return Number(val).toLocaleString('lt-LT', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatDate(iso) {
  if (!iso) return '—';
  return iso.substring(0, 10);
}

function formatMarkdown(text) {
  if (!text) return '';
  return text
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/^## (.+)$/gm, '<h4 style="margin:14px 0 6px;font-size:14px;color:#1e293b">$1</h4>')
    .replace(/^### (.+)$/gm, '<h5 style="margin:10px 0 4px;font-size:13px">$1</h5>')
    .replace(/\n/g, '<br>');
}

// ── PAYABLES MODULE ───────────────────────────────────────────────────────────

let currentSnapshotIdP = null;
let allSuppliers = [];
let sortColP = 'balance';
let sortDirP = 'desc';

document.getElementById('btn-upload-p').addEventListener('click', () => {
  document.getElementById('upload-panel-p').classList.toggle('hidden');
});
document.getElementById('btn-cancel-upload-p').addEventListener('click', () => {
  document.getElementById('upload-panel-p').classList.add('hidden');
});

document.getElementById('btn-do-upload-p').addEventListener('click', async () => {
  const fileInput = document.getElementById('file-input-p');
  const statusEl = document.getElementById('upload-status-p');
  if (!fileInput.files.length) { showStatus(statusEl, 'Pasirinkite Excel failą', 'err'); return; }

  const formData = new FormData();
  formData.append('file', fileInput.files[0]);
  const reportDate = document.getElementById('report-date-p').value;
  if (reportDate) formData.append('report_date', reportDate);

  showStatus(statusEl, 'Įkeliama ir analizuojama...', 'info');
  document.getElementById('btn-do-upload-p').disabled = true;

  const res = await apiFetch('/api/payables/upload', { method: 'POST', body: formData });
  document.getElementById('btn-do-upload-p').disabled = false;

  if (!res || !res.ok) {
    const err = await res?.json().catch(() => ({}));
    showStatus(statusEl, err.detail || 'Klaida įkeliant failą', 'err');
    return;
  }

  const data = await res.json();
  showStatus(statusEl, `✓ Įkelta: ${data.total_suppliers} tiekėjų, balansas ${fmt(data.total_balance)} €. AI analizė vykdoma fone.`, 'ok');
  setTimeout(() => {
    document.getElementById('upload-panel-p').classList.add('hidden');
    statusEl.classList.add('hidden');
    fileInput.value = '';
    loadSnapshotsP();
    loadSnapshotP(data.snapshot_id);
  }, 2500);
});

async function loadSnapshotsP() {
  const res = await apiFetch('/api/payables/snapshots');
  if (!res || !res.ok) return;
  const snapshots = await res.json();

  const container = document.getElementById('snapshots-list-p');
  if (!snapshots.length) {
    container.innerHTML = '<div class="loading">Nėra įkeltų ataskaitų. Įkelkite pirmą Excel failą.</div>';
    return;
  }

  container.innerHTML = snapshots.map(s => `
    <div class="snapshot-row" data-id-p="${s.id}" onclick="loadSnapshotP(${s.id})">
      <div class="snap-info">
        <span class="snap-name">${s.file_name}
          ${s.has_analysis ? '<span class="snap-badge badge-ai">🤖 AI</span>' : ''}
        </span>
        <span class="snap-meta">
          Įkelta: ${formatDate(s.upload_date)}
          ${s.report_date ? ' · Ataskaita: ' + s.report_date : ''}
          · ${s.total_suppliers} tiekėjų
        </span>
      </div>
      <div class="snap-amounts">
        <div class="snap-balance">${fmt(s.total_balance)} €</div>
        ${s.total_overdue > 0
          ? `<div class="snap-overdue">⚠ Pradelsta: ${fmt(s.total_overdue)} €</div>`
          : '<div style="font-size:12px;color:#10b981">✓ Nėra pradelstų</div>'}
      </div>
    </div>
  `).join('');

  if (snapshots.length && !currentSnapshotIdP) loadSnapshotP(snapshots[0].id);
}

async function loadSnapshotP(id) {
  currentSnapshotIdP = id;
  document.querySelectorAll('[data-id-p]').forEach(r => r.classList.remove('active'));
  const row = document.querySelector(`[data-id-p="${id}"]`);
  if (row) row.classList.add('active');

  document.getElementById('snapshot-detail-p').classList.remove('hidden');

  const res = await apiFetch(`/api/payables/snapshots/${id}`);
  if (!res || !res.ok) return;
  const data = await res.json();

  renderStatsP(data);
  allSuppliers = data.suppliers;
  applySuppliersFilters();
  renderAIP(data);
}

function renderStatsP(data) {
  const overduePercent = data.total_balance > 0
    ? ((data.total_overdue / data.total_balance) * 100).toFixed(1) : '0.0';

  document.getElementById('stats-cards-p').innerHTML = `
    <div class="stat-box">
      <div class="stat-value col-primary">${fmt(data.total_balance)} €</div>
      <div class="stat-label">Bendras balansas</div>
      <div class="stat-sub" style="color:#64748b">Neapmokėta: ${fmt(data.total_unpaid)} €</div>
    </div>
    <div class="stat-box">
      <div class="stat-value ${data.total_overdue > 0 ? 'col-danger' : 'col-success'}">${fmt(data.total_overdue)} €</div>
      <div class="stat-label">Pradelsta</div>
      <div class="stat-sub ${data.total_overdue > 0 ? 'col-danger' : ''}">${overduePercent}% nuo balanso</div>
    </div>
    <div class="stat-box">
      <div class="stat-value">${data.total_suppliers}</div>
      <div class="stat-label">Tiekėjų</div>
      <div class="stat-sub" style="color:#64748b">${data.total_invoices} sąskaitos</div>
    </div>
    <div class="stat-box">
      <div class="stat-value ${data.overdue_suppliers > 0 ? 'col-warning' : 'col-success'}">${data.overdue_suppliers}</div>
      <div class="stat-label">Su pradelstomis</div>
      <div class="stat-sub">${data.overdue_invoices} pradelstų sąskaitų</div>
    </div>
  `;
}

function setSortP(col) {
  if (sortColP === col) { sortDirP = sortDirP === 'desc' ? 'asc' : 'desc'; }
  else { sortColP = col; sortDirP = 'desc'; }
  ['balance', 'overdue_amount', 'max_overdue_days'].forEach(c => {
    const thId = c === 'balance' ? 'th-balance-p' : c === 'overdue_amount' ? 'th-overdue-p' : 'th-overdue-days-p';
    const th = document.getElementById(thId);
    th.classList.remove('sort-asc', 'sort-desc');
    th.querySelector('.sort-icon').textContent = '↕';
  });
  const activeId = sortColP === 'balance' ? 'th-balance-p' : sortColP === 'overdue_amount' ? 'th-overdue-p' : 'th-overdue-days-p';
  const activeTh = document.getElementById(activeId);
  activeTh.classList.add(sortDirP === 'asc' ? 'sort-asc' : 'sort-desc');
  activeTh.querySelector('.sort-icon').textContent = '';
  applySuppliersFilters();
}

function applySuppliersFilters() {
  const search = document.getElementById('supplier-search').value.toLowerCase();
  const overdueOnly = document.getElementById('filter-overdue-p').checked;
  const balanceMin = parseFloat(document.getElementById('filter-balance-min-p').value) || 0;
  const balanceMax = parseFloat(document.getElementById('filter-balance-max-p').value) || Infinity;
  const overdueMin = parseFloat(document.getElementById('filter-overdue-min-p').value) || 0;
  const overdueMax = parseFloat(document.getElementById('filter-overdue-max-p').value) || Infinity;

  let filtered = [...allSuppliers];
  if (overdueOnly) filtered = filtered.filter(s => s.has_overdue);
  if (search) filtered = filtered.filter(s => s.supplier_name.toLowerCase().includes(search) || s.supplier_code.includes(search));
  if (balanceMin > 0 || balanceMax < Infinity) filtered = filtered.filter(s => s.balance >= balanceMin && s.balance <= balanceMax);
  if (overdueMin > 0 || overdueMax < Infinity) filtered = filtered.filter(s => s.overdue_amount >= overdueMin && s.overdue_amount <= overdueMax);

  filtered.sort((a, b) => {
    const av = a[sortColP] ?? -1, bv = b[sortColP] ?? -1;
    return sortDirP === 'asc' ? av - bv : bv - av;
  });

  document.getElementById('suppliers-tbody').innerHTML = filtered.map(s => `
    <tr class="${s.has_overdue ? 'overdue-row' : ''}">
      <td>
        <a class="buyer-link" onclick="openSupplierModal(${s.id})">${s.supplier_name}</a>
        <span style="color:#94a3b8;font-size:11px;margin-left:6px">${s.supplier_code}</span>
      </td>
      <td class="num" style="${s.has_prepayment && !s.has_overdue ? 'color:var(--success)' : ''}">${s.has_prepayment && !s.has_overdue ? '-' : ''}${fmt(s.balance)}</td>
      <td class="num">${s.overdue_amount > 0 ? fmt(s.overdue_amount) : '—'}</td>
      <td class="num">${s.max_overdue_days != null
        ? `<span style="color:${s.max_overdue_days > 60 ? 'var(--danger)' : s.max_overdue_days > 30 ? 'var(--warning)' : 'inherit'};font-weight:${s.max_overdue_days > 30 ? '600' : '400'}">${s.max_overdue_days} d.</span>`
        : '—'}</td>
      <td class="num">
        ${s.has_overdue
          ? `<span class="badge-overdue">Pradelsta</span>`
          : s.has_prepayment
            ? `<span class="badge-ok-small">Išankstinis</span>`
            : `<span class="badge-ok-small">Tvarkinga</span>`}
      </td>
    </tr>
  `).join('');

  const totalBalance = filtered.reduce((s, x) => s + x.balance, 0);
  const totalOverdue = filtered.reduce((s, x) => s + x.overdue_amount, 0);
  const isFiltered = filtered.length !== allSuppliers.length;
  document.getElementById('suppliers-tfoot').innerHTML = `
    <tr>
      <td><span class="tfoot-label">Viso ${isFiltered ? '(filtruota)' : ''}</span> <span class="tfoot-count">${filtered.length} tiekėjų</span></td>
      <td class="num">${fmt(totalBalance)} €</td>
      <td class="num">${totalOverdue > 0 ? fmt(totalOverdue) + ' €' : '—'}</td>
      <td></td><td></td>
    </tr>
  `;
}

['supplier-search', 'filter-balance-min-p', 'filter-balance-max-p', 'filter-overdue-min-p', 'filter-overdue-max-p'].forEach(id => {
  document.getElementById(id).addEventListener('input', applySuppliersFilters);
});
document.getElementById('filter-overdue-p').addEventListener('change', applySuppliersFilters);
document.getElementById('btn-clear-filters-p').addEventListener('click', () => {
  ['supplier-search', 'filter-balance-min-p', 'filter-balance-max-p', 'filter-overdue-min-p', 'filter-overdue-max-p'].forEach(id => {
    document.getElementById(id).value = '';
  });
  document.getElementById('filter-overdue-p').checked = false;
  applySuppliersFilters();
});

function renderAIP(data) {
  const el = document.getElementById('ai-content-p');
  if (data.ai_analysis) {
    el.innerHTML = formatMarkdown(data.ai_analysis);
  } else {
    el.innerHTML = '<div class="loading">AI analizė dar vykdoma... Perkraukite po kelių sekundžių.</div>';
    pollAnalysisP(data.id);
  }
}

async function pollAnalysisP(snapshotId) {
  const poll = async () => {
    const res = await apiFetch(`/api/payables/snapshots/${snapshotId}/analysis`);
    if (!res || !res.ok) return;
    const data = await res.json();
    if (data.ready) {
      document.getElementById('ai-content-p').innerHTML = formatMarkdown(data.analysis);
      loadSnapshotsP();
    } else { setTimeout(poll, 4000); }
  };
  setTimeout(poll, 4000);
}

async function openSupplierModal(supplierId) {
  const modal = document.getElementById('invoices-modal-p');
  modal.classList.remove('hidden');
  document.getElementById('invoices-tbody-p').innerHTML = '<tr><td colspan="7" class="loading">Kraunama...</td></tr>';

  const res = await apiFetch(`/api/payables/snapshots/${currentSnapshotIdP}/supplier/${supplierId}`);
  if (!res || !res.ok) return;
  const data = await res.json();

  document.getElementById('modal-supplier-name').textContent = `${data.supplier_name} — skola: ${fmt(data.balance)} €`;

  document.getElementById('invoices-tbody-p').innerHTML = data.invoices.map(inv => `
    <tr class="${inv.is_overdue ? 'overdue-row' : ''}">
      <td>${inv.is_prepayment ? '<em>Išankstinis</em>' : (inv.invoice_number || '—')}</td>
      <td>${inv.supplier_invoice_number || '—'}</td>
      <td>${inv.invoice_datetime ? inv.invoice_datetime.substring(0, 10) : '—'}</td>
      <td>${inv.payment_date || '—'}</td>
      <td class="num">${inv.payment_term ?? '—'}</td>
      <td class="num" style="${inv.is_prepayment ? 'color:var(--success)' : ''}">${inv.is_prepayment ? '-' : ''}${fmt(inv.amount)}</td>
      <td class="num ${inv.is_overdue ? 'col-danger' : inv.days_remaining !== null && inv.days_remaining <= 5 ? 'col-warning' : ''}">
        ${inv.days_remaining !== null
          ? (inv.is_overdue ? `${Math.abs(inv.days_remaining)} d. vėluoja` : `${inv.days_remaining} d.`)
          : '—'}
      </td>
    </tr>
  `).join('');
}

document.getElementById('modal-close-p').addEventListener('click', () => {
  document.getElementById('invoices-modal-p').classList.add('hidden');
});
document.getElementById('invoices-modal-p').addEventListener('click', (e) => {
  if (e.target === document.getElementById('invoices-modal-p'))
    document.getElementById('invoices-modal-p').classList.add('hidden');
});

// Load payables when module tab is clicked
document.getElementById('nav-payables').addEventListener('click', () => {
  if (!currentSnapshotIdP) loadSnapshotsP();
});

// ── SALES MODULE ──────────────────────────────────────────────────────────────

let currentSalesMonthId = null;
let salesModuleLoaded = false;
let salesData = null; // cached current month data
const teamSortState = { SAVI: 'suma', ROMBAS: 'suma', SOSTINES: 'suma', UZ_VILNIAUS: 'suma' };

const TEAM_COLORS = {
  SAVI: '#3b82f6',
  ROMBAS: '#10b981',
  SOSTINES: '#f59e0b',
  UZ_VILNIAUS: '#8b5cf6',
};
const TEAM_ORDER = ['SAVI', 'ROMBAS', 'SOSTINES', 'UZ_VILNIAUS'];

document.getElementById('nav-sales').addEventListener('click', () => {
  document.querySelector('.main-content').classList.add('wide');
  if (!salesModuleLoaded) {
    salesModuleLoaded = true;
    loadSalesClientsStatus();
    loadSalesMonths();
  }
});

// Remove wide class when switching to other modules
document.querySelectorAll('.nav-item:not(#nav-sales)').forEach(item => {
  item.addEventListener('click', () => {
    document.querySelector('.main-content').classList.remove('wide');
  });
});

// ── CLIENT UPLOAD ─────────────────────────────────────────────────────────────

document.getElementById('btn-upload-clients').addEventListener('click', () => {
  document.getElementById('upload-panel-clients').classList.toggle('hidden');
  document.getElementById('upload-panel-sales-m').classList.add('hidden');
});

document.getElementById('btn-cancel-upload-clients').addEventListener('click', () => {
  document.getElementById('upload-panel-clients').classList.add('hidden');
});

document.getElementById('btn-do-upload-clients').addEventListener('click', async () => {
  const fileInput = document.getElementById('file-input-clients');
  const statusEl = document.getElementById('upload-status-clients');
  if (!fileInput.files.length) { showStatus(statusEl, 'Pasirinkite Excel failą', 'err'); return; }

  const formData = new FormData();
  formData.append('file', fileInput.files[0]);

  showStatus(statusEl, 'Įkeliama...', 'info');
  document.getElementById('btn-do-upload-clients').disabled = true;

  const res = await apiFetch('/api/sales/clients/upload', { method: 'POST', body: formData });
  document.getElementById('btn-do-upload-clients').disabled = false;

  if (!res || !res.ok) {
    const err = await res?.json().catch(() => ({}));
    showStatus(statusEl, err.detail || 'Klaida įkeliant failą', 'err');
    return;
  }

  const data = await res.json();
  showStatus(statusEl, `✓ Įkelta: ${data.total_clients.toLocaleString()} klientų, ${data.total_assigned.toLocaleString()} priskirti komandoms.`, 'ok');
  setTimeout(() => {
    document.getElementById('upload-panel-clients').classList.add('hidden');
    statusEl.classList.add('hidden');
    fileInput.value = '';
    loadSalesClientsStatus();
  }, 2500);
});

// ── MONTHLY SALES UPLOAD ──────────────────────────────────────────────────────

document.getElementById('btn-upload-sales-m').addEventListener('click', () => {
  document.getElementById('upload-panel-sales-m').classList.toggle('hidden');
  document.getElementById('upload-panel-clients').classList.add('hidden');
});

document.getElementById('btn-cancel-upload-sales-m').addEventListener('click', () => {
  document.getElementById('upload-panel-sales-m').classList.add('hidden');
});

document.getElementById('btn-do-upload-sales-m').addEventListener('click', async () => {
  const fileInput = document.getElementById('file-input-sales-m');
  const statusEl = document.getElementById('upload-status-sales-m');
  if (!fileInput.files.length) { showStatus(statusEl, 'Pasirinkite Excel failą', 'err'); return; }

  const year = document.getElementById('sales-upload-year').value;
  const month = document.getElementById('sales-upload-month').value;

  const formData = new FormData();
  formData.append('file', fileInput.files[0]);

  showStatus(statusEl, 'Įkeliama ir analizuojama...', 'info');
  document.getElementById('btn-do-upload-sales-m').disabled = true;

  const res = await apiFetch(`/api/sales/sales/upload?year=${year}&month=${month}`, {
    method: 'POST', body: formData,
  });
  document.getElementById('btn-do-upload-sales-m').disabled = false;

  if (!res || !res.ok) {
    const err = await res?.json().catch(() => ({}));
    showStatus(statusEl, err.detail || 'Klaida įkeliant failą', 'err');
    return;
  }

  const data = await res.json();
  showStatus(statusEl, `✓ Įkelta: ${data.total_invoices} sąskaitos, ${fmt(data.total_suma)} €.`, 'ok');
  setTimeout(async () => {
    document.getElementById('upload-panel-sales-m').classList.add('hidden');
    statusEl.classList.add('hidden');
    fileInput.value = '';
    // Refresh months list then load the new one
    const r2 = await apiFetch('/api/sales/months');
    if (r2 && r2.ok) allSalesMonths = await r2.json();
    loadSalesMonth(data.snapshot_id);
  }, 2500);
});

// ── CLIENTS STATUS BAR ────────────────────────────────────────────────────────

async function loadSalesClientsStatus() {
  const res = await apiFetch('/api/sales/clients/status');
  if (!res || !res.ok) return;
  const data = await res.json();
  const bar = document.getElementById('sales-clients-bar');
  if (data.has_clients) {
    bar.innerHTML = `👥 Aktyvus pirkėjų sąrašas: <strong>${data.file_name}</strong>
      — ${data.total_clients.toLocaleString()} klientų
      (${data.total_assigned.toLocaleString()} priskirti komandoms)
      · Įkeltas: ${formatDate(data.upload_date)}`;
    bar.style.color = '#0369a1';
  } else {
    bar.innerHTML = '⚠ Pirkėjų sąrašas neįkeltas. Įkelkite klientų failą (mr_aru_kliendid.xlsx) paspaudę „Pirkėjų sąrašas".';
    bar.style.color = 'var(--warning)';
    bar.style.background = '#fffbeb';
    bar.style.borderColor = '#fcd34d';
  }
  bar.classList.remove('hidden');
}

// ── MONTHS DROPDOWN ───────────────────────────────────────────────────────────

let allSalesMonths = [];
let monthPickerOpen = false;

async function loadSalesMonths() {
  const res = await apiFetch('/api/sales/months');
  if (!res || !res.ok) return;
  allSalesMonths = await res.json();
  renderMonthPicker();
  if (allSalesMonths.length && !currentSalesMonthId) {
    loadSalesMonth(allSalesMonths[0].id);
  }
}

function renderMonthPicker() {
  const wrap = document.getElementById('sales-month-picker');
  if (!allSalesMonths.length) {
    wrap.innerHTML = '<div class="month-picker-empty">Nėra įkeltų mėnesių. Įkelkite pirmą pardavimų ataskaitą.</div>';
    return;
  }
  const a = allSalesMonths.find(m => m.id === currentSalesMonthId) || allSalesMonths[0];

  wrap.innerHTML = `
    <div class="mpb" id="mpb-btn" onclick="toggleMonthPicker()">
      <div class="mpb-main">
        <div class="mpb-lbl">Pasirinktas mėnuo</div>
        <div class="mpb-name">${a.label}${a.has_analysis ? ' 🤖' : ''}</div>
      </div>
      <div class="mpb-sep"></div>
      <div class="mpb-cell">
        <div class="mpb-lbl">Apyvarta</div>
        <div class="mpb-val col-primary">${fmt(a.total_suma)} €</div>
      </div>
      <div class="mpb-sep"></div>
      <div class="mpb-cell">
        <div class="mpb-lbl">Bendr. pelnas</div>
        <div class="mpb-val col-success">${fmt(a.total_bp)} €</div>
      </div>
      <div class="mpb-sep"></div>
      <div class="mpb-cell">
        <div class="mpb-lbl">Marža</div>
        <div class="mpb-val">${a.total_bp_pct.toFixed(1)}%</div>
      </div>
      <div class="mpb-sep"></div>
      <div class="mpb-cell">
        <div class="mpb-lbl">Sąskaitos</div>
        <div class="mpb-val">${a.total_invoices}</div>
      </div>
      <div class="mpb-arrow">▼</div>
    </div>
    <div class="mpb-dropdown" id="mpb-dropdown">
      ${allSalesMonths.map(m => `
        <div class="mpb-row${m.id === a.id ? ' sel' : ''}" onclick="selectMonth(${m.id})">
          <div class="mpb-row-name">${m.label}${m.has_analysis ? ' <span class="mpb-ai">🤖</span>' : ''}</div>
          <div class="mpb-row-sep"></div>
          <div class="mpb-row-cell"><strong class="col-primary">${fmt(m.total_suma)} €</strong> <span class="rl">apyvarta</span></div>
          <div class="mpb-row-sep"></div>
          <div class="mpb-row-cell"><strong class="col-success">${fmt(m.total_bp)} €</strong> <span class="rl">BP</span></div>
          <div class="mpb-row-sep"></div>
          <div class="mpb-row-cell"><strong>${m.total_bp_pct.toFixed(1)}%</strong> <span class="rl">marža</span></div>
          <div class="mpb-row-sep"></div>
          <div class="mpb-row-cell"><strong>${m.total_invoices}</strong> <span class="rl">sąsk.</span></div>
        </div>
      `).join('')}
    </div>
  `;
}

function toggleMonthPicker() {
  monthPickerOpen = !monthPickerOpen;
  const btn = document.getElementById('mpb-btn');
  const dd  = document.getElementById('mpb-dropdown');
  if (!btn || !dd) return;
  btn.classList.toggle('open', monthPickerOpen);
  dd.classList.toggle('open', monthPickerOpen);
}

function selectMonth(id) {
  monthPickerOpen = false;
  const btn = document.getElementById('mpb-btn');
  const dd  = document.getElementById('mpb-dropdown');
  if (btn) btn.classList.remove('open');
  if (dd)  dd.classList.remove('open');
  loadSalesMonth(id);
}

document.addEventListener('click', (e) => {
  if (!monthPickerOpen) return;
  const bar = document.getElementById('sales-month-picker');
  if (bar && !bar.contains(e.target)) {
    monthPickerOpen = false;
    const btn = document.getElementById('mpb-btn');
    const dd  = document.getElementById('mpb-dropdown');
    if (btn) btn.classList.remove('open');
    if (dd)  dd.classList.remove('open');
  }
});

// ── MONTH DETAIL ──────────────────────────────────────────────────────────────

async function loadSalesMonth(id) {
  currentSalesMonthId = id;
  renderMonthPicker(); // update selected month display

  document.getElementById('sales-month-detail').classList.remove('hidden');

  const res = await apiFetch(`/api/sales/months/${id}`);
  if (!res || !res.ok) return;
  const data = await res.json();

  salesData = data;
  renderSalesStats(data);
  renderSalesTeams(data);
  renderSalesAI(data);
  clearChat();
  showChatPanel(data.year >= 2026);
}

function renderSalesStats(data) {
  const bpPct = data.total_bp_pct ? data.total_bp_pct.toFixed(1) : '0.0';
  document.getElementById('sales-stats-cards').innerHTML = `
    <div class="stat-box">
      <div class="stat-value col-primary">${fmt(data.total_suma)} €</div>
      <div class="stat-label">Pardavimų suma</div>
    </div>
    <div class="stat-box">
      <div class="stat-value col-success">${fmt(data.total_bp)} €</div>
      <div class="stat-label">Bendrasis pelnas</div>
    </div>
    <div class="stat-box">
      <div class="stat-value">${bpPct}%</div>
      <div class="stat-label">Vidutinė marža</div>
    </div>
    <div class="stat-box">
      <div class="stat-value">${data.total_invoices}</div>
      <div class="stat-label">Sąskaitos</div>
    </div>
  `;
}

function renderSalesTeams(data) {
  const grid = document.getElementById('sales-teams-grid');
  grid.innerHTML = TEAM_ORDER.map(teamCode => renderTeamCard(teamCode, data.teams[teamCode])).join('');
}

function renderTeamCard(teamCode, td) {
  if (!td) return '';
  const color = TEAM_COLORS[teamCode] || '#3b82f6';
  const sortBy = teamSortState[teamCode] || 'suma';
  let clients = [...(td.top_clients || [])];
  if (sortBy === 'bp') clients.sort((a, b) => b.bp - a.bp);
  const top = clients.slice(0, 7);

  return `
    <div class="team-card">
      <div class="tc-header" style="border-left-color:${color}">
        <div class="tc-title">${td.team_display}</div>
        <div class="tc-actions">
          <button class="tc-btn${sortBy === 'bp' ? ' sort-bp' : ''}"
            onclick="toggleTeamSort('${teamCode}')">
            ↓ ${sortBy === 'suma' ? 'Apyvarta' : 'BP'}
          </button>
          <button class="tc-btn" onclick="openTeamClientsModal('${teamCode}','${td.team_display}')">Visi →</button>
        </div>
      </div>
      <div class="tc-stats">
        <div class="tc-stat-big">
          <div class="v col-primary">${fmt(td.suma)} €</div>
          <div class="l">Apyvarta</div>
        </div>
        <div class="tc-stat-big">
          <div class="v col-success">${fmt(td.bp)} €</div>
          <div class="l">Bendr. pelnas</div>
        </div>
      </div>
      <div class="tc-stats-sm">
        <div class="tc-stat-sm">
          <div class="v">${(td.bp_pct||0).toFixed(1)}%</div>
          <div class="l">Marža</div>
        </div>
        <div class="tc-stat-sm">
          <div class="v">${td.active_clients}</div>
          <div class="l">Aktyvūs</div>
        </div>
        <div class="tc-stat-sm">
          <div class="v${td.inactive_clients > 0 ? ' col-warning' : ''}">${td.inactive_clients}</div>
          <div class="l">Neaktyvūs</div>
        </div>
      </div>
      <div class="tc-clients">
        ${top.length ? `
          <table>
            <thead><tr>
              <th>Pirkėjas</th>
              <th class="${sortBy==='suma'?'sort-hi-s':''}">Apyvarta €</th>
              <th class="${sortBy==='bp'?'sort-hi-b':''}">BP €</th>
            </tr></thead>
            <tbody>
              ${top.map(c => `<tr>
                <td class="cn" title="${c.name}">${c.name}</td>
                <td class="cs">${fmt(c.suma)}</td>
                <td class="cb">${fmt(c.bp)}</td>
              </tr>`).join('')}
            </tbody>
          </table>
        ` : '<div style="color:#94a3b8;font-size:12px;padding:10px 14px">Nėra duomenų</div>'}
      </div>
    </div>
  `;
}

function toggleTeamSort(teamCode) {
  teamSortState[teamCode] = teamSortState[teamCode] === 'suma' ? 'bp' : 'suma';
  if (!salesData) return;
  renderSalesTeams(salesData);
}

// ── TEAM CLIENTS MODAL ────────────────────────────────────────────────────────

async function openTeamClientsModal(teamCode, teamDisplay) {
  const modal = document.getElementById('sales-team-modal');
  modal.classList.remove('hidden');
  document.getElementById('sales-team-modal-title').textContent = `${teamDisplay} — klientai`;
  document.getElementById('sales-team-clients-tbody').innerHTML =
    '<tr><td colspan="7" class="loading">Kraunama...</td></tr>';

  const res = await apiFetch(`/api/sales/months/${currentSalesMonthId}/team/${teamCode}/clients`);
  if (!res || !res.ok) return;
  const clients = await res.json();

  document.getElementById('sales-team-clients-tbody').innerHTML = clients.map((c, i) => `
    <tr>
      <td style="color:var(--text-muted);font-size:11px;width:30px">${i + 1}</td>
      <td>
        ${c.client_name}
        <span style="color:#94a3b8;font-size:11px;margin-left:6px">${c.client_code}</span>
      </td>
      <td style="color:var(--text-muted)">${c.salesperson || '—'}</td>
      <td class="num">${fmt(c.suma)}</td>
      <td class="num">${fmt(c.bp)}</td>
      <td class="num">${c.bp_pct.toFixed(1)}%</td>
      <td class="num">${c.invoice_count}</td>
    </tr>
  `).join('');
}

document.getElementById('sales-team-modal-close').addEventListener('click', () => {
  document.getElementById('sales-team-modal').classList.add('hidden');
});
document.getElementById('sales-team-modal').addEventListener('click', (e) => {
  if (e.target === document.getElementById('sales-team-modal'))
    document.getElementById('sales-team-modal').classList.add('hidden');
});

// ── SALES AI ANALYSIS ─────────────────────────────────────────────────────────

const TEAM_AI_COLORS = {
  'SAVI':       { bg: '#eff6ff', border: '#3b82f6', label: '#1d4ed8' },
  'ROMBAS':     { bg: '#f0fdf4', border: '#10b981', label: '#065f46' },
  'SOSTINĖ':    { bg: '#fffbeb', border: '#f59e0b', label: '#92400e' },
  'UZ LIETUVĄ': { bg: '#f5f3ff', border: '#8b5cf6', label: '#4c1d95' },
};

function renderSalesAI(data) {
  const panel = document.getElementById('sales-ai-panel');
  const el    = document.getElementById('sales-ai-content');
  const rerunBtn = document.getElementById('btn-rerun-sales-ai');

  // 2025 metai – analizės nėra
  if (data.year && data.year < 2026) {
    panel.classList.add('hidden');
    return;
  }
  panel.classList.remove('hidden');

  const overall = data.ai_analysis || '';
  const teams   = data.ai_teams || {};
  const hasAny  = overall || Object.values(teams).some(t => t);
  const hasError = overall.startsWith('Klaida');

  // Mygtukas visada matomas (2026 metams)
  if (rerunBtn) rerunBtn.classList.remove('hidden');

  if (hasError) {
    el.innerHTML = `<div style="color:var(--danger);padding:8px">${overall}</div>`;
    return;
  }

  if (!hasAny) {
    el.innerHTML = '<div style="color:var(--text-muted);padding:8px">AI analizė dar negeneruota. Spauskite mygtuką.</div>';
    return;
  }

  el.innerHTML = buildSalesAIHtml(overall, teams);
}

const TEAM_CODES_MAP = {
  'SAVI': 'SAVI', 'ROMBAS': 'ROMBAS', 'SOSTINES': 'SOSTINĖ', 'UZ_VILNIAUS': 'UZ LIETUVĄ'
};

function buildSalesAIHtml(overall, teams) {
  let html = '';

  // Overall block
  if (overall) {
    html += `<div class="ai-overall-block">${formatAIBody(overall)}</div>`;
  }

  // Per-team blocks
  const teamOrder = ['SAVI', 'ROMBAS', 'SOSTINES', 'UZ_VILNIAUS'];
  for (const code of teamOrder) {
    const text = teams[code];
    if (!text) continue;
    const displayName = TEAM_CODES_MAP[code] || code;
    const colors = TEAM_AI_COLORS[displayName] || { bg: '#f8fafc', border: '#94a3b8', label: '#334155' };
    html += `
      <div class="ai-team-block" style="background:${colors.bg};border-color:${colors.border}">
        <div class="ai-team-title" style="color:${colors.label}">⬛ ${displayName}</div>
        <div class="ai-team-body">${formatAIBody(text)}</div>
      </div>`;
  }
  return html;
}

function formatAIBody(text) {
  return text
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/^### (.+)$/gm, '<h5 class="ai-h5">$1</h5>')
    .replace(/^#### (.+)$/gm, '<h6 class="ai-h6">$1</h6>')
    .replace(/^[-•]\s+(.+)$/gm, '<li>$1</li>')
    .replace(/(<li>.*<\/li>\n?)+/g, m => `<ul>${m}</ul>`)
    .replace(/\n{2,}/g, '</p><p>')
    .replace(/\n/g, '<br>')
    .replace(/^(?!<[hup])(.+)/, '<p>$1')
    .replace(/(.+)(?!<\/[hup])$/, '$1</p>');
}

// ── SALES CHAT ────────────────────────────────────────────────────────────────

let chatHistory = [];

function showChatPanel(show) {
  const panel = document.getElementById('sales-chat-panel');
  if (panel) panel.style.display = show ? 'block' : 'none';
}

function clearChat() {
  chatHistory = [];
  document.getElementById('chat-messages').innerHTML = '';
}

function appendChatMsg(role, text) {
  const container = document.getElementById('chat-messages');
  const div = document.createElement('div');
  div.className = `chat-msg ${role === 'user' ? 'user' : 'ai'}`;

  const avatar = role === 'user' ? 'J' : '🤖';
  const bubble = role === 'user'
    ? `<div class="chat-bubble">${escHtml(text)}</div>`
    : `<div class="chat-bubble">${formatAIBody(text)}</div>`;

  div.innerHTML = `<div class="chat-avatar">${avatar}</div>${bubble}`;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
  return div;
}

function escHtml(t) {
  return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function escAttr(t) {
  return String(t).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/'/g,'&#39;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

async function sendChatMessage() {
  const input = document.getElementById('chat-input');
  const btn   = document.getElementById('chat-send');
  const question = input.value.trim();
  if (!question || !currentSalesMonthId) return;

  input.value = '';
  btn.disabled = true;

  appendChatMsg('user', question);

  // Typing indicator
  const container = document.getElementById('chat-messages');
  const typing = document.createElement('div');
  typing.className = 'chat-msg ai';
  typing.innerHTML = '<div class="chat-avatar">🤖</div><div class="chat-bubble chat-typing">Rašoma...</div>';
  container.appendChild(typing);
  container.scrollTop = container.scrollHeight;

  let res;
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 90000); // 90s timeout
    res = await apiFetch(`/api/sales/months/${currentSalesMonthId}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, history: chatHistory }),
      signal: controller.signal,
    });
    clearTimeout(timeoutId);
  } catch (err) {
    typing.remove();
    btn.disabled = false;
    appendChatMsg('ai', '⚠ Užklausa užtruko per ilgai (>90 sek.). Pabandykite suformuluoti klausimą trumpiau arba konkrečiau.');
    return;
  }

  typing.remove();
  btn.disabled = false;

  if (!res || !res.ok) {
    appendChatMsg('ai', '⚠ Klaida kreipiantis į AI. Bandykite dar kartą.');
    return;
  }

  const data = await res.json();
  const answer = data.answer || '';
  appendChatMsg('ai', answer);

  // Save to history
  chatHistory.push({ role: 'user', content: question });
  chatHistory.push({ role: 'assistant', content: answer });
  if (chatHistory.length > 16) chatHistory = chatHistory.slice(-16);
}

// Enter key to send
document.getElementById('chat-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChatMessage(); }
});

function salesAIProgressHtml(progress) {
  const steps = [
    { key: 'overall',    label: 'Bendra situacija' },
    { key: 'SAVI',       label: 'SAVI' },
    { key: 'ROMBAS',     label: 'ROMBAS' },
    { key: 'SOSTINES',   label: 'SOSTINĖ' },
    { key: 'UZ_VILNIAUS',label: 'UZ LIETUVĄ' },
  ];
  const done  = steps.filter(s => progress && progress[s.key]).length;
  const total = steps.length;
  // Find first not-done step (currently running)
  const runningIdx = progress ? steps.findIndex(s => !progress[s.key]) : 0;

  const rows = steps.map((s, i) => {
    const isDone    = progress && progress[s.key];
    const isRunning = !isDone && i === runningIdx;
    const icon = isDone ? '✅' : isRunning ? '⏳' : '⬜';
    const cls  = isDone ? 'ai-prog-done' : isRunning ? 'ai-prog-run' : 'ai-prog-wait';
    return `<div class="ai-prog-row ${cls}">${icon} ${s.label}</div>`;
  }).join('');

  return `
    <div class="ai-progress-box">
      <div class="ai-prog-title">🤖 AI analizė vykdoma... ${done}/${total}</div>
      <div class="ai-prog-bar-wrap"><div class="ai-prog-bar" style="width:${Math.round(done/total*100)}%"></div></div>
      <div class="ai-prog-steps">${rows}</div>
      <div class="ai-prog-hint">Automatiškai atnaujinama kas ~6 sek.</div>
    </div>`;
}

async function rerunSalesAI() {
  const el = document.getElementById('sales-ai-content');
  const btn = document.getElementById('btn-rerun-sales-ai');
  btn.disabled = true;
  btn.textContent = 'Paleidžiama...';
  const res = await apiFetch(`/api/sales/months/${currentSalesMonthId}/rerun-analysis`, { method: 'POST' });
  if (!res || !res.ok) {
    btn.disabled = false;
    btn.textContent = '🔄 Paleisti iš naujo';
    return;
  }
  el.innerHTML = salesAIProgressHtml(null);
  btn.classList.add('hidden');
  btn.disabled = false;
  btn.textContent = '🔄 Generuoti analizę';
  pollSalesAnalysis(currentSalesMonthId);
}

async function pollSalesAnalysis(snapshotId) {
  const poll = async () => {
    if (currentSalesMonthId !== snapshotId) return;
    const res = await apiFetch(`/api/sales/months/${snapshotId}/analysis`);
    if (!res || !res.ok) return;
    const data = await res.json();
    if (data.ready) {
      document.getElementById('sales-ai-content').innerHTML =
        buildSalesAIHtml(data.analysis || '', data.ai_teams || {});
      // Refresh month picker to show 🤖 badge
      const r2 = await apiFetch('/api/sales/months');
      if (r2 && r2.ok) { allSalesMonths = await r2.json(); renderMonthPicker(); }
    } else {
      // Show live progress
      const el = document.getElementById('sales-ai-content');
      if (el) el.innerHTML = salesAIProgressHtml(data.progress || null);
      setTimeout(poll, 6000);
    }
  };
  setTimeout(poll, 6000);
}

// ═══════════════════════════════════════════════════════
// PURCHASES MODULE
// ═══════════════════════════════════════════════════════

let purchasesLoaded = false;
let purCurrentSnapId = null;
let purAllItems = [];      // cached items for current snapshot
let purDisplayed = 0;
let purCatMap = {};        // code → {name, level, parent_code}
const PUR_PAGE = 100;
let purSortCol = 'total_value';
let purSortDir = 'desc';

// Illiquid tabs sort state
let purIlliquidNoneItems    = [];
let purIlliquidPartialItems = [];
let purNoneSortCol    = 'total_value'; let purNoneSortDir    = 'desc';
let purPartialSortCol = 'total_value'; let purPartialSortDir = 'desc';

// Overstock / Low stock sort state
let purOverstockItems = [];

// "Tik NAUJA" filtrai
let purOverSortCol = 'total_value'; let purOverSortDir = 'desc';

// Illiquid history (all snapshots)
let purIlliquidHistory = [];

// Product comments cache
let purCommentsCache = {};      // { product_code: [comment, ...] }
let purCommentModalCode = null; // product code currently open in comment modal
let purProductIlliqHistory = []; // illiquid history for currently open product

// ── Nav ────────────────────────────────────────────────

document.getElementById('nav-purchases').addEventListener('click', () => {
  document.querySelector('.main-content').classList.add('wide');
  if (!purchasesLoaded) {
    purchasesLoaded = true;
    purInit();
  }
});

document.getElementById('nav-purchases').addEventListener('click', () => {
  // also remove wide from other-module handler above
}, { capture: true });

// Also register purchases in the "remove wide" handler
document.querySelectorAll('.nav-item:not(#nav-sales):not(#nav-purchases)').forEach(item => {
  item.addEventListener('click', () => {
    document.querySelector('.main-content').classList.remove('wide');
  });
});

async function purInit() {
  await purLoadCategoryStatus();
  await purLoadWarehouseSnapshots();
  await purLoadWeeks();
  purSetupUploadHandlers();
  purSetupTabs();
  purSetupFilters();
}

// ── Data upload modal ─────────────────────────────────────────────────────────

function purOpenDataModal() {
  document.getElementById('pur-data-modal').classList.remove('hidden');
}

function purCloseDataModal() {
  document.getElementById('pur-data-modal').classList.add('hidden');
}

function purDataModalOverlay(e) {
  if (e.target === document.getElementById('pur-data-modal')) purCloseDataModal();
}

function purSetChip(id, text, loaded) {
  const el = document.getElementById(id);
  if (!el) return;
  const strong = el.querySelector('strong');
  if (strong) strong.textContent = text;
  el.classList.toggle('pur-chip-loaded', loaded);
}

// ── Category status ────────────────────────────────────

async function purLoadCategoryStatus() {
  const res = await apiFetch('/api/purchases/categories');
  if (!res || !res.ok) return;
  const cats = await res.json();
  // Build global category map
  purCatMap = {};
  cats.forEach(c => { purCatMap[c.code] = { name: c.name, level: c.level, parent_code: c.parent_code }; });
  const el = document.getElementById('pur-cat-status');
  if (cats.length > 0) {
    el.textContent = `${cats.length} kategorijų įkelta`;
    el.style.color = 'var(--success)';
    purSetChip('pur-chip-cat', `${cats.length} kat.`, true);
    // Populate category filter
    const sel = document.getElementById('pur-cat-filter');
    sel.innerHTML = '<option value="">Visos klasės</option>';
    // Only top-level (level 0 and 1) for filter
    const topCats = cats.filter(c => c.level <= 1).sort((a,b) => a.code.localeCompare(b.code));
    topCats.forEach(c => {
      const opt = document.createElement('option');
      opt.value = c.code;
      opt.textContent = `${c.code} — ${c.name}`;
      sel.appendChild(opt);
    });
  } else {
    el.textContent = 'Nenurodytos';
    purSetChip('pur-chip-cat', '—', false);
  }
}

// ── Upload handlers ────────────────────────────────────

function purSetupUploadHandlers() {
  // Categories
  document.getElementById('pur-cat-file').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const statusEl = document.getElementById('pur-cat-status');
    statusEl.textContent = 'Įkeliama...';
    const fd = new FormData();
    fd.append('file', file);
    const res = await apiFetch('/api/purchases/categories/upload', { method: 'POST', body: fd });
    if (res && res.ok) {
      const d = await res.json();
      statusEl.textContent = `✓ ${d.total} kategorijų`;
      statusEl.style.color = 'var(--success)';
      await purLoadCategoryStatus();
    } else {
      statusEl.textContent = '⚠ Klaida įkeliant';
      statusEl.style.color = 'var(--danger)';
    }
    e.target.value = '';
  });

  document.getElementById('pur-cl-file').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const el = document.getElementById('pur-cl-status');
    el.textContent = 'Įkeliama…';
    const fd = new FormData();
    fd.append('file', file);
    const res = await apiFetch('/api/purchases/clearance/upload', { method: 'POST', body: fd });
    e.target.value = '';
    if (!res || !res.ok) {
      const err = await res?.json().catch(() => ({}));
      el.textContent = 'Klaida: ' + (err.detail || 'nepavyko įkelti');
      return;
    }
    const data = await res.json();
    el.textContent = `${data.count} prekių`;
    // Reload current snapshot to refresh is_clearance flags
    if (purCurrentSnapId) purLoadInventory();
  });

  // Warehouse
  document.getElementById('pur-wh-file').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const snapDate = document.getElementById('pur-wh-date').value;
    if (!snapDate) { alert('Pasirinkite sandėlio datą'); e.target.value = ''; return; }
    const statusEl = document.getElementById('pur-wh-status');
    statusEl.textContent = 'Įkeliama...';
    const fd = new FormData();
    fd.append('file', file);
    const res = await apiFetch(`/api/purchases/warehouse/upload?snap_date=${snapDate}`, { method: 'POST', body: fd });
    if (res && res.ok) {
      const d = await res.json();
      statusEl.textContent = `✓ ${d.snap_date}: ${d.total_items} prekių, ${fmt(d.total_value)} €`;
      statusEl.style.color = 'var(--success)';
      purSetChip('pur-chip-wh', d.snap_date, true);
      await purLoadWarehouseSnapshots();
    } else {
      const err = await res?.json().catch(() => ({}));
      statusEl.textContent = `⚠ Klaida: ${err.detail || 'Nepavyko įkelti'}`;
      statusEl.style.color = 'var(--danger)';
    }
    e.target.value = '';
  });

  // Sales week
  document.getElementById('pur-sw-file').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const from = document.getElementById('pur-sw-from').value;
    const to   = document.getElementById('pur-sw-to').value;
    if (!from || !to) { alert('Pasirinkite periodo pradžią ir pabaigą'); e.target.value = ''; return; }
    const statusEl = document.getElementById('pur-sw-status');
    statusEl.textContent = 'Įkeliama...';
    const fd = new FormData();
    fd.append('file', file);
    const res = await apiFetch(`/api/purchases/sales-weeks/upload?period_from=${from}&period_to=${to}`, { method: 'POST', body: fd });
    if (res && res.ok) {
      const d = await res.json();
      statusEl.textContent = `✓ ${d.period_from} – ${d.period_to}: ${d.total_items} prekių`;
      statusEl.style.color = 'var(--success)';
      purSetChip('pur-chip-sw', `${d.period_from}`, true);
      await purLoadWeeks();
      // Reload inventory with updated velocity
      if (purCurrentSnapId) purLoadInventory();
    } else {
      const err = await res?.json().catch(() => ({}));
      statusEl.textContent = `⚠ Klaida: ${err.detail || 'Nepavyko įkelti'}`;
      statusEl.style.color = 'var(--danger)';
    }
    e.target.value = '';
  });

  // Annual sales upload
  document.getElementById('pur-annual-file').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const year = parseInt(document.getElementById('pur-annual-year').value);
    if (!year) { alert('Įveskite metus'); e.target.value = ''; return; }
    const statusEl = document.getElementById('pur-annual-status');
    statusEl.textContent = 'Įkeliama...';
    statusEl.style.color = 'var(--text-muted)';
    const fd = new FormData();
    fd.append('file', file);
    const res = await apiFetch(`/api/purchases/sales-weeks/upload-annual?year=${year}`, { method: 'POST', body: fd });
    if (res && res.ok) {
      const d = await res.json();
      statusEl.textContent = `✓ ${d.imported_months} mėn. įkelta`;
      statusEl.style.color = 'var(--success)';
      await purLoadWeeks();
      if (purCurrentSnapId) purLoadInventory();
    } else {
      const err = await res?.json().catch(() => ({}));
      statusEl.textContent = `⚠ ${err.detail || 'Klaida'}`;
      statusEl.style.color = 'var(--danger)';
    }
    e.target.value = '';
  });

  // Analyze button
  document.getElementById('btn-pur-analyze').addEventListener('click', () => purRunAnalysis());

  // Delete snapshot button
  document.getElementById('btn-pur-delete-snap').addEventListener('click', async () => {
    if (!purCurrentSnapId) return;
    const sel = document.getElementById('pur-snap-select');
    const label = sel.options[sel.selectedIndex]?.text || purCurrentSnapId;
    if (!confirm(`Ištrinti sandėlio snapshot'ą: ${label}?\n\nBus ištrinti ir visi su juo susiję AI analizės duomenys.`)) return;
    const res = await apiFetch(`/api/purchases/warehouse/snapshots/${purCurrentSnapId}`, { method: 'DELETE' });
    if (res && res.ok) {
      purCurrentSnapId = null;
      purAllItems = [];
      document.getElementById('pur-inv-body').innerHTML = '';
      document.getElementById('pur-ai-content').innerHTML = '<div class="pur-ai-placeholder">Paspauskite „🤖 Generuoti analizę" kad pradėtumėte.</div>';
      await purLoadWarehouseSnapshots();
    } else {
      alert('Klaida trinant snapshot\'ą');
    }
  });
}

// ── Warehouse snapshots ────────────────────────────────

async function purLoadWarehouseSnapshots() {
  const res = await apiFetch('/api/purchases/warehouse/snapshots');
  if (!res || !res.ok) return;
  const snaps = await res.json();

  const bar = document.getElementById('pur-snap-bar');
  const tabs = document.getElementById('pur-tabs');
  const sel = document.getElementById('pur-snap-select');

  if (snaps.length === 0) { bar.style.display = 'none'; tabs.style.display = 'none'; return; }

  bar.style.display = 'flex';
  tabs.style.display = 'flex';

  sel.innerHTML = snaps.map(s =>
    `<option value="${s.id}" data-value="${s.total_value}" data-items="${s.total_items}" data-analysis="${s.analysis_complete}">${s.snap_date}${s.is_latest ? ' (naujausias)' : ''}</option>`
  ).join('');

  sel.addEventListener('change', () => {
    purSelectSnapshot(parseInt(sel.value));
  });

  purSetChip('pur-chip-wh', snaps[0].snap_date, true);
  purSelectSnapshot(snaps[0].id, snaps[0]);
}

function purSelectSnapshot(id, snapData) {
  purCurrentSnapId = id;
  const sel = document.getElementById('pur-snap-select');
  sel.value = id;
  const opt = sel.options[sel.selectedIndex];
  const value = parseFloat(opt.dataset.value || 0);
  const items = parseInt(opt.dataset.items || 0);

  document.getElementById('pur-snap-stats').innerHTML = `
    <div class="pur-stat-item"><div class="pur-stat-val">${fmt(value)} €</div><div class="pur-stat-lbl">Sandėlio vertė</div></div>
    <div class="pur-stat-item"><div class="pur-stat-val">${items.toLocaleString('lt')}</div><div class="pur-stat-lbl">Unikalios prekės</div></div>
  `;

  purLoadInventory();
  purCheckAnalysis();
}

// ── Inventory tab ──────────────────────────────────────

async function purLoadClearanceStatus() {
  const res = await apiFetch('/api/purchases/clearance/status');
  if (!res || !res.ok) return;
  const data = await res.json();
  const el = document.getElementById('pur-cl-status');
  if (!el) return;
  if (data.count === 0) {
    el.textContent = 'Neįkeltas';
    purSetChip('pur-chip-cl', '—', false);
  } else {
    const d = data.uploaded_at ? new Date(data.uploaded_at).toLocaleDateString('lt') : '';
    el.textContent = `${data.count} prekių${d ? ' · ' + d : ''}`;
    el.style.color = 'var(--success)';
    purSetChip('pur-chip-cl', `${data.count} pr.`, true);
  }
}

async function purLoadFilterOptions(snapId) {
  const res = await apiFetch(`/api/purchases/warehouse/snapshots/${snapId}/filter-options`);
  if (!res || !res.ok) return;
  const data = await res.json();

  // Helper: populate a select with options, keeping current value if possible
  function fillSelect(id, values, allLabel) {
    const sel = document.getElementById(id);
    if (!sel) return;
    const cur = sel.value;
    sel.innerHTML = `<option value="">${allLabel}</option>` +
      values.map(v => `<option value="${escHtml(v)}"${v === cur ? ' selected' : ''}>${escHtml(v)}</option>`).join('');
  }

  // Inventory tab
  fillSelect('pur-manager-filter',  data.managers,   'Visi vadovai');
  fillSelect('pur-prodcat-filter',  data.categories, 'Visos kategorijos');
  // Special tabs
  ['none','partial','over'].forEach(suffix => {
    fillSelect(`pur-${suffix}-manager-filter`, data.managers,   'Visi vadovai');
    fillSelect(`pur-${suffix}-prodcat-filter`, data.categories, 'Visos kategorijos');
  });
}

async function purLoadInventory() {
  if (!purCurrentSnapId) return;
  purDisplayed = 0;
  purAllItems = [];

  const cat     = document.getElementById('pur-cat-filter').value;
  const manager = document.getElementById('pur-manager-filter').value;
  const prodcat = document.getElementById('pur-prodcat-filter').value;
  const search  = document.getElementById('pur-search').value.trim();

  const params = new URLSearchParams({ limit: 10000, offset: 0, sort: 'value_desc' });
  if (cat)     params.set('category', cat);
  if (search)  params.set('search', search);

  const res = await apiFetch(`/api/purchases/warehouse/snapshots/${purCurrentSnapId}/items?${params}`);
  if (!res || !res.ok) return;
  const data = await res.json();

  // Client-side filter by manager / prodcat (all items already loaded)
  let filtered = data.items;
  if (manager) filtered = filtered.filter(i => i.product_group_manager === manager);
  if (prodcat) filtered = filtered.filter(i => i.product_category === prodcat);

  purAllItems = filtered;
  const prevDate = data.items.length > 0 && data.items[0].prev_snap_date
    ? `  |  pokytis nuo ${data.items[0].prev_snap_date}` : '';
  document.getElementById('pur-items-count').textContent = `${filtered.length.toLocaleString('lt')} prekių  |  ${data.total_days} dienų duomenų${prevDate}`;

  // Populate illiquid / overstock / low tabs (unfiltered — they have own filters)
  purRenderSpecialTabs(data.items, data.total_days);

  // Load product comments (refreshes comment cells after data is rendered)
  purLoadComments();

  // Populate filter dropdowns from server
  purLoadFilterOptions(purCurrentSnapId);

  // Load illiquid history for all snapshots (for comparison dropdown)
  purLoadIlliquidHistory();
  purLoadClearanceStatus();

  purSortAndRender();
}

function purSortAndRender() {
  const numCols = ['quantity','qty_delta','unit_cost','total_value','daily_avg','days_stock'];
  purAllItems.sort((a, b) => {
    let av = a[purSortCol], bv = b[purSortCol];
    if (av === null || av === undefined) av = purSortDir === 'asc' ? Infinity : -Infinity;
    if (bv === null || bv === undefined) bv = purSortDir === 'asc' ? Infinity : -Infinity;
    if (typeof av === 'string') return purSortDir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
    return purSortDir === 'asc' ? av - bv : bv - av;
  });
  // Update header icons
  document.querySelectorAll('#pur-inv-table .pur-sortable').forEach(th => {
    th.classList.remove('sort-asc','sort-desc');
    if (th.dataset.col === purSortCol) th.classList.add(purSortDir === 'asc' ? 'sort-asc' : 'sort-desc');
  });
  purDisplayed = 0;
  document.getElementById('pur-inv-body').innerHTML = '';
  purRenderInventoryPage();
}

function purRenderInventoryPage() {
  const tbody = document.getElementById('pur-inv-body');
  const batch = purAllItems.slice(purDisplayed, purDisplayed + PUR_PAGE);

  batch.forEach(it => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td style="font-size:12px;color:var(--text-muted)">${escHtml(it.product_code)}</td>
      <td>${escHtml(it.product_name)}</td>
      <td style="font-size:12px">${escHtml(it.category_code)}</td>
      <td style="font-size:12px">${escHtml(it.product_group_manager || '—')}</td>
      <td style="font-size:12px">${escHtml(it.product_category || '—')}</td>
      <td class="num">${it.quantity.toLocaleString('lt', {maximumFractionDigits:2})}</td>
      <td class="num">${purQtyDelta(it.qty_delta)}</td>
      <td class="num">${fmt(it.unit_cost)}</td>
      <td class="num"><strong>${fmt(it.total_value)}</strong></td>
      <td class="num">${it.daily_avg > 0 ? it.daily_avg.toLocaleString('lt',{maximumFractionDigits:3}) : '—'}</td>
      <td class="num">${purWeeksStockBadge(it.days_stock)}</td>
      <td style="text-align:center">${purClearanceBadge(it.is_clearance)}</td>
      <td class="pur-comment-cell" data-code="${escAttr(it.product_code)}">${purCommentCellInner(it.product_code)}</td>
    `;
    tbody.appendChild(tr);
  });

  purDisplayed += batch.length;
  const moreBtn = document.getElementById('pur-load-more');
  moreBtn.style.display = purDisplayed < purAllItems.length ? 'block' : 'none';
}

function purClearanceBadge(v) {
  return v ? '<span class="pur-clearance-badge">TAIP</span>' : '<span style="color:var(--text-muted)">—</span>';
}

function purQtyDelta(d) {
  if (d === null || d === undefined) return '<span style="color:var(--text-muted)">—</span>';
  if (d === 0) return '<span style="color:var(--text-muted)">↔ 0</span>';
  if (d > 0) return `<span style="color:#16a34a">▲ ${d.toLocaleString('lt',{maximumFractionDigits:2})}</span>`;
  return `<span style="color:#dc2626">▼ ${Math.abs(d).toLocaleString('lt',{maximumFractionDigits:2})}</span>`;
}

// Nauja prekė šią savaitę: qty_delta === quantity (nebuvo ankstesniame snapshot'e)
// ARBA prekė pirmą kartą patenka į "Visiškai nejuda" (is_newly_illiquid)
function purNewBadge(i) {
  if (i.is_newly_illiquid) {
    return '<span style="color:#dc2626;font-weight:700;font-size:11px;white-space:nowrap">● NAUJA</span>';
  }
  if (!i.prev_snap_date || i.qty_delta === null || i.qty_delta === undefined) return '';
  if (i.quantity > 0 && Math.abs(i.qty_delta - i.quantity) < 0.001) {
    return '<span style="color:#dc2626;font-weight:700;font-size:11px;white-space:nowrap">● NAUJA</span>';
  }
  return '';
}

// Nelikvidžioms prekėms: sumažėjimas (parduota) = žalia, padidėjimas = raudona
function purQtyDeltaIlliq(d) {
  if (d === null || d === undefined) return '<span style="color:var(--text-muted)">—</span>';
  if (d === 0) return '<span style="color:var(--text-muted)">↔ 0</span>';
  if (d > 0) return `<span style="color:#dc2626">▲ ${d.toLocaleString('lt',{maximumFractionDigits:2})}</span>`;
  return `<span style="color:#16a34a">▼ ${Math.abs(d).toLocaleString('lt',{maximumFractionDigits:2})}</span>`;
}

function purWeeksStockBadge(w) {
  if (w === null || w === undefined) return '<span class="ws-none">0 d.</span>';
  if (w >= 999) return `<span class="ws-over">&gt;365 d.</span>`;
  if (w < 7)   return `<span class="ws-danger">${w} d.</span>`;
  if (w < 14)  return `<span class="ws-warning">${w} d.</span>`;
  if (w <= 84) return `<span class="ws-ok">${w} d.</span>`;
  return `<span class="ws-over">${w} d.</span>`;
}

document.getElementById('btn-pur-more').addEventListener('click', () => purRenderInventoryPage());

function purRenderCatSummary(items, elId) {
  const el = document.getElementById(elId);
  if (!el) return;

  // 1. Susumuoti pagal lapų kategorijas
  const leafAgg = {};
  for (const i of items) {
    const code = i.category_code || '—';
    if (!leafAgg[code]) leafAgg[code] = 0;
    leafAgg[code] += i.total_value;
  }

  // 2. Sukurti mazgus visiems lygiams (roll-up į tėvus)
  const totals = {};
  function addToNode(code, value) {
    if (!code) return;
    const cat = purCatMap[code];
    const name = cat ? cat.name : code;
    const level = cat ? cat.level : 2;
    const parent = cat ? cat.parent_code : null;
    if (!totals[code]) totals[code] = { code, name, level, parent_code: parent, value: 0 };
    totals[code].value += value;
    if (parent) addToNode(parent, value);
  }
  for (const [code, value] of Object.entries(leafAgg)) {
    addToNode(code, value);
  }

  // 3. Grupuoti pagal lygius
  const byLevel = { 0: [], 1: [], 2: [] };
  for (const n of Object.values(totals)) {
    const lvl = Math.min(n.level, 2);
    byLevel[lvl].push(n);
  }
  byLevel[0].sort((a, b) => b.value - a.value);
  byLevel[1].sort((a, b) => b.value - a.value);
  byLevel[2].sort((a, b) => b.value - a.value);

  // 4. Render hierarchiškai: top → mid → leaf
  let rows = '';
  for (const t of byLevel[0]) {
    rows += `<tr class="pcs-top">
      <td><strong>${escHtml(t.code)}</strong></td>
      <td><strong>${escHtml(t.name)}</strong></td>
      <td class="num"><strong>${fmt(t.value)}</strong></td>
    </tr>`;
    const mids = byLevel[1].filter(m => m.parent_code === t.code);
    for (const m of mids) {
      rows += `<tr class="pcs-mid">
        <td style="padding-left:16px">${escHtml(m.code)}</td>
        <td style="padding-left:16px">${escHtml(m.name)}</td>
        <td class="num">${fmt(m.value)}</td>
      </tr>`;
      const leaves = byLevel[2].filter(l => l.parent_code === m.code);
      for (const l of leaves) {
        rows += `<tr class="pcs-leaf">
          <td style="padding-left:34px">${escHtml(l.code)}</td>
          <td style="padding-left:34px">${escHtml(l.name)}</td>
          <td class="num">${fmt(l.value)}</td>
        </tr>`;
      }
    }
  }

  el.innerHTML = `
    <div class="pur-cat-summary-title">Suvestinė pagal kategorijas</div>
    <table class="pur-cat-summary-table">
      <thead><tr><th>Kodas</th><th>Kategorija</th><th class="num">Vertė €</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

// ── Illiquid history panel ─────────────────────────────

let purIlliqPanelOpen = false;

async function purLoadIlliquidHistory() {
  const res = await apiFetch('/api/purchases/warehouse/illiquid-history');
  if (!res || !res.ok) return;
  purIlliquidHistory = await res.json();
  purIlliqPanelOpen = false;
  purRenderIlliqPanel('none');
  purRenderIlliqPanel('partial');
}

function _ihlDelta(d, unit) {
  if (d === 0) return `<span class="d-same">↔ 0${unit}</span>`;
  if (d > 0)   return `<span class="d-up">▲ ${unit === ' €' ? fmt(Math.abs(d)) : Math.abs(d)}${unit}</span>`;
               return `<span class="d-down">▼ ${unit === ' €' ? fmt(Math.abs(d)) : Math.abs(d)}${unit}</span>`;
}

function purRenderIlliqPanel(suffix) {
  const containerId = `pur-illiq-panel-${suffix}`;
  const el = document.getElementById(containerId);
  if (!el) return;

  if (!purIlliquidHistory.length || !purCurrentSnapId) {
    el.innerHTML = '';
    return;
  }

  const curr = purIlliquidHistory.find(h => h.snap_id === purCurrentSnapId);
  if (!curr) return;

  // Current snapshot's live item counts from the already-loaded items
  const noneCount    = purIlliquidNoneItems.length;
  const noneVal      = purIlliquidNoneItems.reduce((s, i) => s + i.total_value, 0);
  const partialCount = purIlliquidPartialItems.length;
  const partialVal   = purIlliquidPartialItems.reduce((s, i) => s + i.total_value, 0);

  // Find previous snapshot (the one right before current in history, which is sorted desc)
  const idx  = purIlliquidHistory.findIndex(h => h.snap_id === purCurrentSnapId);
  const prev = purIlliquidHistory[idx + 1] || null;

  // Summary delta vs previous snapshot
  const noneDeltaHtml = prev ? `
    <div class="pur-illiq-sum-delta">
      ${_ihlDelta(curr.none_count - prev.none_count, ' pr.')}
      ${_ihlDelta(curr.none_value - prev.none_value, ' €')}
      <span class="d-same">nuo ${prev.snap_date}</span>
    </div>` : '';
  const partialDeltaHtml = prev ? `
    <div class="pur-illiq-sum-delta">
      ${_ihlDelta(curr.partial_count - prev.partial_count, ' pr.')}
      ${_ihlDelta(curr.partial_value - prev.partial_value, ' €')}
      <span class="d-same">nuo ${prev.snap_date}</span>
    </div>` : '';

  // History table rows
  const tableRows = purIlliquidHistory.map((h, i) => {
    const isCurrent = h.snap_id === purCurrentSnapId;
    const next = purIlliquidHistory[i + 1] || null; // older snapshot
    const noneDC  = next ? _ihlDelta(h.none_count    - next.none_count,    ' pr.') : '';
    const noneDV  = next ? _ihlDelta(h.none_value    - next.none_value,    ' €')   : '';
    const partDC  = next ? _ihlDelta(h.partial_count - next.partial_count, ' pr.') : '';
    const partDV  = next ? _ihlDelta(h.partial_value - next.partial_value, ' €')   : '';

    const noneCntDelta  = next ? `<div class="ihl-delta-cell">${noneDC}</div>` : '';
    const noneValDelta  = next ? `<div class="ihl-delta-cell">${noneDV}</div>` : '';
    const partCntDelta  = next ? `<div class="ihl-delta-cell">${partDC}</div>` : '';
    const partValDelta  = next ? `<div class="ihl-delta-cell">${partDV}</div>` : '';

    return `<tr class="${isCurrent ? 'ihl-current' : ''}">
      <td class="ihl-date">${h.snap_date}${isCurrent ? '<span class="ihl-cur-badge">dabar</span>' : ''}</td>
      <td class="num"><strong>${h.none_count}</strong>${noneCntDelta}</td>
      <td class="num">${fmt(h.none_value)} €${noneValDelta}</td>
      <td class="num"><strong>${h.partial_count}</strong>${partCntDelta}</td>
      <td class="num">${fmt(h.partial_value)} €${partValDelta}</td>
    </tr>`;
  }).join('');

  const bodyStyle = purIlliqPanelOpen ? '' : 'display:none';
  const arrowClass = purIlliqPanelOpen ? 'open' : '';

  el.innerHTML = `
    <div class="pur-illiq-panel">
      <div class="pur-illiq-panel-head" onclick="purToggleIlliqPanel()">
        <div class="pur-illiq-summary">
          <div class="pur-illiq-sum-block">
            <div class="pur-illiq-sum-label">🔴 Visiškai nejuda</div>
            <div>
              <div class="pur-illiq-sum-vals">
                <strong>${noneCount}</strong> pr. &nbsp;|&nbsp; <strong>${fmt(noneVal)} €</strong>
              </div>
              ${noneDeltaHtml}
            </div>
          </div>
          <div class="pur-illiq-divider"></div>
          <div class="pur-illiq-sum-block">
            <div class="pur-illiq-sum-label">🟡 Dalinai nejuda</div>
            <div>
              <div class="pur-illiq-sum-vals">
                <strong>${partialCount}</strong> pr. &nbsp;|&nbsp; <strong>${fmt(partialVal)} €</strong>
              </div>
              ${partialDeltaHtml}
            </div>
          </div>
        </div>
        ${purIlliquidHistory.length > 1
          ? `<span class="pur-illiq-panel-arrow ${arrowClass}">▼</span>`
          : ''}
      </div>
      ${purIlliquidHistory.length > 1 ? `
      <div class="pur-illiq-panel-body" style="${bodyStyle}">
        <table class="pur-illiq-hist-table">
          <thead><tr>
            <th>Data</th>
            <th class="num">🔴 Prekių</th>
            <th class="num">🔴 Suma €</th>
            <th class="num">🟡 Prekių</th>
            <th class="num">🟡 Suma €</th>
          </tr></thead>
          <tbody>${tableRows}</tbody>
        </table>
      </div>` : ''}
    </div>`;
}

function purToggleIlliqPanel() {
  purIlliqPanelOpen = !purIlliqPanelOpen;
  // Re-render both panels with the new open state
  purRenderIlliqPanel('none');
  purRenderIlliqPanel('partial');
}

// ─────────────────────────────────────────────────────

function purIlliquidSortVal(i, col) {
  if (col === 'sold_pct') {
    return (i.quantity > 0 && i.sales_12m > 0) ? (i.sales_12m / i.quantity * 100) : 0;
  }
  if (col === 'is_new') {
    return purNewBadge(i) ? 1 : 0;
  }
  if (col === 'is_clearance') {
    return i.is_clearance ? 1 : 0;
  }
  return i[col];
}

function purSortItems(arr, col, dir) {
  const textCols = ['product_code', 'product_name', 'category_code', 'product_group_manager', 'product_category'];
  return [...arr].sort((a, b) => {
    let av = purIlliquidSortVal(a, col);
    let bv = purIlliquidSortVal(b, col);
    if (av === null || av === undefined) av = dir === 'asc' ? Infinity : -Infinity;
    if (bv === null || bv === undefined) bv = dir === 'asc' ? Infinity : -Infinity;
    if (textCols.includes(col)) return dir === 'asc' ? String(av).localeCompare(String(bv)) : String(bv).localeCompare(String(av));
    return dir === 'asc' ? av - bv : bv - av;
  });
}

function purUpdateSortIcons(tableId, activeCol, activeDir) {
  document.querySelectorAll(`#${tableId} .pur-sortable`).forEach(th => {
    th.classList.remove('sort-asc', 'sort-desc');
    if (th.dataset.col === activeCol) th.classList.add(activeDir === 'asc' ? 'sort-asc' : 'sort-desc');
  });
}

function purRenderNone() {
  const manager = document.getElementById('pur-none-manager-filter')?.value || '';
  const prodcat = document.getElementById('pur-none-prodcat-filter')?.value || '';
  let items = purIlliquidNoneItems;
  if (manager) items = items.filter(i => i.product_group_manager === manager);
  if (prodcat) items = items.filter(i => i.product_category === prodcat);
  const sorted = purSortItems(items, purNoneSortCol, purNoneSortDir);
  purUpdateSortIcons('pur-illiquid-none-table', purNoneSortCol, purNoneSortDir);
  document.getElementById('pur-illiquid-none-body').innerHTML = sorted.slice(0, 300).map(i => `
    <tr>
      <td style="font-size:12px;color:var(--text-muted)">${escHtml(i.product_code)}</td>
      <td>${escHtml(i.product_name)}</td>
      <td style="font-size:12px">${escHtml(i.category_code)}</td>
      <td style="font-size:12px">${escHtml(i.product_group_manager || '—')}</td>
      <td style="font-size:12px">${escHtml(i.product_category || '—')}</td>
      <td>${purNewBadge(i)}</td>
      <td class="num">${i.quantity.toLocaleString('lt',{maximumFractionDigits:2})}</td>
      <td class="num">${purQtyDeltaIlliq(i.qty_delta)}</td>
      <td class="num"><strong>${fmt(i.total_value)}</strong></td>
      <td style="text-align:center">${purClearanceBadge(i.is_clearance)}</td>
      <td class="pur-comment-cell" data-code="${escAttr(i.product_code)}">${purCommentCellInner(i.product_code)}</td>
    </tr>`).join('');
}

function purRenderPartial() {
  const manager = document.getElementById('pur-partial-manager-filter')?.value || '';
  const prodcat = document.getElementById('pur-partial-prodcat-filter')?.value || '';
  let items = purIlliquidPartialItems;
  if (manager) items = items.filter(i => i.product_group_manager === manager);
  if (prodcat) items = items.filter(i => i.product_category === prodcat);
  const sorted = purSortItems(items, purPartialSortCol, purPartialSortDir);
  purUpdateSortIcons('pur-illiquid-partial-table', purPartialSortCol, purPartialSortDir);
  document.getElementById('pur-illiquid-partial-body').innerHTML = sorted.slice(0, 300).map(i => {
    const pct = i.sales_12m > 0 && i.quantity > 0
      ? (i.sales_12m / i.quantity * 100).toFixed(1)
      : '0.0';
    return `<tr>
      <td style="font-size:12px;color:var(--text-muted)">${escHtml(i.product_code)}</td>
      <td>${escHtml(i.product_name)}</td>
      <td style="font-size:12px">${escHtml(i.category_code)}</td>
      <td style="font-size:12px">${escHtml(i.product_group_manager || '—')}</td>
      <td style="font-size:12px">${escHtml(i.product_category || '—')}</td>
      <td>${purNewBadge(i)}</td>
      <td class="num">${i.quantity.toLocaleString('lt',{maximumFractionDigits:2})}</td>
      <td class="num">${purQtyDeltaIlliq(i.qty_delta)}</td>
      <td class="num"><strong>${fmt(i.total_value)}</strong></td>
      <td class="num">${i.sales_12m.toLocaleString('lt',{maximumFractionDigits:2})}</td>
      <td class="num">${pct}%</td>
      <td style="text-align:center">${purClearanceBadge(i.is_clearance)}</td>
      <td class="pur-comment-cell" data-code="${escAttr(i.product_code)}">${purCommentCellInner(i.product_code)}</td>
    </tr>`;
  }).join('');
}

function purRenderSpecialTabs(items, totalDays) {
  purIlliquidNoneItems    = items.filter(i => i.illiquid_type === 'none');
  purIlliquidPartialItems = items.filter(i => i.illiquid_type === 'partial');
  const overstock = items.filter(i => i.illiquid_type === null && i.days_stock !== null && i.days_stock > 84 && i.days_stock < 999);

  const overstockVal = overstock.reduce((s, i) => s + i.total_value, 0);

  // Visiškai nejuda + Dalinai nejuda — panels rendered once history loads
  // Show a simple loading placeholder immediately
  ['none','partial'].forEach(s => {
    const el = document.getElementById(`pur-illiq-panel-${s}`);
    if (el) el.innerHTML = '<div style="padding:12px 18px;font-size:13px;color:var(--text-muted)">Kraunama...</div>';
  });
  purNoneSortCol = 'total_value'; purNoneSortDir = 'desc';
  purRenderNone();
  purRenderCatSummary(purIlliquidNoneItems, 'pur-illiquid-none-cats');

  purPartialSortCol = 'total_value'; purPartialSortDir = 'desc';
  purRenderPartial();
  purRenderCatSummary(purIlliquidPartialItems, 'pur-illiquid-partial-cats');

  // Overstock
  purOverstockItems = overstock;
  document.getElementById('pur-overstock-header').innerHTML = `
    <span><strong>${overstock.length}</strong> prekių su per dideliu likučiu (&gt;84 d.)</span>
    <span>Bendra vertė: <strong>${fmt(overstockVal)} €</strong></span>
  `;
  purOverSortCol = 'total_value'; purOverSortDir = 'desc';
  purRenderOverstock();

}

function purRenderOverstock() {
  const manager = document.getElementById('pur-over-manager-filter')?.value || '';
  const prodcat = document.getElementById('pur-over-prodcat-filter')?.value || '';
  let items = purOverstockItems;
  if (manager) items = items.filter(i => i.product_group_manager === manager);
  if (prodcat) items = items.filter(i => i.product_category === prodcat);
  const sorted = purSortItems(items, purOverSortCol, purOverSortDir);
  purUpdateSortIcons('pur-overstock-table', purOverSortCol, purOverSortDir);
  document.getElementById('pur-overstock-body').innerHTML = sorted.slice(0, 200).map(i => `
    <tr>
      <td style="font-size:12px;color:var(--text-muted)">${escHtml(i.product_code)}</td>
      <td>${escHtml(i.product_name)}</td>
      <td style="font-size:12px">${escHtml(i.category_code)}</td>
      <td style="font-size:12px">${escHtml(i.product_group_manager || '—')}</td>
      <td style="font-size:12px">${escHtml(i.product_category || '—')}</td>
      <td>${purNewBadge(i)}</td>
      <td class="num">${i.quantity.toLocaleString('lt',{maximumFractionDigits:2})}</td>
      <td class="num">${purQtyDelta(i.qty_delta)}</td>
      <td class="num"><strong>${fmt(i.total_value)}</strong></td>
      <td class="num">${i.daily_avg > 0 ? i.daily_avg.toFixed(3) : '—'}</td>
      <td class="num">${purWeeksStockBadge(i.days_stock)}</td>
      <td style="text-align:center">${purClearanceBadge(i.is_clearance)}</td>
    </tr>`).join('');
}


// ── Filters ────────────────────────────────────────────

function purSetupFilters() {
  let searchTimer;
  document.getElementById('pur-search').addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => purLoadInventory(), 350);
  });
  document.getElementById('pur-cat-filter').addEventListener('change', () => purLoadInventory());
  document.getElementById('pur-manager-filter').addEventListener('change', () => purLoadInventory());
  document.getElementById('pur-prodcat-filter').addEventListener('change', () => purLoadInventory());

  // Special tab filters (client-side re-render)
  ['none','partial','over'].forEach(suffix => {
    const mgr = document.getElementById(`pur-${suffix}-manager-filter`);
    const cat = document.getElementById(`pur-${suffix}-prodcat-filter`);
    const renderFn = { none: purRenderNone, partial: purRenderPartial, over: purRenderOverstock }[suffix];
    if (mgr) mgr.addEventListener('change', renderFn);
    if (cat) cat.addEventListener('change', renderFn);
  });
  document.getElementById('pur-sort-select').addEventListener('change', () => {
    const v = document.getElementById('pur-sort-select').value;
    const map = {
      value_desc:  ['total_value','desc'], value_asc:  ['total_value','asc'],
      weeks_asc:   ['days_stock','asc'],   weeks_desc: ['days_stock','desc'],
      sold_desc:   ['total_sold','desc'],  name_asc:   ['product_name','asc'],
    };
    [purSortCol, purSortDir] = map[v] || ['total_value','desc'];
    purSortAndRender();
  });

  // Column header click sorting — main inventory
  document.getElementById('pur-inv-table').addEventListener('click', e => {
    const th = e.target.closest('.pur-sortable');
    if (!th) return;
    const col = th.dataset.col;
    if (purSortCol === col) {
      purSortDir = purSortDir === 'asc' ? 'desc' : 'asc';
    } else {
      purSortCol = col;
      purSortDir = ['product_code','product_name','category_code'].includes(col) ? 'asc' : 'desc';
    }
    // Sync dropdown
    const revMap = {
      'total_value:desc':'value_desc','total_value:asc':'value_asc',
      'days_stock:asc':'weeks_asc','days_stock:desc':'weeks_desc',
      'total_sold:desc':'sold_desc','product_name:asc':'name_asc',
    };
    const key = `${purSortCol}:${purSortDir}`;
    if (revMap[key]) document.getElementById('pur-sort-select').value = revMap[key];
    purSortAndRender();
  });

  // Column header click sorting — Visiškai nejuda
  document.getElementById('pur-illiquid-none-table').addEventListener('click', e => {
    const th = e.target.closest('.pur-sortable');
    if (!th) return;
    const col = th.dataset.col;
    if (purNoneSortCol === col) {
      purNoneSortDir = purNoneSortDir === 'asc' ? 'desc' : 'asc';
    } else {
      purNoneSortCol = col;
      purNoneSortDir = ['product_code','product_name','category_code','product_group_manager','product_category'].includes(col) ? 'asc' : 'desc';
    }
    purRenderNone();
  });

  // Column header click sorting — Dalinai nejuda
  document.getElementById('pur-illiquid-partial-table').addEventListener('click', e => {
    const th = e.target.closest('.pur-sortable');
    if (!th) return;
    const col = th.dataset.col;
    if (purPartialSortCol === col) {
      purPartialSortDir = purPartialSortDir === 'asc' ? 'desc' : 'asc';
    } else {
      purPartialSortCol = col;
      purPartialSortDir = ['product_code','product_name','category_code','product_group_manager','product_category'].includes(col) ? 'asc' : 'desc';
    }
    purRenderPartial();
  });

  // Column header click sorting — Per daug sandėlyje
  document.getElementById('pur-overstock-table').addEventListener('click', e => {
    const th = e.target.closest('.pur-sortable');
    if (!th) return;
    const col = th.dataset.col;
    if (purOverSortCol === col) {
      purOverSortDir = purOverSortDir === 'asc' ? 'desc' : 'asc';
    } else {
      purOverSortCol = col;
      purOverSortDir = ['product_code','product_name','category_code'].includes(col) ? 'asc' : 'desc';
    }
    purRenderOverstock();
  });


  // Comment compose textarea: Enter = send, Shift+Enter = newline
  document.getElementById('pur-comment-textarea').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); purSaveComment(); }
  });

}

// ── Tabs ───────────────────────────────────────────────

function purSetupTabs() {
  document.querySelectorAll('.pur-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.pur-tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.pur-tab-content').forEach(c => c.classList.remove('active'));
      tab.classList.add('active');
      document.getElementById('pur-tab-' + tab.dataset.tab).classList.add('active');
    });
  });
}

function purSwitchToWeeks() {
  document.querySelectorAll('.pur-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.pur-tab-content').forEach(c => c.classList.remove('active'));
  document.getElementById('pur-tab-weeks').classList.add('active');
}

// ── Sales weeks list ───────────────────────────────────

let purAllWeeks = [];
let purSelectedWeekId = null;
let purPeriodPickerOpen = false;

async function purLoadWeeks() {
  const res = await apiFetch('/api/purchases/sales-weeks');
  if (!res || !res.ok) return;
  purAllWeeks = await res.json();

  const statusEl    = document.getElementById('pur-sw-status');
  const tabList     = document.getElementById('pur-weeks-list');
  const modalSec    = document.getElementById('pur-modal-weeks-section');
  const modalList   = document.getElementById('pur-weeks-modal-list');

  if (purAllWeeks.length === 0) {
    statusEl.textContent = 'Neįkelta';
    statusEl.style.color = '';
    if (modalSec) modalSec.style.display = 'none';
    if (tabList) tabList.innerHTML = '<div class="pur-ai-placeholder">Pardavimų periodų duomenų nėra.</div>';
    return;
  }

  statusEl.textContent = `✓ ${purAllWeeks.length} periodų įkelta`;
  statusEl.style.color = 'var(--success)';
  purSetChip('pur-chip-sw', `${purAllWeeks[0].period_from}`, true);

  // Modal compact list
  if (modalSec) modalSec.style.display = 'block';
  if (modalList) {
    modalList.innerHTML = purAllWeeks.map(w => `
      <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;padding:5px 8px;background:white;border:1px solid var(--border);border-radius:6px;font-size:12.5px;">
        <div>
          <span style="font-weight:600;color:var(--text)">${w.period_from} – ${w.period_to}</span>
          <span style="color:var(--text-muted);margin-left:8px">${w.total_items} prk.</span>
        </div>
        <button class="btn-danger btn-sm" style="padding:2px 8px;font-size:11px" onclick="purDeletePeriod(${w.id},'${w.period_from} – ${w.period_to}')">🗑</button>
      </div>
    `).join('');
  }

  // Tab list (Visi periodai)
  if (tabList) {
    tabList.innerHTML = purAllWeeks.map(w => `
      <div class="pur-week-card">
        <div style="flex:1">
          <div class="pur-week-period">${w.period_from} – ${w.period_to}</div>
          <div class="pur-week-detail">${w.file_name}  ·  ${w.total_items} prekių  ·  ${w.total_qty.toLocaleString('lt',{maximumFractionDigits:0})} vnt.</div>
        </div>
        <button class="btn-danger btn-sm" onclick="purDeletePeriod(${w.id}, '${w.period_from} – ${w.period_to}')">🗑</button>
      </div>
    `).join('');
  }
}

function purRenderPeriodDropdown() {
  const list = document.getElementById('pur-period-list');
  if (!list) return;

  const byYear = {};
  purAllWeeks.forEach(w => {
    const yr = w.period_from.slice(0, 4);
    if (!byYear[yr]) byYear[yr] = [];
    byYear[yr].push(w);
  });

  list.innerHTML = Object.keys(byYear).sort().reverse().map(yr => {
    const rows = byYear[yr].map(w => `
      <div class="pur-pp-row ${purSelectedWeekId === w.id ? 'active' : ''}" onclick="purSelectPeriod(${w.id})">
        <span class="pur-pp-row-period">${w.period_from} – ${w.period_to}</span>
        <span class="pur-pp-row-detail">${w.total_items} prk. · ${w.total_qty.toLocaleString('lt',{maximumFractionDigits:0})} vnt.</span>
        <button class="pur-pp-del" onclick="event.stopPropagation();purDeletePeriod(${w.id},'${w.period_from} – ${w.period_to}')" title="Ištrinti">×</button>
      </div>`).join('');
    return `<div class="pur-pp-year">${yr}</div>${rows}`;
  }).join('');
}

function purTogglePeriodPicker() {
  const dd = document.getElementById('pur-period-dropdown');
  const arrow = document.getElementById('pur-pp-arrow');
  purPeriodPickerOpen = !purPeriodPickerOpen;
  dd.classList.toggle('open', purPeriodPickerOpen);
  if (arrow) arrow.classList.toggle('open', purPeriodPickerOpen);
}

function purSelectPeriod(id) {
  purSelectedWeekId = id;
}

async function purDeletePeriod(id, label) {
  if (!confirm(`Ištrinti periodą: ${label || id}?`)) return;
  const res = await apiFetch(`/api/purchases/sales-weeks/${id}`, { method: 'DELETE' });
  if (!res || !res.ok) { alert('Klaida trinant'); return; }
  if (purSelectedWeekId === id) purSelectedWeekId = null;
  await purLoadWeeks();
}

// Close picker on outside click
document.addEventListener('click', e => {
  if (!e.target.closest('#pur-period-picker')) {
    const dd = document.getElementById('pur-period-dropdown');
    const arrow = document.getElementById('pur-pp-arrow');
    if (dd) { dd.classList.remove('open'); purPeriodPickerOpen = false; }
    if (arrow) arrow.classList.remove('open');
  }
});

// ── AI Analysis ────────────────────────────────────────

async function purRunAnalysis() {
  if (!purCurrentSnapId) return;
  const btn = document.getElementById('btn-pur-analyze');
  btn.disabled = true;
  btn.textContent = 'Paleidžiama...';

  const res = await apiFetch(`/api/purchases/warehouse/snapshots/${purCurrentSnapId}/analyze`, { method: 'POST' });
  if (!res || !res.ok) {
    btn.disabled = false;
    btn.textContent = '🤖 Generuoti analizę';
    return;
  }
  btn.disabled = false;
  btn.textContent = '🤖 Generuoti analizę';

  // Switch to AI tab
  document.querySelectorAll('.pur-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.pur-tab-content').forEach(c => c.classList.remove('active'));
  document.querySelector('.pur-tab[data-tab="ai"]').classList.add('active');
  document.getElementById('pur-tab-ai').classList.add('active');

  document.getElementById('pur-ai-content').innerHTML = purAIProgressHtml(null);
  purPollAnalysis(purCurrentSnapId);
}

function purAIProgressHtml(progress) {
  const steps = [
    { key: 'overall',   label: 'Bendra situacija' },
    { key: 'illiquid',  label: 'Nelikvidžios prekės' },
    { key: 'overstock', label: 'Per daug sandėlyje' },
    { key: 'reorder',   label: 'Rekomendacijos užsakymui' },
  ];
  const done  = steps.filter(s => progress && progress[s.key]).length;
  const total = steps.length;
  const runningIdx = progress ? steps.findIndex(s => !progress[s.key]) : 0;
  const rows = steps.map((s, i) => {
    const isDone = progress && progress[s.key];
    const isRunning = !isDone && i === runningIdx;
    const icon = isDone ? '✅' : isRunning ? '⏳' : '⬜';
    const cls  = isDone ? 'ai-prog-done' : isRunning ? 'ai-prog-run' : 'ai-prog-wait';
    return `<div class="ai-prog-row ${cls}">${icon} ${s.label}</div>`;
  }).join('');
  return `
    <div class="ai-progress-box">
      <div class="ai-prog-title">🤖 AI analizė vykdoma... ${done}/${total}</div>
      <div class="ai-prog-bar-wrap"><div class="ai-prog-bar" style="width:${Math.round(done/total*100)}%"></div></div>
      <div class="ai-prog-steps">${rows}</div>
      <div class="ai-prog-hint">Automatiškai atnaujinama kas ~8 sek.</div>
    </div>`;
}

async function purPollAnalysis(snapId) {
  const poll = async () => {
    if (purCurrentSnapId !== snapId) return;
    const res = await apiFetch(`/api/purchases/warehouse/snapshots/${snapId}/analysis`);
    if (!res || !res.ok) return;
    const data = await res.json();
    const el = document.getElementById('pur-ai-content');
    if (!el) return;
    if (data.ready) {
      el.innerHTML = purBuildAIHtml(data);
    } else {
      el.innerHTML = purAIProgressHtml(data.progress || null);
      setTimeout(poll, 8000);
    }
  };
  setTimeout(poll, 8000);
}

async function purCheckAnalysis() {
  if (!purCurrentSnapId) return;
  const res = await apiFetch(`/api/purchases/warehouse/snapshots/${purCurrentSnapId}/analysis`);
  if (!res || !res.ok) return;
  const data = await res.json();
  if (data.ready) {
    document.getElementById('pur-ai-content').innerHTML = purBuildAIHtml(data);
  } else if (data.progress && Object.values(data.progress).some(Boolean)) {
    // Partially done — still running
    document.getElementById('pur-ai-content').innerHTML = purAIProgressHtml(data.progress);
    purPollAnalysis(purCurrentSnapId);
  }
}

function purBuildAIHtml(data) {
  const sections = [
    { key: 'ai_overall',   title: '📊 Bendra situacija',          color: '#3b82f6' },
    { key: 'ai_illiquid',  title: '🔴 Nelikvidžios prekės',        color: '#ef4444' },
    { key: 'ai_overstock', title: '📈 Per daug sandėlyje',         color: '#f59e0b' },
    { key: 'ai_reorder',   title: '🛒 Rekomendacijos užsakymui',   color: '#10b981' },
  ];
  return sections.map(s => {
    const text = data[s.key];
    if (!text) return '';
    return `
      <div class="pur-ai-section" style="border-left:4px solid ${s.color}">
        <div class="pur-ai-section-title" style="color:${s.color}">${s.title}</div>
        <div class="pur-ai-body">${purFormatAIText(text)}</div>
      </div>`;
  }).join('');
}

function purFormatAIText(text) {
  return text
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/^### (.+)$/gm, '<div class="ai-h4">$1</div>')
    .replace(/^## (.+)$/gm, '<div class="ai-h4">$1</div>')
    .replace(/^# (.+)$/gm, '<div class="ai-h4">$1</div>')
    .replace(/\n/g, '<br>');
}

// ── ADMIN MODULE ──────────────────────────────────────────────────────────────
//
// KAIP PRIDĖTI NAUJĄ MODULĮ:
//  1. backend/models/user.py      → pridėti lauką:  can_view_[modulis] = Column(Boolean, default=False)
//  2. backend/auth.py             → pridėti funkciją: require_[modulis](current_user=Depends(get_current_user))
//  3. backend/routers/auth.py     → į UserCreate ir UserUpdate įtraukti naują lauką
//                                   → _user_dict() grąžinamame dict'e pridėti naują lauką
//  4. frontend/js/app.js          → žemiau esančiame ADMIN_MODULES masyve pridėti eilutę
//  5. frontend/js/app.js showApp()→ pridėti: if (mods.[modulis]) document.getElementById('nav-[modulis]').classList.remove('disabled');
//
// Admin UI automatiškai atvaizduos naują checkbox'ą vartotojo formoje.

// Moduliai — pridėjus naują modulį į sistemą, tereikia papildyti šį masyvą
const ADMIN_MODULES = [
  { key: 'can_view_receivables', label: '💰 Klientų skolos' },
  { key: 'can_view_payables',    label: '📤 Skolos tiekėjams' },
  { key: 'can_view_sales',       label: '📈 Pardavimai' },
  { key: 'can_view_purchases',   label: '🛒 Pirkimai' },
];

let adminUsersCache = [];
let adminEditingId = null;

// Sugeneruojame modulių checkbox'us modaliniame lange
function adminInitModulesGrid() {
  const grid = document.getElementById('admin-modules-grid');
  if (!grid || grid.dataset.built) return;
  grid.innerHTML = ADMIN_MODULES.map(m => `
    <label class="checkbox-label">
      <input type="checkbox" id="admin-mod-${m.key}" /> ${m.label}
    </label>`).join('');
  grid.dataset.built = '1';
}

async function adminLoad() {
  if (!currentUser || !currentUser.is_admin) return;
  const res = await apiFetch('/api/auth/users');
  if (!res || !res.ok) return;
  adminUsersCache = await res.json();
  adminRenderUsers(adminUsersCache);
}

function adminRenderUsers(users) {
  const tbody = document.getElementById('admin-users-tbody');
  if (!users.length) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--text-muted);padding:24px">Nėra vartotojų</td></tr>';
    return;
  }
  tbody.innerHTML = users.map(u => {
    const modLabels = u.is_admin
      ? '<em style="color:#7c3aed">Visi moduliai</em>'
      : (ADMIN_MODULES.filter(m => u[m.key]).map(m => m.label).join(', ') || '<span style="color:#94a3b8">—</span>');
    const roleEl = u.is_admin
      ? '<span class="admin-badge-admin">Admin</span>'
      : '<span class="admin-badge-user">Vartotojas</span>';
    const statusEl = u.is_active
      ? '<span class="admin-badge-active">● Aktyvus</span>'
      : '<span class="admin-badge-inactive">● Išjungtas</span>';
    const created = u.created_at ? new Date(u.created_at).toLocaleDateString('lt') : '—';
    const isSelf = currentUser && u.id === currentUser.id;
    return `<tr>
      <td><strong>${escHtml(u.username)}</strong>${isSelf ? ' <span style="font-size:11px;color:#94a3b8">(jūs)</span>' : ''}</td>
      <td>${escHtml(u.full_name || '—')}</td>
      <td style="font-size:12px">${modLabels}</td>
      <td>${roleEl}</td>
      <td>${statusEl}</td>
      <td style="font-size:12px;color:var(--text-muted)">${created}</td>
      <td>
        <div class="admin-action-btns">
          <button class="btn-secondary btn-sm" onclick="adminOpenEdit(${u.id})">✏ Redaguoti</button>
          ${!isSelf ? `<button class="btn-danger btn-sm" onclick="adminDeleteUser(${u.id}, '${escHtml(u.username)}')">🗑</button>` : ''}
        </div>
      </td>
    </tr>`;
  }).join('');
}

function adminOpenCreate() {
  adminInitModulesGrid();
  adminEditingId = null;
  document.getElementById('admin-modal-title').textContent = 'Naujas vartotojas';
  document.getElementById('admin-field-username').value = '';
  document.getElementById('admin-field-username').disabled = false;
  document.getElementById('admin-field-fullname').value = '';
  document.getElementById('admin-field-password').value = '';
  document.getElementById('admin-field-password').placeholder = 'Slaptažodis (privaloma)';
  document.getElementById('admin-pwd-label').textContent = 'Slaptažodis *';
  document.getElementById('admin-field-is-admin').checked = false;
  document.getElementById('admin-field-is-active').checked = true;
  document.getElementById('admin-field-is-active').parentElement.style.display = '';
  ADMIN_MODULES.forEach(m => {
    const el = document.getElementById(`admin-mod-${m.key}`);
    if (el) el.checked = false;
  });
  document.getElementById('admin-error').textContent = '';
  document.getElementById('admin-user-modal').classList.remove('hidden');
  setTimeout(() => document.getElementById('admin-field-username').focus(), 50);
}

function adminOpenEdit(userId) {
  adminInitModulesGrid();
  const u = adminUsersCache.find(x => x.id === userId);
  if (!u) return;
  adminEditingId = userId;
  document.getElementById('admin-modal-title').textContent = `Redaguoti: ${u.username}`;
  document.getElementById('admin-field-username').value = u.username;
  document.getElementById('admin-field-username').disabled = true;
  document.getElementById('admin-field-fullname').value = u.full_name || '';
  document.getElementById('admin-field-password').value = '';
  document.getElementById('admin-field-password').placeholder = 'Palikite tuščią jei nekeičiate';
  document.getElementById('admin-pwd-label').textContent = 'Naujas slaptažodis (neprivaloma)';
  document.getElementById('admin-field-is-admin').checked = u.is_admin;
  document.getElementById('admin-field-is-active').checked = u.is_active;
  // negalima pačiam sau išjungti paskyros
  document.getElementById('admin-field-is-active').parentElement.style.display =
    (currentUser && u.id === currentUser.id) ? 'none' : '';
  ADMIN_MODULES.forEach(m => {
    const el = document.getElementById(`admin-mod-${m.key}`);
    if (el) el.checked = u[m.key] || false;
  });
  document.getElementById('admin-error').textContent = '';
  document.getElementById('admin-user-modal').classList.remove('hidden');
  setTimeout(() => document.getElementById('admin-field-fullname').focus(), 50);
}

function adminCloseModal() {
  document.getElementById('admin-user-modal').classList.add('hidden');
}

function adminModalOverlayClick(e) {
  if (e.target === document.getElementById('admin-user-modal')) adminCloseModal();
}

async function adminSaveUser() {
  const errEl = document.getElementById('admin-error');
  errEl.textContent = '';

  const username  = document.getElementById('admin-field-username').value.trim();
  const full_name = document.getElementById('admin-field-fullname').value.trim() || null;
  const password  = document.getElementById('admin-field-password').value;
  const is_admin  = document.getElementById('admin-field-is-admin').checked;
  const is_active = document.getElementById('admin-field-is-active').checked;

  const mods = {};
  ADMIN_MODULES.forEach(m => {
    const el = document.getElementById(`admin-mod-${m.key}`);
    mods[m.key] = el ? el.checked : false;
  });

  let res;
  if (!adminEditingId) {
    // Kuriame naują vartotoją
    if (!username) { errEl.textContent = 'Vartotojo vardas privalomas'; return; }
    if (!password) { errEl.textContent = 'Slaptažodis privalomas kuriant vartotoją'; return; }
    res = await apiFetch('/api/auth/users', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, full_name, password, is_admin, ...mods }),
    });
  } else {
    // Atnaujiname esamą
    const body = { full_name, is_admin, is_active, ...mods };
    if (password) body.password = password;
    res = await apiFetch(`/api/auth/users/${adminEditingId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  }

  if (!res) return;
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    errEl.textContent = data.detail || 'Klaida išsaugant';
    return;
  }

  adminCloseModal();
  adminLoad();
}

async function adminDeleteUser(userId, username) {
  if (!confirm(`Ištrinti vartotoją „${username}"?\n\nŠio veiksmo negalima atšaukti.`)) return;
  const res = await apiFetch(`/api/auth/users/${userId}`, { method: 'DELETE' });
  if (!res) return;
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    alert(data.detail || 'Klaida trinant vartotoją');
    return;
  }
  adminLoad();
}

// ── PROFILIS / SLAPTAŽODŽIO KEITIMAS ──────────────────────────────────────────

function profileOpenModal() {
  const name = currentUser
    ? (currentUser.full_name ? `${currentUser.full_name} (${currentUser.username})` : currentUser.username)
    : '';
  document.getElementById('profile-current-user').textContent = `Prisijungęs kaip: ${name}`;
  document.getElementById('profile-old-password').value = '';
  document.getElementById('profile-new-password').value = '';
  document.getElementById('profile-confirm-password').value = '';
  document.getElementById('profile-error').textContent = '';
  document.getElementById('profile-success').textContent = '';
  document.getElementById('profile-modal').classList.remove('hidden');
  setTimeout(() => document.getElementById('profile-old-password').focus(), 50);
}

function profileCloseModal() {
  document.getElementById('profile-modal').classList.add('hidden');
}

function profileModalOverlayClick(e) {
  if (e.target === document.getElementById('profile-modal')) profileCloseModal();
}

async function profileSavePassword() {
  const errEl = document.getElementById('profile-error');
  const okEl  = document.getElementById('profile-success');
  errEl.textContent = '';
  okEl.textContent  = '';

  const old_password = document.getElementById('profile-old-password').value;
  const new_password = document.getElementById('profile-new-password').value;
  const confirm      = document.getElementById('profile-confirm-password').value;

  if (!old_password || !new_password || !confirm) {
    errEl.textContent = 'Užpildykite visus laukus'; return;
  }
  if (new_password !== confirm) {
    errEl.textContent = 'Nauji slaptažodžiai nesutampa'; return;
  }
  if (new_password.length < 4) {
    errEl.textContent = 'Slaptažodis per trumpas (min. 4 simboliai)'; return;
  }

  const res = await apiFetch('/api/auth/me/password', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ old_password, new_password }),
  });

  if (!res) return;
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    errEl.textContent = data.detail || 'Klaida keičiant slaptažodį';
    return;
  }

  okEl.textContent = '✓ Slaptažodis sėkmingai pakeistas';
  document.getElementById('profile-old-password').value = '';
  document.getElementById('profile-new-password').value = '';
  document.getElementById('profile-confirm-password').value = '';
}

// ── PRODUCT COMMENTS ─────────────────────────────────────────────────────────

function purCommentCellInner(code) {
  const comments = purCommentsCache[code] || [];
  const count    = comments.length;
  const last     = count ? comments[count - 1] : null;
  const preview  = last
    ? escHtml(last.text.length > 38 ? last.text.slice(0, 38) + '…' : last.text)
    : '';
  const btnClass = count > 0 ? 'pur-comment-btn has-comments' : 'pur-comment-btn';
  return `<div class="pur-comment-inner">
    ${preview ? `<span class="pur-comment-preview" title="${escAttr(last.text)}">${preview}</span>` : ''}
    <button class="${btnClass}" onclick="purOpenCommentModal('${escAttr(code)}',event)">💬${count > 0 ? ' ' + count : ''}</button>
  </div>`;
}

function purRefreshCommentCells(code) {
  document.querySelectorAll('.pur-comment-cell').forEach(td => {
    if (td.dataset.code === code) td.innerHTML = purCommentCellInner(code);
  });
}

async function purLoadComments() {
  const res = await apiFetch('/api/comments?module=purchases&entity_type=product');
  if (!res || !res.ok) return;
  const comments = await res.json();
  purCommentsCache = {};
  comments.forEach(c => {
    if (!purCommentsCache[c.entity_id]) purCommentsCache[c.entity_id] = [];
    purCommentsCache[c.entity_id].push(c);
  });
  // Refresh all visible comment cells
  document.querySelectorAll('.pur-comment-cell[data-code]').forEach(td => {
    td.innerHTML = purCommentCellInner(td.dataset.code);
  });
}

async function purOpenCommentModal(code, e) {
  if (e) e.stopPropagation();
  const allItems = [...(purIlliquidNoneItems || []), ...(purIlliquidPartialItems || [])];
  const item = allItems.find(i => i.product_code === code);
  const name = item ? item.product_name : code;
  purCommentModalCode = code;
  purProductIlliqHistory = [];
  document.getElementById('pur-comment-modal-title').textContent = `${escHtml(name)}`;
  document.getElementById('pur-comment-textarea').value = '';
  // Rodo modalą iš karto su loading būsena
  document.getElementById('pur-comment-history').innerHTML =
    '<div class="pur-comment-empty" style="padding:16px 0">Kraunama istorija…</div>';
  document.getElementById('pur-comment-modal').classList.remove('hidden');
  // Lygiagrečiai krauname istorija
  const res = await apiFetch(`/api/purchases/warehouse/product-illiquid-history?product_code=${encodeURIComponent(code)}`);
  if (res && res.ok) purProductIlliqHistory = await res.json();
  purRenderCommentHistory();
  setTimeout(() => document.getElementById('pur-comment-textarea').focus(), 50);
}

function purCloseCommentModal() {
  document.getElementById('pur-comment-modal').classList.add('hidden');
  purCommentModalCode = null;
}

function purCommentModalOverlayClick(e) {
  if (e.target === document.getElementById('pur-comment-modal')) purCloseCommentModal();
}

function purFormatCommentTime(isoStr) {
  if (!isoStr) return '';
  const d = new Date(isoStr);
  return d.toLocaleString('lt', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  });
}

function purAvatarInitials(name) {
  const parts = (name || '?').trim().split(/\s+/);
  return parts.length >= 2
    ? (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
    : name.slice(0, 2).toUpperCase();
}

// Generuoja unikalią spalvą pagal vardą (pastovioms komentarų spalvoms)
function purAvatarColor(name) {
  const palette = [
    'linear-gradient(135deg,#3b82f6,#6366f1)',
    'linear-gradient(135deg,#10b981,#0d9488)',
    'linear-gradient(135deg,#f59e0b,#ef4444)',
    'linear-gradient(135deg,#8b5cf6,#ec4899)',
    'linear-gradient(135deg,#06b6d4,#3b82f6)',
    'linear-gradient(135deg,#f97316,#eab308)',
  ];
  let hash = 0;
  for (let i = 0; i < (name || '').length; i++) hash = (hash * 31 + name.charCodeAt(i)) & 0xffff;
  return palette[hash % palette.length];
}

function purRenderIlliqHistoryBlock() {
  const h = purProductIlliqHistory;
  if (!h.length) return '';

  // Pirmas ir paskutinis nelikvidus snapshot'as
  const illiqEntries = h.filter(e => e.illiquid_type);
  if (!illiqEntries.length) return '';

  const first = illiqEntries[0];
  const firstLabel = first.illiquid_type === 'none' ? '🔴 Visiškai nejuda' : '🟡 Dalinai nejuda';

  // Laiko juosta — sugrupuoti iš eilės einantys to paties tipo įrašai
  const rows = illiqEntries.map(e => {
    const icon  = e.illiquid_type === 'none' ? '🔴' : '🟡';
    const label = e.illiquid_type === 'none' ? 'Visiškai nejuda' : 'Dalinai nejuda';
    return `<tr>
      <td style="white-space:nowrap;color:var(--text-muted);font-size:12px;padding:3px 8px 3px 0">${e.snap_date}</td>
      <td style="white-space:nowrap;font-size:12px;padding:3px 8px 3px 0">${icon} ${label}</td>
      <td style="text-align:right;font-size:12px;color:var(--text-muted);padding:3px 0">
        ${e.quantity.toLocaleString('lt',{maximumFractionDigits:2})} vnt &nbsp;·&nbsp; ${fmt(e.total_value)} €
      </td>
    </tr>`;
  }).join('');

  return `<div class="pur-illiq-history-block">
    <div class="pur-illiq-history-title">
      📅 Sandėlio istorija
      <span class="pur-illiq-history-first">pirmą kartą: <strong>${first.snap_date}</strong> — ${firstLabel}</span>
    </div>
    <table style="width:100%;border-collapse:collapse">${rows}</table>
  </div>`;
}

function purRenderCommentHistory() {
  const el = document.getElementById('pur-comment-history');
  const comments = purCommentsCache[purCommentModalCode] || [];
  const historyBlock = purRenderIlliqHistoryBlock();

  if (!comments.length && !historyBlock) {
    el.innerHTML = '<div class="pur-comment-empty">💬 Komentarų nėra — parašykite pirmą!</div>';
    return;
  }

  const commentsHtml = comments.length ? comments.map(c => {
    const isOwn    = currentUser && c.user_id === currentUser.id;
    const isAdmin  = currentUser && currentUser.is_admin;
    const canEdit  = isOwn || isAdmin;
    const rawName  = c.full_name || c.username || 'Nežinomas';
    const author   = escHtml(rawName);
    const initials = purAvatarInitials(rawName);
    const avatarBg = purAvatarColor(rawName);
    const wasEdited = c.updated_at && c.updated_at !== c.created_at;
    const editedStr = wasEdited
      ? `<span class="pur-comment-edited"> · redaguota ${purFormatCommentTime(c.updated_at)}</span>` : '';
    return `<div class="pur-comment-item" id="pur-citem-${c.id}">
      <div class="pur-comment-avatar" style="background:${avatarBg}">${initials}</div>
      <div class="pur-comment-body">
        <div class="pur-comment-meta">
          <span class="pur-comment-author">${author}</span>
          <span class="pur-comment-time">${purFormatCommentTime(c.created_at)}</span>
          ${editedStr}
        </div>
        <div class="pur-comment-text" id="pur-ctext-${c.id}">${escHtml(c.text)}</div>
        ${canEdit ? `<div class="pur-comment-actions" id="pur-cactions-${c.id}">
          ${isOwn ? `<button class="pur-comment-action-btn" onclick="purStartEditComment(${c.id})">✏ Redaguoti</button>` : ''}
          <button class="pur-comment-action-btn del" onclick="purDeleteComment(${c.id})">🗑 Ištrinti</button>
        </div>` : ''}
      </div>
    </div>`;
  }).join('') : '<div class="pur-comment-empty" style="padding:16px 0">💬 Komentarų nėra — parašykite pirmą!</div>';

  el.innerHTML = historyBlock + commentsHtml;
  el.scrollTop = el.scrollHeight;
}

async function purSaveComment() {
  if (!purCommentModalCode) return;
  const textarea = document.getElementById('pur-comment-textarea');
  const text = textarea.value.trim();
  if (!text) return;
  textarea.disabled = true;
  const res = await apiFetch('/api/comments', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ module: 'purchases', entity_type: 'product', entity_id: purCommentModalCode, text }),
  });
  textarea.disabled = false;
  if (!res || !res.ok) { alert('Klaida išsaugant komentarą'); return; }
  const comment = await res.json();
  if (!purCommentsCache[purCommentModalCode]) purCommentsCache[purCommentModalCode] = [];
  purCommentsCache[purCommentModalCode].push(comment);
  textarea.value = '';
  textarea.style.height = '';
  purRenderCommentHistory();
  purRefreshCommentCells(purCommentModalCode);
}

async function purDeleteComment(id) {
  if (!confirm('Ištrinti šį komentarą?')) return;
  const res = await apiFetch(`/api/comments/${id}`, { method: 'DELETE' });
  if (!res || !res.ok) { alert('Klaida trinant komentarą'); return; }
  if (purCommentModalCode) {
    purCommentsCache[purCommentModalCode] = (purCommentsCache[purCommentModalCode] || []).filter(c => c.id !== id);
    purRenderCommentHistory();
    purRefreshCommentCells(purCommentModalCode);
  }
}

function purStartEditComment(id) {
  const comment = (purCommentsCache[purCommentModalCode] || []).find(c => c.id === id);
  if (!comment) return;
  const textEl    = document.getElementById(`pur-ctext-${id}`);
  const actionsEl = document.getElementById(`pur-cactions-${id}`);
  textEl.innerHTML = `<textarea class="pur-comment-edit-area" id="pur-cedit-${id}">${escHtml(comment.text)}</textarea>
    <div class="pur-comment-edit-actions">
      <button class="btn-primary btn-sm" style="font-size:12px;padding:4px 10px" onclick="purSaveEditComment(${id})">Išsaugoti</button>
      <button class="btn-secondary btn-sm" style="font-size:12px;padding:4px 10px" onclick="purCancelEditComment()">Atšaukti</button>
    </div>`;
  if (actionsEl) actionsEl.style.display = 'none';
  const ta = document.getElementById(`pur-cedit-${id}`);
  if (ta) { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); }
}

async function purSaveEditComment(id) {
  const ta = document.getElementById(`pur-cedit-${id}`);
  if (!ta) return;
  const text = ta.value.trim();
  if (!text) return;
  const res = await apiFetch(`/api/comments/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  });
  if (!res || !res.ok) { alert('Klaida redaguojant komentarą'); return; }
  const updated = await res.json();
  if (purCommentModalCode) {
    const cache = purCommentsCache[purCommentModalCode] || [];
    const idx = cache.findIndex(c => c.id === id);
    if (idx >= 0) cache[idx] = updated;
  }
  purRenderCommentHistory();
  purRefreshCommentCells(purCommentModalCode);
}

function purCancelEditComment() {
  purRenderCommentHistory();
}

// ── INIT ──────────────────────────────────────────────────────────────────────
checkAuth();

'use strict';
/* CARACAL Fleet Controller UI - no build step, vanilla JS. Translations live in i18n.js. */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const store = {
  get(k, d = null) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch { /* ignore */ } },
};

const S = {
  token: store.get('caracalToken', ''),
  me: null,
  lang: store.get('caracalLang', 'cs'),
  theme: store.get('caracalTheme', 'auto'),
  devices: [],
  agentVersion: '',
  attentionCount: 0,
  org: { groups: [], locations: [] },
  selected: new Set(),
  filters: { q: '', status: '', group: '', location: '' },
  detail: null,
  route: { view: 'overview', id: '', tab: '' },
  pendingOrder: null,
  lastOk: 0,
};

// ------------------------------------------------------------------ helpers

function t(key, vars) {
  // unknown dynamic keys such as kind_<custom> fall back to their suffix
  let s = (I18N[S.lang] && I18N[S.lang][key]) ?? I18N.en[key] ?? (key.includes('_') ? key.slice(key.indexOf('_') + 1) : key);
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.replaceAll(`{${k}}`, v);
  return s;
}
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const can = p => !!S.me && S.me.permissions.includes(p);
const dev = id => S.devices.find(d => d.id === id);
const num = v => (v === null || v === undefined || v === '' ? null : Number(v));

function errText(e) { const m = e && e.message ? e.message : String(e); return I18N[S.lang]['err_' + m] || I18N.en['err_' + m] || m; }

async function api(path, opts = {}) {
  const o = { ...opts, headers: { ...(opts.headers || {}) } };
  if (S.token) o.headers.Authorization = 'Bearer ' + S.token;
  if (o.json !== undefined) { o.body = JSON.stringify(o.json); o.headers['Content-Type'] = 'application/json'; delete o.json; }
  const r = await fetch(path, o);
  if (r.status === 401 && S.token) { logout(); throw new Error('session_expired'); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : r.statusText);
  return data;
}

function toast(msg, kind = 'ok') {
  const el = document.createElement('div');
  el.className = 'toast ' + kind;
  el.textContent = msg;
  $('#toasts').append(el);
  const ms = kind === 'ok' ? 3200 : 7000;   // errors and warnings stay long enough to be read
  el.onclick = () => el.remove();
  setTimeout(() => el.classList.add('out'), ms);
  setTimeout(() => el.remove(), ms + 500);
}

function ago(ts) {
  if (!ts) return '—';
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return t('agoSec', { n: Math.round(s) });
  if (s < 3600) return t('agoMin', { n: Math.round(s / 60) });
  if (s < 86400) return t('agoHour', { n: Math.round(s / 3600) });
  return t('agoDay', { n: Math.round(s / 86400) });
}
const dt = ts => (ts ? new Date(ts * 1000).toLocaleString(S.lang === 'cs' ? 'cs-CZ' : 'en-GB') : '—');
function dur(s) {
  s = num(s);
  if (s === null) return '—';
  s = Math.round(s);
  const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${s % 60}s`;
  return `${s}s`;
}

const ICONS = {
  overview: 'M3 13h8V3H3zm0 8h8v-6H3zm10 0h8V11h-8zm0-18v6h8V3z',
  devices: 'M4 5h16v11H4zM8 20h8M12 16v4',
  attention: 'M12 3 2 21h20zM12 10v5M12 18h.01',
  playlists: 'M4 6h12M4 12h12M4 18h8M18 15v6l4-3z',
  global: 'M3 7h18v13H3zM6 4h12M9 11h6M9 15h4',
  updates: 'M12 3v12M7 10l5 5 5-5M5 21h14',
  upload: 'M12 21V9M7 14l5-5 5 5M5 3h14',
  download: 'M12 3v12M7 10l5 5 5-5M5 21h14',
  organization: 'M12 3l9 5-9 5-9-5zM3 13l9 5 9-5M3 18l9 5 9-5',
  operations: 'M4 4h16v16H4zM8 9l3 3-3 3M13 15h4',
  audit: 'M9 3h6l4 4v14H5V3zM9 12h6M9 16h6M9 8h2',
  users: 'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8M22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8',
  settings: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2V21a2 2 0 1 1-4 0v-.1A1.7 1.7 0 0 0 7 19.4a1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.7 1.7 0 0 0 1.2 14H1a2 2 0 1 1 0-4h.1A1.7 1.7 0 0 0 4.6 7a1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A1.7 1.7 0 0 0 10 1.2V1a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 2.9 1.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0 1.2 2.9H23a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-3.5 3z',
  web: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18',
  image: 'M4 4h16v16H4zM4 16l5-5 4 4 3-3 4 4M15 9h.01',
  video: 'M3 6h13v12H3zM16 10l5-3v10l-5-3z',
  grafana: 'M4 20V10M10 20V4M16 20v-7M22 20H2',
  play: 'M7 4l13 8-13 8z',
  pause: 'M6 4h4v16H6zM14 4h4v16h-4z',
  next: 'M5 4l10 8-10 8zM19 5v14',
  restart: 'M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5',
  power: 'M12 2v10M18.4 6.6a9 9 0 1 1-12.8 0',
  edit: 'M4 20h4L19 9l-4-4L4 16zM14 6l4 4',
  copy: 'M9 9h11v11H9zM5 15H4V4h11v1',
  trash: 'M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3',
  eye: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
  snow: 'M12 2v20M4 6l16 12M20 6 4 18',
  drag: 'M9 6h.01M15 6h.01M9 12h.01M15 12h.01M9 18h.01M15 18h.01',
  up: 'M12 19V5M5 12l7-7 7 7',
  down: 'M12 5v14M19 12l-7 7-7-7',
  plus: 'M12 5v14M5 12h14',
  refresh: 'M21 12a9 9 0 1 1-3-6.7L21 8M21 3v5h-5',
  menu: 'M3 6h18M3 12h18M3 18h18',
  sun: 'M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4',
  moon: 'M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z',
  auto: 'M12 21a9 9 0 1 0 0-18v18z',
  back: 'M15 18l-6-6 6-6',
  agent: 'M12 3v12M7 10l5 5 5-5M5 21h14',
  ssh: 'M4 17l6-5-6-5M12 19h8',
  x: 'M18 6 6 18M6 6l12 12',
  location: 'M12 21s-7-6.2-7-11a7 7 0 1 1 14 0c0 4.8-7 11-7 11zM12 12a2 2 0 1 0 0-4 2 2 0 0 0 0 4z',
  group: 'M3 7h7l2 2h9v11H3z',
  lock: 'M5 11h14v10H5zM8 11V7a4 4 0 0 1 8 0v4M12 15v2',
};
const icon = (n, cls = '') => `<svg class="ic ${cls}" viewBox="0 0 24 24" aria-hidden="true"><path d="${ICONS[n] || ICONS.web}"/></svg>`;
const isTag = k => k === 'grafana-tag';
const kindLabel = k => t((k || '').includes('grafana') ? 'kind_grafana' : 'kind_' + k);
const kindIcon = k => icon(k === 'image' ? 'image' : k === 'video' ? 'video' : (k || '').includes('grafana') ? 'grafana' : 'web');

function bar(v, warn, crit, unit = '%') {
  v = num(v);
  if (v === null) return '<span class="muted">—</span>';
  const cls = v >= crit ? 'crit' : v >= warn ? 'warn' : '';
  const w = unit === '%' ? Math.min(100, v) : Math.min(100, v / 90 * 100);
  return `<div class="meter ${cls}"><span>${v}${unit === '%' ? ' %' : ' °C'}</span><i style="width:${w}%"></i></div>`;
}

function statusBadge(d) {
  if (!d.online) return `<span class="badge offline">${t('offline')}</span>`;
  if (d.needs_attention) return `<span class="badge warning">${t('attentionShort')}</span>`;
  return `<span class="badge online">${t('online')}</span>`;
}
const dot = d => `<span class="sdot ${!d.online ? 'offline' : d.needs_attention ? 'warning' : 'online'}"></span>`;

function attText(a) {
  const det = a.detail;
  const vars = { v: det, ago: ago(det) };
  return t('att_' + a.code, vars) + (a.code === 'local_api' || a.code === 'player' ? (det ? `: ${det}` : '') : '');
}

function progress(d) {
  if (!d.online) return '<span class="muted">—</span>';
  const name = d.current_name || (d.current_id != null ? '#' + d.current_id : '');
  if (!name) return `<span class="muted">${t('nothingPlaying')}</span>`;
  const total = num(d.duration), rem = num(d.remaining);
  const pct = total && rem !== null ? Math.max(0, Math.min(100, (1 - rem / total) * 100)) : 0;
  return `<div class="now"><div class="now-name">${d.frozen ? `<span class="tag frozen">${icon('snow')}${t('frozen')}</span>` : ''}<span title="${esc(name)}">${esc(name)}</span></div>
    ${d.frozen ? '' : `<div class="prog"><i style="width:${pct}%"></i></div>`}</div>`;
}

function patch(el, html) { if (el && el._html !== html) { el.innerHTML = html; el._html = html; } }

// ------------------------------------------------------------------ session

function applyStatic() {
  document.documentElement.lang = S.lang;
  $$('[data-i]').forEach(el => { el.textContent = t(el.dataset.i); });
  $$('[data-lang]').forEach(b => b.classList.toggle('active', b.dataset.lang === S.lang));
  $('#loginUser').placeholder = 'admin';
  const th = S.theme === 'auto' ? (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : S.theme;
  document.documentElement.dataset.theme = th;
  $('#themeToggle').innerHTML = icon(S.theme === 'auto' ? 'auto' : S.theme === 'dark' ? 'moon' : 'sun');
  $('#themeToggle').title = t('theme_' + S.theme);
  $('#menuBtn').innerHTML = icon('menu');
  $('#refreshBtn').innerHTML = icon('refresh');
  $('#refreshBtn').title = t('refresh');
  $('#addDeviceBtn').innerHTML = icon('plus') + `<span>${t('addDevice')}</span>`;
}

async function setLang(l) {
  S.lang = l;
  store.set('caracalLang', l);
  applyStatic();
  if (S.me) { api('/api/me', { method: 'PATCH', json: { language: l } }).catch(() => {}); buildNav(); render(true); }
}

async function doLogin(e) {
  e.preventDefault();
  $('#loginError').textContent = '';
  try {
    const r = await fetch('/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: $('#loginUser').value.trim(), password: $('#loginPass').value }) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || r.statusText);
    S.token = d.token;
    store.set('caracalToken', S.token);
    if (!store.get('caracalLang')) S.lang = d.user.language;
    $('#loginPass').value = '';
    await start();
  } catch (err) { $('#loginError').textContent = errText(err); }
}

let PUBLIC = {};

async function loadPublic() {
  PUBLIC = await fetch('/api/public').then(r => r.json()).catch(() => ({}));
  applyLogo();
  return PUBLIC;
}

function applyLogo() {
  $$('[data-logo]').forEach(el => {
    el.classList.toggle('has-logo', !!PUBLIC.logo);
    el.innerHTML = PUBLIC.logo ? `<img src="${esc(PUBLIC.logo)}" alt="CARACAL">` : 'C';
  });
  if (PUBLIC.logo) $('link[rel=icon]').href = PUBLIC.logo;
}

function logout() {
  S.token = '';
  S.me = null;
  store.set('caracalToken', null);
  $('#shell').hidden = true;
  $('#login').hidden = false;
  $('#loginForm').hidden = !!PUBLIC.setup_required;
  $('#setupForm').hidden = !PUBLIC.setup_required;
  applyStatic();
}

async function doSetup(e) {
  e.preventDefault();
  $('#setupError').textContent = '';
  try {
    const r = await fetch('/api/setup', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ setup_code: $('#setupCode').value.trim(), username: $('#setupUser').value.trim(), password: $('#setupPass').value, language: S.lang }) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || r.statusText);
    PUBLIC.setup_required = false;
    S.token = d.token;
    store.set('caracalToken', S.token);
    $('#setupPass').value = '';
    await start();
  } catch (err) { $('#setupError').textContent = errText(err); }
}

async function start() {
  try {
    S.me = await api('/api/me');
  } catch { logout(); return; }
  if (!store.get('caracalLang')) S.lang = S.me.language || 'cs';
  S.agentVersion = S.me.agent_version;
  $('#login').hidden = true;
  $('#shell').hidden = false;
  applyStatic();
  buildNav();
  $('#addDeviceBtn').hidden = !can('manage');
  $('#userBtn').textContent = S.me.username.slice(0, 2).toUpperCase();
  await refresh();
  route();
}

// ------------------------------------------------------------------ data

async function refresh() {
  try {
    const [d, org] = await Promise.all([api('/api/devices'), api('/api/org')]);
    S.devices = d.devices;
    S.attentionCount = d.attention_count;
    S.agentVersion = d.agent_version;
    S.org = org;
    for (const id of [...S.selected]) if (!dev(id)) S.selected.delete(id);
    if (S.route.view === 'device' || (S.route.view === 'playlists' && S.route.id)) {
      S.detail = await api('/api/devices/' + encodeURIComponent(S.route.id)).catch(() => null);
    }
    S.lastOk = Date.now();
  } catch (e) {
    if (e.message !== 'session_expired') console.warn(e);
  }
  const live = $('#liveDot');
  const fresh = Date.now() - S.lastOk < 20000;
  live.className = 'live-dot ' + (fresh ? 'ok' : 'bad');
  live.title = fresh ? t('liveOk') : t('liveBad');
  updateNavBadges();
}

// ------------------------------------------------------------------ navigation

const NAV = [
  ['overview', 'view'], ['devices', 'view'], ['attention', 'view'], ['playlists', 'view'], ['global', 'view'],
  ['organization', 'view'],
  ['operations', 'view'], ['updates', 'manage'], ['audit', 'manage'], ['users', 'admin'], ['settings', 'view'],
];

function buildNav() {
  $('#nav').innerHTML = NAV.filter(([, p]) => can(p)).map(([v]) =>
    `<a href="#/${v}" data-nav="${v}">${icon(v)}<span>${t('nav_' + v)}</span>${v === 'attention' ? '<em id="attBadge"></em>' : ''}</a>`).join('');
  updateNavBadges();
}

function updateNavBadges() {
  const b = $('#attBadge');
  if (b) { b.textContent = S.attentionCount || ''; b.hidden = !S.attentionCount; }
  const active = S.route.view === 'device' ? 'devices' : S.route.view;
  $$('#nav a').forEach(a => a.classList.toggle('active', a.dataset.nav === active));
}

function parseRoute() {
  const [path, query] = location.hash.replace(/^#\/?/, '').split('?');
  const parts = path.split('/').map(decodeURIComponent);
  const params = new URLSearchParams(query || '');
  return { view: parts[0] || 'overview', id: parts[1] || '', tab: parts[2] || '', params };
}

let lastRouteKey = '';
async function route() {
  const r = parseRoute();
  const key = `${r.view}/${r.id}`;
  const changed = key !== lastRouteKey;
  S.route = r;
  if (r.view === 'devices') {
    for (const k of ['group', 'location', 'status']) if (r.params.has(k)) S.filters[k] = r.params.get(k);
  }
  if (changed) {
    lastRouteKey = key;
    S.pendingOrder = null;
    S.detail = null;
    if (r.view === 'device' || (r.view === 'playlists' && r.id)) {
      S.detail = await api('/api/devices/' + encodeURIComponent(r.id)).catch(() => null);
    }
    $('#sidebar').classList.remove('open');
  }
  updateNavBadges();
  render(true);
}

// ------------------------------------------------------------------ rendering

const VIEWS = {};
let viewTimer = null;

function render(full = false) {
  const v = VIEWS[S.route.view] || VIEWS.overview;
  const root = $('#view');
  if (full || root.dataset.view !== S.route.view + S.route.id + S.route.tab) {
    root.dataset.view = S.route.view + S.route.id + S.route.tab;
    root.innerHTML = '';
    root._html = null;
    v.mount(root);
  }
  v.update && v.update(root);
}

function setTitle(title, crumb = 'CARACAL FLEET') {
  $('#title').textContent = title;
  $('#crumb').textContent = crumb;
  document.title = `${title} · CARACAL Fleet`;
}

// ---------- overview

VIEWS.overview = {
  mount(root) {
    setTitle(t('nav_overview'));
    root.innerHTML = `<div class="stats" id="ovStats"></div>
      <div class="grid two">
        <section class="card"><div class="card-head"><h2>${t('needsAttention')}</h2><a href="#/attention" class="link">${t('showAll')}</a></div><div id="ovAtt"></div></section>
        <section class="card"><div class="card-head"><h2>${t('locations')}</h2><a href="#/organization" class="link">${t('manage')}</a></div><div id="ovLoc"></div></section>
      </div>
      <section class="card"><div class="card-head"><h2>${t('nowPlayingAll')}</h2><span class="muted" id="ovCount"></span></div><div class="tiles" id="ovTiles"></div></section>`;
  },
  update(root) {
    const ds = S.devices;
    const online = ds.filter(d => d.online).length;
    const outdated = ds.filter(d => d.attention.some(a => a.code === 'agent_outdated')).length;
    const stat = (label, val, cls, href, sub = '') => `<a class="stat ${cls}" ${href ? `href="${href}"` : ''}><span>${label}</span><b>${val}</b><small>${sub}</small></a>`;
    patch($('#ovStats', root), [
      stat(t('totalDevices'), ds.length, '', '#/devices?status='),
      stat(t('online'), online, 'ok', '#/devices?status=online', ds.length ? Math.round(online / ds.length * 100) + ' %' : ''),
      stat(t('offline'), ds.length - online, ds.length - online ? 'bad' : '', '#/devices?status=offline'),
      stat(t('needsAttention'), S.attentionCount, S.attentionCount ? 'warn' : '', '#/attention', t('clickForDetail')),
      stat(t('frozen'), ds.filter(d => d.online && d.frozen).length, '', '#/devices?status=frozen'),
      stat(t('outdatedAgents'), outdated, outdated ? 'info' : '', '#/devices?status=outdated', 'v' + S.agentVersion),
    ].join(''));
    const att = ds.filter(d => d.needs_attention);
    patch($('#ovAtt', root), att.length ? att.slice(0, 8).map(d => `<a class="att-row" href="#/device/${encodeURIComponent(d.id)}">
        ${dot(d)}<div><b>${esc(d.name)}</b><small class="muted">${esc(d.location || '')}</small></div>
        <div class="reasons">${d.attention.filter(a => a.level !== 'info').map(a => `<span class="reason ${a.level}">${esc(attText(a))}</span>`).join('')}</div></a>`).join('')
      : `<div class="empty ok">${t('allGood')}</div>`);
    const locs = {};
    for (const d of ds) { const k = d.location || ''; (locs[k] ||= { total: 0, online: 0, att: 0 }); locs[k].total++; if (d.online) locs[k].online++; if (d.needs_attention) locs[k].att++; }
    patch($('#ovLoc', root), Object.keys(locs).length ? Object.entries(locs).sort().map(([k, v]) => `<a class="loc-row" href="#/devices?location=${encodeURIComponent(k)}">
        ${icon('location')}<b>${esc(k || t('unassigned'))}</b><span class="muted">${v.online}/${v.total} ${t('online').toLowerCase()}</span>
        ${v.att ? `<span class="reason warning">${v.att} ${t('attentionShort').toLowerCase()}</span>` : ''}
        <div class="mini-prog"><i style="width:${v.total ? v.online / v.total * 100 : 0}%"></i></div></a>`).join('')
      : `<div class="empty">${t('noDevices')}</div>`);
    $('#ovCount', root).textContent = `${ds.length}`;
    patch($('#ovTiles', root), ds.map(d => `<a class="tile ${d.online ? '' : 'off'}" href="#/device/${encodeURIComponent(d.id)}">
        <div class="tile-head">${dot(d)}<b>${esc(d.name)}</b>${d.current_kind ? kindIcon(d.current_kind) : ''}</div>
        ${progress(d)}<small class="muted">${esc([d.location, d.group].filter(Boolean).join(' · ') || d.id)}</small></a>`).join('')
      || `<div class="empty">${t('noDevicesHint')}</div>`);
  },
};

// ---------- devices

function filteredDevices() {
  const f = S.filters, q = f.q.toLowerCase();
  return S.devices.filter(d => {
    if (q && ![d.name, d.id, d.ip, d.location, d.group, d.current_name, d.hostname].join(' ').toLowerCase().includes(q)) return false;
    if (f.group && d.group !== f.group) return false;
    if (f.location && d.location !== f.location) return false;
    switch (f.status) {
      case 'online': return d.online;
      case 'offline': return !d.online;
      case 'attention': return d.needs_attention;
      case 'frozen': return d.online && d.frozen;
      case 'outdated': return d.attention.some(a => a.code === 'agent_outdated');
      default: return true;
    }
  });
}

const options = (list, sel, empty) => `<option value="">${esc(empty)}</option>` + list.map(x => `<option ${x === sel ? 'selected' : ''}>${esc(x)}</option>`).join('');
const orgNames = kind => S.org[kind].map(x => x.name);

VIEWS.devices = {
  mount(root) {
    setTitle(t('nav_devices'));
    const f = S.filters;
    root.innerHTML = `<section class="card flush">
      <div class="toolbar">
        <input id="fQ" type="search" placeholder="${t('searchDevices')}" value="${esc(f.q)}">
        <select id="fStatus">${['', 'online', 'offline', 'attention', 'frozen', 'outdated'].map(s => `<option value="${s}" ${s === f.status ? 'selected' : ''}>${t('status_' + (s || 'all'))}</option>`).join('')}</select>
        <select id="fGroup">${options(orgNames('groups'), f.group, t('allGroups'))}</select>
        <select id="fLoc">${options(orgNames('locations'), f.location, t('allLocations'))}</select>
        <span class="muted grow" id="fCount"></span>
      </div>
      <div class="bulkbar" id="bulkBar" hidden></div>
      <div class="table-wrap"><table class="table devices-table">
        <thead><tr><th class="cb"><input type="checkbox" id="selAll"></th><th>${t('device')}</th><th>${t('locationGroup')}</th><th>${t('currentContent')}</th>
        <th>CPU</th><th>RAM</th><th>${t('disk')}</th><th>${t('temperature')}</th><th>${t('agent')}</th><th>${t('lastSeen')}</th></tr></thead>
        <tbody id="devRows"></tbody></table></div></section>`;
    const upd = () => this.update(root);
    $('#fQ', root).oninput = e => { S.filters.q = e.target.value; upd(); };
    $('#fStatus', root).onchange = e => { S.filters.status = e.target.value; upd(); };
    $('#fGroup', root).onchange = e => { S.filters.group = e.target.value; upd(); };
    $('#fLoc', root).onchange = e => { S.filters.location = e.target.value; upd(); };
    $('#selAll', root).onchange = e => { filteredDevices().forEach(d => e.target.checked ? S.selected.add(d.id) : S.selected.delete(d.id)); upd(); };
  },
  update(root) {
    const list = filteredDevices();
    $('#fCount', root).textContent = t('nOfM', { n: list.length, m: S.devices.length });
    patch($('#devRows', root), list.map(d => `<tr data-href="#/device/${encodeURIComponent(d.id)}" class="${S.selected.has(d.id) ? 'sel' : ''}">
      <td class="cb"><input type="checkbox" data-pick="${esc(d.id)}" ${S.selected.has(d.id) ? 'checked' : ''}></td>
      <td><div class="dev-name">${dot(d)}<div><b>${esc(d.name)}</b><small>${esc(d.id)} · ${esc(d.ip || '—')}</small></div></div></td>
      <td><small class="stack">${d.location ? `<span>${icon('location')}${esc(d.location)}</span>` : ''}${d.group ? `<span>${icon('group')}${esc(d.group)}</span>` : ''}${!d.location && !d.group ? '—' : ''}</small></td>
      <td class="content-cell">${progress(d)}${d.needs_attention ? `<small class="reason-inline">${esc(d.attention.filter(a => a.level !== 'info').map(attText).join(', '))}</small>` : ''}</td>
      <td>${d.online ? bar(d.cpu, 80, 95) : '—'}</td><td>${d.online ? bar(d.ram, 80, 90) : '—'}</td><td>${bar(d.disk, 85, 95)}</td>
      <td>${d.online ? bar(d.temp, 70, 80, 'C') : '—'}</td>
      <td><span class="${d.attention.some(a => a.code === 'agent_outdated') ? 'tag info' : 'muted'}">${esc(d.version || '—')}</span></td>
      <td class="muted nowrap">${d.online ? t('now') : ago(d.last_seen)}${d.pending_commands ? `<br><span class="tag">${t('pendingN', { n: d.pending_commands })}</span>` : ''}</td></tr>`).join('')
      || `<tr><td colspan="10" class="empty">${S.devices.length ? t('noMatch') : t('noDevicesHint')}</td></tr>`);
    const sa = $('#selAll', root);
    sa.checked = list.length > 0 && list.every(d => S.selected.has(d.id));
    sa.indeterminate = !sa.checked && list.some(d => S.selected.has(d.id));
    renderBulkBar($('#bulkBar', root));
  },
};

function renderBulkBar(el) {
  const n = S.selected.size;
  el.hidden = !n;
  if (!n) { patch(el, ''); return; }
  const b = (act, label, ic, cls = '') => `<button class="btn sm ${cls}" data-do="bulk" data-act="${act}">${icon(ic)}${label}</button>`;
  patch(el, `<b>${t('selectedN', { n })}</b>
    ${can('control') ? b('next', t('act_next'), 'next') + b('unfreeze', t('act_unfreeze'), 'play') + b('restart_player', t('act_restart_player'), 'restart') + b('reboot', t('act_reboot'), 'power', 'danger') : ''}
    ${can('content') ? `<button class="btn sm" data-do="bulkAddContent">${icon('plus')}${t('addContent')}</button><button class="btn sm" data-do="bulkCopy">${icon('copy')}${t('copyPlaylistHere')}</button>` : ''}
    ${can('manage') ? b('update_agent', t('act_update_agent'), 'agent') + `<button class="btn sm" data-do="bulkAssign">${icon('group')}${t('assignGroupLocation')}</button>
      <button class="btn sm" data-do="bulkSetHub">${icon('upload')}${t('act_set_hub')}</button>
      <button class="btn sm" data-do="caracalUpdate">${icon('updates')}${t('act_update_caracal')}</button>` : ''}
    <button class="btn sm ghost" data-do="clearSel">${icon('x')}${t('clearSelection')}</button>`);
}

// ---------- attention

VIEWS.attention = {
  mount(root) {
    setTitle(t('nav_attention'));
    root.innerHTML = `<section class="card flush"><div class="table-wrap"><table class="table">
      <thead><tr><th>${t('severity')}</th><th>${t('device')}</th><th>${t('problem')}</th><th>${t('locationGroup')}</th><th>${t('lastSeen')}</th><th></th></tr></thead>
      <tbody id="attRows"></tbody></table></div></section>`;
  },
  update(root) {
    const rows = [];
    const order = { critical: 0, warning: 1, info: 2 };
    for (const d of S.devices) for (const a of d.attention) rows.push({ d, a });
    rows.sort((x, y) => order[x.a.level] - order[y.a.level] || x.d.name.localeCompare(y.d.name));
    patch($('#attRows', root), rows.map(({ d, a }) => `<tr data-href="#/device/${encodeURIComponent(d.id)}">
      <td><span class="badge ${a.level}">${t('level_' + a.level)}</span></td>
      <td><div class="dev-name">${dot(d)}<div><b>${esc(d.name)}</b><small>${esc(d.id)} · ${esc(d.ip)}</small></div></div></td>
      <td><b>${esc(attText(a))}</b><br><small class="muted">${esc(t('hint_' + a.code))}</small></td>
      <td><small>${esc([d.location, d.group].filter(Boolean).join(' · ') || '—')}</small></td>
      <td class="muted nowrap">${d.online ? t('now') : ago(d.last_seen)}</td>
      <td class="actions-cell">${quickFix(d, a)}</td></tr>`).join('') || `<tr><td colspan="6" class="empty ok">${t('allGood')}</td></tr>`);
  },
};

function quickFix(d, a, inDetail = false) {
  const btn = (act, label) => `<button class="btn sm" data-do="cmd" data-id="${esc(d.id)}" data-act="${act}">${label}</button>`;
  if (a.code === 'player' && can('control')) return btn('restart_player', t('act_restart_player'));
  if (a.code === 'agent_outdated' && can('manage') && d.online) return btn('update_agent', t('act_update_agent'));
  if (a.code === 'caracal_outdated' && can('manage') && d.online) return `<button class="btn sm" data-do="caracalUpdate" data-id="${esc(d.id)}">${t('act_update_caracal')}</button>`;
  if (a.code === 'local_api' && can('control')) return btn('reboot', t('act_reboot'));
  return inDetail ? '' : `<a class="btn sm" href="#/device/${encodeURIComponent(d.id)}">${t('open')}</a>`;
}

// ---------- device detail

const DEVICE_TABS = ['overview', 'playlist', 'collections', 'logins', 'history', 'settings'];
const TAB_COUNTS = { playlist: 'playlist_count', collections: 'collection_count', logins: 'profile_count' };

function deviceTabs(d, tab) {
  return DEVICE_TABS.filter(x => x !== 'settings' || can('manage')).map(x => `<a href="#/device/${encodeURIComponent(d.id)}/${x}" class="${x === tab ? 'active' : ''}">${t('tab_' + x)}${TAB_COUNTS[x] && d[TAB_COUNTS[x]] ? ` <span class="count">${d[TAB_COUNTS[x]]}</span>` : ''}</a>`).join('');
}

VIEWS.device = {
  mount(root) {
    const d = S.detail;
    if (!d) { setTitle(t('device')); root.innerHTML = `<div class="card empty">${t('err_device_not_found')}</div>`; return; }
    setTitle(d.name, t('nav_devices').toUpperCase());
    root.innerHTML = `<a class="back" href="#/devices">${icon('back')}${t('nav_devices')}</a>
      <section class="card dev-hero" id="devHero"></section>
      <div id="devAtt"></div>
      <nav class="tabs" id="devTabs"></nav>
      <div id="devTab"></div>`;
  },
  update(root) {
    const d = S.detail;
    if (!d) return;
    const tab = DEVICE_TABS.includes(S.route.tab) ? S.route.tab : 'overview';
    patch($('#devHero', root), `<div class="hero-main"><div class="hero-title">${statusBadge(d)}<h2>${esc(d.name)}</h2></div>
      <div class="chips"><span>${esc(d.id)}</span><span>${esc(d.ip || '—')}</span>${d.location ? `<span>${icon('location')}${esc(d.location)}</span>` : ''}${d.group ? `<span>${icon('group')}${esc(d.group)}</span>` : ''}
      <span>CARACAL ${esc(d.caracal_version || '?')} · ${runtimeLabel(d)}</span><span>${t('agent')} ${esc(d.version || '—')}</span>${d.model ? `<span>${esc(d.model)}</span>` : ''}<span>${d.online ? t('uptime') + ' ' + dur(d.uptime) : t('lastSeen') + ' ' + ago(d.last_seen)}</span></div></div>
      <div class="hero-actions">${controlButtons(d)}</div>`);
    patch($('#devTabs', root), deviceTabs(d, tab));
    patch($('#devAtt', root), d.attention.length ? `<div class="alert-list">${d.attention.map(a => `<div class="alert ${a.level}"><b>${esc(attText(a))}</b><span>${esc(t('hint_' + a.code))}</span>${quickFix(d, a, true)}</div>`).join('')}</div>` : '');
    const el = $('#devTab', root);
    if (tab === 'overview') patch(el, deviceOverview(d));
    else if (tab === 'playlist') renderPlaylist(el, d);
    else if (tab === 'collections') patch(el, renderCollections(d));
    else if (tab === 'logins') patch(el, renderProfiles(d));
    else if (tab === 'history') patch(el, deviceHistory(d));
    else if (tab === 'settings') { if (!el._html) patch(el, deviceSettings(d)); }
  },
};

function controlButtons(d) {
  if (!can('control')) return '';
  const b = (act, ic, label, cls = '') => `<button class="btn ${cls}" data-do="cmd" data-id="${esc(d.id)}" data-act="${act}" ${d.online ? '' : 'disabled'}>${icon(ic)}<span>${label}</span></button>`;
  return b('unfreeze', 'play', t('act_unfreeze')) + b('next', 'next', t('act_next')) + b('restart_player', 'restart', t('act_restart_player'))
    + b('reboot', 'power', t('act_reboot'), 'danger')
    + (can('manage') ? `<button class="btn" data-do="cmd" data-id="${esc(d.id)}" data-act="update_agent" ${d.online ? '' : 'disabled'}>${icon('agent')}<span>${t('act_update_agent')}</span></button>
       ${d.runtime === 'host' ? `<button class="btn" data-do="convertNodes" data-id="${esc(d.id)}" ${d.online ? '' : 'disabled'}>${icon('upload')}<span>${t('convertToDocker')}</span></button>` : ''}
       <button class="btn" data-do="caracalUpdate" data-id="${esc(d.id)}" ${d.online ? '' : 'disabled'}>${icon('updates')}<span>${t('act_update_caracal')}</span></button>
       <button class="btn" data-do="provision" data-host="${esc(d.ip)}" data-name="${esc(d.name)}">${icon('ssh')}<span>${t('sshInstall')}</span></button>` : '');
}

function deviceOverview(d) {
  const metric = (label, html) => `<div class="metric"><span>${label}</span>${html}</div>`;
  const total = num(d.duration), rem = num(d.remaining);
  return `<div class="grid metrics4">
      ${metric('CPU', bar(d.online ? d.cpu : null, 80, 95))}${metric('RAM', bar(d.online ? d.ram : null, 80, 90))}
      ${metric(t('disk'), bar(d.disk, 85, 95))}${metric(t('temperature'), bar(d.online ? d.temp : null, 70, 80, 'C'))}</div>
    <div class="grid two">
      <section class="card"><div class="card-head"><h2>${t('nowPlaying')}</h2>${d.frozen ? `<span class="tag frozen">${icon('snow')}${t('frozen')}</span>` : ''}</div>
        <div class="now-big">${d.current_kind ? kindIcon(d.current_kind) : ''}<b>${esc(d.current_name || t('nothingPlaying'))}</b></div>
        ${d.frozen ? `<p class="muted">${d.frozen_until ? t('frozenUntil', { time: dt(d.frozen_until) }) : t('frozenIndef')}</p>`
          : total ? `<div class="prog big"><i style="width:${rem !== null ? Math.max(0, Math.min(100, (1 - rem / total) * 100)) : 0}%"></i></div><p class="muted">${t('remaining')}: ${dur(rem)} / ${dur(total)}</p>` : ''}
        <p class="muted">${t('playlistItems')}: ${d.playlist_count} · ${t('collections')}: ${d.collection_count}</p></section>
      <section class="card"><div class="card-head"><h2>${t('info')}</h2></div><dl class="kv">
        <dt>${t('hostname')}</dt><dd>${esc(d.hostname || '—')}</dd><dt>${t('model')}</dt><dd>${esc(d.model || '—')}</dd>
        <dt>IP</dt><dd>${esc(d.ip || '—')}</dd><dt>${t('uptime')}</dt><dd>${d.online ? dur(d.uptime) : '—'}</dd>
        <dt>${t('lastSeen')}</dt><dd>${dt(d.last_seen)}</dd><dt>${t('agent')}</dt><dd>${esc(d.version || '—')} (${t('latest')} ${esc(S.agentVersion)})</dd>
        <dt>${t('localApi')}</dt><dd>${d.api_ok === false ? `<span class="tag critical">${t('unavailable')}</span>` : d.api_ok ? `<span class="tag ok">OK</span>` : '—'}</dd>
        <dt>${t('pendingCommands')}</dt><dd>${d.pending_commands}</dd>${d.notes ? `<dt>${t('notes')}</dt><dd>${esc(d.notes)}</dd>` : ''}</dl></section>
    </div>`;
}

const RESULTS = {};

function commandsTable(rows, showDevice) {
  for (const c of rows) RESULTS[c.id] = c;
  return `<div class="table-wrap"><table class="table">
    <thead><tr><th>${t('time')}</th>${showDevice ? `<th>${t('device')}</th>` : ''}<th>${t('action')}</th><th>${t('detail')}</th><th>${t('user')}</th><th>${t('state')}</th><th>${t('result')}</th><th></th></tr></thead>
    <tbody>${rows.map(c => `<tr><td class="nowrap">${dt(c.created)}</td>${showDevice ? `<td><a href="#/device/${encodeURIComponent(c.device_id)}">${esc(c.device_name || c.device_id)}</a></td>` : ''}
      <td><b>${esc(t('act_' + c.action))}</b></td><td><small class="muted">${esc(payloadSummary(c.payload_json))}</small></td><td>${esc(c.username || '—')}</td>
      <td><span class="badge st-${c.state}">${t('state_' + c.state)}</span></td><td>${c.result ? `<button type="button" class="result result-btn" data-do="showResult" data-cid="${c.id}" title="${t('showDetail')}">${esc(c.result.slice(0, 140))}</button>` : ''}</td>
      <td>${c.state === 'queued' && can('control') ? `<button class="btn sm ghost" data-do="cancelCmd" data-cid="${c.id}">${t('cancel')}</button>` : ''}</td></tr>`).join('')
      || `<tr><td colspan="8" class="empty">${t('noCommands')}</td></tr>`}</tbody></table></div>`;
}

function payloadSummary(json) {
  let p;
  try { p = JSON.parse(json || '{}'); } catch { return ''; }
  const parts = [];
  if (p.name) parts.push(p.name);
  if (p.item_id != null) parts.push('#' + p.item_id);
  if (p.collection_id != null) parts.push(t('collection') + ' #' + p.collection_id);
  if (p.id != null) parts.push('#' + p.id);
  if (p.minutes) parts.push(p.minutes + ' min');
  if (p.source) parts.push(p.source);
  if (p.items) parts.push(t('nItems', { n: p.items.length }) + (p.mode ? ` (${t('mode_' + p.mode)})` : ''));
  if (p.asset_ids) parts.push(t('nItems', { n: p.asset_ids.length }));
  if (p.order) parts.push(t('nItems', { n: p.order.length }));
  if (p.global_playlist) parts.unshift(t('globalPlaylist') + ': ' + p.global_playlist);
  if (p.hub) parts.push(p.hub);
  if (p.version && p.release_id) parts.push('CARACAL ' + p.version);
  return parts.join(' · ');
}

const deviceHistory = d => `<section class="card flush">${commandsTable(d.commands, false)}</section>`;

function deviceSettings(d) {
  return `<section class="card narrow"><form id="devForm" class="form" data-id="${esc(d.id)}">
      <label>${t('name')}<input name="name" value="${esc(d.name)}" required></label>
      <div class="row2"><label>${t('group')}<input name="group" value="${esc(d.group)}" list="dlGroups"></label>
      <label>${t('location')}<input name="location" value="${esc(d.location)}" list="dlLocations"></label></div>
      <label>${t('notes')}<textarea name="notes" rows="3">${esc(d.notes)}</textarea></label>
      ${orgDatalists()}
      <div class="form-actions"><button class="btn primary">${t('save')}</button></div></form></section>
    <section class="card narrow danger-zone"><h2>${t('dangerZone')}</h2><p class="muted">${t('deleteDeviceHint')}</p>
      <button class="btn danger" data-do="deleteDevice" data-id="${esc(d.id)}">${icon('trash')}${t('deleteDevice')}</button></section>`;
}

const orgDatalists = () => `<datalist id="dlGroups">${orgNames('groups').map(x => `<option value="${esc(x)}">`).join('')}</datalist>
  <datalist id="dlLocations">${orgNames('locations').map(x => `<option value="${esc(x)}">`).join('')}</datalist>`;

// ---------- playlist editor

function orderedAssets(d) {
  if (!S.pendingOrder) return d.assets;
  const by = new Map(d.assets.map(a => [String(a.id), a]));
  const out = S.pendingOrder.map(id => by.get(id)).filter(Boolean);
  for (const a of d.assets) if (!S.pendingOrder.includes(String(a.id))) out.push(a);
  return out;
}

function renderPlaylist(el, d) {
  const edit = can('content'), ctl = can('control') && d.online;
  const assets = orderedAssets(d);
  const html = `<section class="card flush">
    <div class="toolbar">
      <h2 class="grow">${t('playlist')} <span class="muted">(${assets.length})</span></h2>
      ${edit ? `<button class="btn" data-do="addAsset" data-kind="web" data-id="${esc(d.id)}">${icon('web')}${t('addWeb')}</button>
      ${d.capabilities.upload === false ? '' : `<button class="btn" data-do="addAsset" data-kind="image" data-id="${esc(d.id)}">${icon('image')}${t('addImage')}</button>
      <button class="btn" data-do="addAsset" data-kind="video" data-id="${esc(d.id)}">${icon('video')}${t('addVideo')}</button>`}
      <button class="btn" data-do="copyContent" data-id="${esc(d.id)}">${icon('copy')}${t('copyPlaylist')}</button>` : ''}
    </div>
    ${S.pendingOrder ? `<div class="savebar">${t('orderChanged')}<button class="btn primary sm" data-do="saveOrder" data-id="${esc(d.id)}">${t('saveOrder')}</button><button class="btn sm ghost" data-do="resetOrder">${t('cancel')}</button></div>` : ''}
    ${!d.online ? `<div class="note">${t('offlineQueued')}</div>` : ''}
    ${d.pending_commands ? `<div class="note info">${t('pendingNote', { n: d.pending_commands })}</div>` : ''}
    ${d.capabilities.upload === false && edit ? `<div class="note">${t('noMediaApi')}</div>` : ''}
    <ol class="playlist" id="plist">${assets.map((a, i) => {
      const playing = d.online && String(a.id) === String(d.current_asset_id ?? d.current_id);
      return `<li class="pl-row ${playing ? 'playing' : ''} ${a.enabled ? '' : 'disabled'}" data-aid="${esc(a.id)}" ${edit ? 'draggable="true"' : ''}>
        ${edit ? `<span class="handle" title="${t('dragToReorder')}">${icon('drag')}</span>` : ''}<span class="idx">${i + 1}</span>
        <span class="kind k-${esc(a.kind)}">${kindIcon(a.kind)}</span>
        <div class="pl-main"><b>${esc(a.name)}</b><small>${esc(kindLabel(a.kind))}${isTag(a.kind) ? ` · ${esc(t('grafanaTag'))} ${esc(a.tag)}` : a.source && a.kind === 'web' ? ' · ' + esc(a.source) : ''}</small></div>
        <span class="pl-dur">${a.duration ? dur(a.duration) : (a.kind === 'video' ? t('fullLength') : '—')}</span>
        <span class="pl-tags">${playing ? `<span class="tag ok">${d.frozen ? icon('snow') + t('frozen') : icon('play') + t('playing')}</span>` : ''}${a.enabled ? '' : `<span class="tag">${t('disabled')}</span>`}${loginTag(d, a)}</span>
        <span class="pl-actions">
          ${ctl && a.enabled ? (isTag(a.kind)
            ? `<button class="icon-btn" title="${t('showNow')}" data-do="cmd" data-id="${esc(d.id)}" data-act="show_collection" data-col="${esc(a.id)}">${icon('eye')}</button>
          <button class="icon-btn" title="${t('freezeItem')}" data-do="freeze" data-id="${esc(d.id)}" data-col="${esc(a.id)}" data-name="${esc(a.name)}">${icon('snow')}</button>`
            : `<button class="icon-btn" title="${t('showNow')}" data-do="cmd" data-id="${esc(d.id)}" data-act="show" data-item="${esc(a.id)}">${icon('eye')}</button>
          <button class="icon-btn" title="${t('freezeItem')}" data-do="freeze" data-id="${esc(d.id)}" data-item="${esc(a.id)}" data-name="${esc(a.name)}">${icon('snow')}</button>`) : ''}
          ${edit ? `<button class="icon-btn" title="${t('moveUp')}" data-do="move" data-dir="-1" data-item="${esc(a.id)}" ${i ? '' : 'disabled'}>${icon('up')}</button>
          <button class="icon-btn" title="${t('moveDown')}" data-do="move" data-dir="1" data-item="${esc(a.id)}" ${i < assets.length - 1 ? '' : 'disabled'}>${icon('down')}</button>
          <button class="icon-btn" title="${t('edit')}" data-do="editAsset" data-id="${esc(d.id)}" data-item="${esc(a.id)}">${icon('edit')}</button>
          <button class="icon-btn" title="${t('copyTo')}" data-do="copyContent" data-id="${esc(d.id)}" data-item="${esc(a.id)}">${icon('copy')}</button>
          <button class="icon-btn danger" title="${t('delete')}" data-do="deleteAsset" data-id="${esc(d.id)}" data-item="${esc(a.id)}" data-name="${esc(a.name)}">${icon('trash')}</button>` : ''}
        </span></li>`;
    }).join('') || `<li class="empty">${d.api_ok === false ? t('playlistUnavailable') : t('playlistEmpty')}</li>`}</ol></section>`;
  if (el._dragging) return;
  patch(el, html);
  if (edit) bindDrag($('#plist', el));
}

function bindDrag(list) {
  if (!list) return;
  let dragged = null;
  list.ondragstart = e => { dragged = e.target.closest('.pl-row'); if (!dragged) return; dragged.classList.add('dragging'); list.parentElement.parentElement._dragging = true; e.dataTransfer.effectAllowed = 'move'; };
  list.ondragover = e => {
    e.preventDefault();
    const over = e.target.closest('.pl-row');
    if (!dragged || !over || over === dragged) return;
    const r = over.getBoundingClientRect();
    over.parentNode.insertBefore(dragged, e.clientY > r.top + r.height / 2 ? over.nextSibling : over);
  };
  list.ondragend = () => {
    if (!dragged) return;
    dragged.classList.remove('dragging');
    list.parentElement.parentElement._dragging = false;
    dragged = null;
    setOrder($$('.pl-row', list).map(x => x.dataset.aid));
  };
}

function setOrder(order) {
  const current = S.detail.assets.map(a => String(a.id));
  S.pendingOrder = order.join(',') === current.join(',') ? null : order;
  render();
}

// ---------- collections

function renderCollections(d) {
  const edit = can('content'), ctl = can('control') && d.online;
  const canAdd = edit && d.capabilities.add_grafana_tag !== false;
  return `<section class="card flush"><div class="toolbar"><h2 class="grow">${t('grafanaCollections')} <span class="muted">(${d.collections.length})</span></h2>
    ${canAdd ? `<button class="btn" data-do="addCollection" data-id="${esc(d.id)}">${icon('plus')}${t('addCollection')}</button>
    <button class="btn" data-do="copyCollections" data-id="${esc(d.id)}">${icon('copy')}${t('copyCollections')}</button>` : ''}</div>
    ${edit && !canAdd ? `<div class="note">${t('tagCollectionsNote')}</div>` : ''}
    <div class="col-grid">${d.collections.map(c => `<div class="col-card">
      <div class="col-head">${icon('grafana')}<b>${esc(c.name)}</b><span class="muted">${t('grafanaTag')}: ${esc(c.tag || '—')}${c.duration ? ' · ' + dur(c.duration) : ''}</span></div>
      <ul><li title="${esc(c.grafana_url)}">${esc(c.grafana_url || '—')}</li></ul>
      <div class="col-actions">
        ${ctl ? `<button class="btn sm" data-do="cmd" data-id="${esc(d.id)}" data-act="show_collection" data-col="${esc(c.id)}">${icon('eye')}${t('showNow')}</button>
        <button class="btn sm" data-do="freeze" data-id="${esc(d.id)}" data-col="${esc(c.id)}" data-name="${esc(c.name)}">${icon('snow')}${t('freezeItem')}</button>` : ''}
        ${edit ? `<button class="icon-btn" title="${t('edit')}" data-do="editCollection" data-id="${esc(d.id)}" data-col="${esc(c.id)}">${icon('edit')}</button>
        <button class="icon-btn" title="${t('copyTo')}" data-do="copyContent" data-id="${esc(d.id)}" data-col="${esc(c.id)}">${icon('copy')}</button>
        <button class="icon-btn danger" title="${t('delete')}" data-do="deleteCollection" data-id="${esc(d.id)}" data-col="${esc(c.id)}" data-name="${esc(c.name)}">${icon('trash')}</button>` : ''}
      </div></div>`).join('') || `<div class="empty">${t('noCollections')}</div>`}</div></section>`;
}

// ---------- login profiles of web pages

// 'ok': the node and its agent manage logins; 'agent': the agent is too old to report them; 'node': CARACAL is too old
function loginSupport(d) {
  const cap = (d.capabilities || {}).add_profile;
  return cap === true ? 'ok' : cap === false ? 'node' : 'agent';
}
const profileName = (d, id) => ((d.profiles || []).find(p => String(p.id) === String(id))?.name) || '#' + id;
const loginTag = (d, a) => (a.kind === 'web' && a.auth_profile_id ? `<span class="tag login-tag" title="${esc(t('login'))}">${icon('lock')}${esc(profileName(d, a.auth_profile_id))}</span>` : '');

function renderProfiles(d) {
  const support = loginSupport(d), edit = can('content') && support === 'ok';
  const profiles = d.profiles || [];
  return `<section class="card flush"><div class="toolbar"><h2 class="grow">${t('loginProfiles')} <span class="muted">(${profiles.length})</span></h2>
    ${edit ? `<button class="btn primary" data-do="addProfile" data-id="${esc(d.id)}">${icon('plus')}${t('addLogin')}</button>` : ''}</div>
    ${support !== 'ok' && can('content') ? `<div class="note">${t(support === 'node' ? 'loginsNeedCaracal' : 'loginsNeedAgent')}</div>` : ''}
    ${!d.online && edit ? `<div class="note">${t('offlineQueued')}</div>` : ''}
    <div class="col-grid">${profiles.map(p => {
      const pages = d.assets.filter(a => a.kind === 'web' && String(a.auth_profile_id) === String(p.id));
      return `<div class="col-card profile-card">
        <div class="col-head">${icon('lock')}<b>${esc(p.name)}</b><span class="muted">${pages.length ? t('profileUsedBy', { list: esc(pages.map(a => a.name).join(', ')) }) : t('profileUnused')}</span></div>
        ${p.login_url ? `<dl class="kv small"><dt>${t('loginUrl')}</dt><dd title="${esc(p.login_url)}">${esc(p.login_url)}</dd><dt>${t('targetUrl')}</dt><dd title="${esc(p.target_url)}">${esc(p.target_url)}</dd></dl>` : ''}
        ${edit ? `<div class="col-actions"><button class="btn sm" data-do="addWebWithLogin" data-id="${esc(d.id)}" data-profile="${esc(p.id)}">${icon('web')}${t('addWebWithLogin')}</button>
          <button class="icon-btn" title="${t('edit')}" data-do="editProfile" data-id="${esc(d.id)}" data-profile="${esc(p.id)}">${icon('edit')}</button>
          <button class="icon-btn danger" title="${t('delete')}" data-do="deleteProfile" data-id="${esc(d.id)}" data-profile="${esc(p.id)}" data-name="${esc(p.name)}" data-used="${pages.length}">${icon('trash')}</button></div>` : ''}
      </div>`;
    }).join('') || `<div class="empty">${support === 'ok' ? t('noProfiles') : t('noProfilesShort')}</div>`}</div>
    ${edit ? `<div class="note">${icon('lock')}${t('loginsSecurity')}</div>` : ''}</section>`;
}

// ---------- playlists view (device picker + editor)

VIEWS.playlists = {
  mount(root) {
    setTitle(t('nav_playlists'));
    root.innerHTML = `<div class="split"><section class="card flush picker"><div class="toolbar"><input id="pQ" type="search" placeholder="${t('searchDevices')}"></div><div id="pList"></div></section>
      <div id="pEditor">${S.route.id ? '' : `<section class="card empty">${t('pickDevice')}</section>`}</div></div>`;
    $('#pQ', root).oninput = () => this.update(root);
  },
  update(root) {
    const q = ($('#pQ', root).value || '').toLowerCase();
    patch($('#pList', root), S.devices.filter(d => !q || (d.name + d.id + d.location).toLowerCase().includes(q)).map(d => `<a class="pick-row ${d.id === S.route.id ? 'active' : ''}" href="#/playlists/${encodeURIComponent(d.id)}">
      ${dot(d)}<div><b>${esc(d.name)}</b><small>${esc(d.current_name || '—')}</small></div><span class="muted">${d.playlist_count}</span></a>`).join('') || `<div class="empty">${t('noDevices')}</div>`);
    const d = S.detail;
    if (!S.route.id || !d) return;
    let ed = $('#pEditor', root);
    if (!ed.dataset.ready) { ed.innerHTML = '<div id="pHead"></div><div id="pPl"></div><div id="pCol"></div><div id="pLog"></div>'; ed.dataset.ready = 1; }
    patch($('#pHead', ed), `<section class="card dev-hero slim"><div class="hero-main"><div class="hero-title">${statusBadge(d)}<h2><a href="#/device/${encodeURIComponent(d.id)}">${esc(d.name)}</a></h2></div>
      <div class="chips"><span>${t('nowPlaying')}: ${esc(d.current_name || '—')}${d.frozen ? ' (' + t('frozen') + ')' : ''}</span></div></div><div class="hero-actions">${can('control') ? `
      <button class="btn" data-do="cmd" data-id="${esc(d.id)}" data-act="unfreeze" ${d.online ? '' : 'disabled'}>${icon('play')}${t('act_unfreeze')}</button>
      <button class="btn" data-do="cmd" data-id="${esc(d.id)}" data-act="next" ${d.online ? '' : 'disabled'}>${icon('next')}${t('act_next')}</button>` : ''}</div></section>`);
    renderPlaylist($('#pPl', ed), d);
    patch($('#pCol', ed), renderCollections(d));
    patch($('#pLog', ed), renderProfiles(d));
  },
};

// ---------- organization

VIEWS.organization = {
  mount(root) {
    setTitle(t('nav_organization'));
    root.innerHTML = `<div class="grid two"><section class="card flush" id="orgGroups"></section><section class="card flush" id="orgLocations"></section></div>`;
  },
  update(root) {
    for (const kind of ['groups', 'locations']) {
      const unassigned = S.devices.filter(d => !(kind === 'groups' ? d.group : d.location)).length;
      const field = kind === 'groups' ? 'group' : 'location';
      patch($('#org' + kind[0].toUpperCase() + kind.slice(1), root), `<div class="toolbar"><h2 class="grow">${icon(kind === 'groups' ? 'group' : 'location')} ${t(kind)}</h2>
        ${can('manage') ? `<button class="btn sm primary" data-do="orgEdit" data-kind="${kind}">${icon('plus')}${t('add')}</button>` : ''}</div>
        <div class="org-list">${S.org[kind].map(o => `<div class="org-row">
          <a href="#/devices?${field}=${encodeURIComponent(o.name)}"><b>${esc(o.name)}</b><small class="muted">${esc([o.address, o.description].filter(Boolean).join(' · '))}</small></a>
          <span class="muted nowrap">${o.online}/${o.total} ${t('online').toLowerCase()}</span>
          ${can('manage') ? `<span class="row-actions"><button class="icon-btn" data-do="orgEdit" data-kind="${kind}" data-name="${esc(o.name)}" title="${t('edit')}">${icon('edit')}</button>
          <button class="icon-btn danger" data-do="orgDelete" data-kind="${kind}" data-name="${esc(o.name)}" title="${t('delete')}">${icon('trash')}</button></span>` : ''}</div>`).join('') || `<div class="empty">${t('noEntries')}</div>`}
        <div class="org-row muted"><span>${t('unassigned')}</span><span>${unassigned}</span></div></div>`);
    }
  },
};

// ---------- operations (commands + SSH jobs)

VIEWS.operations = {
  mount(root) {
    setTitle(t('nav_operations'));
    const tab = S.route.id === 'jobs' ? 'jobs' : 'commands';
    root.innerHTML = `<nav class="tabs"><a href="#/operations/commands" class="${tab === 'commands' ? 'active' : ''}">${t('commandHistory')}</a><a href="#/operations/jobs" class="${tab === 'jobs' ? 'active' : ''}">${t('sshJobs')}</a></nav>
      ${tab === 'commands' ? `<section class="card flush"><div class="toolbar"><select id="cDev"><option value="">${t('allDevices')}</option>${S.devices.map(d => `<option value="${esc(d.id)}">${esc(d.name)}</option>`).join('')}</select>
      <select id="cState"><option value="">${t('allStates')}</option>${['queued', 'delivered', 'completed', 'failed', 'expired', 'timeout', 'cancelled'].map(s => `<option value="${s}">${t('state_' + s)}</option>`).join('')}</select></div><div id="cTable"></div></section>`
      : `<section class="card flush"><div id="jTable"></div></section>`}`;
    const reload = () => this.update(root);
    if ($('#cDev', root)) { $('#cDev', root).onchange = reload; $('#cState', root).onchange = reload; }
  },
  async update(root) {
    if ($('#cTable', root)) {
      const q = new URLSearchParams({ device_id: $('#cDev', root).value, state: $('#cState', root).value, limit: 300 });
      const rows = await api('/api/commands?' + q).catch(() => null);
      if (rows) patch($('#cTable', root), commandsTable(rows, true));
    } else {
      const jobs = await api('/api/jobs').catch(() => null);
      if (jobs) patch($('#jTable', root), `<div class="table-wrap"><table class="table"><thead><tr><th>${t('time')}</th><th>${t('target')}</th><th>${t('user')}</th><th>${t('state')}</th><th>${t('device')}</th><th></th></tr></thead>
        <tbody>${jobs.map(j => `<tr><td class="nowrap">${dt(j.created)}</td><td>${esc(j.target)}</td><td>${esc(j.username)}</td><td><span class="badge st-${j.state}">${t('state_' + j.state)}</span></td>
        <td>${j.device_id ? `<a href="#/device/${encodeURIComponent(j.device_id)}">${esc(j.device_id)}</a>` : '—'}</td><td><button class="btn sm" data-do="jobLog" data-job="${j.id}">${t('showLog')}</button></td></tr>`).join('')
        || `<tr><td colspan="6" class="empty">${t('noJobs')}</td></tr>`}</tbody></table></div>`);
    }
  },
};

// ---------- audit

VIEWS.audit = {
  mount(root) {
    setTitle(t('nav_audit'));
    root.innerHTML = `<section class="card flush"><div class="toolbar"><input id="aQ" type="search" placeholder="${t('searchAudit')}"></div><div id="aTable"></div></section>`;
    let timer;
    $('#aQ', root).oninput = () => { clearTimeout(timer); timer = setTimeout(() => this.update(root), 300); };
  },
  async update(root) {
    const rows = await api('/api/audit?' + new URLSearchParams({ q: $('#aQ', root).value, limit: 500 })).catch(() => null);
    if (!rows) return;
    patch($('#aTable', root), `<div class="table-wrap"><table class="table"><thead><tr><th>${t('time')}</th><th>${t('user')}</th><th>${t('action')}</th><th>${t('target')}</th><th>${t('detail')}</th></tr></thead>
      <tbody>${rows.map(a => `<tr><td class="nowrap">${dt(a.created)}</td><td><b>${esc(a.username)}</b></td><td>${esc(auditAction(a.action))}</td><td>${esc(a.target)}</td><td><small class="result" title="${esc(a.detail)}">${esc((a.detail || '').slice(0, 160))}</small></td></tr>`).join('')
      || `<tr><td colspan="5" class="empty">${t('noEntries')}</td></tr>`}</tbody></table></div>`);
  },
};

function auditAction(a) {
  const [prefix, rest] = a.split('.', 2);
  if ((prefix === 'command' || prefix === 'bulk') && rest) return t('audit_' + prefix) + ': ' + t('act_' + rest);
  return t('audit_' + a.replace('.', '_'));
}

// ---------- users

VIEWS.users = {
  mount(root) {
    setTitle(t('nav_users'));
    root.innerHTML = `<section class="card flush"><div class="toolbar"><h2 class="grow">${t('nav_users')}</h2><button class="btn primary" data-do="userEdit">${icon('plus')}${t('newUser')}</button></div><div id="uTable"></div></section>
      <section class="card"><h2>${t('rolesTitle')}</h2><div class="roles">${['admin', 'manager', 'operator', 'viewer'].map(r => `<div><span class="badge role">${t('role_' + r)}</span><p class="muted">${t('roleDesc_' + r)}</p></div>`).join('')}</div></section>`;
  },
  async update(root) {
    const users = await api('/api/users').catch(() => null);
    if (!users) return;
    S.users = users;
    patch($('#uTable', root), `<div class="table-wrap"><table class="table"><thead><tr><th>${t('username')}</th><th>${t('role')}</th><th>${t('language')}</th><th>${t('state')}</th><th>${t('created')}</th><th></th></tr></thead>
      <tbody>${users.map(u => `<tr><td><b>${esc(u.username)}</b>${u.username === S.me.username ? ` <span class="tag">${t('you')}</span>` : ''}</td><td><span class="badge role">${t('role_' + u.role)}</span></td>
      <td>${u.language === 'en' ? 'English' : 'Čeština'}</td><td>${u.enabled ? `<span class="tag ok">${t('active')}</span>` : `<span class="tag">${t('disabled')}</span>`}</td><td class="nowrap">${dt(u.created)}</td>
      <td class="actions-cell"><button class="icon-btn" data-do="userEdit" data-uid="${u.id}" title="${t('edit')}">${icon('edit')}</button>
      ${u.username !== S.me.username ? `<button class="icon-btn danger" data-do="userDelete" data-uid="${u.id}" data-name="${esc(u.username)}" title="${t('delete')}">${icon('trash')}</button>` : ''}</td></tr>`).join('')}</tbody></table></div>`);
  },
};

// ---------- settings

VIEWS.settings = {
  async mount(root) {
    setTitle(t('nav_settings'));
    root.innerHTML = `<div class="grid two"><section class="card"><h2>${t('myAccount')}</h2>
      <dl class="kv"><dt>${t('username')}</dt><dd>${esc(S.me.username)}</dd><dt>${t('role')}</dt><dd>${t('role_' + S.me.role)}</dd></dl>
      <form id="pwForm" class="form"><h3>${t('changePassword')}</h3>
        <label>${t('currentPassword')}<input type="password" name="current_password" autocomplete="current-password" required></label>
        <label>${t('newPassword')}<input type="password" name="new_password" minlength="10" autocomplete="new-password" required></label>
        <div class="form-actions"><button class="btn primary">${t('changePassword')}</button></div></form></section>
      <section class="card" id="sysCard"></section></div>`;
    if (!can('admin')) { $('#sysCard', root).innerHTML = `<h2>${t('system')}</h2><dl class="kv"><dt>${t('hubVersion')}</dt><dd>${esc(S.me.hub_version)}</dd><dt>${t('agentVersion')}</dt><dd>${esc(S.me.agent_version)}</dd></dl>`; return; }
    const s = await api('/api/settings').catch(() => null);
    if (!s) return;
    const cmd = `curl -fsSL ${location.origin}/api/bootstrap/install-agent.sh | sudo bash -s -- --hub ${location.origin} --token ${s.enroll_token} --name "NODE"`;
    $('#sysCard', root).innerHTML = `<h2>${t('system')}</h2><dl class="kv"><dt>${t('hubVersion')}</dt><dd>${esc(s.hub_version)}</dd><dt>${t('agentVersion')}</dt><dd>${esc(s.agent_version)}</dd>
      <dt>${t('enrollToken')}</dt><dd><code class="secret" id="tokVal">••••••••••••</code> <button class="btn sm ghost" id="tokShow">${t('show')}</button> <button class="btn sm ghost" id="tokRotate">${icon('refresh')}${t('rotateToken')}</button></dd></dl>
      <h3>${t('sdCardTitle')}</h3><p class="muted">${t('sdCardHint')}</p><button class="btn sm" data-do="sdCard">${icon('download')}${t('sdCardTitle')}</button>
      <h3>${t('manualInstall')}</h3><p class="muted">${t('manualInstallHint')}</p><pre class="code" id="instCmd">${esc(cmd)}</pre><button class="btn sm" id="instCopy">${icon('copy')}${t('copy')}</button>`;
    $('#tokShow', root).onclick = () => { $('#tokVal', root).textContent = s.enroll_token; };
    $('#tokRotate', root).onclick = async () => {
      if (!await confirmBox(t('confirmRotateToken'))) return;
      try { await api('/api/settings/enroll-token', { method: 'POST' }); toast(t('tokenRotated')); VIEWS.settings.mount(root); } catch (err) { toast(errText(err), 'err'); }
    };
    $('#instCopy', root).onclick = () => navigator.clipboard.writeText(cmd).then(() => toast(t('copied')));
    root.insertAdjacentHTML('beforeend', `<div class="grid two">
      <section class="card"><h2>${t('branding')}</h2><p class="muted">${t('brandingHint')}</p>
        <div class="logo-preview"><span class="mark" data-logo>C</span></div>
        <div class="form"><label>${t('logoFile')}<input type="file" id="logoFile" accept="image/png,image/jpeg,image/webp,image/svg+xml"></label>
        <div class="form-actions"><button class="btn ghost" id="logoRemove">${icon('trash')}${t('logoRemove')}</button><button class="btn primary" id="logoUpload">${icon('upload')}${t('logoUpload')}</button></div></div></section>
      <section class="card"><h2>${t('backupTitle')}</h2><p class="muted">${t('backupHint')}</p>
        <div class="form-actions start"><button class="btn primary" id="backupDownload">${icon('download')}${t('backupDownload')}</button></div>
        <h3>${t('restoreTitle')}</h3><p class="muted">${t('restoreHint')}</p>
        <div class="form"><label>${t('backupFile')}<input type="file" id="restoreFile" accept=".zip,application/zip"></label>
        <div class="form-actions"><button class="btn danger" id="restoreRun">${icon('upload')}${t('restoreRun')}</button></div></div>
        <h3>${t('migrateTitle')}</h3><ol class="steps">${t('migrateSteps')}</ol></section></div>`);
    applyLogo();
    $('#logoUpload', root).onclick = async () => {
      const f = $('#logoFile', root).files[0];
      if (!f) return toast(t('err_unsupported_file'), 'err');
      try {
        const r = await fetch('/api/branding/logo', { method: 'POST', body: f, headers: { Authorization: 'Bearer ' + S.token, 'Content-Type': f.type } });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.detail || r.statusText);
        PUBLIC = { ...PUBLIC, ...d };
        applyLogo();
        toast(t('saved'));
      } catch (err) { toast(errText(err), 'err'); }
    };
    $('#logoRemove', root).onclick = async () => {
      try { PUBLIC = { ...PUBLIC, ...(await api('/api/branding/logo', { method: 'DELETE' })) }; applyLogo(); toast(t('deleted')); } catch (err) { toast(errText(err), 'err'); }
    };
    $('#backupDownload', root).onclick = async () => {
      try {
        await downloadResponse(await fetch('/api/backup', { headers: { Authorization: 'Bearer ' + S.token } }), 'caracal-fleet-backup.zip');
      } catch (err) { toast(errText(err), 'err'); }
    };
    $('#restoreRun', root).onclick = async () => {
      const f = $('#restoreFile', root).files[0];
      if (!f) return toast(t('err_invalid_backup'), 'err');
      if (!await confirmBox(t('confirmRestore'))) return;
      try {
        const r = await fetch('/api/backup/restore', { method: 'POST', body: f, headers: { Authorization: 'Bearer ' + S.token, 'Content-Type': 'application/zip' } });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.detail || r.statusText);
        toast(t('restoreDone'));
        waitForRestart();
      } catch (err) { toast(errText(err), 'err'); }
    };
  },
};

// After a restore the hub restarts; its session secret comes from the backup, so sign in again.
function waitForRestart() {
  let tries = 0;
  const poll = async () => {
    tries++;
    const ok = await fetch('/api/health').then(r => r.ok).catch(() => false);
    if (ok && tries > 2) { store.set('caracalToken', null); location.reload(); } else if (tries < 90) setTimeout(poll, 2000);
  };
  setTimeout(poll, 3000);
}

// ---------- CARACAL updates

const MOVING_TAGS = ['latest', 'edge', 'main', 'master'];
const latestOf = versions => (versions || []).find(v => !MOVING_TAGS.includes(v)) || '';
const runtimeLabel = d => (d.runtime === 'docker' ? 'Docker' : d.runtime === 'host' ? t('runtimeClassic') : '—');

VIEWS.updates = {
  mount(root) {
    setTitle(t('nav_updates'));
    root.innerHTML = `<div class="grid two">
      <section class="card"><div class="card-head"><h2>${t('nodeImage')}</h2><button class="btn sm ghost" data-do="imageRefresh">${icon('refresh')}${t('refresh')}</button></div>
        <p class="muted">${t('nodeImageHint')}</p>
        <form id="imgForm" class="form"><label>${t('imageName')}<input name="image" placeholder="ghcr.io/OWNER/caracal-node" ${can('admin') ? '' : 'readonly'}></label>
        ${can('admin') ? `<div class="form-actions"><button class="btn">${t('save')}</button></div>` : ''}</form>
        <h3>${t('availableVersions')}</h3><div id="imgVersions"></div></section>
      <section class="card"><h2>${t('howUpdateWorks')}</h2><ol class="steps">${t('updateStepsDocker')}</ol></section></div>
      <section class="card flush"><div class="toolbar"><h2 class="grow">${t('nodeVersions')}</h2>
        <button class="btn" data-do="convertNodes">${icon('upload')}${t('convertToDocker')}</button>
        <button class="btn primary" data-do="caracalUpdate" data-outdated="1">${icon('updates')}${t('updateOutdated')}</button></div><div id="relNodes"></div></section>
      <details class="card legacy"><summary><b>${t('classicNodes')}</b> <span class="muted">${t('classicNodesHint')}</span></summary>
        <div class="grid two legacy-body"><section><h3>${t('releaseUpload')}</h3><p class="muted">${t('releaseUploadHint')}</p>
          <form id="relForm" class="form"><label>${t('releaseFile')}<input type="file" name="file" accept=".zip,.tar.gz,.tgz,application/zip,application/gzip" required></label>
          <div class="row2"><label>${t('releaseVersion')}<input name="version" placeholder="${t('releaseVersionAuto')}"></label><label>${t('notes')}<input name="notes"></label></div>
          <div class="upload-prog" hidden><i></i></div><div class="form-actions"><button class="btn primary">${icon('upload')}${t('releaseUploadBtn')}</button></div></form></section>
        <section><h3>${t('howUpdateWorks')}</h3><ol class="steps">${t('updateSteps')}</ol></section></div>
        <div id="relList"></div></details>`;
    const img = $('#imgForm input[name=image]', root);
    img.oninput = () => { img.dataset.touched = '1'; };
    $('#imgForm', root).onsubmit = async e => {
      e.preventDefault();
      try {
        S.image = await api('/api/node-image', { method: 'PUT', json: { image: img.value.trim() } });
        delete img.dataset.touched;
        toast(t('saved'));
        render();
      } catch (err) { toast(errText(err), 'err'); }
    };
    $('#relForm', root).onsubmit = async e => {
      e.preventDefault();
      const form = e.target, file = form.file.files[0];
      const pr = $('.upload-prog', form);
      pr.hidden = false;
      try {
        await new Promise((resolve, reject) => {
          const x = new XMLHttpRequest();
          x.open('POST', '/api/node-releases');
          x.setRequestHeader('Authorization', 'Bearer ' + S.token);
          x.setRequestHeader('X-File-Name', encodeURIComponent(file.name));
          x.setRequestHeader('X-Release-Version', encodeURIComponent(form.version.value.trim()));
          x.setRequestHeader('X-Release-Notes', encodeURIComponent(form.notes.value.trim()));
          x.upload.onprogress = ev => ev.lengthComputable && ($('i', pr).style.width = (ev.loaded / ev.total * 100) + '%');
          x.onload = () => { let d = {}; try { d = JSON.parse(x.responseText); } catch { /* ignore */ } x.status < 300 ? resolve(d) : reject(new Error(d.detail || x.statusText)); };
          x.onerror = () => reject(new Error('network_error'));
          x.send(file);
        });
        form.reset();
        toast(t('saved'));
        render();
      } catch (err) { toast(errText(err), 'err'); } finally { pr.hidden = true; }
    };
  },
  async update(root) {
    const [img, rels] = await Promise.all([api('/api/node-image').catch(() => null), api('/api/node-releases').catch(() => null)]);
    if (!$('#relNodes', root)) return;
    if (img) {
      S.image = img;
      const input = $('#imgForm input[name=image]', root);
      if (input && !input.dataset.touched && document.activeElement !== input) input.value = img.image || '';
      const err = img.error ? img.error.split(':')[0] : '';
      patch($('#imgVersions', root), err ? `<div class="note warn">${esc(errText(new Error(err)))}${img.error.includes(':') ? `<br><small>${esc(img.error.split(':').slice(1).join(':'))}</small>` : ''}</div>`
        : `<div class="version-list">${img.versions.slice(0, 16).map(v => `<span class="tag ${v === latestOf(img.versions) ? 'ok' : ''}">${esc(v)}</span>`).join('') || `<span class="muted">${t('noVersions')}</span>`}</div>`);
    }
    if (rels) {
      S.releases = rels;
      patch($('#relList', root), `<div class="table-wrap"><table class="table"><thead><tr><th>${t('releaseVersion')}</th><th>${t('file')}</th><th>${t('notes')}</th><th>${t('created')}</th><th></th></tr></thead>
        <tbody>${rels.map(r => `<tr><td><b>${esc(r.version)}</b> ${r.latest ? `<span class="tag ok">${t('latest')}</span>` : ''}</td><td><small>${esc(r.filename)} · ${fmtSize(r.size)}</small></td>
        <td><small class="muted">${esc(r.notes)}</small></td><td class="nowrap">${dt(r.created)}<br><small class="muted">${esc(r.username)}</small></td>
        <td class="actions-cell"><button class="btn sm" data-do="caracalUpdate" data-release="${r.id}">${icon('updates')}${t('deploy')}</button>
        <button class="icon-btn danger" data-do="releaseDelete" data-release="${r.id}" data-name="${esc(r.version)}" title="${t('delete')}">${icon('trash')}</button></td></tr>`).join('')
        || `<tr><td colspan="5" class="empty">${t('noReleases')}</td></tr>`}</tbody></table></div>`);
    }
    const latest = { docker: latestOf(S.image?.versions), host: (S.releases || [])[0]?.version };
    patch($('#relNodes', root), `<div class="table-wrap"><table class="table"><thead><tr><th>${t('device')}</th><th>${t('locationGroup')}</th><th>${t('runtime')}</th><th>CARACAL</th><th>${t('agent')}</th><th>${t('state')}</th><th></th></tr></thead>
      <tbody>${S.devices.map(d => {
        const want = latest[d.runtime];
        const action = !can('manage') || !d.online ? '' : d.runtime === 'docker'
          ? `<button class="btn sm" data-do="caracalUpdate" data-id="${esc(d.id)}">${icon('updates')}${t('act_update_caracal')}</button>`
          : `<button class="btn sm" data-do="convertNodes" data-id="${esc(d.id)}">${icon('upload')}${t('convertToDocker')}</button>`;
        return `<tr data-href="#/device/${encodeURIComponent(d.id)}"><td><div class="dev-name">${dot(d)}<b>${esc(d.name)}</b></div></td>
        <td><small>${esc([d.location, d.group].filter(Boolean).join(' · ') || '—')}</small></td><td>${runtimeLabel(d)}</td>
        <td>${esc(d.caracal_version || '?')} ${want && d.caracal_version !== want ? `<span class="tag info">${t('outdated')}</span>` : want ? `<span class="tag ok">${t('latest')}</span>` : ''}</td>
        <td>${esc(d.version || '—')}</td><td>${d.maintenance ? `<span class="badge info">${t('att_maintenance')}</span>` : statusBadge(d)}</td><td class="actions-cell">${action}</td></tr>`;
      }).join('') || `<tr><td colspan="7" class="empty">${t('noDevicesHint')}</td></tr>`}</tbody></table></div>`);
  },
};

async function imageInfo() {
  const img = await api('/api/node-image').catch(() => null);
  if (!img || !img.image) { toast(t('err_node_image_missing'), 'warn'); location.hash = '#/updates'; return null; }
  return img;
}

const versionField = img => `<label>${t('releaseVersion')}<input name="version" list="dlVersions" value="${esc(latestOf(img.versions) || img.versions[0] || '')}" required></label>
  <datalist id="dlVersions">${img.versions.map(v => `<option value="${esc(v)}">`).join('')}</datalist>
  ${img.error ? `<div class="note warn">${esc(errText(new Error(img.error.split(':')[0])))}</div>` : ''}`;

function reportQueued(r, action) {
  toast(t('bulkQueued', { n: r.command_ids.length, action }));
  if (r.skipped.length) toast(t('bulkSkipped', { n: r.skipped.length, list: r.skipped.map(s => (dev(s.device_id)?.name || s.device_id) + ' (' + errText(new Error(s.reason)) + ')').join(', ') }), 'warn');
  setTimeout(tick, 1500);
}

// Update Docker nodes to an image version.
async function caracalUpdateDialog(opts = {}) {
  const img = await imageInfo();
  if (!img) return;
  const latest = latestOf(img.versions);
  const pre = opts.targets || (opts.outdated ? S.devices.filter(d => d.online && d.runtime === 'docker' && d.caracal_version !== latest).map(d => d.id) : []);
  modal({
    title: t('act_update_caracal'), submit: t('updateNow'), danger: true, wide: true,
    body: `<div class="form"><p class="muted">${t('imageName')}: <code>${esc(img.image)}</code></p>${versionField(img)}
      <label>${t('targetDevices')}</label>${targetPicker(null, pre)}<div class="note warn">${t('updateWarningDocker')}</div></div>`,
    onOpen: bindTargetPicker,
    onSubmit: async data => {
      const ids = pickedTargets(data);
      if (!ids.length) throw new Error('no_devices');
      reportQueued(await api('/api/node-image/deploy', { method: 'POST', json: { version: data.version.trim(), device_ids: ids } }), t('act_update_caracal'));
    },
  });
}

// Convert classic nodes (/opt/caracal) to CARACAL on Docker.
async function convertDialog(opts = {}) {
  const img = await imageInfo();
  if (!img) return;
  const pre = opts.targets || S.devices.filter(d => d.online && d.runtime === 'host').map(d => d.id);
  modal({
    title: t('convertToDocker'), submit: t('convertNow'), danger: true, wide: true,
    body: `<div class="form"><p class="muted">${t('convertHint')}</p>${versionField(img)}
      <label>${t('targetDevices')}</label>${targetPicker(null, pre)}<div class="note warn">${t('convertWarning')}</div></div>`,
    onOpen: bindTargetPicker,
    onSubmit: async data => {
      const ids = pickedTargets(data);
      if (!ids.length) throw new Error('no_devices');
      reportQueued(await api('/api/node-image/convert', { method: 'POST', json: { version: data.version.trim(), device_ids: ids } }), t('convertToDocker'));
    },
  });
}

// Classic nodes: install a release archive with its install.sh.
async function zipUpdateDialog(opts = {}) {
  const rels = await api('/api/node-releases').catch(() => []);
  if (!rels.length) { toast(t('noReleases'), 'warn'); location.hash = '#/updates'; return; }
  const chosen = opts.release || rels[0].id;
  const pre = opts.targets || [];
  modal({
    title: t('act_update_caracal') + ' (' + t('runtimeClassic') + ')', submit: t('updateNow'), danger: true, wide: true,
    body: `<div class="form"><label>${t('releaseVersion')}<select name="release_id">${rels.map(r => `<option value="${r.id}" ${r.id === chosen ? 'selected' : ''}>${esc(r.version)}${r.latest ? ' (' + t('latest') + ')' : ''}</option>`).join('')}</select></label>
      <label>${t('targetDevices')}</label>${targetPicker(null, pre)}
      <div class="note warn">${t('updateWarning')}</div></div>`,
    onOpen: bindTargetPicker,
    onSubmit: async data => {
      const ids = pickedTargets(data);
      if (!ids.length) throw new Error('no_devices');
      reportQueued(await api(`/api/node-releases/${data.release_id}/deploy`, { method: 'POST', json: { device_ids: ids } }), t('act_update_caracal'));
    },
  });
}

function discoverDialog() {
  modal({
    title: t('discoverTitle'), submit: t('discoverStart'), wide: true,
    body: `<div class="form"><p class="muted">${t('discoverHint')}</p>
      <div class="row2"><label>${t('network')}<input name="cidr" placeholder="192.168.1.0/24" value="${esc(store.get('caracalCidr', ''))}" required></label>
      <label>${t('sshPort')}<input name="port" type="number" value="22"></label></div><div id="discRes"></div></div>`,
    onSubmit: async data => {
      store.set('caracalCidr', data.cidr.trim());
      const r = await api('/api/discover', { method: 'POST', json: { cidr: data.cidr.trim(), port: Number(data.port || 22) } });
      pollDiscovery(r.job_id);
      return false;   // keep the dialog open for the results
    },
  });
}

async function pollDiscovery(id) {
  const el = $('#discRes');
  if (!el) return;
  const j = await api('/api/jobs/' + id).catch(() => null);
  if (!j) return;
  if (['queued', 'running'].includes(j.state)) {
    el.innerHTML = `<div class="note info">${t('discoverRunning')}</div>`;
    setTimeout(() => pollDiscovery(id), 1500);
    return;
  }
  let found = [];
  try { found = JSON.parse(j.result_json || '[]'); } catch { /* ignore */ }
  el.innerHTML = found.length ? `<div class="table-wrap"><table class="table"><thead><tr><th>IP</th><th>${t('system')}</th><th>SSH</th><th></th></tr></thead><tbody>
    ${found.map(x => `<tr><td><b>${esc(x.ip)}</b></td><td>${esc(x.system || '—')}</td><td><small class="muted">${esc(x.banner)}</small></td>
    <td class="actions-cell">${x.device_id ? `<a class="btn sm" href="#/device/${encodeURIComponent(x.device_id)}">${esc(x.device_name)}</a>`
      : `<button type="button" class="btn sm primary" data-do="installHost" data-host="${esc(x.ip)}">${icon('plus')}${t('install')}</button>`}</td></tr>`).join('')}</tbody></table></div>`
    : `<div class="empty">${t('discoverNone')}</div>`;
}

// Zero-touch SD card: files for the boot partition of a fresh Raspberry Pi OS Lite / DietPi card.
async function sdCardDialog() {
  const img = await api('/api/node-image').catch(() => ({ image: '' }));
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
  const country = ((navigator.language || '').split('-')[1] || (S.lang === 'cs' ? 'CZ' : '')).toUpperCase();
  modal({
    title: t('sdCardTitle'), submit: t('sdCardDownload'), wide: true,
    body: `<div class="form"><p class="muted">${t('sdCardHint')}</p>
      ${img.image ? '' : `<div class="note warn">${t('err_node_image_missing')} <a href="#/updates">${t('nav_updates')}</a></div>`}
      <div class="mode-pick"><label class="check"><input type="radio" name="os" value="raspios" checked><span><b>${t('osRaspios')}</b><small>${t('osRaspiosHint')}</small></span></label>
      <label class="check"><input type="radio" name="os" value="dietpi"><span><b>${t('osDietpi')}</b><small>${t('osDietpiHint')}</small></span></label></div>
      <label class="dietpi-only">${t('dietpiTxt')}<input type="file" id="sdDietpi" accept=".txt,text/plain"><small class="muted">${t('dietpiTxtHint')}</small></label>
      <label>${t('hubUrl')}<input name="hub_url" value="${esc(location.origin)}" required></label>
      <div class="row2"><label>${t('namePrefix')}<input name="name_prefix" value="caracal" pattern="[a-z0-9][a-z0-9\\-]{0,30}" required></label>
      <label>${t('timezone')}<input name="timezone" value="${esc(tz)}"></label></div>
      <details><summary>${t('wifi')}</summary><div class="row2"><label>${t('wifiSsid')}<input name="wifi_ssid" maxlength="32"></label>
        <label>${t('wifiPassword')}<input name="wifi_password" type="password" autocomplete="off" minlength="8" maxlength="63"></label></div>
        <label>${t('wifiCountry')}<input name="wifi_country" value="${esc(country)}" maxlength="2"></label></details>
      <details><summary>${t('deviceLogin')}</summary><p class="muted">${t('deviceLoginHint')}</p>
        <div class="row2"><label class="raspios-only">${t('username')}<input name="user" value="admin"></label>
        <label>${t('password')}<input name="password" type="password" autocomplete="new-password" minlength="8" maxlength="100"></label></div>
        <label>${t('sshPublicKey')}<textarea name="ssh_key" rows="2" placeholder="ssh-ed25519 AAAA… user@pc"></textarea></label></details>
      <details><summary>${t('dockerNetwork')}</summary><p class="muted">${t('dockerNetworkHint')}</p>
        <label>${t('dockerPool')}<input name="docker_pool" placeholder="10.200.0.0/16" pattern="[0-9]{1,3}(\\.[0-9]{1,3}){3}/[0-9]{2}" spellcheck="false"></label></details>
      <div class="note info">${t('sdCardTokenNote')}</div></div>`,
    onOpen: form => {
      const sync = () => {
        const dietpi = form.os.value === 'dietpi';
        $$('.dietpi-only', form).forEach(x => { x.hidden = !dietpi; });
        $$('.raspios-only', form).forEach(x => { x.hidden = dietpi; });
      };
      $$('input[name=os]', form).forEach(x => { x.onchange = sync; });
      sync();
    },
    onSubmit: async data => {
      const body = { ...data, wifi_country: (data.wifi_country || '').trim().toUpperCase() };
      if (data.os === 'dietpi') {
        const f = $('#sdDietpi').files[0];
        if (!f) throw new Error('invalid_dietpi_txt');
        body.dietpi_txt = await f.text();
      }
      const r = await fetch('/api/sdcard', { method: 'POST', body: JSON.stringify(body), headers: { Authorization: 'Bearer ' + S.token, 'Content-Type': 'application/json' } });
      await downloadResponse(r, `caracal-sdcard-${data.os}.zip`);
      setTimeout(() => modal({ title: t('sdCardTitle'), body: `<ol class="steps">${t(data.os === 'dietpi' ? 'sdCardStepsDietpi' : 'sdCardSteps')}</ol>` }), 50);
    },
  });
}

async function downloadResponse(r, fallback) {
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  const name = (r.headers.get('Content-Disposition') || '').match(/filename="?([^";]+)/)?.[1] || fallback;
  const url = URL.createObjectURL(await r.blob());
  const a = Object.assign(document.createElement('a'), { href: url, download: name });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

// ---------- global playlists

VIEWS.global = {
  mount(root) {
    setTitle(t('nav_global'));
    if (S.route.id) {
      root.innerHTML = `<a class="back" href="#/global">${icon('back')}${t('nav_global')}</a><div id="gHead"></div><div id="gItems"></div><div id="gDeps"></div>`;
      return;
    }
    root.innerHTML = `<section class="card flush"><div class="toolbar"><h2 class="grow">${t('nav_global')}</h2>
      ${can('content') ? `<button class="btn" data-do="globalFromDevice">${icon('copy')}${t('globalFromDevice')}</button><button class="btn primary" data-do="globalNew">${icon('plus')}${t('globalNew')}</button>` : ''}</div>
      <div id="gList"></div></section><p class="muted">${t('globalHint')}</p>`;
  },
  async update(root) {
    if (!S.route.id) {
      const rows = await api('/api/global-playlists').catch(() => null);
      if (!rows || !$('#gList', root)) return;
      patch($('#gList', root), `<div class="table-wrap"><table class="table"><thead><tr><th>${t('name')}</th><th>${t('playlistItems')}</th><th>${t('lastDeployment')}</th><th>${t('updated')}</th></tr></thead>
        <tbody>${rows.map(p => `<tr data-href="#/global/${p.id}"><td><b>${esc(p.name)}</b><br><small class="muted">${esc(p.description)}</small></td><td>${p.items}</td>
        <td>${p.last_deployment ? `${dt(p.last_deployment.created)}<br><small class="muted">${t('nDevices', { n: p.last_deployment.targets })} · ${t('mode_' + p.last_deployment.mode)}</small>` : '<span class="muted">—</span>'}</td>
        <td class="nowrap">${dt(p.updated)}<br><small class="muted">${esc(p.updated_by)}</small></td></tr>`).join('') || `<tr><td colspan="4" class="empty">${t('noGlobalPlaylists')}</td></tr>`}</tbody></table></div>`);
      return;
    }
    const p = await api('/api/global-playlists/' + encodeURIComponent(S.route.id)).catch(() => null);
    if (!$('#gHead', root)) return;
    if (!p) { patch($('#gHead', root), `<div class="card empty">${t('err_not_found')}</div>`); return; }
    S.global = p;
    setTitle(p.name, t('nav_global').toUpperCase());
    const edit = can('content');
    patch($('#gHead', root), `<section class="card dev-hero"><div class="hero-main"><div class="hero-title">${icon('global')}<h2>${esc(p.name)}</h2></div>
      <div class="chips"><span>${t('nItems', { n: p.items.length })}</span><span>${t('updated')} ${dt(p.updated)} · ${esc(p.updated_by)}</span></div>
      ${p.description ? `<p class="muted">${esc(p.description)}</p>` : ''}</div>
      ${edit ? `<div class="hero-actions"><button class="btn primary" data-do="globalDeploy" ${p.items.length ? '' : 'disabled'}>${icon('upload')}${t('deploy')}</button>
      <button class="btn" data-do="globalEdit">${icon('edit')}${t('edit')}</button><button class="btn danger" data-do="globalDelete">${icon('trash')}${t('delete')}</button></div>` : ''}</section>`);
    patch($('#gItems', root), `<section class="card flush"><div class="toolbar"><h2 class="grow">${t('playlist')} <span class="muted">(${p.items.length})</span></h2>
      ${edit ? ['web', 'image', 'video', 'grafana-tag'].map(k => `<button class="btn" data-do="globalAddItem" data-kind="${k}">${kindIcon(k)}${t({ web: 'addWeb', image: 'addImage', video: 'addVideo' }[k] || 'grafanaCollection')}</button>`).join('') : ''}</div>
      <ol class="playlist">${p.items.map((it, i) => `<li class="g-row">
        <span class="idx">${i + 1}</span><span class="kind k-${esc(it.kind)}">${kindIcon(it.kind)}</span>
        <div class="pl-main"><b>${esc(it.name)}</b><small>${esc(kindLabel(it.kind))} · ${esc(it.kind === 'web' ? it.source : isTag(it.kind) ? `${t('grafanaTag')} ${it.tag} · ${it.grafana_url}` : (it.file_name || '') + (it.file_size ? ' · ' + fmtSize(it.file_size) : ''))}</small></div>
        <span class="pl-dur">${it.duration ? dur(it.duration) : t('fullLength')}</span>
        <span class="pl-actions">${edit ? `<button class="icon-btn" title="${t('moveUp')}" data-do="globalMove" data-iid="${it.id}" data-dir="-1" ${i ? '' : 'disabled'}>${icon('up')}</button>
          <button class="icon-btn" title="${t('moveDown')}" data-do="globalMove" data-iid="${it.id}" data-dir="1" ${i < p.items.length - 1 ? '' : 'disabled'}>${icon('down')}</button>
          <button class="icon-btn" title="${t('edit')}" data-do="globalEditItem" data-iid="${it.id}">${icon('edit')}</button>
          <button class="icon-btn danger" title="${t('delete')}" data-do="globalDeleteItem" data-iid="${it.id}" data-name="${esc(it.name)}">${icon('trash')}</button>` : ''}</span></li>`).join('')
        || `<li class="empty">${t('playlistEmpty')}</li>`}</ol></section>`);
    patch($('#gDeps', root), `<section class="card flush"><div class="toolbar"><h2 class="grow">${t('deployments')}</h2></div>
      <div class="table-wrap"><table class="table"><thead><tr><th>${t('time')}</th><th>${t('user')}</th><th>${t('mode')}</th><th>${t('result')}</th></tr></thead>
      <tbody>${p.deployments.map(dep => {
        const count = s => dep.results.filter(x => s.includes(x.state)).length;
        return `<tr><td class="nowrap">${dt(dep.created)}</td><td>${esc(dep.username)}</td><td>${t('mode_' + dep.mode)}</td>
        <td><details><summary><span class="badge st-completed">${count(['completed'])}</span> <span class="badge st-failed">${count(['failed', 'timeout', 'expired'])}</span> <span class="badge st-queued">${count(['queued', 'delivered'])}</span> / ${dep.targets.length}</summary>
        ${dep.results.map(x => `<div class="dep-row"><a href="#/device/${encodeURIComponent(x.device_id)}">${esc(x.device_name || x.device_id)}</a><span class="badge st-${x.state}">${t('state_' + x.state)}</span><small class="result" title="${esc(x.result)}">${esc((x.result || '').slice(0, 160))}</small></div>`).join('')}</details></td></tr>`;
      }).join('') || `<tr><td colspan="4" class="empty">${t('noDeployments')}</td></tr>`}</tbody></table></div></section>`);
  },
};

function fmtSize(n) {
  return n > 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' kB';
}

function globalItemDialog(pid, kind, item = null) {
  const isMedia = kind === 'image' || kind === 'video';
  const it = item || { name: '', source: '', duration: kind === 'grafana-tag' ? 60 : kind === 'web' ? 30 : 15, scale: 1, kiosk: true };
  modal({
    title: item ? t('editItem') : isTag(kind) ? t('addCollection') : t('add_' + kind),
    body: `<div class="form">${isMedia && !item ? `<label>${t('file')}<input type="file" name="file" accept="${kind}/*" required></label><div class="upload-prog" hidden><i></i></div>` : ''}
      <label>${t('name')}<input name="name" value="${esc(it.name)}" ${isMedia ? '' : 'required'}></label>
      ${kind === 'web' ? `<label>URL<input name="source" type="url" value="${esc(it.source)}" placeholder="https://" required></label>` : ''}
      ${isTag(kind) ? grafanaFields(it) : ''}
      <div class="row2"><label>${t(isTag(kind) ? 'durationPerDashboard' : 'durationSec')}<input name="duration" type="number" min="5" value="${esc(it.duration)}" required></label>
      ${kind === 'web' || isTag(kind) ? `<label>${t('scale')}<input name="scale" type="number" step="0.05" min="0.5" max="3" value="${esc(it.scale ?? 1)}"></label>` : '<span></span>'}</div>
      ${kind === 'video' ? `<small class="muted">${t('videoDurationHint')}</small>` : ''}</div>`,
    onOpen: form => {
      const f = $('input[name=file]', form);
      if (f) f.onchange = () => { if (f.files[0] && !form.name.value) form.name.value = f.files[0].name.replace(/\.[^.]+$/, ''); };
    },
    onSubmit: async (data, form) => {
      const payload = { kind, name: data.name.trim(), duration: Number(data.duration || 0), scale: Number(data.scale || 1) };
      if (kind === 'web') payload.source = data.source.trim();
      if (isTag(kind)) Object.assign(payload, grafanaPayload(data));
      if (item) {
        await api(`/api/global-playlists/${pid}/items/${item.id}`, { method: 'PATCH', json: payload });
      } else {
        if (isMedia) {
          const file = form.file.files[0];
          const pr = $('.upload-prog', form);
          pr.hidden = false;
          const up = await uploadFile(file, x => { $('i', pr).style.width = (x * 100) + '%'; });
          payload.file_id = up.id;
          payload.name = payload.name || file.name;
        }
        await api(`/api/global-playlists/${pid}/items`, { method: 'POST', json: payload });
      }
      toast(t('saved'));
      render();
    },
  });
}

function globalDeployDialog(p) {
  const media = p.items.some(it => it.kind !== 'web');
  modal({
    title: t('deployTitle', { name: esc(p.name) }), submit: t('deploy'), wide: true,
    body: `<div class="form"><p class="muted">${t('deployHint')}</p>${targetPicker(null)}
      <div class="radios"><label class="check"><input type="radio" name="mode" value="append" checked>${t('mode_append')}</label>
      <label class="check"><input type="radio" name="mode" value="replace">${t('mode_replace')}</label></div>
      <div class="note warn">${t('deployReplaceWarning')}</div>
      ${media ? `<div class="note">${t('deployMediaNote')}</div>` : ''}</div>`,
    onOpen: bindTargetPicker,
    onSubmit: async data => {
      const targets = pickedTargets(data);
      if (!targets.length) throw new Error('no_devices');
      const r = await api(`/api/global-playlists/${p.id}/deploy`, { method: 'POST', json: { target_ids: targets, mode: data.mode } });
      toast(t('deployQueued', { n: targets.length }));
      if (r.media_unsupported.length) toast(t('deployMediaSkipped', { list: r.media_unsupported.join(', ') }), 'warn');
      render();
    },
  });
}

// ------------------------------------------------------------------ modals

function modal({ title, body, submit = t('save'), danger = false, wide = false, onSubmit, onOpen }) {
  const dlg = $('#modal');
  dlg.className = wide ? 'wide' : '';
  dlg.innerHTML = `<form method="dialog" class="modal-form" novalidate><header><h2>${title}</h2><button type="button" class="icon-btn ghost" data-close>${icon('x')}</button></header>
    <div class="modal-body">${body}</div><div class="form-error" id="mErr"></div>
    <footer>${onSubmit ? `<button type="button" class="btn ghost" data-close>${t('cancel')}</button><button class="btn ${danger ? 'danger' : 'primary'}" id="mSubmit">${submit}</button>` : `<button type="button" class="btn" data-close>${t('close')}</button>`}</footer></form>`;
  const form = $('form', dlg);
  $$('[data-close]', dlg).forEach(b => { b.onclick = () => dlg.close(); });
  form.onsubmit = async e => {
    e.preventDefault();
    if (!onSubmit) return dlg.close();
    if (!form.checkValidity()) { form.reportValidity(); return; }
    const btn = $('#mSubmit', dlg);
    btn.disabled = true;
    btn.classList.add('busy');
    $('#mErr', dlg).textContent = '';
    try {
      const keep = await onSubmit(Object.fromEntries(new FormData(form)), form);
      if (keep !== false) dlg.close();
    } catch (err) {
      $('#mErr', dlg).textContent = errText(err);
    } finally { btn.disabled = false; btn.classList.remove('busy'); }
  };
  dlg.showModal();
  onOpen && onOpen(form);
  return dlg;
}

function confirmBox(text, danger = true, submit = t('confirm')) {
  return new Promise(resolve => {
    let ok = false;
    const dlg = modal({ title: t('areYouSure'), body: `<p>${text}</p>`, submit, danger, onSubmit: () => { ok = true; } });
    dlg.addEventListener('close', () => resolve(ok), { once: true });
  });
}

function targetPicker(excludeId, preselect = [], accept = null) {
  const groups = orgNames('groups');
  return `<div class="targets"><div class="target-tools"><button type="button" class="btn sm ghost" data-tsel="all">${t('selectAll')}</button><button type="button" class="btn sm ghost" data-tsel="none">${t('selectNone')}</button>
    ${groups.map(g => `<button type="button" class="btn sm ghost" data-tsel="g:${esc(g)}">${icon('group')}${esc(g)}</button>`).join('')}</div>
    <div class="target-list">${S.devices.filter(d => d.id !== excludeId && (!accept || accept(d))).map(d => `<label class="check"><input type="checkbox" name="t:${esc(d.id)}" data-group="${esc(d.group)}" ${preselect.includes(d.id) ? 'checked' : ''}>${dot(d)}<span>${esc(d.name)}</span><small class="muted">${esc(d.location)}</small></label>`).join('') || `<div class="muted">${t('noOtherDevices')}</div>`}</div></div>`;
}

function bindTargetPicker(form) {
  $$('[data-tsel]', form).forEach(b => {
    b.onclick = () => {
      const v = b.dataset.tsel;
      $$('.target-list input', form).forEach(i => { i.checked = v === 'all' ? true : v === 'none' ? false : (v.slice(2) === i.dataset.group ? true : i.checked); });
    };
  });
}
const pickedTargets = data => Object.keys(data).filter(k => k.startsWith('t:')).map(k => k.slice(2));

async function sendCommand(id, action, payload = {}) {
  await api(`/api/devices/${encodeURIComponent(id)}/commands`, { method: 'POST', json: { action, payload } });
  toast(t('commandQueued', { action: t('act_' + action) }));
  setTimeout(tick, 1500);
}

async function sendBulk(ids, action, payload = {}) {
  const r = await api('/api/bulk/commands', { method: 'POST', json: { device_ids: ids, action, payload } });
  toast(t('bulkQueued', { n: r.command_ids.length, action: t('act_' + action) }));
  if (r.skipped.length) toast(t('bulkSkipped', { n: r.skipped.length, list: r.skipped.map(s => (dev(s.device_id)?.name || s.device_id) + ' (' + errText(new Error(s.reason)) + ')').join(', ') }), 'warn');
  setTimeout(tick, 1500);
}

function uploadFile(file, onProgress) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open('POST', '/api/files');
    x.setRequestHeader('Authorization', 'Bearer ' + S.token);
    x.setRequestHeader('Content-Type', file.type || 'application/octet-stream');
    x.setRequestHeader('X-File-Name', encodeURIComponent(file.name));
    x.upload.onprogress = e => e.lengthComputable && onProgress(e.loaded / e.total);
    x.onload = () => { let d = {}; try { d = JSON.parse(x.responseText); } catch { /* ignore */ } x.status < 300 ? resolve(d) : reject(new Error(d.detail || x.statusText)); };
    x.onerror = () => reject(new Error('network_error'));
    x.send(file);
  });
}

function assetDialog(deviceId, kind, asset = null, targets = null, preset = {}) {
  const isMedia = kind === 'image' || kind === 'video';
  const multi = !asset;
  const here = S.detail && S.detail.id === deviceId ? S.detail : null;
  const showEnabled = !(here && !here.supports_enabled);
  // a login profile belongs to one node, so it can be chosen only when the page goes to this node alone
  const profiles = kind === 'web' && !targets && here && loginSupport(here) === 'ok' ? here.profiles || [] : null;
  const a = asset || { name: preset.name || '', source: preset.source || '', duration: kind === 'web' ? 30 : 15, scale: 1, enabled: true, auth_profile_id: preset.profileId ?? null };
  const body = `<div class="form">
    ${isMedia && !asset ? `<label>${t('file')}<input type="file" name="file" accept="${kind}/*" required></label><div class="upload-prog" hidden><i></i></div>` : ''}
    <label>${t('name')}<input name="name" value="${esc(a.name)}" ${isMedia ? '' : 'required'}></label>
    ${kind === 'web' ? `<label>URL<input name="source" type="url" value="${esc(a.source)}" placeholder="https://" required></label>` : ''}
    ${profiles ? `<label>${t('login')}<select name="auth_profile_id"><option value="">${t('noLogin')}</option>${profiles.map(p => `<option value="${esc(p.id)}" ${String(p.id) === String(a.auth_profile_id) ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}</select>
      <small class="muted login-hint"></small></label>` : ''}
    <div class="row2"><label>${t('durationSec')}<input name="duration" type="number" min="5" value="${esc(a.duration ?? '')}" required></label>
    ${kind === 'web' ? `<label>${t('scale')}<input name="scale" type="number" step="0.05" min="0.5" max="3" value="${esc(a.scale ?? 1)}"></label>` : '<span></span>'}</div>
    ${kind === 'video' ? `<small class="muted">${t('videoDurationHint')}</small>` : ''}
    ${showEnabled ? `<label class="check"><input type="checkbox" name="enabled" ${a.enabled ? 'checked' : ''}>${t('enabledInPlaylist')}</label>` : ''}
    ${multi && targets === null ? `<details class="also-add"><summary>${t('alsoAddTo')}</summary>${targetPicker(deviceId)}</details>` : ''}
    ${multi && targets ? `<p class="muted">${t('addToSelected', { n: targets.length })}</p>` : ''}</div>`;
  modal({
    title: asset ? t('editItem') : t('add_' + kind), body, wide: multi && targets === null,
    onOpen: form => {
      bindTargetPicker(form);
      const f = $('input[name=file]', form);
      if (f) f.onchange = () => { if (f.files[0] && !form.name.value) form.name.value = f.files[0].name.replace(/\.[^.]+$/, ''); };
      const sel = $('select[name=auth_profile_id]', form);
      if (!sel) return;
      const sync = () => {
        const p = profiles.find(x => String(x.id) === sel.value);
        // CARACAL opens the target page of the profile after signing in
        $('.login-hint', form).textContent = p ? t('loginTargetHint', { url: p.target_url || '—' }) : '';
        if (p && p.target_url && !form.source.value) form.source.value = p.target_url;
        const also = $('.also-add', form);
        if (also) {
          also.hidden = !!p;
          if (p) $$('.target-list input', form).forEach(i => { i.checked = false; });
        }
      };
      sel.onchange = sync;
      sync();
    },
    onSubmit: async (data, form) => {
      const payload = { name: data.name.trim(), duration: Number(data.duration || 0) };
      if (showEnabled) payload.enabled = !!data.enabled;
      if (kind === 'web') { payload.source = data.source.trim(); payload.scale = Number(data.scale || 1); }
      if (profiles) payload.auth_profile_id = data.auth_profile_id ? Number(data.auth_profile_id) : null;
      if (asset) return sendCommand(deviceId, 'update_asset', { id: asset.id, ...payload });
      const ids = targets || [deviceId, ...(payload.auth_profile_id ? [] : pickedTargets(data))];
      if (isMedia) {
        const file = form.file.files[0];
        const pr = $('.upload-prog', form);
        pr.hidden = false;
        const up = await uploadFile(file, p => { $('i', pr).style.width = (p * 100) + '%'; });
        Object.assign(payload, { file_id: up.id, kind, name: payload.name || file.name });
      }
      return ids.length === 1 ? sendCommand(ids[0], isMedia ? 'add_media' : 'add_web', payload) : sendBulk(ids, isMedia ? 'add_media' : 'add_web', payload);
    },
  });
}

// Login profile of web pages: CARACAL opens the login page, fills the form and then shows the target page.
const SELECTOR_DEFAULTS = {
  user_selector: 'input[name="username"], input[name="user"], input[name="login"], input[name="name"], input[type="email"]',
  pass_selector: 'input[type="password"]',
  submit_selector: 'button[type="submit"], input[type="submit"]',
};

function profileDialog(deviceId, profile = null, targets = null) {
  const p = profile || { name: '', login_url: '', target_url: '', ...SELECTOR_DEFAULTS };
  const keep = profile ? `placeholder="${esc(t('leaveEmpty'))}"` : 'required';
  modal({
    title: profile ? t('editProfile') : t('add_profile'), submit: profile ? t('save') : t('add'), wide: !profile && !targets,
    body: `<div class="form"><p class="muted">${t('loginHint')}</p>
      <label>${t('name')}<input name="name" value="${esc(p.name)}" placeholder="${esc(t('loginNamePlaceholder'))}" required></label>
      <label>${t('loginUrl')}<input name="login_url" type="url" value="${esc(p.login_url)}" placeholder="https://zabbix.example/index.php" required><small class="muted">${t('loginUrlHint')}</small></label>
      <label>${t('targetUrl')}<input name="target_url" type="url" value="${esc(p.target_url)}" placeholder="https://zabbix.example/zabbix.php?action=dashboard.view" required><small class="muted">${t('targetUrlHint')}</small></label>
      <div class="row2"><label>${t('username')}<input name="username" autocomplete="off" spellcheck="false" ${keep}></label>
      <label>${t('password')}<input name="password" type="password" autocomplete="new-password" ${keep}></label></div>
      <label class="check"><input type="checkbox" data-reveal>${t('showPassword')}</label>
      <details><summary>${t('loginSelectors')}</summary><p class="muted">${t('selectorsHint')}</p>
        <label>${t('selectorUser')}<input name="user_selector" value="${esc(p.user_selector)}" spellcheck="false" required></label>
        <label>${t('selectorPass')}<input name="pass_selector" value="${esc(p.pass_selector)}" spellcheck="false" required></label>
        <label>${t('selectorSubmit')}<input name="submit_selector" value="${esc(p.submit_selector)}" spellcheck="false" required></label></details>
      ${targets ? `<p class="muted">${t('addToSelected', { n: targets.length })}</p>` : profile ? '' : `<details><summary>${t('alsoAddTo')}</summary>${targetPicker(deviceId, [], d => loginSupport(d) === 'ok')}</details>`}
      <div class="note info">${icon('lock')}${t('loginsSecurity')}</div></div>`,
    onOpen: form => {
      bindTargetPicker(form);
      const reveal = $('[data-reveal]', form);
      reveal.onchange = () => { form.password.type = reveal.checked ? 'text' : 'password'; };
    },
    onSubmit: data => {
      const payload = { name: data.name.trim(), login_url: data.login_url.trim(), target_url: data.target_url.trim(),
        user_selector: data.user_selector.trim(), pass_selector: data.pass_selector.trim(), submit_selector: data.submit_selector.trim() };
      if (data.username.trim()) payload.username = data.username.trim();
      if (data.password) payload.password = data.password;
      if (profile) return sendCommand(deviceId, 'update_profile', { id: profile.id, ...payload });
      const ids = targets || [deviceId, ...pickedTargets(data)];
      return ids.length === 1 ? sendCommand(ids[0], 'add_profile', payload) : sendBulk(ids, 'add_profile', payload);
    },
  });
}

// A CARACAL Grafana collection shows every dashboard with a given tag (Grafana guest access).
const grafanaFields = (c = {}) => `<label>${t('grafanaUrl')}<input name="grafana_url" type="url" value="${esc(c.grafana_url || '')}" placeholder="https://grafana.example" required></label>
  <label>${t('grafanaTag')}<input name="tag" value="${esc(c.tag || '')}" required></label>
  <label class="check"><input type="checkbox" name="kiosk" ${c.kiosk === false ? '' : 'checked'}>${t('grafanaKiosk')}</label>`;
const grafanaPayload = data => ({ grafana_url: data.grafana_url.trim(), tag: data.tag.trim(), kiosk: !!data.kiosk });

function collectionDialog(deviceId, col = null, targets = null) {
  const c = col || { name: '', duration: 60, scale: 1, kiosk: true };
  modal({
    title: col ? t('editCollection') : t('addCollection'), wide: !col && !targets,
    body: `<div class="form"><label>${t('name')}<input name="name" value="${esc(c.name)}" required></label>${grafanaFields(c)}
      <div class="row2"><label>${t('durationPerDashboard')}<input name="duration" type="number" min="5" value="${esc(c.duration ?? 60)}" required></label>
      <label>${t('scale')}<input name="scale" type="number" step="0.05" min="0.5" max="3" value="${esc(c.scale ?? 1)}"></label></div>
      <small class="muted">${t('grafanaHint')}</small>
      ${targets ? `<p class="muted">${t('addToSelected', { n: targets.length })}</p>` : col ? '' : `<details><summary>${t('alsoAddTo')}</summary>${targetPicker(deviceId)}</details>`}</div>`,
    onOpen: bindTargetPicker,
    onSubmit: data => {
      const payload = { name: data.name.trim(), ...grafanaPayload(data), duration: Number(data.duration || 60), scale: Number(data.scale || 1) };
      if (col) return sendCommand(deviceId, 'update_collection', { id: col.id, ...payload });
      const ids = targets || [deviceId, ...pickedTargets(data)];
      return ids.length === 1 ? sendCommand(ids[0], 'add_collection', payload) : sendBulk(ids, 'add_collection', payload);
    },
  });
}

function freezeDialog(deviceId, item, col, name) {
  modal({
    title: t('freezeTitle', { name: esc(name) }), submit: t('freezeItem'),
    body: `<p class="muted">${t('freezeHint')}</p><div class="radios">${[0, 5, 15, 30, 60, 240].map((m, i) => `<label class="check"><input type="radio" name="minutes" value="${m}" ${i === 0 ? 'checked' : ''}>${m ? t('forMinutes', { n: m }) : t('untilResume')}</label>`).join('')}</div>`,
    onSubmit: data => (col != null ? sendCommand(deviceId, 'freeze_collection', { collection_id: col, minutes: Number(data.minutes) })
      : sendCommand(deviceId, 'freeze', { item_id: item, minutes: Number(data.minutes) })),
  });
}

function copyDialog(sourceId, opts = {}) {
  const src = sourceId ? dev(sourceId) : null;
  const what = opts.assetIds ? t('copyWhatItem') : opts.collectionIds ? t('copyWhatCollection') : opts.onlyCollections ? t('copyWhatCollections') : t('copyWhatPlaylist');
  const whole = !opts.assetIds && !opts.collectionIds && !opts.onlyCollections;
  modal({
    title: t('copyTitle'), submit: t('copy'), wide: true,
    body: `<div class="form">
      ${src ? `<p>${what} <b>${esc(src.name)}</b></p>` : `<label>${t('sourceDevice')}<select name="source" required><option value="">—</option>${S.devices.filter(d => !opts.targets.includes(d.id)).map(d => `<option value="${esc(d.id)}">${esc(d.name)}</option>`).join('')}</select></label><p class="muted">${t('copyToSelected', { n: opts.targets.length })}</p>`}
      ${opts.targets ? '' : `<label>${t('targetDevices')}</label>${targetPicker(sourceId)}`}
      ${whole || opts.onlyCollections ? `<div class="radios"><label class="check"><input type="radio" name="mode" value="append" checked>${t('mode_append')}</label>
        <label class="check"><input type="radio" name="mode" value="replace">${t('mode_replace')}</label></div><small class="muted">${t('replaceHint')}</small>` : ''}
      ${whole ? `<label class="check"><input type="checkbox" name="include_collections" checked>${t('includeCollections')}</label>` : ''}</div>`,
    onOpen: bindTargetPicker,
    onSubmit: async data => {
      const targets = opts.targets || pickedTargets(data);
      const source = sourceId || data.source;
      if (!targets.length) throw new Error('no_devices');
      const body = { source_id: source, target_ids: targets, mode: data.mode || 'append', include_collections: !!data.include_collections };
      if (opts.assetIds) { body.asset_ids = opts.assetIds; body.collection_ids = []; }
      if (opts.collectionIds) { body.asset_ids = []; body.collection_ids = opts.collectionIds; }
      if (opts.onlyCollections) { body.asset_ids = []; body.collection_ids = (dev(source) && S.detail?.collections || []).map(c => c.id); }
      const r = await api('/api/copy', { method: 'POST', json: body });
      toast(t('copyQueued', { n: targets.length }));
      if (r.skipped && r.skipped.length) toast(t('copySkipped', { list: r.skipped.join(', ') }), 'warn');
      if (r.without_login && r.without_login.length) toast(t('copyWithoutLogin', { list: r.without_login.join(', ') }), 'warn');
    },
  });
}

async function provisionDialog(host = '', name = '', mode = 'node') {
  const img = await api('/api/node-image').catch(() => ({ image: '', versions: [] }));
  modal({
    title: t('addDevice'), submit: t('install'), wide: true,
    body: `<div class="form">
      <div class="mode-pick"><label class="check"><input type="radio" name="mode" value="node" ${mode === 'node' ? 'checked' : ''}><span><b>${t('modeNode')}</b><small>${t('modeNodeHint')}</small></span></label>
      <label class="check"><input type="radio" name="mode" value="agent" ${mode === 'agent' ? 'checked' : ''}><span><b>${t('modeAgent')}</b><small>${t('modeAgentHint')}</small></span></label></div>
      <div class="node-only">${img.image ? versionField(img) : `<div class="note warn">${t('err_node_image_missing')} <a href="#/updates">${t('nav_updates')}</a></div>`}</div>
      <div class="row2"><label>${t('hostIp')}<input name="host" value="${esc(host)}" required></label><label>${t('sshPort')}<input name="port" type="number" value="22"></label></div>
      <div class="form-actions start"><button type="button" class="btn sm ghost" data-do="discover">${icon('refresh')}${t('discoverTitle')}</button>${can('admin') ? `<button type="button" class="btn sm ghost" data-do="sdCard">${icon('download')}${t('sdCardTitle')}</button>` : ''}</div>
      <div class="row2"><label>${t('sshUser')}<input name="username" value="pi" required></label><label>${t('sshPassword')}<input name="password" type="password" autocomplete="off"></label></div>
      <details><summary>${t('sshKeyAuth')}</summary><label>${t('privateKey')}<textarea name="private_key" rows="4" placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"></textarea></label><label>${t('passphrase')}<input name="passphrase" type="password"></label></details>
      <label>${t('deviceName')}<input name="name" value="${esc(name)}"></label>
      <div class="row2"><label>${t('group')}<input name="group" list="dlGroups"></label><label>${t('location')}<input name="location" list="dlLocations"></label></div>${orgDatalists()}
      <label>${t('hubUrl')}<input name="hub_url" value="${esc(location.origin)}" required></label>
      <label class="check agent-only"><input type="checkbox" name="reenroll">${t('reenroll')}</label>
      <label class="check"><input type="checkbox" name="forget_host_key">${t('forgetHostKey')}</label>
      <p class="muted">${t('sshInstallHint')}</p></div>`,
    onOpen: form => {
      const sync = () => {
        const node = form.mode.value === 'node';
        $$('.node-only', form).forEach(x => { x.hidden = !node; });
        $$('.agent-only', form).forEach(x => { x.hidden = node; });
        const v = $('input[name=version]', form);
        if (v) v.required = node;
      };
      $$('input[name=mode]', form).forEach(x => { x.onchange = sync; });
      sync();
    },
    onSubmit: async data => {
      if (!data.password && !data.private_key) throw new Error('missing_fields');
      const r = await api('/api/provision', { method: 'POST', json: { ...data, port: Number(data.port || 22), reenroll: !!data.reenroll, forget_host_key: !!data.forget_host_key } });
      setTimeout(() => jobDialog(r.job_id), 50);
    },
  });
}

function jobDialog(id) {
  const dlg = modal({ title: t('jobLog', { id }), wide: true, body: `<div class="job-state" id="jState"></div><pre class="code log" id="jLog">…</pre>` });
  const poll = async () => {
    if (!dlg.open) return;
    const j = await api('/api/jobs/' + id).catch(() => null);
    if (j) {
      $('#jState', dlg).innerHTML = `<span class="badge st-${j.state}">${t('state_' + j.state)}</span> ${esc(j.target)}${j.device_id ? ` → <a href="#/device/${encodeURIComponent(j.device_id)}">${esc(j.device_id)}</a>` : ''}`;
      const log = $('#jLog', dlg);
      log.textContent = j.log || '…';
      log.scrollTop = log.scrollHeight;
      if (['queued', 'running'].includes(j.state)) setTimeout(poll, 1500); else tick();
    }
  };
  poll();
}

function orgDialog(kind, name) {
  const o = name ? S.org[kind].find(x => x.name === name) : { name: '', description: '', address: '' };
  modal({
    title: (name ? t('edit') : t('add')) + ': ' + t(kind === 'groups' ? 'group' : 'location'),
    body: `<div class="form"><label>${t('name')}<input name="name" value="${esc(o.name)}" required></label>
      ${kind === 'locations' ? `<label>${t('address')}<input name="address" value="${esc(o.address || '')}"></label>` : ''}
      <label>${t('description')}<textarea name="description" rows="2">${esc(o.description || '')}</textarea></label></div>`,
    onSubmit: async data => {
      if (name) await api(`/api/org/${kind}/${encodeURIComponent(name)}`, { method: 'PATCH', json: data });
      else await api(`/api/org/${kind}`, { method: 'POST', json: data });
      toast(t('saved'));
      tick();
    },
  });
}

function assignDialog(ids) {
  modal({
    title: t('assignGroupLocation'),
    body: `<div class="form"><p class="muted">${t('selectedN', { n: ids.length })}</p>
      <label class="check"><input type="checkbox" name="setGroup">${t('setGroup')}</label><input name="group" list="dlGroups">
      <label class="check"><input type="checkbox" name="setLocation">${t('setLocation')}</label><input name="location" list="dlLocations">${orgDatalists()}
      <small class="muted">${t('assignHint')}</small></div>`,
    onSubmit: async data => {
      const body = { device_ids: ids };
      if (data.setGroup) body.group = data.group.trim();
      if (data.setLocation) body.location = data.location.trim();
      await api('/api/devices/assign', { method: 'POST', json: body });
      toast(t('saved'));
      tick();
    },
  });
}

function userDialog(uid) {
  const u = uid ? S.users.find(x => x.id === uid) : null;
  const roles = ['viewer', 'operator', 'manager', 'admin'];
  modal({
    title: u ? t('editUser') + ': ' + esc(u.username) : t('newUser'),
    body: `<div class="form">${u ? '' : `<label>${t('username')}<input name="username" required pattern="[A-Za-z0-9._@\\-]{2,64}"></label>`}
      <label>${u ? t('resetPassword') : t('password')}<input name="password" type="password" minlength="10" autocomplete="new-password" ${u ? `placeholder="${t('leaveEmpty')}"` : 'required'}></label>
      <div class="row2"><label>${t('role')}<select name="role">${roles.map(r => `<option value="${r}" ${(u ? u.role : 'viewer') === r ? 'selected' : ''}>${t('role_' + r)}</option>`).join('')}</select></label>
      <label>${t('language')}<select name="language"><option value="cs" ${u?.language === 'en' ? '' : 'selected'}>Čeština</option><option value="en" ${u?.language === 'en' ? 'selected' : ''}>English</option></select></label></div>
      ${u ? `<label class="check"><input type="checkbox" name="enabled" ${u.enabled ? 'checked' : ''}>${t('accountEnabled')}</label>` : ''}</div>`,
    onSubmit: async data => {
      if (u) {
        const body = { role: data.role, language: data.language, enabled: !!data.enabled };
        if (data.password) body.password = data.password;
        await api('/api/users/' + u.id, { method: 'PATCH', json: body });
      } else {
        await api('/api/users', { method: 'POST', json: data });
      }
      toast(t('saved'));
      render();
    },
  });
}

// ------------------------------------------------------------------ actions (event delegation)

const ACTIONS_UI = {
  async cmd(b) {
    const { id, act } = b.dataset;
    const d = dev(id);
    if (act === 'reboot' && !await confirmBox(t('confirmReboot', { name: esc(d?.name || id) }))) return;
    if (act === 'restart_player' && !await confirmBox(t('confirmRestartPlayer', { name: esc(d?.name || id) }), false)) return;
    const payload = b.dataset.item != null ? { item_id: b.dataset.item } : b.dataset.col != null ? { collection_id: b.dataset.col } : {};
    await sendCommand(id, act, payload);
  },
  async bulk(b) {
    const ids = [...S.selected], act = b.dataset.act;
    if (['reboot', 'restart_player', 'update_agent'].includes(act) && !await confirmBox(t('confirmBulk', { action: t('act_' + act), n: ids.length }), act === 'reboot')) return;
    await sendBulk(ids, act);
  },
  bulkAddContent() {
    modal({
      title: t('addContent'), submit: t('continue'),
      body: `<div class="radios">${bulkKinds().map((k, i) => `<label class="check"><input type="radio" name="kind" value="${k}" ${i ? '' : 'checked'}>${t('add_' + k)}</label>`).join('')}</div>`,
      onSubmit: data => {
        const ids = [...S.selected];
        setTimeout(() => {
          if (data.kind === 'collection') bulkCollection(ids);
          else if (data.kind === 'profile') profileDialog(ids[0], null, ids);
          else assetDialog(ids[0], data.kind, null, ids);
        }, 50);
      },
    });
  },
  bulkCopy() { copyDialog('', { targets: [...S.selected] }); },
  bulkAssign() { assignDialog([...S.selected]); },
  caracalUpdate(b) {
    if (b.dataset.release) return zipUpdateDialog({ release: Number(b.dataset.release) });
    const opts = {};
    if (b.dataset.id) opts.targets = [b.dataset.id];
    else if (b.dataset.outdated) opts.outdated = true;
    else if (S.selected.size && S.route.view === 'devices') opts.targets = [...S.selected];
    // classic nodes are updated with release archives (or converted to Docker)
    if (opts.targets && opts.targets.every(id => dev(id)?.runtime === 'host')) return zipUpdateDialog(opts);
    return caracalUpdateDialog(opts);
  },
  convertNodes(b) { convertDialog(b.dataset.id ? { targets: [b.dataset.id] } : {}); },
  imageRefresh() { api('/api/node-image?refresh=true').then(img => { S.image = img; render(); toast(t('refresh')); }).catch(err => toast(errText(err), 'err')); },
  discover() { discoverDialog(); },
  sdCard() { sdCardDialog(); },
  installHost(b) { provisionDialog(b.dataset.host, '', 'node'); },
  async releaseDelete(b) {
    if (!await confirmBox(t('confirmDeleteItem', { name: esc(b.dataset.name) }))) return;
    await api('/api/node-releases/' + b.dataset.release, { method: 'DELETE' });
    toast(t('deleted'));
    render();
  },
  bulkSetHub() {
    const ids = [...S.selected];
    modal({
      title: t('act_set_hub'), submit: t('confirm'), danger: true,
      body: `<div class="form"><p class="muted">${t('setHubHint')}</p><label>${t('hubUrl')}<input name="hub" type="url" placeholder="https://" required></label></div>`,
      onSubmit: data => sendBulk(ids, 'set_hub', { hub: data.hub.trim() }),
    });
  },
  globalNew() {
    modal({
      title: t('globalNew'),
      body: `<div class="form"><label>${t('name')}<input name="name" required></label><label>${t('description')}<textarea name="description" rows="2"></textarea></label></div>`,
      onSubmit: async data => { const r = await api('/api/global-playlists', { method: 'POST', json: data }); location.hash = '#/global/' + r.id; },
    });
  },
  globalFromDevice() {
    modal({
      title: t('globalFromDevice'),
      body: `<div class="form"><p class="muted">${t('globalFromDeviceHint')}</p><label>${t('sourceDevice')}<select name="device_id" required><option value="">—</option>${S.devices.map(d => `<option value="${esc(d.id)}">${esc(d.name)}</option>`).join('')}</select></label>
        <label>${t('name')}<input name="name"></label></div>`,
      onSubmit: async data => {
        const r = await api('/api/global-playlists/from-device', { method: 'POST', json: data });
        if (r.skipped.length) toast(t('copySkipped', { list: r.skipped.join(', ') }), 'warn');
        location.hash = '#/global/' + r.id;
      },
    });
  },
  globalEdit() {
    const p = S.global;
    modal({
      title: t('edit'),
      body: `<div class="form"><label>${t('name')}<input name="name" value="${esc(p.name)}" required></label><label>${t('description')}<textarea name="description" rows="2">${esc(p.description)}</textarea></label></div>`,
      onSubmit: async data => { await api('/api/global-playlists/' + p.id, { method: 'PATCH', json: data }); toast(t('saved')); render(); },
    });
  },
  async globalDelete() {
    if (!await confirmBox(t('confirmDeleteItem', { name: esc(S.global.name) }))) return;
    await api('/api/global-playlists/' + S.global.id, { method: 'DELETE' });
    toast(t('deleted'));
    location.hash = '#/global';
  },
  globalAddItem(b) { globalItemDialog(S.global.id, b.dataset.kind); },
  globalEditItem(b) {
    const it = S.global.items.find(x => String(x.id) === b.dataset.iid);
    if (it) globalItemDialog(S.global.id, it.kind, it);
  },
  async globalDeleteItem(b) {
    if (!await confirmBox(t('confirmDeleteItem', { name: esc(b.dataset.name) }))) return;
    await api(`/api/global-playlists/${S.global.id}/items/${b.dataset.iid}`, { method: 'DELETE' });
    render();
  },
  async globalMove(b) {
    const order = S.global.items.map(x => x.id);
    const i = order.indexOf(Number(b.dataset.iid)), j = i + Number(b.dataset.dir);
    if (i < 0 || j < 0 || j >= order.length) return;
    [order[i], order[j]] = [order[j], order[i]];
    await api(`/api/global-playlists/${S.global.id}/order`, { method: 'PUT', json: { order } });
    render();
  },
  globalDeploy() { globalDeployDialog(S.global); },
  clearSel() { S.selected.clear(); render(); },
  addAsset(b) { assetDialog(b.dataset.id, b.dataset.kind); },
  addProfile(b) { profileDialog(b.dataset.id); },
  editProfile(b) {
    const p = (S.detail?.profiles || []).find(x => String(x.id) === b.dataset.profile);
    if (p) profileDialog(b.dataset.id, p);
  },
  async deleteProfile(b) {
    const used = Number(b.dataset.used || 0);
    if (!await confirmBox(t('confirmDeleteItem', { name: esc(b.dataset.name) }) + (used ? '<br>' + t('confirmDeleteProfileUsed', { n: used }) : ''))) return;
    await sendCommand(b.dataset.id, 'delete_profile', { id: b.dataset.profile });
  },
  addWebWithLogin(b) {
    const p = (S.detail?.profiles || []).find(x => String(x.id) === b.dataset.profile);
    if (p) assetDialog(b.dataset.id, 'web', null, null, { profileId: p.id, name: p.name, source: p.target_url });
  },
  editAsset(b) {
    const a = S.detail.assets.find(x => String(x.id) === b.dataset.item);
    if (a && isTag(a.kind)) collectionDialog(b.dataset.id, a);
    else if (a) assetDialog(b.dataset.id, ['image', 'video'].includes(a.kind) ? a.kind : 'web', a);
  },
  async deleteAsset(b) {
    if (await confirmBox(t('confirmDeleteItem', { name: esc(b.dataset.name) }))) await sendCommand(b.dataset.id, 'delete_asset', { id: b.dataset.item });
  },
  freeze(b) { freezeDialog(b.dataset.id, b.dataset.item, b.dataset.col, b.dataset.name); },
  move(b) {
    const order = orderedAssets(S.detail).map(a => String(a.id));
    const i = order.indexOf(b.dataset.item), j = i + Number(b.dataset.dir);
    if (i < 0 || j < 0 || j >= order.length) return;
    [order[i], order[j]] = [order[j], order[i]];
    setOrder(order);
  },
  async saveOrder(b) {
    await sendCommand(b.dataset.id, 'reorder', { order: orderedAssets(S.detail).map(a => String(a.id)) });
    S.detail.assets = orderedAssets(S.detail);
    S.pendingOrder = null;
    render();
  },
  resetOrder() { S.pendingOrder = null; render(); },
  copyContent(b) {
    if (b.dataset.item) copyDialog(b.dataset.id, { assetIds: [b.dataset.item] });
    else if (b.dataset.col) copyDialog(b.dataset.id, { collectionIds: [b.dataset.col] });
    else copyDialog(b.dataset.id);
  },
  copyCollections(b) { copyDialog(b.dataset.id, { onlyCollections: true }); },
  addCollection(b) { collectionDialog(b.dataset.id); },
  editCollection(b) {
    const c = S.detail.collections.find(x => String(x.id) === b.dataset.col);
    if (c) collectionDialog(b.dataset.id, c);
  },
  async deleteCollection(b) {
    if (!await confirmBox(t('confirmDeleteItem', { name: esc(b.dataset.name) }))) return;
    await sendCommand(b.dataset.id, 'delete_collection', { id: b.dataset.col });
  },
  showResult(b) {
    const c = RESULTS[b.dataset.cid];
    if (!c) return;
    let text = c.result || '';
    try { text = JSON.stringify(JSON.parse(text), null, 2); } catch { /* plain text */ }
    modal({ title: `${esc(t('act_' + c.action))} · ${esc(c.device_name || c.device_id || '')}`, wide: true,
      body: `<div class="job-state"><span class="badge st-${c.state}">${t('state_' + c.state)}</span> ${dt(c.updated || c.created)}</div><pre class="code log">${esc(text)}</pre>` });
    const log = $('#modal .log');
    if (log) log.scrollTop = log.scrollHeight;
  },
  async cancelCmd(b) { await api(`/api/commands/${b.dataset.cid}/cancel`, { method: 'POST' }); toast(t('cancelled')); tick(); },
  async deleteDevice(b) {
    const d = dev(b.dataset.id);
    if (!await confirmBox(t('confirmDeleteDevice', { name: esc(d?.name || b.dataset.id) }))) return;
    await api('/api/devices/' + encodeURIComponent(b.dataset.id), { method: 'DELETE' });
    toast(t('deleted'));
    location.hash = '#/devices';
  },
  provision(b) { provisionDialog(b.dataset.host, b.dataset.name); },
  jobLog(b) { jobDialog(b.dataset.job); },
  orgEdit(b) { orgDialog(b.dataset.kind, b.dataset.name); },
  async orgDelete(b) {
    if (!await confirmBox(t('confirmDeleteOrg', { name: esc(b.dataset.name) }))) return;
    await api(`/api/org/${b.dataset.kind}/${encodeURIComponent(b.dataset.name)}`, { method: 'DELETE' });
    toast(t('deleted'));
    tick();
  },
  userEdit(b) { userDialog(b.dataset.uid ? Number(b.dataset.uid) : null); },
  async userDelete(b) {
    if (!await confirmBox(t('confirmDeleteUser', { name: esc(b.dataset.name) }))) return;
    await api('/api/users/' + b.dataset.uid, { method: 'DELETE' });
    toast(t('deleted'));
    render();
  },
};

// Offer only content types every selected node can receive.
function bulkKinds() {
  const sel = [...S.selected].map(dev).filter(Boolean);
  const all = cap => sel.every(d => (d.capabilities || {})[cap] !== false);
  const logins = sel.length && sel.every(d => loginSupport(d) === 'ok');
  return ['web', ...(all('upload') ? ['image', 'video'] : []), ...(all('add_grafana_tag') ? ['collection'] : []), ...(logins ? ['profile'] : [])];
}

function bulkCollection(ids) { collectionDialog(ids[0], null, ids); }

document.addEventListener('click', async e => {
  const b = e.target.closest('[data-do]');
  if (b) {
    e.preventDefault();
    e.stopPropagation();
    try { await ACTIONS_UI[b.dataset.do](b); } catch (err) { toast(errText(err), 'err'); }
    return;
  }
  const row = e.target.closest('tr[data-href]');
  if (row && !e.target.closest('input,button,a,label')) location.hash = row.dataset.href;
});

document.addEventListener('change', e => {
  const pick = e.target.closest('[data-pick]');
  if (pick) { pick.checked ? S.selected.add(pick.dataset.pick) : S.selected.delete(pick.dataset.pick); render(); }
});

document.addEventListener('submit', async e => {
  if (e.target.id === 'devForm') {
    e.preventDefault();
    try {
      await api('/api/devices/' + encodeURIComponent(e.target.dataset.id), { method: 'PATCH', json: Object.fromEntries(new FormData(e.target)) });
      toast(t('saved'));
      await tick();
    } catch (err) { toast(errText(err), 'err'); }
  }
  if (e.target.id === 'pwForm') {
    e.preventDefault();
    try {
      const r = await api('/api/me', { method: 'PATCH', json: Object.fromEntries(new FormData(e.target)) });
      if (r.token) { S.token = r.token; store.set('caracalToken', r.token); }
      e.target.reset();
      toast(t('passwordChanged'));
    } catch (err) { toast(errText(err), 'err'); }
  }
});

// ------------------------------------------------------------------ boot

async function tick() {
  if (!S.me || document.hidden) return;
  await refresh();
  const focus = document.activeElement;
  if (S.route.view === 'device' && S.route.tab === 'settings' && focus && focus.closest('#devForm')) return;
  render();
}

$('#loginForm').addEventListener('submit', doLogin);
$$('[data-lang]').forEach(b => { b.onclick = () => setLang(b.dataset.lang); });
$('#themeToggle').onclick = () => {
  S.theme = { auto: 'light', light: 'dark', dark: 'auto' }[S.theme];
  store.set('caracalTheme', S.theme);
  applyStatic();
};
$('#refreshBtn').onclick = () => tick();
$('#addDeviceBtn').onclick = () => provisionDialog();
$('#menuBtn').onclick = () => $('#sidebar').classList.toggle('open');
$('#userBtn').onclick = e => {
  e.stopPropagation();
  const d = $('#userDrop');
  d.innerHTML = `<div class="dd-head"><b>${esc(S.me.username)}</b><small>${t('role_' + S.me.role)}</small></div><a href="#/settings">${t('nav_settings')}</a><button id="logoutBtn">${t('signOut')}</button>`;
  d.hidden = !d.hidden;
  $('#logoutBtn').onclick = logout;
};
document.addEventListener('click', () => { $('#userDrop').hidden = true; });
window.addEventListener('hashchange', route);
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyStatic);

$('#setupForm').addEventListener('submit', doSetup);
applyStatic();
loadPublic().then(() => { if (S.token && !PUBLIC.setup_required) start(); else logout(); });
setInterval(tick, 5000);

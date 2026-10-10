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
  filters: { q: '', status: '', group: '', location: '', ...JSON.parse(store.get('caracalFilters', '{}') || '{}') },
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
  chev: 'M7 10l5 5 5-5',
  search: 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-4-4',
  more: 'M5 12h.01M12 12h.01M19 12h.01',
  bell: 'M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.9 1.9 0 0 0 3.4 0',
  check: 'M20 6 9 17l-5-5',
  keyboard: 'M3 6h18v12H3zM7 10h.01M11 10h.01M15 10h.01M7 14h10',
  agent: 'M12 3v12M7 10l5 5 5-5M5 21h14',
  ssh: 'M4 17l6-5-6-5M12 19h8',
  console: 'M3 4h18v16H3zM7 9l3 3-3 3M12 15h5',
  fullscreen: 'M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5',
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
  if (d.needs_attention) return `<span class="badge ${d.attention.some(a => a.level === 'critical') ? 'critical' : 'warning'}">${t('attentionShort')}</span>`;
  return `<span class="badge online">${t('online')}</span>`;
}
const dot = d => `<span class="sdot ${!d.online ? 'offline' : d.attention.some(a => a.level === 'critical') ? 'critical' : d.needs_attention ? 'warning' : 'online'}"></span>`;
const clock = ts => new Date(ts * 1000).toLocaleString(S.lang === 'cs' ? 'cs-CZ' : 'en-GB', new Date(ts * 1000).toDateString() === new Date().toDateString() ? { hour: '2-digit', minute: '2-digit' } : { day: 'numeric', month: 'numeric', hour: '2-digit', minute: '2-digit' });

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

const emptyState = (ic, title, text, action) => `<div class="empty-state">${icon(ic)}<b>${title}</b>${text ? `<p>${text}</p>` : ''}${action ? `<div>${action}</div>` : ''}</div>`;

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
  $('#paletteBtn').innerHTML = `${icon('search')}<span>${t('search')}</span><kbd>${isMac ? '⌘' : 'Ctrl'} K</kbd>`;
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
  closeConsole();
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
    closeConsole();
    if (r.view === 'device' || r.view === 'console' || (r.view === 'playlists' && r.id)) {
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
let lastMount = '', enterTimer = null;

function render(full = false) {
  const v = VIEWS[S.route.view] || VIEWS.overview;
  const root = $('#view');
  if (full || root.dataset.view !== S.route.view + S.route.id + S.route.tab) {
    root.dataset.view = S.route.view + S.route.id + S.route.tab;
    root.innerHTML = '';
    root._html = null;
    // a new page slides in as a whole, another tab of the same page only with its content
    const key = S.route.view === 'settings' ? 'settings' : S.route.view + '/' + S.route.id;
    root.classList.remove('enter', 'enter-tab');
    root.classList.add(key === lastMount ? 'enter-tab' : 'enter');
    lastMount = key;
    clearTimeout(enterTimer);
    enterTimer = setTimeout(() => root.classList.remove('enter', 'enter-tab'), 700);
    v.mount(root);
  }
  v.update && v.update(root);
  markSwitched(root);
}

// A screen whose content changed since the last refresh plays a short "channel switch".
const NOW_SEEN = {};
function markSwitched(root) {
  $$('.screen[data-dev]', root).forEach(el => {
    const k = el.dataset.dev + (el.classList.contains('screen-lg') ? ':lg' : ''), v = el.dataset.now;
    if (k in NOW_SEEN && NOW_SEEN[k] !== v) el.classList.add('switched');
    NOW_SEEN[k] = v;
  });
}

function setTitle(title, crumb = '', href = '') {
  $('#title').textContent = title;
  $('#crumb').innerHTML = crumb && href ? `<a href="${href}">${esc(crumb)}</a>` : esc(crumb);
  document.title = `${title} · CARACAL Fleet`;
}

// ---------- overview

VIEWS.overview = {
  mount(root) {
    setTitle(t('nav_overview'));
    root.innerHTML = `<div id="ovWelcome"></div><div class="stats" id="ovStats"></div>
      <div class="grid two" id="ovRow">
        <section class="card"><div class="card-head"><h2>${t('needsAttention')}</h2><a href="#/attention" class="link">${t('showAll')}</a></div><div id="ovAtt"></div></section>
        <section class="card"><div class="card-head"><h2>${t('recentEvents')}</h2><span class="muted">${t('last24h')}</span></div><div id="ovEvents"><div class="empty">…</div></div></section>
      </div>
      <section class="card wall" id="ovWall"><div class="card-head"><h2>${t('nowPlayingAll')}</h2><span class="muted" id="ovCount"></span></div><div class="tiles" id="ovTiles"></div></section>
      <section class="card" id="ovLocCard"><div class="card-head"><h2>${t('locations')}</h2><a href="#/organization" class="link">${t('manage')}</a></div><div id="ovLoc"></div></section>`;
    loadEvents(root, true);
  },
  update(root) {
    const ds = S.devices;
    const first = !ds.length;
    patch($('#ovWelcome', root), first ? welcomeCard() : '');
    for (const id of ['#ovStats', '#ovRow', '#ovWall']) $(id, root).hidden = first;
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
      : `<div class="all-good">${icon('check')}<b>${t('allGood')}</b></div>`);
    const locs = {};
    for (const d of ds) { const k = d.location || ''; (locs[k] ||= { total: 0, online: 0, att: 0 }); locs[k].total++; if (d.online) locs[k].online++; if (d.needs_attention) locs[k].att++; }
    // a single "unassigned" row says nothing; the card appears once locations are used
    $('#ovLocCard', root).hidden = first || !Object.keys(locs).some(Boolean);
    patch($('#ovLoc', root), Object.entries(locs).sort().map(([k, v]) => `<a class="loc-row" href="#/devices?location=${encodeURIComponent(k)}">
        ${icon('location')}<b>${esc(k || t('unassigned'))}</b><span class="muted">${v.online}/${v.total} ${t('online').toLowerCase()}</span>
        ${v.att ? `<span class="reason warning">${v.att} ${t('attentionShort').toLowerCase()}</span>` : ''}
        <div class="mini-prog"><i style="width:${v.total ? v.online / v.total * 100 : 0}%"></i></div></a>`).join(''));
    $('#ovCount', root).textContent = `${ds.length}`;
    patch($('#ovTiles', root), ds.map(d => `<a class="tile ${d.online ? '' : 'off'}" href="#/device/${encodeURIComponent(d.id)}">
        <div class="screen" data-dev="${esc(d.id)}" data-now="${esc(d.online ? d.current_name || '' : '')}">${d.online ? (d.current_kind ? kindIcon(d.current_kind) : '') + progress(d) : `<span class="no-signal">${t('offline')}</span>`}</div>
        <div class="tile-head">${dot(d)}<b>${esc(d.name)}</b>${d.muted_until ? `<span title="${esc(t('mutedUntil', { time: clock(d.muted_until) }))}">${icon('bell')}</span>` : ''}</div>
        <small class="muted">${esc([d.location, d.group].filter(Boolean).join(' / ') || d.id)}</small></a>`).join(''));
    loadEvents(root);
  },
};

// The latest events of the fleet (problems that started or ended), refreshed every half a minute.
let eventsAt = 0;
async function loadEvents(root, force = false) {
  if (!force && Date.now() - eventsAt < 30000) return;
  eventsAt = Date.now();
  const rows = await api('/api/events?hours=24&limit=8').catch(() => null);
  const el = $('#ovEvents', root);
  if (!rows || !el) return;
  patch(el, rows.length ? `<div class="event-list">${rows.map(e => eventRow(e, true)).join('')}</div>`
    : `<div class="all-good quiet">${icon('check')}<b>${t('noEvents')}</b></div>`);
}

function eventText(e) {
  const what = e.code === 'offline' ? t('offline') : attText({ code: e.code, detail: e.detail });
  return e.kind === 'end' ? (e.code === 'offline' ? t('eventOnline') : t('eventEnded', { what })) : what;
}
const eventRow = (e, withDevice) => `<a class="event ${e.kind === 'end' ? 'ok' : e.level}" href="#/device/${encodeURIComponent(e.device_id)}">
  <span class="sdot ${e.kind === 'end' ? 'online' : e.level === 'critical' ? 'critical' : 'warning'}"></span>
  <span>${withDevice ? `<b>${esc(e.device_name || e.device_id)}</b> ` : ''}${esc(eventText(e))}</span><small class="muted">${clock(e.ts)}</small></a>`;

// First start: nothing is connected yet, so the overview shows the ways to add a screen.
function welcomeCard() {
  const opt = (todo, ic, title, text) => `<button class="welcome-opt" data-do="${todo}">${icon(ic)}<b>${title}</b><span>${text}</span></button>`;
  return `<section class="card welcome"><div class="welcome-head">${icon('devices')}<div><h2>${t('welcomeTitle')}</h2><p class="muted">${t('welcomeText')}</p></div></div>
    ${can('manage') ? `<div class="welcome-opts">${opt('sdCard', 'download', t('sdCardTitle'), t('welcomeSd'))}${opt('discover', 'search', t('discoverTitle'), t('welcomeDiscover'))}${opt('provision', 'ssh', t('sshInstall'), t('welcomeSsh'))}</div>`
      : `<p class="muted">${t('welcomeViewer')}</p>`}</section>`;
}

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
        <span class="muted grow" id="fCount"></span><button class="btn sm ghost" id="fReset" hidden>${icon('x')}${t('resetFilters')}</button>
      </div>
      <div class="bulkbar" id="bulkBar" hidden></div>
      <div class="table-wrap"><table class="table devices-table">
        <thead><tr><th class="cb"><input type="checkbox" id="selAll"></th><th>${t('device')}</th><th>${t('locationGroup')}</th><th>${t('currentContent')}</th>
        <th>CPU</th><th>RAM</th><th>${t('disk')}</th><th>${t('temperature')}</th><th>${t('agent')}</th><th>${t('lastSeen')}</th><th class="more-cell"></th></tr></thead>
        <tbody id="devRows"></tbody></table></div></section>`;
    const upd = () => this.update(root);
    const set = (k, v) => { S.filters[k] = v; store.set('caracalFilters', JSON.stringify(S.filters)); upd(); };
    $('#fQ', root).oninput = e => set('q', e.target.value);
    $('#fStatus', root).onchange = e => set('status', e.target.value);
    $('#fGroup', root).onchange = e => set('group', e.target.value);
    $('#fLoc', root).onchange = e => set('location', e.target.value);
    $('#fReset', root).onclick = () => { S.filters = { q: '', status: '', group: '', location: '' }; store.set('caracalFilters', null); this.mount(root); this.update(root); };
    $('#selAll', root).onchange = e => { filteredDevices().forEach(d => e.target.checked ? S.selected.add(d.id) : S.selected.delete(d.id)); upd(); };
  },
  update(root) {
    const list = filteredDevices();
    $('#fCount', root).textContent = t('nOfM', { n: list.length, m: S.devices.length });
    $('#fReset', root).hidden = !Object.values(S.filters).some(Boolean);
    patch($('#devRows', root), list.map(d => `<tr data-href="#/device/${encodeURIComponent(d.id)}" class="${S.selected.has(d.id) ? 'sel' : ''}">
      <td class="cb"><input type="checkbox" data-pick="${esc(d.id)}" ${S.selected.has(d.id) ? 'checked' : ''}></td>
      <td><div class="dev-name">${dot(d)}<div><b>${esc(d.name)}${d.muted_until ? ` <span class="muted-ic" title="${esc(t('mutedUntil', { time: clock(d.muted_until) }))}">${icon('bell')}</span>` : ''}</b><small>${esc(d.id)} · ${esc(d.ip || '—')}</small></div></div></td>
      <td><small class="stack">${d.location ? `<span>${icon('location')}${esc(d.location)}</span>` : ''}${d.group ? `<span>${icon('group')}${esc(d.group)}</span>` : ''}${!d.location && !d.group ? '—' : ''}</small></td>
      <td class="content-cell">${progress(d)}${d.needs_attention ? `<small class="reason-inline">${esc(d.attention.filter(a => a.level !== 'info').map(attText).join(', '))}</small>` : ''}</td>
      <td>${d.online ? bar(d.cpu, 80, 95) : '—'}</td><td>${d.online ? bar(d.ram, 80, 90) : '—'}</td><td>${bar(d.disk, 85, 95)}</td>
      <td>${d.online ? bar(d.temp, 70, 80, 'C') : '—'}</td>
      <td><span class="${d.attention.some(a => a.code === 'agent_outdated') ? 'tag info' : 'muted'}">${esc(d.version || '—')}</span></td>
      <td class="muted nowrap">${d.online ? t('now') : ago(d.last_seen)}${d.pending_commands ? `<br><span class="tag">${t('pendingN', { n: d.pending_commands })}</span>` : ''}</td>
      <td class="more-cell"><button class="icon-btn row-more" data-more="${esc(d.id)}" title="${esc(t('moreActions'))}" aria-label="${esc(t('moreActions'))}">${icon('more')}</button></td></tr>`).join('')
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
  patch(el, `<b>${t('selectedN', { n })}</b>${actionBar()}<span class="grow"></span>
    <button class="btn sm ghost" data-do="clearSel">${icon('x')}${t('clearSelection')}</button>`);
}

// Actions of one device (d) or of the selected devices (d = null). One model feeds the action bar, the right-click
// menu and the command palette, so every place offers the same set in the same order.
function deviceActions(d = null) {
  const off = !!d && !d.online;
  // a feature that depends on the node's version is offered when the device (or any selected one) supports it;
  // for the selection the hub skips the others
  const sel = d ? [d] : [...S.selected].map(dev).filter(Boolean);
  const why = ok => (ok ? '' : t(d ? 'needNewerCaracal' : 'noneSelectedSupport'));
  const has = cap => sel.some(x => (x.capabilities || {})[cap]);
  const notify = sel.some(x => notifySupport(x) === 'ok');
  const notifyWhy = notify ? '' : d ? t(notifySupport(d) === 'node' ? 'notifyNeedCaracal' : 'notifyNeedAgent') : why(false);
  const sound = notify && has('notify_sound'), overlay = has('overlay'), admin = sel.some(adminSupport);
  // the look editor needs a node that reports its look (newer CARACAL) and an agent that passes it on
  const look = notify && sel.some(x => lookSupport(x));
  const ctl = can('control'), content = can('content'), manage = can('manage');
  const A = (todo, ic, label, o = {}) => ({ todo, ic, label, ...o, why: o.disabled ? o.why || t('offline') : '' });
  const cmd = d ? 'cmd' : 'bulk';
  const play = ctl ? [
    // resuming makes sense only for a frozen screen; the selection may contain one
    (!d || d.frozen) && A(cmd, 'play', t('act_unfreeze'), { act: 'unfreeze', disabled: off }),
    A(cmd, 'next', t('act_next'), { act: 'next', disabled: off }),
    A(cmd, 'restart', t('act_restart_player'), { act: 'restart_player', disabled: off }),
  ].filter(Boolean) : [];
  const menus = [
    { ic: 'playlists', label: t('menuContent'), sections: [
      [content && A('bulkAddContent', 'plus', t('addContent')), content && A('bulkCopy', 'copy', t('copyPlaylistHere'))],
      [content && A(d ? 'overlaySettings' : 'bulkOverlay', 'settings', t('overlayBar'), { disabled: !overlay, why: why(overlay) })],
    ] },
    { ic: 'attention', label: t('menuNotify'), sections: [
      [ctl && A(d ? 'notifySend' : 'bulkNotify', 'plus', t('notifySend'), { disabled: !notify, why: notifyWhy })],
      [content && A(d ? 'notifySettings' : 'bulkNotifySettings', 'settings', t('notifySettings'), { disabled: !notify, why: notifyWhy }),
        content && A(d ? 'notifySound' : 'bulkNotifySound', 'upload', t('notifySoundUpload'), { disabled: !sound, why: notifyWhy || why(sound) }),
        content && A(d ? 'addWatcher' : 'bulkWatcher', 'eye', t('addWatcher'), { disabled: !notify, why: notifyWhy })],
      [content && A('notifyStyle', 'image', t('notifyLook'), { disabled: !look, why: notifyWhy || why(look) })],
    ] },
    { ic: 'settings', label: t('menuManage'), sections: [
      [manage && A('muteAlerts', 'bell', d && d.muted_until ? t('unmuteAlerts') : t('muteAlerts')),
        manage && A('bulkAssign', 'group', t('assignGroupLocation')),
        manage && A(d ? 'nodeAdmin' : 'bulkNodeAdmin', 'lock', t('webAdmin'), { disabled: off || !admin, why: why(admin) || t('offline') }),
        manage && (!d || d.download_source) && A(d ? 'downloadSource' : 'bulkDownloadSource', 'download', t('downloadSource'))],
      [manage && A(cmd, 'agent', t('act_update_agent'), { act: 'update_agent', disabled: off }),
        manage && A('caracalUpdate', 'updates', t('act_update_caracal'), { disabled: off }),
        manage && d && d.runtime === 'host' && A('convertNodes', 'upload', t('convertToDocker'), { disabled: off })],
      [manage && d && A('provision', 'ssh', t('sshInstall'), { data: { host: d.ip || '', name: d.name } }),
        manage && d && A('', 'console', t('console'), { href: '#/console/' + encodeURIComponent(d.id) })],
      [manage && A('bulkSetHub', 'upload', t('act_set_hub')),
        ctl && A(cmd, 'power', t('act_reboot'), { act: 'reboot', cls: 'danger', disabled: off })],
    ] },
  ].map(m => ({ ...m, sections: m.sections.map(x => x.filter(Boolean)).filter(x => x.length) })).filter(m => m.sections.length);
  return { play, menus };
}

const actionAttrs = (a, id) => `data-do="${a.todo}"${a.act ? ` data-act="${a.act}"` : ''}${id ? ` data-id="${esc(id)}"` : ''}`
  + Object.entries(a.data || {}).map(([k, v]) => ` data-${k}="${esc(v)}"`).join('');
function actionItem(a, id, cls = 'mi', compact = false) {
  const inner = `${icon(a.ic)}<span>${a.label}${a.disabled && !compact ? `<small>${t(a.why && a.why !== t('offline') ? 'needsUpdate' : 'offline')}</small>` : ''}</span>`;
  if (a.href) return `<a class="${cls}" role="menuitem" href="${a.href}">${inner}</a>`;
  return `<button class="${cls} ${a.cls || ''}" role="menuitem" ${actionAttrs(a, id)} ${a.disabled ? `disabled title="${esc(a.why)}"` : ''}>${inner}</button>`;
}
const menuHtml = (sections, id) => sections.map(x => x.map(a => actionItem(a, id)).join('')).join('<hr>');

// playback as buttons, everything else in the menus Content, Notifications and Manage
function actionBar(d = null) {
  const { play, menus } = deviceActions(d), id = d && d.id;
  return `<div class="action-bar">
    ${play.length ? `<div class="action-group">${play.map(a => `<button class="btn" ${actionAttrs(a, id)} title="${esc(a.label)}" ${a.disabled ? 'disabled' : ''}>${icon(a.ic)}<span>${a.label}</span></button>`).join('')}</div>` : ''}
    ${menus.map(m => `<div class="menu"><button class="btn" data-menu aria-haspopup="menu" aria-expanded="false">${icon(m.ic)}<span>${m.label}</span>${icon('chev', 'chev')}</button>
      <div class="menu-pop" role="menu" hidden>${menuHtml(m.sections, id)}</div></div>`).join('')}</div>`;
}

// Right click (or the … button) on a device: everything it can do, without opening it first.
// On a selected row of several, the menu acts on the whole selection.
function openContextMenu(id, x, y) {
  const d = dev(id);
  if (!d) return;
  const bulk = S.route.view === 'devices' && S.selected.size > 1 && S.selected.has(id);
  const { play, menus } = deviceActions(bulk ? null : d), did = bulk ? null : d.id;
  let pop = $('#ctxMenu');
  if (!pop) {
    pop = document.createElement('div');
    pop.id = 'ctxMenu';
    pop.className = 'menu-pop ctx';
    pop.setAttribute('role', 'menu');
    document.body.append(pop);
  }
  pop.innerHTML = `<div class="ctx-head">${bulk ? `<b>${t('selectedN', { n: S.selected.size })}</b>` : `${dot(d)}<b>${esc(d.name)}</b>`}</div>`
    + [bulk ? '' : `<a class="mi" role="menuitem" href="#/device/${encodeURIComponent(d.id)}">${icon('devices')}<span>${t('open')}</span></a>`,
      play.map(a => actionItem(a, did, 'mi', true)).join(''),
      ...menus.map(m => `<div class="ctx-label">${m.label}</div>${m.sections.map(x => x.map(a => actionItem(a, did, 'mi', true)).join('')).join('<hr>')}`)].filter(Boolean).join('<hr>');
  closeMenus();
  pop.hidden = false;
  menuOpenedAt = performance.now();
  const w = pop.offsetWidth, h = pop.offsetHeight;
  pop.style.left = Math.max(8, Math.min(x, innerWidth - w - 8)) + 'px';
  pop.style.top = Math.max(8, Math.min(y, innerHeight - h - 8)) + 'px';
  menuItems(pop)[0]?.focus({ preventScroll: true });
}
const deviceIdOf = el => decodeURIComponent((el.getAttribute('href') || el.dataset.href || '').split('/')[2] || '');
document.addEventListener('contextmenu', e => {
  const el = e.target.closest('tr[data-href^="#/device/"], a.tile');
  if (!el || !S.me) return;
  e.preventDefault();
  openContextMenu(deviceIdOf(el), e.clientX, e.clientY);
});

// ---------- command palette (Ctrl/⌘ K): jump to a device or a page, or run an action, all from the keyboard

const norm = v => String(v ?? '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

function paletteResults(q) {
  const words = norm(q).split(/\s+/).filter(Boolean);
  const hit = text => words.every(w => text.includes(w));
  const groups = [];
  const add = (title, items, max) => { const list = items.filter(x => !words.length || hit(x.text)).slice(0, max); if (list.length) groups.push({ title, list }); };
  const actionsOf = (d, id, extra) => {
    const { play, menus } = deviceActions(d);
    return [...play, ...menus.flatMap(m => m.sections.flat())].filter(a => !a.disabled)
      .map(a => ({ html: actionItem(a, id, 'pi').replace('<span>', `<span>${extra ? `<small class="pi-ctx">${esc(extra)}</small>` : ''}`), text: norm(a.label + ' ' + (extra || '')) }));
  };
  const here = S.route.view === 'device' && S.detail ? dev(S.detail.id) : null;
  if (here) add(t('paletteThisDevice', { name: here.name }), actionsOf(here, here.id, ''), words.length ? 6 : 8);
  if (S.selected.size && S.route.view === 'devices') add(t('selectedN', { n: S.selected.size }), actionsOf(null, null, ''), words.length ? 6 : 8);
  add(t('nav_devices'), S.devices.map(d => ({
    html: `<a class="pi" href="#/device/${encodeURIComponent(d.id)}">${dot(d)}<span>${esc(d.name)}<small>${esc([d.location, d.group, d.ip].filter(Boolean).join(' / ') || d.id)}</small></span></a>`,
    text: norm([d.name, d.id, d.ip, d.location, d.group, d.current_name].join(' ')),
  })), words.length ? 6 : 5);
  if (words.length) add(t('paletteActions'), S.devices.filter(d => d !== here).flatMap(d => actionsOf(d, d.id, d.name)), 8);
  add(t('palettePages'), NAV.filter(([, p]) => can(p)).map(([v]) => ({ html: `<a class="pi" href="#/${v}">${icon(v)}<span>${t('nav_' + v)}</span></a>`, text: norm(t('nav_' + v)) })), words.length ? 4 : 11);
  add(t('paletteGeneral'), [
    can('manage') && { html: `<button class="pi" data-do="provision">${icon('plus')}<span>${t('addDevice')}</span></button>`, text: norm(t('addDevice')) },
    { html: `<button class="pi" data-do="toggleTheme">${icon('moon')}<span>${t('paletteTheme')}</span></button>`, text: norm(t('paletteTheme') + ' dark light') },
    { html: `<button class="pi" data-do="switchLang">${icon('web')}<span>${t('paletteLang')}</span></button>`, text: norm(t('paletteLang') + ' language jazyk') },
    { html: `<button class="pi" data-do="showShortcuts">${icon('keyboard')}<span>${t('shortcuts')}</span></button>`, text: norm(t('shortcuts') + ' keyboard klavesnice') },
    { html: `<button class="pi" data-do="signOut">${icon('back')}<span>${t('signOut')}</span></button>`, text: norm(t('signOut')) },
  ].filter(Boolean), 4);
  return groups;
}

function openPalette() {
  if (!S.me) return;
  let box = $('#palette');
  if (!box) {
    box = document.createElement('div');
    box.id = 'palette';
    box.className = 'palette';
    document.body.append(box);
    box.addEventListener('mousedown', e => { if (e.target === box) closePalette(); });
    box.addEventListener('mousemove', e => { const it = e.target.closest('.pi'); if (it && !it.disabled) paletteActive(it); });
  }
  box.innerHTML = `<div class="palette-box" role="dialog" aria-label="${esc(t('search'))}">
    <label class="palette-search">${icon('search')}<input id="paletteQ" placeholder="${esc(t('paletteHint'))}" autocomplete="off" spellcheck="false"><kbd>esc</kbd></label>
    <div class="palette-list" id="paletteList" role="listbox"></div>
    <div class="palette-foot"><span><kbd>↑</kbd><kbd>↓</kbd> ${t('paletteMove')}</span><span><kbd>↵</kbd> ${t('paletteRun')}</span></div></div>`;
  box.hidden = false;
  closeMenus();
  const q = $('#paletteQ', box);
  q.oninput = () => paletteRender(q.value);
  q.onkeydown = e => {
    const items = $$('.pi', box), i = items.indexOf($('.pi.active', box));
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); paletteActive(items[(i + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length], true); }
    else if (e.key === 'Enter') { e.preventDefault(); $('.pi.active', box)?.click(); }
    else if (e.key === 'Escape') { e.preventDefault(); closePalette(); }
  };
  paletteRender('');
  q.focus();
}
function paletteRender(q) {
  const groups = paletteResults(q);
  $('#paletteList').innerHTML = groups.map(g => `<div class="pg"><div class="pg-title">${esc(g.title)}</div>${g.list.map(x => x.html).join('')}</div>`).join('')
    || `<div class="palette-empty">${t('paletteEmpty')}</div>`;
  paletteActive($('#paletteList .pi'));
}
function paletteActive(it, scroll = false) {
  $$('#palette .pi.active').forEach(x => x.classList.remove('active'));
  if (!it) return;
  it.classList.add('active');
  if (scroll) it.scrollIntoView({ block: 'nearest' });
}
function closePalette() { const box = $('#palette'); if (box) box.hidden = true; }
document.addEventListener('keydown', e => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); $('#palette:not([hidden])') ? closePalette() : openPalette(); }
  else if (e.key === '/' && !e.target.closest('input, textarea, select, [contenteditable]') && !$('#modal').open) { e.preventDefault(); openPalette(); }
  else if (e.key === '?' && S.me && !e.target.closest('input, textarea, select, [contenteditable]') && !$('#modal').open) { e.preventDefault(); ACTIONS_UI.showShortcuts(); }
});

// the large page title shrinks into the toolbar once the page scrolls
window.addEventListener('scroll', () => { $('.topbar')?.classList.toggle('scrolled', scrollY > 6); }, { passive: true });

// The thumb of the segmented tabs slides from the tab it was on.
let thumbFrom = null;
function slideThumb(nav) {
  const a = nav && $('a.active', nav);
  if (!a) return;
  let th = $('.tab-thumb', nav);
  if (!th) {
    th = document.createElement('i');
    th.className = 'tab-thumb';
    nav.prepend(th);
    if (thumbFrom) { th.style.transition = 'none'; th.style.width = thumbFrom.w + 'px'; th.style.transform = `translateX(${thumbFrom.x}px)`; th.getBoundingClientRect(); th.style.transition = ''; }
  }
  thumbFrom = { x: a.offsetLeft, w: a.offsetWidth };
  th.style.width = thumbFrom.w + 'px';
  th.style.transform = `translateX(${thumbFrom.x}px)`;
}

const menuItems = pop => $$('.mi:not(:disabled)', pop);
function closeMenus(except = null) {
  $$('.menu-pop').forEach(p => {
    if (p === except || p.hidden) return;
    p.hidden = true;
    const b = p.previousElementSibling;
    if (b && b.hasAttribute('data-menu')) b.setAttribute('aria-expanded', 'false');
  });
}
function toggleMenu(btn) {
  const pop = btn.nextElementSibling;
  closeMenus(pop);
  if (!pop.hidden) { pop.hidden = true; btn.setAttribute('aria-expanded', 'false'); return; }
  pop.hidden = false;
  menuOpenedAt = performance.now();
  btn.setAttribute('aria-expanded', 'true');
  // fixed position, so the menu is not cut off by a scrolling or clipped panel
  const r = btn.getBoundingClientRect(), w = pop.offsetWidth, h = pop.offsetHeight;
  pop.style.left = Math.max(8, Math.min(r.left, innerWidth - w - 8)) + 'px';
  pop.style.top = (r.bottom + 4 + h > innerHeight && r.top - 4 - h > 0 ? r.top - 4 - h : r.bottom + 4) + 'px';
  menuItems(pop)[0]?.focus();
}
document.addEventListener('keydown', e => {
  const pop = e.target.closest && e.target.closest('.menu-pop');
  if (!pop) return;
  const items = menuItems(pop), i = items.indexOf(e.target);
  if (e.key === 'Escape') { const b = pop.previousElementSibling; closeMenus(); if (b && b.hasAttribute('data-menu')) b.focus(); }
  else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); items[(i + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length]?.focus(); }
  else if (e.key === 'Tab') closeMenus();
});
window.addEventListener('resize', () => closeMenus());
// scrolling moves a menu's anchor away, so it closes - except right after opening (the click may scroll its row into view)
let menuOpenedAt = 0;
window.addEventListener('scroll', e => {
  if (performance.now() - menuOpenedAt < 400 || (e.target.closest && e.target.closest('.menu-pop'))) return;
  closeMenus();
}, true);

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
  if (a.code === 'admin_missing' && can('manage') && d.online && adminSupport(d)) return `<button class="btn sm" data-do="nodeAdmin" data-id="${esc(d.id)}">${t('webAdminCreate')}</button>`;
  return inDetail ? '' : `<a class="btn sm" href="#/device/${encodeURIComponent(d.id)}">${t('open')}</a>`;
}

// ---------- device detail

const DEVICE_TABS = ['overview', 'playlist', 'collections', 'logins', 'notify', 'history', 'settings'];
const TAB_COUNTS = { playlist: 'playlist_count', collections: 'collection_count', logins: 'profile_count', notify: 'watcher_count' };

function deviceTabs(d, tab) {
  return DEVICE_TABS.filter(x => x !== 'settings' || can('manage')).map(x => `<a href="#/device/${encodeURIComponent(d.id)}/${x}" class="${x === tab ? 'active' : ''}">${t('tab_' + x)}${TAB_COUNTS[x] && d[TAB_COUNTS[x]] ? ` <span class="count">${d[TAB_COUNTS[x]]}</span>` : ''}</a>`).join('');
}

VIEWS.device = {
  mount(root) {
    const d = S.detail;
    if (!d) { setTitle(t('device')); root.innerHTML = `<div class="card empty">${t('err_device_not_found')}</div>`; return; }
    setTitle(d.name, t('nav_devices'), '#/devices');
    root.innerHTML = `<section class="card device-hero"><div id="devHero"></div><div id="devActions"></div></section>
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
      ${d.caracal_version || d.runtime ? `<span>CARACAL ${esc(d.caracal_version || '?')} · ${runtimeLabel(d)}</span>` : ''}
      ${d.muted_until ? `<span class="chip-muted">${icon('bell')}${t('mutedUntil', { time: clock(d.muted_until) })}</span>` : ''}<span>${t('agent')} ${esc(d.version || '—')}</span>${d.model ? `<span>${esc(d.model)}</span>` : ''}<span>${d.online ? t('uptime') + ' ' + dur(d.uptime) : t('lastSeen') + ' ' + ago(d.last_seen)}</span></div></div>`);
    patch($('#devActions', root), actionBar(d));
    patch($('#devTabs', root), deviceTabs(d, tab));
    slideThumb($('#devTabs', root));
    patch($('#devAtt', root), d.attention.length ? `<div class="alert-list">${d.attention.map(a => `<div class="alert ${a.level}"><b>${esc(attText(a))}</b><span>${esc(t('hint_' + a.code))}</span>${quickFix(d, a, true)}</div>`).join('')}</div>` : '');
    const el = $('#devTab', root);
    if (tab === 'overview') patch(el, deviceOverview(d));
    else if (tab === 'playlist') renderPlaylist(el, d);
    else if (tab === 'collections') patch(el, renderCollections(d));
    else if (tab === 'logins') patch(el, renderProfiles(d));
    else if (tab === 'notify') patch(el, renderNotifications(d));
    else if (tab === 'history') patch(el, deviceHistory(d));
    else if (tab === 'settings') { if (!el._html) patch(el, deviceSettings(d)); }
  },
};

// ---------- SSH console (#/console/<device id>): the hub opens an SSH shell on the node and relays it over a WebSocket

const CONSOLE = { id: '', ws: null, term: null, ro: null, box: null };
let xtermLoading = null;

function loadXterm() {
  // xterm.js is loaded only when a console is opened
  if (!xtermLoading) {
    const v = encodeURIComponent(S.me?.hub_version || '');
    const css = document.createElement('link');
    css.rel = 'stylesheet';
    css.href = '/static/vendor/xterm/xterm.css?v=' + v;
    document.head.append(css);
    const script = src => new Promise((resolve, reject) => {
      const el = document.createElement('script');
      el.src = src + '?v=' + v;
      el.onload = resolve;
      el.onerror = () => { el.remove(); reject(new Error('console_load_failed')); };
      document.head.append(el);
    });
    xtermLoading = script('/static/vendor/xterm/xterm.js').then(() => script('/static/vendor/xterm/addon-fit.js'))
      .catch(e => { xtermLoading = null; throw e; });
  }
  return xtermLoading;
}

function closeConsole() {
  const c = CONSOLE;
  if (c.ws) { c.ws.onclose = null; c.ws.close(); }
  if (c.ro) c.ro.disconnect();
  if (c.term) c.term.dispose();
  if (c.box) c.box.remove();
  Object.assign(c, { id: '', ws: null, term: null, ro: null, box: null });
}

VIEWS.console = {
  mount(root) {
    const d = S.detail;
    if (!d || !can('manage')) { setTitle(t('console')); root.innerHTML = `<div class="card empty">${t(d ? 'err_forbidden' : 'err_device_not_found')}</div>`; return; }
    setTitle(t('console'), d.name, '#/device/' + encodeURIComponent(d.id));
    root.innerHTML = `<form class="card console-login" id="conForm" novalidate><div class="form">
        <div class="row2"><label>${t('hostIp')}<input name="host" value="${esc(d.ip)}" required></label><label>${t('sshPort')}<input name="port" type="number" min="1" max="65535" value="22"></label></div>
        <div class="row2"><label>${t('sshUser')}<input name="username" value="${esc(store.get('caracalSshUser', 'pi'))}" autocomplete="off" required></label><label>${t('sshPassword')}<input name="password" type="password" autocomplete="off"></label></div>
        <details><summary>${t('sshKeyAuth')}</summary><label>${t('privateKey')}<textarea name="private_key" rows="4" placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"></textarea></label><label>${t('passphrase')}<input name="passphrase" type="password" autocomplete="off"></label></details>
        <label class="check"><input type="checkbox" name="forget_host_key">${t('forgetHostKey')}</label>
        <p class="muted">${t('consoleHint')}</p>
        <div class="form-error" id="conErr"></div>
        <div class="form-actions start"><button class="btn primary" id="conConnect">${icon('console')}${t('consoleConnect')}</button></div>
      </div></form>`;
    const form = $('#conForm', root);
    form.onsubmit = async e => {
      e.preventDefault();
      if (!form.checkValidity()) { form.reportValidity(); return; }
      const data = Object.fromEntries(new FormData(form));
      $('#conErr', form).textContent = '';
      if (!data.password && !data.private_key) { $('#conErr', form).textContent = errText(new Error('missing_fields')); return; }
      const btn = $('#conConnect', form);
      btn.disabled = true;
      try { await openConsole(d, data, root); } catch (err) { $('#conErr', form).textContent = errText(err); } finally { btn.disabled = false; }
    };
    if (CONSOLE.box && CONSOLE.id === d.id) {   // a re-render (e.g. language switch) keeps the running session
      root.append(CONSOLE.box);
      form.hidden = !!CONSOLE.ws && CONSOLE.ws.readyState <= 1;
    }
  },
  update() { /* the terminal keeps its own state, periodic refreshes must not touch it */ },
};

async function openConsole(d, data, root) {
  await loadXterm();
  closeConsole();
  store.set('caracalSshUser', data.username.trim());
  const form = $('#conForm', root);
  const box = document.createElement('section');
  box.className = 'console';
  box.innerHTML = `<div class="console-bar"><span class="badge" id="conState"></span><b id="conTarget">${esc(data.username.trim() + '@' + data.host.trim())}</b><span class="grow"></span>
    <button type="button" class="btn sm ghost" id="conFull">${icon('fullscreen')}${t('fullscreen')}</button>
    <button type="button" class="btn sm danger" id="conClose">${icon('x')}${t('consoleDisconnect')}</button></div><div class="console-term"></div>`;
  root.append(box);
  const term = new Terminal({
    cursorBlink: true, scrollback: 5000, fontSize: 14,
    fontFamily: 'ui-monospace, "Cascadia Code", Consolas, "DejaVu Sans Mono", monospace',
    theme: { background: '#0d1117', foreground: '#e6edf3', cursor: '#e6edf3', selectionBackground: '#3b5070' },
  });
  const fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open($('.console-term', box));
  fit.fit();
  const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/devices/${encodeURIComponent(d.id)}/console`);
  ws.binaryType = 'arraybuffer';
  const ro = new ResizeObserver(() => { try { fit.fit(); } catch { /* not visible */ } });
  ro.observe($('.console-term', box));
  Object.assign(CONSOLE, { id: d.id, ws, term, ro, box });

  const state = (key, cls) => { const el = $('#conState', box); el.className = 'badge ' + cls; el.textContent = t(key); };
  const ended = (key, cls, msg) => {
    state(key, cls);
    if (msg) term.write(`\r\n\x1b[33m${msg}\x1b[0m\r\n`);
    $('#conClose', box).hidden = true;
    form.hidden = false;
  };
  state('consoleConnecting', 'st-queued');
  form.hidden = true;
  ws.onopen = () => ws.send(JSON.stringify({
    token: S.token, host: data.host.trim(), port: Number(data.port || 22), username: data.username.trim(),
    password: data.password, private_key: data.private_key || '', passphrase: data.passphrase || '',
    forget_host_key: !!data.forget_host_key, cols: term.cols, rows: term.rows,
  }));
  ws.onmessage = e => {
    if (typeof e.data !== 'string') { term.write(new Uint8Array(e.data)); return; }
    const m = JSON.parse(e.data);
    if (m.type === 'connected') {
      state('consoleConnected', 'st-completed');
      $('#conTarget', box).textContent = m.target;
      form.forget_host_key.checked = false;
      term.focus();
    } else if (m.type === 'error') {
      ws.onclose = null;
      ended('consoleFailed', 'st-failed', errText(new Error(m.code)) + (m.detail ? ` (${m.detail})` : ''));
    } else if (m.type === 'closed') {
      ws.onclose = null;
      ended('consoleClosed', 'st-cancelled', t('consoleEnd_' + m.reason));
    }
  };
  ws.onclose = () => ended('consoleClosed', 'st-cancelled', t('consoleEnd_error'));
  const enc = new TextEncoder();
  term.onData(s => { if (ws.readyState === 1) ws.send(enc.encode(s)); });
  term.onBinary(s => { if (ws.readyState === 1) ws.send(Uint8Array.from(s, c => c.charCodeAt(0) & 255)); });
  term.onResize(({ cols, rows }) => { if (ws.readyState === 1) ws.send(JSON.stringify({ type: 'resize', cols, rows })); });
  $('#conClose', box).onclick = () => { ws.onclose = null; ws.close(); ended('consoleClosed', 'st-cancelled', t('consoleEnd_disconnect')); };
  $('#conFull', box).onclick = () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else box.requestFullscreen().then(() => term.focus()).catch(() => {});
  };
}

function deviceOverview(d) {
  loadMetrics(d);
  const m = METRICS.id === d.id && METRICS.data;
  const metric = (label, html, idx, lo, hi, unit) => `<div class="metric"><span>${label}</span>${html}${m ? sparkline(m, idx, lo, hi, unit) : ''}</div>`;
  const total = num(d.duration), rem = num(d.remaining);
  return `<div class="grid metrics4">
      ${metric('CPU', bar(d.online ? d.cpu : null, 80, 95), 1, 0, 100, '%')}${metric('RAM', bar(d.online ? d.ram : null, 80, 90), 2, 0, 100, '%')}
      ${metric(t('disk'), bar(d.disk, 85, 95), 3, 0, 100, '%')}${metric(t('temperature'), bar(d.online ? d.temp : null, 70, 80, 'C'), 4, 20, 90, '°C')}</div>
    ${m ? uptimeCard(m) : ''}
    <div class="grid two">
      <section class="card"><div class="card-head"><h2>${t('nowPlaying')}</h2>${d.frozen ? `<span class="tag frozen">${icon('snow')}${t('frozen')}</span>` : ''}</div>
        <div class="screen screen-lg ${d.online ? '' : 'off'}" data-dev="${esc(d.id)}" data-now="${esc(d.online ? d.current_name || '' : '')}">${!d.online ? `<span class="no-signal">${t('offline')}</span>`
          : `${d.current_kind ? kindIcon(d.current_kind) : ''}<div class="now"><div class="now-name"><span>${esc(d.current_name || t('nothingPlaying'))}</span></div>
          ${!d.frozen && total ? `<div class="prog"><i style="width:${rem !== null ? Math.max(0, Math.min(100, (1 - rem / total) * 100)) : 0}%"></i></div>` : ''}</div>`}</div>
        ${!d.online ? '' : d.frozen ? `<p class="muted">${d.frozen_until ? t('frozenUntil', { time: dt(d.frozen_until) }) : t('frozenIndef')}</p>`
          : total ? `<p class="muted">${t('remaining')}: ${dur(rem)} / ${dur(total)}</p>` : ''}
        <p class="muted">${t('playlistItems')}: ${d.playlist_count} · ${t('collections')}: ${d.collection_count}</p></section>
      <section class="card"><div class="card-head"><h2>${t('info')}</h2></div><dl class="kv">
        <dt>${t('hostname')}</dt><dd>${esc(d.hostname || '—')}</dd><dt>${t('model')}</dt><dd>${esc(d.model || '—')}</dd>
        <dt>IP</dt><dd>${esc(d.ip || '—')}</dd><dt>${t('uptime')}</dt><dd>${d.online ? dur(d.uptime) : '—'}</dd>
        <dt>${t('lastSeen')}</dt><dd>${dt(d.last_seen)}</dd><dt>${t('agent')}</dt><dd>${esc(d.version || '—')} (${t('latest')} ${esc(S.agentVersion)})</dd>
        <dt>${t('localApi')}</dt><dd>${d.api_ok === false ? `<span class="tag critical">${t('unavailable')}</span>` : d.api_ok ? `<span class="tag ok">OK</span>` : '—'}</dd>
        <dt>${t('downloadSource')}</dt><dd>${d.download_source ? t('downloadSource_' + d.download_source) : '—'}${can('manage') && d.download_source ? ` <button class="btn sm ghost" data-do="downloadSource" data-id="${esc(d.id)}">${icon('edit')}${t('edit')}</button>` : ''}</dd>
        <dt>${t('pendingCommands')}</dt><dd>${d.pending_commands}</dd>${d.notes ? `<dt>${t('notes')}</dt><dd>${esc(d.notes)}</dd>` : ''}</dl></section>
    </div>${nodeAdminCard(d)}`;
}

// The node's own web administration (CARACAL on port 8080): its administrator and the countdown on the TV.
const adminSupport = d => 'admin' in (d.capabilities || {});
const nodeAdminUrl = d => d.ip ? `http://${d.ip.includes(':') ? `[${d.ip}]` : d.ip}:8080` : '';

function nodeAdminCard(d) {
  if (!adminSupport(d) && !d.admin && !d.overlay) return '';
  const a = d.admin, o = d.overlay, url = nodeAdminUrl(d);
  return `<section class="card"><div class="card-head"><h2>${t('webAdmin')}</h2>${url ? `<a class="btn sm ghost" href="${esc(url)}" target="_blank" rel="noopener">${icon('web')}${t('webAdminOpen')}</a>` : ''}</div>
    <dl class="kv"><dt>${t('webAdminAccount')}</dt><dd>${!a ? '—' : a.configured ? `<b>${esc(a.username)}</b>` : `<span class="tag critical">${t('webAdminMissing')}</span>`}
        ${can('manage') && adminSupport(d) ? ` <button class="btn sm ghost" data-do="nodeAdmin" data-id="${esc(d.id)}" ${d.online ? '' : 'disabled'}>${icon('lock')}${t(a && a.configured ? 'webAdminChange' : 'webAdminCreate')}</button>` : ''}</dd>
      <dt>${t('overlayBar')}</dt><dd>${o ? `${t(o.enabled ? 'overlayOn' : 'overlayOff')}${o.enabled ? ` · ${esc(o.size)} px` : ''}` : '—'}
        ${can('content') && (d.capabilities || {}).overlay ? ` <button class="btn sm ghost" data-do="overlaySettings" data-id="${esc(d.id)}">${icon('edit')}${t('edit')}</button>` : ''}</dd></dl>
    <p class="muted">${t('webAdminHint')}</p></section>`;
}

// Name and password of the node's web administrator: never stored in Fleet, removed from the command once delivered.
const adminFields = (prefix = '', required = true) => `<div class="row2"><label>${t('webAdminUser')}<input name="${prefix}username" autocomplete="off" spellcheck="false" maxlength="64" pattern="\\S+" ${required ? 'required' : ''} placeholder="admin"></label>
  <label>${t('webAdminPassword')}<input name="${prefix}password" type="password" autocomplete="new-password" minlength="10" maxlength="200" ${required ? 'required' : ''}></label></div>
  <label>${t('webAdminPassword2')}<input name="${prefix}password2" type="password" autocomplete="new-password" minlength="10" maxlength="200" ${required ? 'required' : ''}></label>`;

function adminFromForm(data, prefix = '') {
  const username = (data[prefix + 'username'] || '').trim(), password = data[prefix + 'password'] || '';
  if (!username && !password) return null;
  if (!username || /\s/.test(username)) throw new Error('invalid_admin_user');
  if (password.length < 10) throw new Error('admin_password_short');
  if (password !== data[prefix + 'password2']) throw new Error('admin_password_mismatch');
  return { username, password };
}

function nodeAdminDialog(ids) {
  const one = ids.length === 1 ? dev(ids[0]) : null;
  modal({
    title: t('webAdmin'), submit: t('save'),
    body: `<div class="form"><p class="muted">${t(one && one.admin && one.admin.configured ? 'webAdminChangeHint' : 'webAdminCreateHint')}</p>
      ${ids.length > 1 ? `<p class="muted">${t('webAdminToSelected', { n: ids.length })}</p>` : ''}${adminFields()}
      <div class="note info">${icon('lock')}${t('webAdminSecurity')}</div></div>`,
    onOpen: form => { if (one && one.admin && one.admin.username) form.username.value = one.admin.username; },
    onSubmit: data => {
      const payload = adminFromForm(data);
      return ids.length === 1 ? sendCommand(ids[0], 'set_admin', payload) : sendBulk(ids, 'set_admin', payload);
    },
  });
}

function overlayDialog(ids, current) {
  const o = { enabled: true, size: 16, ...(current || {}) };
  modal({
    title: t('overlayBar'),
    body: `<div class="form"><p class="muted">${t('overlayHint')}</p>${ids.length > 1 ? `<p class="muted">${t('notifySettingsToSelected', { n: ids.length })}</p>` : ''}
      <label class="check"><input type="checkbox" name="enabled" ${o.enabled ? 'checked' : ''}>${t('overlayEnabled')}</label>
      <label>${t('overlaySize')}<input name="size" type="number" min="4" max="200" value="${esc(o.size)}" required></label></div>`,
    onSubmit: data => {
      const payload = { enabled: !!data.enabled, size: Number(data.size) };
      return ids.length === 1 ? sendCommand(ids[0], 'overlay_settings', payload) : sendBulk(ids, 'overlay_settings', payload);
    },
  });
}

// Commands whose result a dialog shows: trying a watcher or a Grafana tag, the notification log.
async function commandResult(id, action, payload = {}, timeout = 90000) {
  const r = await api(`/api/devices/${encodeURIComponent(id)}/commands`, { method: 'POST', json: { action, payload } });
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    await new Promise(res => setTimeout(res, 1000));
    const c = await api('/api/commands/' + r.id);
    if (c.state === 'completed') { try { return JSON.parse(c.result || '{}'); } catch { return {}; } }
    if (!['queued', 'delivered', 'running'].includes(c.state)) {
      // the node's own message (e.g. {"detail": "..."} of its API) rather than the whole HTTP error
      const m = /"detail":\s*"((?:[^"\\]|\\.)*)"/.exec(c.result || '');
      throw new Error(m ? JSON.parse('"' + m[1] + '"') : (c.result || t('state_' + c.state)));
    }
  }
  throw new Error('result_timeout');
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
  else if (p.title || p.message) parts.push(p.title || p.message);
  if (p.level && /^(info|success|warning|critical)$/.test(p.level)) parts.push(t('notifyLevel_' + p.level));
  if (p.filename && !p.name) parts.push(p.filename);
  if (p.reset) parts.push(t('notifySoundModeReset'));
  if (p.source === 'internet' || p.source === 'fleet') parts.push(t('downloadSource_' + p.source));
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

const shortcutsHtml = () => {
  const mod = isMac ? '⌘' : 'Ctrl';
  const row = (keys, text) => `<dt>${keys.map(k => `<kbd>${k}</kbd>`).join('')}</dt><dd>${text}</dd>`;
  return `<dl class="keys">${row([mod, 'K'], t('key_palette'))}${row(['/'], t('key_palette'))}${row(['?'], t('key_help'))}
    ${row(['↑', '↓', '↵'], t('key_menu'))}${row(['Esc'], t('key_close'))}${row([t('key_rightClick')], t('key_context'))}</dl>`;
};

// ---------- metric history and the timeline of a device

const METRICS = { id: '', range: 24, at: 0, data: null, busy: false };
function loadMetrics(d) {
  if (METRICS.busy || (METRICS.id === d.id && METRICS.data && Date.now() - METRICS.at < 60000)) return;
  METRICS.busy = true;
  api(`/api/devices/${encodeURIComponent(d.id)}/metrics?hours=${METRICS.range}`)
    .then(m => { Object.assign(METRICS, { id: d.id, at: Date.now(), data: m }); render(); })
    .catch(() => {}).finally(() => { METRICS.busy = false; });
}

// A line of one metric over the chosen range; gaps where the node did not report stay empty.
function sparkline(m, idx, lo, hi, unit) {
  const span = m.to - m.from;
  let path = '', prev = null, peak = null;
  for (const p of m.points) {
    const v = p[idx];
    if (v == null) { prev = null; continue; }
    peak = peak == null ? v : Math.max(peak, v);
    const x = (p[0] - m.from) / span * 100, y = 23 - (Math.min(hi, Math.max(lo, v)) - lo) / (hi - lo) * 21;
    path += `${prev !== null && p[0] - prev <= m.bucket * 1.5 ? 'L' : 'M'}${x.toFixed(2)} ${y.toFixed(2)}`;
    prev = p[0];
  }
  if (!path) return '';
  return `<svg class="spark" viewBox="0 0 100 24" preserveAspectRatio="none" aria-hidden="true"><path d="${path}" vector-effect="non-scaling-stroke"/></svg>
    <small class="spark-meta">${t('peak')} ${Math.round(peak * 10) / 10} ${unit} · ${t('range' + METRICS.range)}</small>`;
}

// When the node reported: one cell per 15 minutes (24 h) or per hour (7 days).
function uptimeCard(m) {
  const cells = METRICS.range <= 24 ? 96 : 168, cell = (m.to - m.from) / cells, now = Date.now() / 1000;
  const have = new Set(m.points.map(p => p[0]));
  const stats = Array.from({ length: cells }, () => ({ exp: 0, got: 0 }));
  let exp = 0, got = 0;
  for (let b = m.from; b < now; b += m.bucket) {
    if (m.first_sample == null || b < m.first_sample) continue;
    const i = Math.min(cells - 1, Math.floor((b - m.from) / cell));
    const ok = have.has(b) || b > now - m.bucket;
    stats[i].exp++; exp++;
    if (ok) { stats[i].got++; got++; }
  }
  const label = i => clock(m.from + i * cell);
  const bar = stats.map((x, i) => `<i class="${!x.exp ? 'none' : x.got >= x.exp ? 'up' : x.got ? 'part' : 'down'}" title="${esc(label(i))}"></i>`).join('');
  const range = r => `<button class="${METRICS.range === r ? 'active' : ''}" data-do="metricsRange" data-range="${r}">${t('range' + r)}</button>`;
  return `<section class="card uptime"><div class="card-head"><h2>${t('availability')}</h2><div class="seg-mini">${range(24)}${range(168)}</div></div>
    <div class="uptime-num"><b>${exp ? (Math.floor(got / exp * 1000) / 10).toLocaleString(S.lang) : '—'} %</b><span class="muted">${t('availabilityHint')}</span></div>
    <div class="uptime-bar">${bar}</div><div class="uptime-axis"><span>${label(0)}</span><span>${t('now')}</span></div></section>`;
}

const HISTORY = { id: '', at: 0, rows: null, filter: 'all' };
function loadHistory(d) {
  if (HISTORY.id === d.id && Date.now() - HISTORY.at < 30000) return;
  HISTORY.at = Date.now();
  api(`/api/events?device_id=${encodeURIComponent(d.id)}&hours=720&limit=400`)
    .then(rows => { HISTORY.id = d.id; HISTORY.rows = rows; render(); }).catch(() => {});
}

// One timeline: problems that started and ended (with how long they lasted) and the commands sent to the node.
function deviceHistory(d) {
  loadHistory(d);
  const events = HISTORY.id === d.id ? HISTORY.rows || [] : [];
  const open = {}, lasted = {};
  for (const e of [...events].reverse()) {
    if (e.kind === 'start') open[e.code] = e.ts;
    else if (open[e.code] != null) { lasted[e.id] = e.ts - open[e.code]; delete open[e.code]; }
  }
  for (const c of d.commands) RESULTS[c.id] = c;
  const f = HISTORY.filter;
  const items = [
    ...(f === 'commands' ? [] : events.map(e => ({ ts: e.ts, html: `<div class="tl-item ${e.kind === 'end' ? 'ok' : e.level}"><span class="tl-dot"></span>
      <div class="tl-body"><b>${esc(eventText(e))}</b>${lasted[e.id] ? ` <small class="muted">${t('lasted', { d: dur(lasted[e.id]) })}</small>` : ''}</div><time>${clock(e.ts)}</time></div>` }))),
    ...(f === 'problems' ? [] : d.commands.map(c => ({ ts: c.created, html: `<div class="tl-item cmd"><span class="tl-dot"></span>
      <div class="tl-body"><b>${esc(t('act_' + c.action))}</b> <small class="muted">${esc([payloadSummary(c.payload_json), c.username].filter(Boolean).join(' · '))}</small>
        <span class="badge st-${c.state}">${t('state_' + c.state)}</span>
        ${c.result ? `<button type="button" class="result result-btn" data-do="showResult" data-cid="${c.id}">${esc(c.result.slice(0, 90))}</button>` : ''}
        ${c.state === 'queued' && can('control') ? `<button class="btn sm ghost" data-do="cancelCmd" data-cid="${c.id}">${t('cancel')}</button>` : ''}</div><time>${clock(c.created)}</time></div>` }))),
  ].sort((a, b) => b.ts - a.ts);
  const day = ts => { const x = new Date(ts * 1000), today = new Date(); const y = new Date(); y.setDate(today.getDate() - 1);
    return x.toDateString() === today.toDateString() ? t('today') : x.toDateString() === y.toDateString() ? t('yesterday') : x.toLocaleDateString(S.lang === 'cs' ? 'cs-CZ' : 'en-GB', { weekday: 'long', day: 'numeric', month: 'long' }); };
  let last = '', body = '';
  for (const it of items) { const dl = day(it.ts); if (dl !== last) { body += `<div class="tl-day">${esc(dl)}</div>`; last = dl; } body += it.html; }
  const tab = (k) => `<button class="${f === k ? 'active' : ''}" data-do="historyFilter" data-f="${k}">${t('tl_' + k)}</button>`;
  return `<section class="card"><div class="card-head"><h2>${t('timeline')}</h2><div class="seg-mini">${tab('all')}${tab('problems')}${tab('commands')}</div></div>
    ${body ? `<div class="timeline">${body}</div>` : emptyState('check', t('noHistory'), t('noHistoryHint'), '')}</section>`;
}

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
        ${p.auth_type === 'http' ? `<span class="tag info">${t('loginType_http')}</span><dl class="kv small"><dt>${t('httpLoginServer')}</dt><dd title="${esc(p.target_url)}">${esc(p.target_url)}</dd></dl>`
          : p.login_url ? `<dl class="kv small"><dt>${t('loginUrl')}</dt><dd title="${esc(p.login_url)}">${esc(p.login_url)}</dd><dt>${t('targetUrl')}</dt><dd title="${esc(p.target_url)}">${esc(p.target_url)}</dd></dl>` : ''}
        ${edit ? `<div class="col-actions"><button class="btn sm" data-do="addWebWithLogin" data-id="${esc(d.id)}" data-profile="${esc(p.id)}">${icon('web')}${t('addWebWithLogin')}</button>
          <button class="icon-btn" title="${t('edit')}" data-do="editProfile" data-id="${esc(d.id)}" data-profile="${esc(p.id)}">${icon('edit')}</button>
          <button class="icon-btn danger" title="${t('delete')}" data-do="deleteProfile" data-id="${esc(d.id)}" data-profile="${esc(p.id)}" data-name="${esc(p.name)}" data-used="${pages.length}">${icon('trash')}</button></div>` : ''}
      </div>`;
    }).join('') || `<div class="empty">${support === 'ok' ? t('noProfiles') : t('noProfilesShort')}</div>`}</div>
    ${edit ? `<div class="note">${icon('lock')}${t('loginsSecurity')}</div>` : ''}</section>`;
}

// ---------- on-screen notifications of a node: settings, queue and watchers

// 'ok': the node and its agent support notifications; 'agent': the agent is too old; 'node': CARACAL is too old
function notifySupport(d) {
  const cap = (d.capabilities || {}).notify_settings;
  return cap === true ? 'ok' : cap === false ? 'node' : 'agent';
}
const NOTIFY_LEVELS = ['info', 'success', 'warning', 'critical'];
const NOTIFY_POSITIONS = ['top-right', 'top', 'top-left', 'bottom-right', 'bottom', 'bottom-left', 'center'];
const NOTIFY_SOUNDS = ['off', 'critical', 'warning', 'all'];
const LEVEL_TAG = { info: 'info', success: 'ok', warning: 'frozen', critical: 'critical' };
const NOTIFY_DEFAULTS = { enabled: true, position: 'top-right', duration: 8, scale: 100, max_queue: 20, sound: 'off', volume: 70, sound_device: '', history_max: 500, history_days: 7 };
const hostOf = url => { try { return new URL(url).host; } catch { return url || ''; } };
// translation keys use _ instead of - (top-right -> notifyPos_top_right)
const choice = (list, sel, prefix) => list.map(x => `<option value="${x}" ${x === sel ? 'selected' : ''}>${t(prefix + x.replace(/-/g, '_'))}</option>`).join('');

function renderNotifications(d) {
  const support = notifySupport(d), n = d.notifications;
  if (support !== 'ok' || !n) return `<section class="card">${emptyState('attention', t('notifyUnsupported'), t(support === 'node' ? 'notifyNeedCaracal' : 'notifyNeedAgent'),
    can('manage') && d.online ? (support === 'node' ? `<button class="btn primary" data-do="caracalUpdate" data-id="${esc(d.id)}">${icon('updates')}${t('act_update_caracal')}</button>`
      : `<button class="btn primary" data-do="cmd" data-id="${esc(d.id)}" data-act="update_agent">${icon('agent')}${t('act_update_agent')}</button>`) : '')}</section>`;
  const s = { ...NOTIFY_DEFAULTS, ...n.settings }, edit = can('content'), ctl = can('control'), caps = d.capabilities || {};
  const cur = n.current;
  return `<div class="grid two">
    <section class="card"><div class="card-head"><h2>${t('notifyScreen')}</h2><span class="tag ${s.enabled ? 'ok' : ''}">${t(s.enabled ? 'notifyOn' : 'notifyOff')}</span></div>
      <dl class="kv"><dt>${t('notifyCurrent')}</dt><dd>${cur ? `<span class="tag ${LEVEL_TAG[cur.level] || 'info'}">${t('notifyLevel_' + (cur.level || 'info'))}</span> ${esc(cur.title || cur.message)}` : '—'}</dd>
        <dt>${t('notifyWaiting')}</dt><dd>${esc(n.waiting)}</dd><dt>${t('notifyNodeTokens')}</dt><dd>${esc(n.tokens)}</dd>
        ${n.history_count != null ? `<dt>${t('notifyHistory')}</dt><dd>${t('nRecords', { n: n.history_count })} · ${t('notifyAudit')}: ${t('nRecords', { n: n.audit_count ?? 0 })}</dd>` : ''}</dl>
      ${!d.online ? `<p class="muted">${t('notifyOfflineHint')}</p>` : ''}
      <div class="form-actions start">${ctl ? `<button class="btn primary" data-do="notifySend" data-id="${esc(d.id)}">${icon('plus')}${t('notifySend')}</button>
        ${caps.notify_skip ? `<button class="btn" data-do="notifySkip" data-id="${esc(d.id)}" ${cur && d.online ? '' : 'disabled'}>${icon('next')}${t('notifySkip')}</button>` : ''}
        <button class="btn" data-do="notifyClear" data-id="${esc(d.id)}" ${n.waiting || cur ? '' : 'disabled'}>${icon('trash')}${t('notifyClear')}</button>` : ''}
        ${caps.notify_log ? `<button class="btn" data-do="notifyLog" data-id="${esc(d.id)}" ${d.online ? '' : 'disabled'}>${icon('audit')}${t('notifyLog')}</button>` : ''}</div></section>
    <section class="card"><div class="card-head"><h2>${t('notifySettings')}</h2>${edit ? `<span class="row-actions">${lookSupport(d) ? `<button class="btn sm" data-do="notifyStyle" data-id="${esc(d.id)}">${icon('image')}${t('notifyLook')}</button>` : ''}<button class="btn sm" data-do="notifySettings" data-id="${esc(d.id)}">${icon('edit')}${t('edit')}</button></span>` : ''}</div>
      <dl class="kv"><dt>${t('notifyPosition')}</dt><dd>${t('notifyPos_' + s.position.replace(/-/g, '_'))}</dd>
        <dt>${t('notifyDuration')}</dt><dd>${esc(s.duration)} s</dd><dt>${t('notifySize')}</dt><dd>${esc(s.scale)} %</dd>
        <dt>${t('notifyMaxQueue')}</dt><dd>${esc(s.max_queue)}</dd>
        <dt>${t('notifySound')}</dt><dd>${t('notifySound_' + s.sound)}${s.sound !== 'off' ? ` · ${esc(s.volume)} %` : ''}</dd>
        <dt>${t('notifyHistory')}</dt><dd>${t('notifyHistoryValue', { n: s.history_max, days: s.history_days })}</dd>
        <dt>${t('notifySounds')}</dt><dd>${NOTIFY_LEVELS.map(l => n.sounds && n.sounds[l] ? `<span class="tag info" title="${esc(n.sounds[l].name)}">${t('notifyLevel_' + l)}: ${esc(n.sounds[l].name)}</span>` : '').join(' ') || t('notifySoundsDefault')}
          ${edit && (d.capabilities || {}).notify_sound ? `<button class="btn sm ghost" data-do="notifySound" data-id="${esc(d.id)}">${icon('upload')}${t('notifySoundUpload')}</button>` : ''}</dd></dl></section></div>
  ${notifyQueueCard(d, n, ctl && caps.notify_remove)}${nodeTokensCard(d, n, can('manage') && caps.notify_token_update)}
  <section class="card flush"><div class="toolbar"><h2 class="grow">${t('watchers')} <span class="muted">(${n.watchers.length})</span></h2>
    ${edit ? `<button class="btn primary" data-do="addWatcher" data-id="${esc(d.id)}">${icon('plus')}${t('addWatcher')}</button>` : ''}</div>
    <div class="note">${t('watchersHint')}</div>
    <div class="col-grid">${n.watchers.map(w => `<div class="col-card">
      <div class="col-head">${icon('eye')}<b>${esc(w.name)}</b><span class="muted">${esc(hostOf(w.url))}</span></div>
      <div class="tag-row"><span class="tag ${w.enabled ? 'ok' : ''}">${t(w.enabled ? 'watcherOn' : 'watcherOff')}</span><span class="tag">${t('watcherEvery', { n: w.interval })}</span>
        <span class="tag">${t('watcherAuth_' + (w.auth_type || 'none'))}</span>${w.last_count != null && !w.last_error ? `<span class="tag">${t('nItems', { n: w.last_count })}</span>` : ''}</div>
      <small class="muted">${t('watcherChecked')}: ${w.last_check ? ago(w.last_check) : t('watcherNever')}</small>
      ${w.last_error ? `<small class="tag critical" title="${esc(w.last_error)}">${esc(w.last_error.slice(0, 160))}</small>` : ''}
      ${edit ? `<div class="col-actions"><button class="btn sm" data-do="checkWatcher" data-id="${esc(d.id)}" data-watcher="${esc(w.id)}" ${d.online ? '' : 'disabled'}>${icon('refresh')}${t('watcherCheck')}</button>
        <button class="btn sm" data-do="toggleWatcher" data-id="${esc(d.id)}" data-watcher="${esc(w.id)}" data-on="${w.enabled ? 0 : 1}">${t(w.enabled ? 'watcherTurnOff' : 'watcherTurnOn')}</button>
        <button class="icon-btn" title="${t('edit')}" data-do="editWatcher" data-id="${esc(d.id)}" data-watcher="${esc(w.id)}">${icon('edit')}</button>
        <button class="icon-btn danger" title="${t('delete')}" data-do="deleteWatcher" data-id="${esc(d.id)}" data-watcher="${esc(w.id)}" data-name="${esc(w.name)}">${icon('trash')}</button></div>` : ''}
    </div>`).join('') || `<div class="empty">${t('noWatchers')}</div>`}</div>
    ${edit ? `<div class="note">${icon('lock')}${t('watchersSecurity')}</div>` : ''}</section>`;
}

function notifyQueueCard(d, n, ctl) {
  if (!n.queue || !n.queue.length) return '';
  return `<section class="card flush"><div class="toolbar"><h2 class="grow">${t('notifyQueue')} <span class="muted">(${esc(n.waiting)})</span></h2></div>
    <div class="table-wrap"><table class="table"><thead><tr><th>${t('notifyLevel')}</th><th>${t('notifyTitle')}</th><th>${t('notifySource')}</th><th>${t('time')}</th><th></th></tr></thead>
    <tbody>${n.queue.map(x => `<tr><td><span class="tag ${LEVEL_TAG[x.level] || 'info'}">${t('notifyLevel_' + (x.level || 'info'))}</span></td>
      <td><b>${esc(x.title || '')}</b>${x.message ? `<br><small class="muted">${esc(x.message)}</small>` : ''}</td><td><small>${esc(x.source || '')}</small></td>
      <td class="nowrap muted">${x.created ? ago(x.created) : ''}</td>
      <td class="actions-cell">${ctl ? `<button class="icon-btn danger" title="${t('notifyRemove')}" data-do="notifyRemove" data-id="${esc(d.id)}" data-nid="${esc(x.id)}" ${d.online ? '' : 'disabled'}>${icon('trash')}</button>` : ''}</td></tr>`).join('')}</tbody></table></div>
    ${n.waiting > n.queue.length ? `<div class="note">${t('notifyQueueMore', { n: n.waiting - n.queue.length })}</div>` : ''}</section>`;
}

// Tokens the node itself issued to apps (its own notification API); new ones are created in the node's administration.
function nodeTokensCard(d, n, edit) {
  if (!n.token_list || !n.token_list.length) return '';
  return `<section class="card flush"><div class="toolbar"><h2 class="grow">${t('notifyNodeTokensList')} <span class="muted">(${n.token_list.length})</span></h2></div>
    <div class="note">${t('notifyNodeTokensHint')}</div>
    <div class="table-wrap"><table class="table"><thead><tr><th>${t('name')}</th><th>${t('notifyTokenPrefix')}</th><th>${t('notifyRate')}</th><th>${t('notifyLastUsed')}</th><th>${t('state')}</th><th></th></tr></thead>
    <tbody>${n.token_list.map(x => `<tr><td><b>${esc(x.name)}</b></td><td><code>${esc(x.prefix)}…</code></td><td>${esc(x.rate_per_min)}/min</td>
      <td class="muted">${x.last_used ? ago(x.last_used) : t('watcherNever')}</td><td><span class="tag ${x.enabled ? 'ok' : ''}">${t(x.enabled ? 'tokenOn' : 'tokenOff')}</span></td>
      <td class="actions-cell">${edit ? `<button class="btn sm" data-do="nodeTokenToggle" data-id="${esc(d.id)}" data-tid="${esc(x.id)}" data-on="${x.enabled ? 0 : 1}">${t(x.enabled ? 'tokenDisable' : 'tokenEnable')}</button>
        <button class="icon-btn danger" title="${t('delete')}" data-do="nodeTokenDelete" data-id="${esc(d.id)}" data-tid="${esc(x.id)}" data-name="${esc(x.name)}">${icon('trash')}</button>` : ''}</td></tr>`).join('')}</tbody></table></div></section>`;
}

const NOTIFY_DONE = { 1: 'notifyDone_shown', 2: 'notifyDone_expired', 3: 'notifyDone_removed' };

async function notifyLogDialog(id) {
  const dlg = modal({ title: t('notifyLog'), wide: true, body: `<div id="ntfLog"><div class="note info">${t('loadingFromNode')}</div></div>` });
  const el = $('#ntfLog', dlg);
  let r;
  try { r = await commandResult(id, 'notify_log'); } catch (e) { if (el.isConnected) el.innerHTML = `<div class="note warn">${esc(errText(e))}</div>`; return; }
  if (!el.isConnected) return;
  const content = can('content'), manage = can('manage');
  el.innerHTML = `<h3>${t('notifyHistory')} <span class="muted">(${r.history_count ?? 0})</span></h3>
    <div class="table-wrap"><table class="table"><thead><tr><th>${t('time')}</th><th>${t('notifyLevel')}</th><th>${t('notifyTitle')}</th><th>${t('notifySource')}</th><th>${t('state')}</th></tr></thead>
    <tbody>${(r.history || []).map(x => `<tr><td class="nowrap">${dt(x.created)}</td><td><span class="tag ${LEVEL_TAG[x.level] || 'info'}">${t('notifyLevel_' + (x.level || 'info'))}</span></td>
      <td><b>${esc(x.title || '')}</b>${x.message ? `<br><small class="muted">${esc(x.message)}</small>` : ''}</td><td><small>${esc(x.source || '')}</small></td><td><small>${t(NOTIFY_DONE[x.done] || 'notifyDone_shown')}</small></td></tr>`).join('')
      || `<tr><td colspan="5" class="empty">${t('noRecords')}</td></tr>`}</tbody></table></div>
    ${content && r.history_count ? `<div class="form-actions start"><button type="button" class="btn sm danger" data-log-clear="history">${icon('trash')}${t('notifyHistoryClear')}</button></div>` : ''}
    <h3>${t('notifyAudit')} <span class="muted">(${r.audit_count ?? 0})</span></h3>
    <div class="table-wrap"><table class="table"><thead><tr><th>${t('time')}</th><th>${t('user')}</th><th>IP</th><th>${t('action')}</th><th>${t('detail')}</th></tr></thead>
    <tbody>${(r.audit || []).map(x => `<tr><td class="nowrap">${dt(x.ts)}</td><td>${esc(x.actor || '—')}</td><td><small>${esc(x.ip || '')}</small></td><td>${esc(x.action)}</td><td><small class="muted">${esc(x.detail || '')}</small></td></tr>`).join('')
      || `<tr><td colspan="5" class="empty">${t('noRecords')}</td></tr>`}</tbody></table></div>
    ${manage && r.audit_count ? `<div class="form-actions start"><button type="button" class="btn sm danger" data-log-clear="audit">${icon('trash')}${t('notifyAuditClear')}</button></div>` : ''}`;
  $$('[data-log-clear]', el).forEach(b => {
    b.onclick = async () => {
      const which = b.dataset.logClear;
      dlg.close();
      if (!await confirmBox(t(which === 'audit' ? 'confirmNotifyAuditClear' : 'confirmNotifyHistoryClear'))) return;
      await sendCommand(id, which === 'audit' ? 'notify_audit_clear' : 'notify_history_clear').catch(e => toast(errText(e), 'warn'));
    };
  });
}

function notifyDialog(ids) {
  modal({
    title: t('notifySend'), submit: t('notifySendSubmit'),
    body: `<div class="form">${ids.length > 1 ? `<p class="muted">${t('notifyToSelected', { n: ids.length })}</p>` : ''}
      <label>${t('notifyTitle')}<input name="title" maxlength="120"></label>
      <label>${t('notifyMessage')}<textarea name="message" rows="3" maxlength="600"></textarea></label>
      <div class="row2"><label>${t('notifyLevel')}<select name="level">${choice(NOTIFY_LEVELS, 'info', 'notifyLevel_')}</select></label>
      <label>${t('notifyDuration')}<input name="duration" type="number" min="3" max="120" placeholder="${esc(t('notifyDurationDefault'))}"></label></div>
      <label>${t('notifySound')}<select name="sound"><option value="">${t('notifySoundBySettings')}</option><option value="1">${t('notifySoundPlay')}</option><option value="0">${t('notifySoundSilent')}</option></select></label></div>`,
    onSubmit: data => {
      if (!data.title.trim() && !data.message.trim()) throw new Error('notification_text_required');
      const payload = { title: data.title.trim(), message: data.message.trim(), level: data.level };
      if (data.duration) payload.duration = Number(data.duration);
      if (data.sound) payload.sound = data.sound === '1';
      return ids.length === 1 ? sendCommand(ids[0], 'notify', payload) : sendBulk(ids, 'notify', payload);
    },
  });
}

// Custom MP3 of a notification level (at most 5 MB, the screen plays at most 15 s), or the generated chime again.
function notifySoundDialog(ids, sounds = {}) {
  modal({
    title: t('notifySoundUpload'), submit: t('save'),
    body: `<div class="form"><p class="muted">${t('notifySoundHint')}</p>${ids.length > 1 ? `<p class="muted">${t('notifySoundToSelected', { n: ids.length })}</p>` : ''}
      <label>${t('notifyLevel')}<select name="level">${choice(NOTIFY_LEVELS, 'critical', 'notifyLevel_')}</select><small class="muted sound-current"></small></label>
      <div class="radios"><label class="check"><input type="radio" name="mode" value="upload" checked>${t('notifySoundModeUpload')}</label>
        <label class="check"><input type="radio" name="mode" value="reset">${t('notifySoundModeReset')}</label></div>
      <label class="sound-file">${t('file')}<input type="file" name="file" accept=".mp3,audio/mpeg"></label><div class="upload-prog" hidden><i></i></div></div>`,
    onOpen: form => {
      const sync = () => {
        const cur = sounds[form.level.value];
        $('.sound-current', form).textContent = ids.length === 1 ? (cur ? t('notifySoundNow', { name: cur.name }) : t('notifySoundsDefault')) : '';
        $('.sound-file', form).hidden = form.querySelector('[name=mode]:checked').value !== 'upload';
      };
      form.level.onchange = sync;
      $$('[name=mode]', form).forEach(r => { r.onchange = sync; });
      sync();
    },
    onSubmit: async (data, form) => {
      let payload = { level: data.level, reset: true };
      if (data.mode === 'upload') {
        const file = form.file.files[0];
        if (!file) throw new Error('file_not_found');
        if (file.size > 5 * 1024 * 1024) throw new Error('file_too_large');
        const pr = $('.upload-prog', form);
        pr.hidden = false;
        // the browser may not know the MP3 type, the hub recognises the extension
        const up = await uploadFile(file.type ? file : new File([file], file.name, { type: 'audio/mpeg' }), p => { $('i', pr).style.width = (p * 100) + '%'; });
        payload = { level: data.level, file_id: up.id };
      }
      return ids.length === 1 ? sendCommand(ids[0], 'notify_sound', payload) : sendBulk(ids, 'notify_sound', payload);
    },
  });
}

// The look of the notifications on the TV, edited visually (static/notify-style.js, the same editor as on the node).
const lookSupport = d => !!(d && (d.notify_style || d.notifications?.settings?.style));
function styleDialog(ids, settings) {
  let editor = null;
  const dlg = modal({
    title: t('notifyLook'), wide: true, submit: t('save'),
    body: `${ids.length > 1 ? `<p class="muted">${t('notifyLookToSelected', { n: ids.length })}</p>` : ''}<p class="muted">${t('notifyLookHint')}</p><div id="lookEditor"></div>`,
    onOpen: form => {
      editor = NotifyStyle.editor($('#lookEditor', form), settings.style, { t: (cs, en) => (S.lang === 'cs' ? cs : en), position: settings.position, scale: settings.scale });
    },
    onSubmit: () => {
      const payload = { style: editor.get() };
      return ids.length === 1 ? sendCommand(ids[0], 'notify_settings', payload) : sendBulk(ids, 'notify_settings', payload);
    },
  });
  dlg.classList.add('xwide');
}

function notifySettingsDialog(ids, current) {
  const s = { ...NOTIFY_DEFAULTS, ...(current || {}) };
  const numIn = (name, min, max, step = 1) => `<input name="${name}" type="number" min="${min}" max="${max}" step="${step}" value="${esc(s[name])}" required>`;
  modal({
    title: t('notifySettings'), wide: true,
    body: `<div class="form">${ids.length > 1 ? `<p class="muted">${t('notifySettingsToSelected', { n: ids.length })}</p>` : ''}
      <label class="check"><input type="checkbox" name="enabled" ${s.enabled ? 'checked' : ''}>${t('notifyEnabled')}</label>
      <div class="row2"><label>${t('notifyPosition')}<select name="position">${choice(NOTIFY_POSITIONS, s.position, 'notifyPos_')}</select></label>
      <label>${t('notifyDuration')}${numIn('duration', 3, 120)}</label></div>
      <div class="row2"><label>${t('notifySize')}${numIn('scale', 50, 300, 10)}</label><label>${t('notifyMaxQueue')}${numIn('max_queue', 1, 200)}</label></div>
      <div class="row2"><label>${t('notifySound')}<select name="sound">${choice(NOTIFY_SOUNDS, s.sound, 'notifySound_')}</select></label><label>${t('notifyVolume')}${numIn('volume', 0, 100, 10)}</label></div>
      <label>${t('notifySoundDevice')}<input name="sound_device" value="${esc(s.sound_device)}" pattern="[A-Za-z0-9:=,._\\-]*" placeholder="hdmi:CARD=vc4hdmi0,DEV=0"><small class="muted">${t('notifySoundDeviceHint')}</small></label>
      <div class="row2"><label>${t('notifyHistoryMax')}${numIn('history_max', 50, 5000, 50)}</label><label>${t('notifyHistoryDays')}${numIn('history_days', 1, 90)}</label></div></div>`,
    onSubmit: data => {
      const payload = { enabled: !!data.enabled, position: data.position, sound: data.sound, sound_device: data.sound_device.trim() };
      for (const k of ['duration', 'scale', 'max_queue', 'volume', 'history_max', 'history_days']) payload[k] = Number(data[k]);
      return ids.length === 1 ? sendCommand(ids[0], 'notify_settings', payload) : sendBulk(ids, 'notify_settings', payload);
    },
  });
}

// Presets of watchers (the same as in the node's admin UI); addresses are examples to replace.
const WATCHER_PRESETS = {
  custom: {},
  zammad: { url: 'https://zammad.example/api/v1/tickets/search?query=state.name:new&sort_by=created_at&order_by=desc&limit=50&expand=true', auth_type: 'header', auth_header: 'Authorization', list_path: '', id_field: 'id', title_template: 'Nový ticket #{number}: {title}', message_template: '{customer} · {group}', level_field: '' },
  jira: { url: 'https://example.atlassian.net/rest/api/3/search/jql?jql=project%3DSD%20AND%20statusCategory%3D%22To%20Do%22%20ORDER%20BY%20created%20DESC&fields=summary,priority,reporter&maxResults=50', auth_type: 'basic', list_path: 'issues', id_field: 'key', title_template: '{key}: {fields.summary}', message_template: '{fields.reporter.displayName}', level_field: 'fields.priority.name' },
  redmine: { url: 'https://redmine.example/issues.json?status_id=open&sort=created_on:desc&limit=50', auth_type: 'header', auth_header: 'X-Redmine-API-Key', list_path: 'issues', id_field: 'id', title_template: '#{id}: {subject}', message_template: '{project.name} · {author.name}', level_field: 'priority.name' },
  freshdesk: { url: 'https://example.freshdesk.com/api/v2/tickets?order_by=created_at&order_type=desc&per_page=50', auth_type: 'basic', list_path: '', id_field: 'id', title_template: 'Nový ticket #{id}: {subject}', message_template: '', level_field: '' },
  gitlab: { url: 'https://gitlab.example/api/v4/projects/123/issues?state=opened&order_by=created_at&per_page=50', auth_type: 'header', auth_header: 'PRIVATE-TOKEN', list_path: '', id_field: 'id', title_template: '#{iid}: {title}', message_template: '{author.name}', level_field: '' },
  github: { url: 'https://api.github.com/repos/OWNER/REPO/issues?state=open&sort=created&per_page=50', auth_type: 'bearer', list_path: '', id_field: 'id', title_template: '#{number}: {title}', message_template: '{user.login}', level_field: '' },
  graph: { url: 'https://graph.microsoft.com/v1.0/users/helpdesk@example.com/mailFolders/inbox/messages?$top=25&$orderby=receivedDateTime%20desc&$select=subject,from,receivedDateTime,importance', auth_type: 'oauth2', oauth_token_url: 'https://login.microsoftonline.com/<TENANT-ID>/oauth2/v2.0/token', oauth_grant: 'client_credentials', oauth_scope: 'https://graph.microsoft.com/.default', oauth_client_auth: 'body', list_path: 'value', id_field: 'id', title_template: '{subject}', message_template: '{from.emailAddress.name}', level_field: '' },
  servicenow: { url: 'https://example.service-now.com/api/now/table/incident?sysparm_query=active%3Dtrue%5EORDERBYDESCsys_created_on&sysparm_limit=50&sysparm_fields=sys_id,number,short_description,priority,category', auth_type: 'oauth2', oauth_token_url: 'https://example.service-now.com/oauth_token.do', oauth_grant: 'password', oauth_client_auth: 'body', list_path: 'result', id_field: 'sys_id', title_template: '{number}: {short_description}', message_template: '{category}', level_field: '' },
};
const WATCHER_AUTH = ['none', 'bearer', 'basic', 'header', 'oauth2'];
const OAUTH_GRANTS = ['client_credentials', 'password', 'refresh_token'];
const WATCHER_TEXT = ['name', 'url', 'auth_header', 'list_path', 'id_field', 'title_template', 'message_template', 'level_field', 'oauth_token_url', 'oauth_client_id', 'oauth_scope', 'oauth_extra'];

function watcherPayload(data) {
  const payload = {};
  for (const k of WATCHER_TEXT) payload[k] = (data[k] || '').trim();
  Object.assign(payload, { auth_type: data.auth_type, oauth_grant: data.oauth_grant, oauth_client_auth: data.oauth_client_auth, level: data.level,
    interval: Number(data.interval), verify_tls: !!data.verify_tls, enabled: !!data.enabled });
  // empty credentials keep the stored ones
  for (const k of ['username', 'secret', 'client_secret', 'refresh_token']) if ((data[k] || '').trim()) payload[k] = data[k].trim();
  return payload;
}

// "Try" buttons in dialogs: runs a command on the node and shows its result below the button.
async function tryCommand(form, btn, run, show) {
  const out = $('.try-result', form);
  btn.disabled = true;
  btn.classList.add('busy');
  out.innerHTML = `<div class="note">${t('loadingFromNode')}</div>`;
  try { out.innerHTML = show(await run()); } catch (e) { out.innerHTML = `<div class="note warn">${esc(errText(e))}</div>`; }
  finally { btn.disabled = false; btn.classList.remove('busy'); }
}

function watcherDialog(deviceId, w = null, targets = null) {
  const v = { name: '', url: '', auth_type: 'none', auth_header: '', list_path: '', id_field: 'id', title_template: '', message_template: '', level: 'info', level_field: '', interval: 60, verify_tls: 1, enabled: 1, oauth_token_url: '', oauth_grant: 'client_credentials', oauth_client_id: '', oauth_scope: '', oauth_extra: '', oauth_client_auth: 'body', ...(w || {}) };
  const keep = w && w.has_credentials ? `placeholder="${esc(t('leaveEmpty'))}"` : '';
  const field = (name, label, attrs = '') => `<label>${label}<input name="${name}" value="${esc(v[name] ?? '')}" spellcheck="false" ${attrs}></label>`;
  const secret = name => `<input name="${name}" type="password" autocomplete="new-password" ${keep}>`;
  modal({
    title: w ? t('editWatcher') : t('addWatcher'), submit: w ? t('save') : t('add'), wide: true,
    body: `<div class="form"><p class="muted">${t('watcherDialogHint')}</p>
      ${w ? '' : `<label>${t('watcherPreset')}<select name="preset">${Object.keys(WATCHER_PRESETS).map(k => `<option value="${k}">${t('watcherPreset_' + k)}</option>`).join('')}</select></label>`}
      ${field('name', t('watcherName'), 'required maxlength="60"')}
      ${field('url', t('watcherUrl'), 'type="url" required placeholder="https://"')}
      <div class="row2"><label>${t('watcherAuth')}<select name="auth_type">${choice(WATCHER_AUTH, v.auth_type, 'watcherAuth_')}</select></label>
        <span data-auth="header">${field('auth_header', t('watcherHeader'), 'placeholder="X-API-Key"')}</span></div>
      <div data-auth="oauth2">${field('oauth_token_url', t('watcherTokenUrl'), 'type="url" placeholder="https://login…/oauth2/token"')}
        <div class="row2"><label>${t('watcherGrant')}<select name="oauth_grant">${choice(OAUTH_GRANTS, v.oauth_grant, 'watcherGrant_')}</select></label>${field('oauth_client_id', 'Client ID')}</div>
        <div class="row2"><label>Client Secret${secret('client_secret')}</label>${field('oauth_scope', 'Scope')}</div>
        <div class="row2"><label>${t('watcherClientAuth')}<select name="oauth_client_auth"><option value="body" ${v.oauth_client_auth !== 'basic' ? 'selected' : ''}>client_secret_post</option><option value="basic" ${v.oauth_client_auth === 'basic' ? 'selected' : ''}>client_secret_basic</option></select></label>${field('oauth_extra', t('watcherTokenExtra'), 'placeholder="audience=https://api.example"')}</div>
        <label data-grant="refresh_token">Refresh token${secret('refresh_token')}</label></div>
      <div class="row2"><label data-auth="basic oauth2" data-grant="password">${t('watcherUser')}<input name="username" autocomplete="off" spellcheck="false" ${keep}></label>
        <label data-auth="bearer basic header oauth2" data-grant="password">${t('watcherSecret')}${secret('secret')}</label></div>
      <div class="row2">${field('list_path', t('watcherListPath'), `placeholder="${esc(t('watcherListPathHint'))}"`)}${field('id_field', t('watcherIdField'), 'required')}</div>
      ${field('title_template', t('watcherTitle'), `placeholder="${esc(t('watcherTitleHint'))}"`)}
      ${field('message_template', t('watcherMessage'), 'placeholder="{customer.name} · {group}"')}
      <div class="row2"><label>${t('notifyLevel')}<select name="level">${choice(NOTIFY_LEVELS, v.level, 'notifyLevel_')}</select></label>${field('level_field', t('watcherLevelField'), 'placeholder="priority.name"')}</div>
      <div class="row2"><label>${t('watcherInterval')}<input name="interval" type="number" min="15" max="86400" value="${esc(v.interval)}" required></label><span></span></div>
      <label class="check"><input type="checkbox" name="verify_tls" ${v.verify_tls ? 'checked' : ''}>${t('watcherVerifyTls')}</label>
      <label class="check"><input type="checkbox" name="enabled" ${v.enabled ? 'checked' : ''}>${t('watcherEnabled')}</label>
      <p class="muted">${t('watcherTemplateHint')}</p>
      ${targets ? `<p class="muted">${t('addToSelected', { n: targets.length })}</p>` : w ? '' : `<details><summary>${t('alsoAddTo')}</summary>${targetPicker(deviceId, [], d => notifySupport(d) === 'ok')}</details>`}
      ${(dev(deviceId)?.capabilities || {}).preview_watcher ? `<div class="form-actions start"><button type="button" class="btn sm" data-try>${icon('refresh')}${t('watcherTry')}</button></div><div class="try-result"></div>` : ''}
      <div class="note info">${icon('lock')}${t('watchersSecurity')}</div></div>`,
    onOpen: form => {
      bindTargetPicker(form);
      const sync = () => {
        const auth = form.auth_type.value, grant = form.oauth_grant.value;
        $$('[data-auth]', form).forEach(el => { el.hidden = !el.dataset.auth.split(' ').includes(auth) || (auth === 'oauth2' && el.dataset.grant !== undefined && !el.dataset.grant.split(' ').includes(grant)); });
        $$('[data-auth="oauth2"] [data-grant]', form).forEach(el => { el.hidden = !el.dataset.grant.split(' ').includes(grant); });
      };
      form.auth_type.onchange = sync;
      form.oauth_grant.onchange = sync;
      const tryBtn = $('[data-try]', form);
      if (tryBtn) {
        tryBtn.onclick = () => tryCommand(form, tryBtn, () => commandResult(deviceId, 'preview_watcher', { ...(w ? { id: w.id } : {}), ...watcherPayload(Object.fromEntries(new FormData(form))) }),
          r => `<div class="note info"><b>${t('watcherTryOk', { n: r.count ?? 0 })}</b>${(r.samples || []).map(x => `<br>${esc(x.title || '')}${x.message ? ` <span class="muted">· ${esc(x.message)}</span>` : ''}`).join('')}</div>`);
      }
      if (form.preset) {
        form.preset.onchange = () => {
          const p = WATCHER_PRESETS[form.preset.value];
          for (const [k, val] of Object.entries(p)) if (form[k]) form[k].value = val;
          if (!form.name.value && form.preset.value !== 'custom') form.name.value = t('watcherPreset_' + form.preset.value).split(' – ')[0];
          sync();
        };
      }
      sync();
    },
    onSubmit: data => {
      const payload = watcherPayload(data);
      if (w) return sendCommand(deviceId, 'update_watcher', { id: w.id, ...payload });
      const ids = targets || [deviceId, ...pickedTargets(data)];
      return ids.length === 1 ? sendCommand(ids[0], 'add_watcher', payload) : sendBulk(ids, 'add_watcher', payload);
    },
  });
}

// Tokens of the notification API (Settings): an app sends one request, Fleet shows it on the screens in the scope.
function scopeText(s) {
  if (s.all) return t('scopeAll');
  const names = (s.devices || []).map(id => dev(id)?.name || id);
  return [...(s.groups || []).map(g => t('group') + ' ' + g), ...(s.locations || []).map(l => t('location') + ' ' + l), ...names].join(', ');
}

async function notifyTokensCard(el) {
  const list = await api('/api/notify-tokens').catch(() => null);
  if (!list) { el.remove(); return; }
  el.innerHTML = `<div class="card-head"><h2>${t('notifyApi')}</h2><button class="btn sm primary" data-do="notifyTokenEdit">${icon('plus')}${t('notifyTokenNew')}</button></div>
    <p class="muted">${t('notifyApiHint')}</p>
    <div class="table-wrap"><table class="table"><thead><tr><th>${t('name')}</th><th>${t('notifyScope')}</th><th>${t('notifyRate')}</th><th>${t('notifyLastUsed')}</th><th></th></tr></thead>
    <tbody>${list.map(x => `<tr><td><b>${esc(x.name)}</b><br><small class="muted">${esc(x.prefix)}… · ${esc(x.created_by || '')}</small>${x.enabled ? '' : ` <span class="tag">${t('disabled')}</span>`}</td>
      <td><small>${esc(scopeText(x.scope))}</small></td><td>${esc(x.rate_per_min)}/min</td><td class="muted nowrap">${x.last_used ? ago(x.last_used) : '—'}</td>
      <td class="actions-cell"><button class="icon-btn" title="${t('edit')}" data-do="notifyTokenEdit" data-tid="${x.id}">${icon('edit')}</button>
        <button class="icon-btn danger" title="${t('delete')}" data-do="notifyTokenDelete" data-tid="${x.id}" data-name="${esc(x.name)}">${icon('trash')}</button></td></tr>`).join('')
      || `<tr><td colspan="5" class="empty">${t('notifyNoTokens')}</td></tr>`}</tbody></table></div>`;
  el._tokens = list;
}

function notifyTokenDialog(token = null) {
  const s = token ? token.scope : { all: true };
  const mode = s.all ? 'all' : 'some';
  const checks = (kind, values) => values.map(v => `<label class="check"><input type="checkbox" name="${kind}:${esc(v.id)}" ${(s[kind] || []).includes(v.id) ? 'checked' : ''}><span>${esc(v.label)}</span></label>`).join('') || `<span class="muted">—</span>`;
  modal({
    title: token ? t('notifyTokenEdit') : t('notifyTokenNew'), submit: token ? t('save') : t('add'), wide: true,
    body: `<div class="form"><label>${t('notifyTokenName')}<input name="name" value="${esc(token?.name || '')}" maxlength="60" required placeholder="Grafana"></label>
      <div class="radios"><label class="check"><input type="radio" name="mode" value="all" ${mode === 'all' ? 'checked' : ''}>${t('scopeAll')}</label>
        <label class="check"><input type="radio" name="mode" value="some" ${mode === 'some' ? 'checked' : ''}>${t('scopeSome')}</label></div>
      <div class="scope-pick" ${mode === 'all' ? 'hidden' : ''}>
        <h3>${t('groups')}</h3><div class="target-list">${checks('groups', orgNames('groups').map(g => ({ id: g, label: g })))}</div>
        <h3>${t('locations')}</h3><div class="target-list">${checks('locations', orgNames('locations').map(l => ({ id: l, label: l })))}</div>
        <h3>${t('nav_devices')}</h3><div class="target-list">${checks('devices', S.devices.map(d => ({ id: d.id, label: d.name })))}</div></div>
      <div class="row2"><label>${t('notifyRate')}<input name="rate_per_min" type="number" min="1" max="600" value="${esc(token?.rate_per_min ?? 30)}" required></label>
        ${token ? `<label class="check"><input type="checkbox" name="enabled" ${token.enabled ? 'checked' : ''}>${t('active')}</label>` : '<span></span>'}</div></div>`,
    onOpen: form => {
      const sync = () => { $('.scope-pick', form).hidden = form.querySelector('[name=mode]:checked').value === 'all'; };
      $$('[name=mode]', form).forEach(r => { r.onchange = sync; });
    },
    onSubmit: async data => {
      const pick = kind => Object.keys(data).filter(k => k.startsWith(kind + ':')).map(k => k.slice(kind.length + 1));
      const scope = data.mode === 'all' ? { all: true } : { groups: pick('groups'), locations: pick('locations'), devices: pick('devices') };
      const body = { name: data.name.trim(), scope, rate_per_min: Number(data.rate_per_min) };
      if (token) {
        await api('/api/notify-tokens/' + token.id, { method: 'PATCH', json: { ...body, enabled: !!data.enabled } });
        toast(t('saved'));
      } else {
        const r = await api('/api/notify-tokens', { method: 'POST', json: body });
        setTimeout(() => notifyTokenShow(r.token), 50);
      }
      render(true);
    },
  });
}

function notifyTokenShow(token) {
  const url = location.origin + '/api/notify';
  const ex = `curl -X POST ${url} \\\n  -H "Authorization: Bearer ${token}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"title":"Záloha dokončena","message":"Server DB01","level":"success"}'`;
  modal({ title: t('notifyTokenCreated'), wide: true,
    body: `<div class="form"><p class="muted">${t('notifyTokenOnce')}</p><pre class="code">${esc(token)}</pre>
      <h3>${t('notifyExample')}</h3><pre class="code">${esc(ex)}</pre><p class="muted">${t('notifyNarrowHint')}</p>
      <div class="form-actions start"><button type="button" class="btn sm" id="ntCopy">${icon('copy')}${t('copy')}</button></div></div>`,
    onOpen: form => { $('#ntCopy', form).onclick = () => navigator.clipboard.writeText(token).then(() => toast(t('copied'))); } });
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
      ${d.frozen ? `<button class="btn" data-do="cmd" data-id="${esc(d.id)}" data-act="unfreeze" ${d.online ? '' : 'disabled'}>${icon('play')}${t('act_unfreeze')}</button>` : ''}
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
          <button class="icon-btn danger" data-do="orgDelete" data-kind="${kind}" data-name="${esc(o.name)}" title="${t('delete')}">${icon('trash')}</button></span>` : ''}</div>`).join('')
          || emptyState(kind === 'groups' ? 'group' : 'location', t(kind === 'groups' ? 'noGroups' : 'noLocations'), t(kind === 'groups' ? 'groupsHint' : 'locationsHint'), '')}
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

// Settings are split into sections with their own navigation (#/settings/<section>).
const SETTINGS = [
  ['account', 'view', 'users'], ['alerts', 'admin', 'bell'], ['install', 'admin', 'plus'], ['notifyapi', 'manage', 'web'],
  ['proxy', 'manage', 'download'], ['branding', 'admin', 'image'], ['backup', 'admin', 'upload'],
];

VIEWS.settings = {
  mount(root) {
    setTitle(t('nav_settings'));
    const list = SETTINGS.filter(([, p]) => can(p));
    const cur = (list.find(x => x[0] === S.route.id) || list[0])[0];
    root.innerHTML = `<div class="settings"><nav class="settings-nav">${list.map(([k, , ic]) => `<a href="#/settings/${k}" class="${k === cur ? 'active' : ''}">${icon(ic)}<span>${t('set_' + k)}</span></a>`).join('')}</nav>
      <div class="settings-body" id="setBody"></div></div>`;
    SETTINGS_VIEW[cur]($('#setBody', root));
  },
};

const SETTINGS_VIEW = {
  account(root) {
    root.innerHTML = `<section class="card"><h2>${t('myAccount')}</h2>
      <dl class="kv"><dt>${t('username')}</dt><dd>${esc(S.me.username)}</dd><dt>${t('role')}</dt><dd>${t('role_' + S.me.role)}</dd></dl>
      <form id="pwForm" class="form"><h3>${t('changePassword')}</h3>
        <label>${t('currentPassword')}<input type="password" name="current_password" autocomplete="current-password" required></label>
        <label>${t('newPassword')}<input type="password" name="new_password" minlength="10" autocomplete="new-password" required></label>
        <div class="form-actions"><button class="btn primary">${t('changePassword')}</button></div></form></section>
      <section class="card"><h2>${t('system')}</h2><dl class="kv"><dt>${t('hubVersion')}</dt><dd>${esc(S.me.hub_version)}</dd><dt>${t('agentVersion')}</dt><dd>${esc(S.me.agent_version)}</dd></dl></section>
      <section class="card"><h2>${t('shortcuts')}</h2>${shortcutsHtml()}</section>`;
  },
  async install(root) {
    root.innerHTML = `<section class="card"><div class="empty">…</div></section>`;
    const s = await api('/api/settings').catch(() => null);
    if (!s || !root.isConnected) return;
    const cmd = `curl -fsSL ${location.origin}/api/bootstrap/install-agent.sh | sudo bash -s -- --hub ${location.origin} --token ${s.enroll_token} --name "NODE"`;
    root.innerHTML = `<section class="card"><h2>${t('sdCardTitle')}</h2><p class="muted">${t('sdCardHint')}</p>
        <div class="form-actions start"><button class="btn primary" data-do="sdCard">${icon('download')}${t('sdCardTitle')}</button><button class="btn" data-do="discover">${icon('search')}${t('discoverTitle')}</button></div></section>
      <section class="card"><h2>${t('manualInstall')}</h2><p class="muted">${t('manualInstallHint')}</p><pre class="code" id="instCmd">${esc(cmd)}</pre>
        <div class="form-actions start"><button class="btn sm" id="instCopy">${icon('copy')}${t('copy')}</button></div></section>
      <section class="card"><h2>${t('enrollToken')}</h2><p class="muted">${t('enrollTokenHint')}</p>
        <div class="token-row"><code class="secret" id="tokVal">••••••••••••</code><button class="btn sm ghost" id="tokShow">${t('show')}</button><button class="btn sm ghost" id="tokRotate">${icon('refresh')}${t('rotateToken')}</button></div></section>`;
    $('#tokShow', root).onclick = () => { $('#tokVal', root).textContent = s.enroll_token; };
    $('#tokRotate', root).onclick = async () => {
      if (!await confirmBox(t('confirmRotateToken'))) return;
      try { await api('/api/settings/enroll-token', { method: 'POST' }); toast(t('tokenRotated')); SETTINGS_VIEW.install(root); } catch (err) { toast(errText(err), 'err'); }
    };
    $('#instCopy', root).onclick = () => navigator.clipboard.writeText(cmd).then(() => toast(t('copied')));
  },
  notifyapi(root) { root.innerHTML = '<section class="card" id="ntfTokens"></section>'; notifyTokensCard($('#ntfTokens', root)); },
  proxy(root) { root.innerHTML = '<section class="card" id="proxyCard"></section>'; proxyCard($('#proxyCard', root)); },
  branding(root) {
    root.innerHTML = `<section class="card"><h2>${t('branding')}</h2><p class="muted">${t('brandingHint')}</p>
        <div class="logo-preview"><span class="mark" data-logo>C</span></div>
        <div class="form"><label>${t('logoFile')}<input type="file" id="logoFile" accept="image/png,image/jpeg,image/webp,image/svg+xml"></label>
        <div class="form-actions"><button class="btn ghost" id="logoRemove">${icon('trash')}${t('logoRemove')}</button><button class="btn primary" id="logoUpload">${icon('upload')}${t('logoUpload')}</button></div></div></section>`;
    this.handlers(root);
  },
  backup(root) {
    root.innerHTML = `<section class="card"><h2>${t('backupTitle')}</h2><p class="muted">${t('backupHint')}</p>
        <div class="form-actions start"><button class="btn primary" id="backupDownload">${icon('download')}${t('backupDownload')}</button></div></section>
      <section class="card"><h2>${t('restoreTitle')}</h2><p class="muted">${t('restoreHint')}</p>
        <div class="form"><label>${t('backupFile')}<input type="file" id="restoreFile" accept=".zip,application/zip"></label>
        <div class="form-actions"><button class="btn danger" id="restoreRun">${icon('upload')}${t('restoreRun')}</button></div></div></section>
      <section class="card"><h2>${t('migrateTitle')}</h2><ol class="steps">${t('migrateSteps')}</ol></section>`;
    this.handlers(root);
  },
  // logo and backup buttons (each wires only what its section shows)
  handlers(root) {
    const on = (sel, fn) => { const el = $(sel, root); if (el) el.onclick = fn; };
    applyLogo();
    on('#logoUpload', async () => {
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
    });
    on('#logoRemove', async () => {
      try { PUBLIC = { ...PUBLIC, ...(await api('/api/branding/logo', { method: 'DELETE' })) }; applyLogo(); toast(t('deleted')); } catch (err) { toast(errText(err), 'err'); }
    });
    on('#backupDownload', async () => {
      try {
        await downloadResponse(await fetch('/api/backup', { headers: { Authorization: 'Bearer ' + S.token } }), 'caracal-fleet-backup.zip');
      } catch (err) { toast(errText(err), 'err'); }
    });
    on('#restoreRun', async () => {
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
    });
  },
  async alerts(root) {
    root.innerHTML = `<section class="card"><div class="empty">…</div></section>`;
    const conf = await api('/api/alerts').catch(err => { toast(errText(err), 'err'); return null; });
    if (conf && root.isConnected) renderAlerts(root, conf);
  },
};

// ---------- alerts for administrators (Settings → Alerts)

const CH_ICON = { email: 'bell', slack: 'web', teams: 'users', discord: 'web', ntfy: 'attention', webhook: 'upload' };
const CH_TYPES = ['email', 'slack', 'teams', 'discord', 'ntfy', 'webhook'];

function renderAlerts(root, conf) {
  const st = conf;
  const save = async patch => {
    try { const next = await api('/api/alerts', { method: 'PUT', json: { ...st, ...patch } }); toast(t('saved')); renderAlerts(root, next); }
    catch (err) { toast(errText(err), 'err'); }
  };
  const on = x => (x ? 'checked' : '');
  const chRow = ch => `<div class="channel">${icon(CH_ICON[ch.type])}<div class="channel-main"><b>${esc(ch.name || t('ch_' + ch.type))}</b>
      <small class="muted">${esc(t('ch_' + ch.type))} · ${esc(ch.to || hostOf(ch.url))}</small></div>
      ${ch.enabled === false ? `<span class="tag">${t('chPaused')}</span>` : ''}
      ${ch.last ? `<span class="tag ${ch.last.ok ? 'ok' : 'critical'}" title="${esc(ch.last.error || '')}">${t(ch.last.ok ? 'chOk' : 'chFailed')} · ${clock(ch.last.ts)}</span>` : ''}
      <span class="row-actions"><button class="btn sm" data-ch-test="${esc(ch.id)}">${t('chTest')}</button>
      <button class="icon-btn" data-ch-edit="${esc(ch.id)}" title="${t('edit')}">${icon('edit')}</button>
      <button class="icon-btn danger" data-ch-del="${esc(ch.id)}" title="${t('delete')}">${icon('trash')}</button></span></div>`;
  const sm = st.smtp;
  root.innerHTML = `<section class="card"><h2>${t('alertsTitle')}</h2><p class="muted">${t('alertsHint')}</p>
      <form class="form" id="alertForm">
        <label class="check"><input type="checkbox" name="enabled" ${on(st.enabled)}>${t('alertsEnabled')}</label>
        <div class="row2"><label>${t('alertsDelay')}<input type="number" name="delay" min="0" max="1440" value="${st.delay}"><small>${t('alertsDelayHint')}</small></label>
          <label>${t('alertsLanguage')}<select name="language"><option value="cs" ${st.language === 'cs' ? 'selected' : ''}>Čeština</option><option value="en" ${st.language === 'en' ? 'selected' : ''}>English</option></select></label></div>
        <label class="check"><input type="checkbox" name="critical" ${on(st.levels.includes('critical'))}>${t('alertsCritical')}</label>
        <label class="check"><input type="checkbox" name="warning" ${on(st.levels.includes('warning'))}>${t('alertsWarning')}</label>
        <label class="check"><input type="checkbox" name="recovery" ${on(st.recovery)}>${t('alertsRecovery')}</label>
        <label>${t('alertsUrl')}<input name="public_url" value="${esc(st.public_url)}" placeholder="${esc(location.origin)}"><small>${t('alertsUrlHint')}</small></label>
        <div class="form-actions"><button class="btn primary">${t('save')}</button></div></form></section>
    <section class="card flush"><div class="toolbar"><h2 class="grow">${t('alertChannels')}</h2><button class="btn sm primary" id="chAdd">${icon('plus')}${t('chAdd')}</button></div>
      ${st.channels.length ? `<div class="channel-list">${st.channels.map(chRow).join('')}</div>` : emptyState('bell', t('noChannels'), t('noChannelsHint'), '')}</section>
    ${st.channels.some(c => c.type === 'email') ? `<section class="card"><h2>${t('smtpTitle')}</h2><p class="muted">${t('smtpHint')}</p>
      <form class="form" id="smtpForm"><div class="row2"><label>${t('smtpHost')}<input name="host" value="${esc(sm.host)}" placeholder="smtp.firma.cz" required></label>
        <label>${t('smtpPort')}<input name="port" type="number" min="1" max="65535" value="${sm.port}"></label></div>
        <div class="row2"><label>${t('smtpSecurity')}<select name="security">${['starttls', 'ssl', 'none'].map(x => `<option value="${x}" ${sm.security === x ? 'selected' : ''}>${t('smtpSec_' + x)}</option>`).join('')}</select></label>
        <label>${t('smtpSender')}<input name="sender" value="${esc(sm.sender)}" placeholder="fleet@firma.cz"></label></div>
        <div class="row2"><label>${t('smtpUser')}<input name="username" value="${esc(sm.username)}" autocomplete="off"></label>
        <label>${t('smtpPassword')}<input name="password" type="password" value="${esc(sm.password)}" autocomplete="new-password"></label></div>
        <div class="form-actions"><button class="btn primary">${t('save')}</button></div></form></section>` : ''}`;
  $('#alertForm', root).onsubmit = e => {
    e.preventDefault();
    const f = e.target;
    save({ enabled: f.enabled.checked, delay: Number(f.delay.value), language: f.language.value, recovery: f.recovery.checked,
      levels: ['critical', 'warning'].filter(x => f[x].checked), public_url: f.public_url.value.trim() });
  };
  const smtp = $('#smtpForm', root);
  if (smtp) smtp.onsubmit = e => { e.preventDefault(); save({ smtp: Object.fromEntries(new FormData(smtp)) }); };
  // the first channel switches alerts on; whoever adds a channel wants the messages
  $('#chAdd', root).onclick = () => channelDialog(null, ch => save({ channels: [...st.channels, ch], ...(st.channels.length ? {} : { enabled: true }) }));
  $$('[data-ch-edit]', root).forEach(b => { b.onclick = () => channelDialog(st.channels.find(c => c.id === b.dataset.chEdit), ch => save({ channels: st.channels.map(c => (c.id === ch.id ? ch : c)) })); });
  $$('[data-ch-del]', root).forEach(b => {
    b.onclick = async () => {
      const ch = st.channels.find(c => c.id === b.dataset.chDel);
      if (await confirmBox(t('confirmDeleteItem', { name: esc(ch.name || t('ch_' + ch.type)) }))) save({ channels: st.channels.filter(c => c !== ch) });
    };
  });
  $$('[data-ch-test]', root).forEach(b => {
    b.onclick = async () => {
      b.disabled = true; b.classList.add('busy');
      try {
        const r = await api('/api/alerts/test', { method: 'POST', json: { channel: st.channels.find(c => c.id === b.dataset.chTest) } });
        r.ok ? toast(t('chTestOk')) : toast(alertError(r.error), 'err');
      } catch (err) { toast(errText(err), 'err'); } finally { b.disabled = false; b.classList.remove('busy'); }
    };
  });
}

const alertError = e => I18N[S.lang]['err_' + e] || I18N.en['err_' + e] || e;

function channelDialog(ch, onSave) {
  const cur = ch || { type: 'email', enabled: true };
  const read = form => ({ id: ch ? ch.id : '', type: ch ? ch.type : form.querySelector('[name=type]:checked').value, name: form.name.value.trim(),
    to: form.to.value.trim(), url: form.url.value.trim(), token: form.token.value.trim(), enabled: form.enabled.checked });
  modal({
    title: ch ? t('chEdit') : t('chAdd'),
    body: `<div class="form">
      ${ch ? '' : `<div class="ch-types">${CH_TYPES.map(x => `<label class="ch-type"><input type="radio" name="type" value="${x}" ${x === cur.type ? 'checked' : ''}>${icon(CH_ICON[x])}<span>${t('ch_' + x)}</span></label>`).join('')}</div>`}
      <label>${t('name')}<input name="name" value="${esc(cur.name || '')}" placeholder="${esc(t('chNameHint'))}"></label>
      <label data-ch="to">${t('chTo')}<input name="to" value="${esc(cur.to || '')}" placeholder="it@firma.cz, servis@firma.cz"><small>${t('chToHint')}</small></label>
      <label data-ch="url">${t('chUrl')}<input name="url" value="${esc(cur.url || '')}" placeholder="https://"><small class="ch-hint"></small></label>
      <label data-ch="token">${t('chToken')}<input name="token" value="${esc(cur.token || '')}" autocomplete="off"><small>${t('chTokenHint')}</small></label>
      <label class="check"><input type="checkbox" name="enabled" ${cur.enabled !== false ? 'checked' : ''}>${t('chEnabled')}</label>
      <div class="form-actions start"><button type="button" class="btn" id="chTry">${icon('bell')}${t('chTest')}</button></div><div class="try-result"></div></div>`,
    onOpen: form => {
      const sync = () => {
        const type = ch ? ch.type : form.querySelector('[name=type]:checked').value;
        $('[data-ch="to"]', form).hidden = type !== 'email';
        $('[data-ch="url"]', form).hidden = type === 'email';
        $('[data-ch="token"]', form).hidden = !['ntfy', 'webhook'].includes(type);
        $('.ch-hint', form).textContent = type === 'email' ? '' : t('chHint_' + type);
      };
      $$('[name=type]', form).forEach(r => { r.onchange = sync; });
      sync();
      $('#chTry', form).onclick = e => tryCommand(form, e.currentTarget, () => api('/api/alerts/test', { method: 'POST', json: { channel: read(form) } }),
        r => (r.ok ? `<div class="note info">${icon('check')}${t('chTestOk')}</div>` : `<div class="note warn">${esc(alertError(r.error))}</div>`));
    },
    onSubmit: (data, form) => onSave(read(form)),
  });
}

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
      <label>${t('targetDevices')}</label>${targetPicker(null, pre)}<details class="admin-pick"><summary>${t('webAdminInstall')}</summary><p class="muted">${t('webAdminInstallHint')}</p>${adminFields('admin_', false)}</details><div class="note warn">${t('convertWarning')}</div></div>`,
    onOpen: bindTargetPicker,
    onSubmit: async data => {
      const ids = pickedTargets(data);
      if (!ids.length) throw new Error('no_devices');
      const admin = adminFromForm(data, 'admin_');
      reportQueued(await api('/api/node-image/convert', { method: 'POST', json: { version: data.version.trim(), device_ids: ids, ...(admin ? { admin_username: admin.username, admin_password: admin.password } : {}) } }), t('convertToDocker'));
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
      <details><summary>${t('staticIp')}</summary><p class="muted">${t('staticIpHint')}</p>
        <div class="row2"><label>${t('staticIpAddress')}<input name="static_ip" placeholder="192.168.1.50/24" pattern="[0-9]{1,3}(\\.[0-9]{1,3}){3}/[0-9]{1,2}" spellcheck="false"></label>
        <label>${t('staticIpGateway')}<input name="gateway" placeholder="192.168.1.1" spellcheck="false"></label></div>
        <label>${t('staticIpDns')}<input name="dns" placeholder="192.168.1.1 1.1.1.1" spellcheck="false"><small class="muted">${t('staticIpDnsHint')}</small></label></details>
      <details><summary>${t('ntpServer')}</summary><p class="muted">${t('ntpServerHint')}</p>
        <label>${t('ntpServer')}<input name="ntp" placeholder="ntp.firma.cz" spellcheck="false"></label></details>
      <details><summary>${t('dockerNetwork')}</summary><p class="muted">${t('dockerNetworkHint')}</p>
        <label>${t('dockerPool')}<input name="docker_pool" placeholder="10.200.0.0/16" pattern="[0-9]{1,3}(\\.[0-9]{1,3}){3}/[0-9]{2}" spellcheck="false"></label></details>
      <details class="admin-pick"><summary>${t('webAdminInstall')}</summary><p class="muted">${t('webAdminInstallHint')}</p>${adminFields('admin_', false)}</details>
      <h3>${t('downloadSource')}</h3>${downloadSourcePick('internet')}
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
      if (data.static_ip && !data.gateway) throw new Error('invalid_gateway');
      const admin = adminFromForm(data, 'admin_');
      const body = { ...data, wifi_country: (data.wifi_country || '').trim().toUpperCase(), admin_user: admin ? admin.username : '', admin_password: admin ? admin.password : '' };
      for (const k of ['admin_username', 'admin_password2']) delete body[k];
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
      root.innerHTML = `<div id="gHead"></div><div id="gItems"></div><div id="gDeps"></div>`;
      return;
    }
    root.innerHTML = `<section class="card flush"><div class="toolbar"><h2 class="grow">${t('nav_global')}</h2>
      ${can('content') ? `<button class="btn" data-do="globalFromDevice">${icon('copy')}${t('globalFromDevice')}</button><button class="btn primary" data-do="globalNew">${icon('plus')}${t('globalNew')}</button>` : ''}</div>
      <div id="gList"></div></section>`;
  },
  async update(root) {
    if (!S.route.id) {
      const rows = await api('/api/global-playlists').catch(() => null);
      if (!rows || !$('#gList', root)) return;
      patch($('#gList', root), `<div class="table-wrap"><table class="table"><thead><tr><th>${t('name')}</th><th>${t('playlistItems')}</th><th>${t('lastDeployment')}</th><th>${t('updated')}</th></tr></thead>
        <tbody>${rows.map(p => `<tr data-href="#/global/${p.id}"><td><b>${esc(p.name)}</b><br><small class="muted">${esc(p.description)}</small></td><td>${p.items}</td>
        <td>${p.last_deployment ? `${dt(p.last_deployment.created)}<br><small class="muted">${t('nDevices', { n: p.last_deployment.targets })} · ${t('mode_' + p.last_deployment.mode)}</small>` : '<span class="muted">—</span>'}</td>
        <td class="nowrap">${dt(p.updated)}<br><small class="muted">${esc(p.updated_by)}</small></td></tr>`).join('') || `<tr><td colspan="4">${emptyState('global', t('noGlobalPlaylists'), t('globalHint'), can('content') ? `<button class="btn primary" data-do="globalNew">${icon('plus')}${t('globalNew')}</button>` : '')}</td></tr>`}</tbody></table></div>`);
      return;
    }
    const p = await api('/api/global-playlists/' + encodeURIComponent(S.route.id)).catch(() => null);
    if (!$('#gHead', root)) return;
    if (!p) { patch($('#gHead', root), `<div class="card empty">${t('err_not_found')}</div>`); return; }
    S.global = p;
    setTitle(p.name, t('nav_global'), '#/global');
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
  if (n >= 1073741824) return (n / 1073741824).toFixed(1) + ' GB';
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

// ---------- download source: the internet, or this hub as a proxy (nodes without internet access)

const downloadSourcePick = sel => `<div class="mode-pick">${['internet', 'fleet'].map(s => `<label class="check"><input type="radio" name="download_source" value="${s}" ${s === sel ? 'checked' : ''}><span><b>${t('downloadSource_' + s)}</b><small>${t('downloadSourceHint_' + s)}</small></span></label>`).join('')}</div>`;

function downloadSourceDialog(ids, current = 'internet') {
  modal({
    title: t('downloadSource'), submit: t('save'),
    body: `<div class="form">${ids.length > 1 ? `<p class="muted">${t('downloadSourceToSelected', { n: ids.length })}</p>` : ''}${downloadSourcePick(current || 'internet')}<p class="muted">${t('downloadSourceSwitchHint')}</p></div>`,
    onSubmit: data => ids.length === 1 ? sendCommand(ids[0], 'set_download_source', { source: data.download_source })
      : sendBulk(ids, 'set_download_source', { source: data.download_source }),
  });
}

async function proxyCard(el) {
  const p = await api('/api/proxy/status').catch(() => null);
  if (!p) { el.remove(); return; }
  const nodes = S.devices.filter(d => d.download_source === 'fleet').length;
  el.innerHTML = `<div class="card-head"><h2>${t('proxyTitle')}</h2>${can('admin') ? `<button class="btn sm ghost" data-do="proxyClear">${icon('trash')}${t('proxyClear')}</button>` : ''}</div>
    <p class="muted">${t('proxyHint')}</p>
    <dl class="kv"><dt>${t('proxyNodes')}</dt><dd>${nodes}</dd>
      <dt>${t('proxyCache')}</dt><dd>${fmtSize(p.cache_bytes)} / ${fmtSize(p.cache_limit)} (${t('proxyAptPart', { size: fmtSize(p.apt_bytes) })})</dd>
      <dt>${t('proxyImages')}</dt><dd>${p.images.map(x => `<div>${esc(x.name)} <span class="muted">${fmtSize(x.size)}</span></div>`).join('') || '—'}</dd>
      ${p.jobs.length ? `<dt>${t('proxyPreparing')}</dt><dd>${p.jobs.map(j => `<div>${esc(j.image)} · ${esc(j.arch)} ${j.state === 'error' ? `<span class="tag critical">${esc(j.error)}</span>` : `<span class="tag info">${t('proxyDownloading')}</span>`}</div>`).join('')}</dd>` : ''}
      <dt>${t('proxyAptHosts')}</dt><dd><small>${p.apt_hosts.map(esc).join(', ')}</small></dd></dl>`;
}

// ------------------------------------------------------------------ modals

// The sheet fades out before it closes; a new sheet opened meanwhile cancels that and replaces it.
const MODAL = { close: null, timer: null };
function animateModalClose(dlg) {
  if (MODAL.close) return;
  MODAL.close = dlg.close.bind(dlg);
  dlg.close = value => {
    if (!dlg.open || dlg.classList.contains('closing')) return;
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) return MODAL.close(value);
    dlg.classList.add('closing');
    MODAL.timer = setTimeout(() => { dlg.classList.remove('closing'); MODAL.close(value); }, 150);
  };
}

function modal({ title, body, submit = t('save'), danger = false, wide = false, onSubmit, onOpen }) {
  const dlg = $('#modal');
  animateModalClose(dlg);
  if (dlg.classList.contains('closing')) { clearTimeout(MODAL.timer); MODAL.close(); }
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

// 'http' log-ins answer the browser's own user name / password pop-up (HTTP Basic/Digest): no login page, no form.
function profileDialog(deviceId, profile = null, targets = null) {
  const p = profile || { name: '', login_url: '', target_url: '', auth_type: 'form', ...SELECTOR_DEFAULTS };
  const keep = profile ? `placeholder="${esc(t('leaveEmpty'))}"` : 'required';
  modal({
    title: profile ? t('editProfile') : t('add_profile'), submit: profile ? t('save') : t('add'), wide: !profile && !targets,
    body: `<div class="form"><p class="muted" data-type-only="form">${t('loginHint')}</p><p class="muted" data-type-only="http" hidden>${t('httpLoginHint')}</p>
      <label>${t('loginType')}<select name="auth_type"><option value="form" ${p.auth_type !== 'http' ? 'selected' : ''}>${t('loginType_form')}</option><option value="http" ${p.auth_type === 'http' ? 'selected' : ''}>${t('loginType_http')}</option></select></label>
      <label>${t('name')}<input name="name" value="${esc(p.name)}" placeholder="${esc(t('loginNamePlaceholder'))}" required></label>
      <label data-type-only="form">${t('loginUrl')}<input name="login_url" type="url" value="${esc(p.login_url)}" placeholder="https://zabbix.example/index.php" required><small class="muted">${t('loginUrlHint')}</small></label>
      <label>${t('targetUrl')}<input name="target_url" type="url" value="${esc(p.target_url)}" placeholder="https://zabbix.example/zabbix.php?action=dashboard.view" required><small class="muted" data-type-only="form">${t('targetUrlHint')}</small><small class="muted" data-type-only="http" hidden>${t('httpLoginServerHint')}</small></label>
      <div class="row2"><label>${t('username')}<input name="username" autocomplete="off" spellcheck="false" ${keep}></label>
      <label>${t('password')}<input name="password" type="password" autocomplete="new-password" ${keep}></label></div>
      <label class="check"><input type="checkbox" data-reveal>${t('showPassword')}</label>
      <details data-type-only="form"><summary>${t('loginSelectors')}</summary><p class="muted">${t('selectorsHint')}</p>
        <label>${t('selectorUser')}<input name="user_selector" value="${esc(p.user_selector)}" spellcheck="false" required></label>
        <label>${t('selectorPass')}<input name="pass_selector" value="${esc(p.pass_selector)}" spellcheck="false" required></label>
        <label>${t('selectorSubmit')}<input name="submit_selector" value="${esc(p.submit_selector)}" spellcheck="false" required></label></details>
      ${targets ? `<p class="muted">${t('addToSelected', { n: targets.length })}</p>` : profile ? '' : `<details><summary>${t('alsoAddTo')}</summary>${targetPicker(deviceId, [], d => loginSupport(d) === 'ok')}</details>`}
      <div class="note info">${icon('lock')}${t('loginsSecurity')}</div></div>`,
    onOpen: form => {
      bindTargetPicker(form);
      const reveal = $('[data-reveal]', form);
      reveal.onchange = () => { form.password.type = reveal.checked ? 'text' : 'password'; };
      // hidden fields must not block the form: the login page and the selectors are not used by HTTP log-ins
      const sync = () => {
        const type = form.auth_type.value;
        $$('[data-type-only]', form).forEach(el => { el.hidden = el.dataset.typeOnly !== type; });
        for (const name of ['login_url', 'user_selector', 'pass_selector', 'submit_selector']) form[name].required = type === 'form';
      };
      form.auth_type.onchange = sync;
      sync();
    },
    onSubmit: data => {
      const http = data.auth_type === 'http';
      const payload = { name: data.name.trim(), auth_type: http ? 'http' : 'form', login_url: http ? '' : data.login_url.trim(), target_url: data.target_url.trim(),
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
      ${(dev(deviceId)?.capabilities || {}).grafana_discover ? `<div class="form-actions start"><button type="button" class="btn sm" data-try>${icon('refresh')}${t('grafanaTry')}</button></div><div class="try-result"></div>` : ''}
      ${targets ? `<p class="muted">${t('addToSelected', { n: targets.length })}</p>` : col ? '' : `<details><summary>${t('alsoAddTo')}</summary>${targetPicker(deviceId)}</details>`}</div>`,
    onOpen: form => {
      bindTargetPicker(form);
      const tryBtn = $('[data-try]', form);
      if (tryBtn) {
        tryBtn.onclick = () => {
          const p = grafanaPayload(Object.fromEntries(new FormData(form)));
          if (!/^https?:\/\//i.test(p.grafana_url || '') || !(p.tag || '').trim()) { $('.try-result', form).innerHTML = `<div class="note warn">${t('err_tag_required')}</div>`; return; }
          tryCommand(form, tryBtn, () => commandResult(deviceId, 'grafana_discover', { grafana_url: p.grafana_url, tag: p.tag }),
            r => `<div class="note ${r.count ? 'info' : 'warn'}"><b>${t('grafanaTryOk', { n: r.count ?? 0 })}</b>${(r.dashboards || []).slice(0, 10).map(x => `<br>${esc(x.title)}`).join('')}${r.count > 10 ? '<br>…' : ''}</div>`);
        };
      }
    },
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
      <label class="check"><input type="checkbox" name="via_fleet">${t('viaFleet')}</label>
      <label class="check"><input type="checkbox" name="forget_host_key">${t('forgetHostKey')}</label>
      <details class="admin-pick"><summary>${t('webAdminInstall')}</summary><p class="muted">${t('webAdminInstallHint')}</p>${adminFields('admin_', false)}</details>
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
      const admin = adminFromForm(data, 'admin_');
      const body = { ...data, port: Number(data.port || 22), reenroll: !!data.reenroll, forget_host_key: !!data.forget_host_key, via_fleet: !!data.via_fleet,
        admin_username: admin ? admin.username : '', admin_password: admin ? admin.password : '' };
      delete body.admin_password2;
      const r = await api('/api/provision', { method: 'POST', json: body });
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
    const ids = targetIds(b), act = b.dataset.act;
    if (['reboot', 'restart_player', 'update_agent'].includes(act) && !await confirmBox(t('confirmBulk', { action: t('act_' + act), n: ids.length }), act === 'reboot')) return;
    await sendBulk(ids, act);
  },
  bulkAddContent(b) {
    const one = b.dataset.id, ids = targetIds(b);
    modal({
      title: t('addContent'), submit: t('continue'),
      body: `<div class="radios">${bulkKinds(ids).map((k, i) => `<label class="check"><input type="radio" name="kind" value="${k}" ${i ? '' : 'checked'}>${t('add_' + k)}</label>`).join('')}</div>`,
      onSubmit: data => {
        setTimeout(() => {
          if (one) data.kind === 'collection' ? collectionDialog(one) : data.kind === 'profile' ? profileDialog(one) : assetDialog(one, data.kind);
          else if (data.kind === 'collection') bulkCollection(ids);
          else if (data.kind === 'profile') profileDialog(ids[0], null, ids);
          else assetDialog(ids[0], data.kind, null, ids);
        }, 50);
      },
    });
  },
  bulkCopy(b) { copyDialog('', { targets: targetIds(b) }); },
  bulkAssign(b) { assignDialog(targetIds(b)); },
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
  downloadSource(b) { downloadSourceDialog([b.dataset.id], dev(b.dataset.id)?.download_source); },
  bulkDownloadSource() { downloadSourceDialog([...S.selected]); },
  async proxyClear() {
    if (!await confirmBox(t('confirmProxyClear'))) return;
    const r = await api('/api/proxy/cache/clear', { method: 'POST' });
    toast(t('proxyCleared', { size: fmtSize(r.freed) }));
    render(true);
  },
  bulkSetHub(b) {
    const ids = targetIds(b);
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
  notifySend(b) { notifyDialog([b.dataset.id]); },
  async notifyClear(b) {
    if (!await confirmBox(t('confirmNotifyClear'))) return;
    await sendCommand(b.dataset.id, 'notify_clear');
  },
  async notifySettings(b) { const d = await detailOf(b.dataset.id); notifySettingsDialog([b.dataset.id], d?.notifications?.settings); },
  async notifySound(b) { const d = await detailOf(b.dataset.id); notifySoundDialog([b.dataset.id], d?.notifications?.sounds || {}); },
  toggleTheme() { $('#themeToggle').click(); },
  showShortcuts() { modal({ title: t('shortcuts'), body: shortcutsHtml() }); },
  async notifyStyle(b) {
    const ids = targetIds(b);
    const one = ids.length === 1 ? await detailOf(ids[0]) : null;
    styleDialog(ids, one?.notifications?.settings || {});
  },
  async muteAlerts(b) {
    const ids = targetIds(b), one = b.dataset.id && dev(b.dataset.id);
    const send = async minutes => { await api('/api/alerts/mute', { method: 'POST', json: { devices: ids, minutes } }); toast(t(minutes ? 'muted' : 'unmuted')); tick(); };
    if (one && one.muted_until) return send(0);
    modal({
      title: t('muteAlerts'), submit: t('mute'),
      body: `<p class="muted">${t('muteHint')}</p><div class="radios">${[60, 240, 480, 1440, 10080].map((m, i) => `<label class="check"><input type="radio" name="minutes" value="${m}" ${i === 1 ? 'checked' : ''}>${t('mute_' + m)}</label>`).join('')}</div>`,
      onSubmit: data => send(Number(data.minutes)),
    });
  },
  metricsRange(b) { METRICS.range = Number(b.dataset.range); METRICS.at = 0; render(); },
  historyFilter(b) { HISTORY.filter = b.dataset.f; render(); },
  switchLang() { setLang(S.lang === 'cs' ? 'en' : 'cs'); },
  signOut() { logout(); },
  bulkNotifySound() { notifySoundDialog([...S.selected]); },
  addWatcher(b) { watcherDialog(b.dataset.id); },
  editWatcher(b) {
    const w = (S.detail?.notifications?.watchers || []).find(x => String(x.id) === b.dataset.watcher);
    if (w) watcherDialog(b.dataset.id, w);
  },
  toggleWatcher(b) { return sendCommand(b.dataset.id, 'update_watcher', { id: b.dataset.watcher, enabled: b.dataset.on === '1' }); },
  checkWatcher(b) { return sendCommand(b.dataset.id, 'check_watcher', { id: b.dataset.watcher }); },
  async deleteWatcher(b) {
    if (!await confirmBox(t('confirmDeleteItem', { name: esc(b.dataset.name) }))) return;
    await sendCommand(b.dataset.id, 'delete_watcher', { id: b.dataset.watcher });
  },
  bulkNotify() { notifyDialog([...S.selected]); },
  nodeAdmin(b) { nodeAdminDialog([b.dataset.id]); },
  bulkNodeAdmin() {
    const ids = [...S.selected];
    if (!ids.every(id => dev(id) && adminSupport(dev(id)))) toast(t('webAdminSomeUnsupported'), 'warn');
    nodeAdminDialog(ids);
  },
  overlaySettings(b) { overlayDialog([b.dataset.id], dev(b.dataset.id)?.overlay); },
  bulkOverlay() { overlayDialog([...S.selected], null); },
  notifySkip(b) { return sendCommand(b.dataset.id, 'notify_skip'); },
  notifyRemove(b) { return sendCommand(b.dataset.id, 'notify_remove', { id: Number(b.dataset.nid) }); },
  notifyLog(b) { notifyLogDialog(b.dataset.id); },
  nodeTokenToggle(b) { return sendCommand(b.dataset.id, 'update_notify_token', { id: Number(b.dataset.tid), enabled: b.dataset.on === '1' }); },
  async nodeTokenDelete(b) {
    if (!await confirmBox(t('confirmDeleteNotifyToken', { name: esc(b.dataset.name) }))) return;
    await sendCommand(b.dataset.id, 'delete_notify_token', { id: Number(b.dataset.tid) });
  },
  bulkNotifySettings() {
    const ids = [...S.selected];
    if (!ids.every(id => dev(id) && notifySupport(dev(id)) === 'ok')) toast(t('notifySomeUnsupported'), 'warn');
    notifySettingsDialog(ids, null);
  },
  bulkWatcher() { const ids = [...S.selected]; watcherDialog(ids[0], null, ids); },
  notifyTokenEdit(b) {
    const tok = b.dataset.tid ? ($('#ntfTokens')?._tokens || []).find(x => String(x.id) === b.dataset.tid) : null;
    notifyTokenDialog(tok);
  },
  async notifyTokenDelete(b) {
    if (!await confirmBox(t('confirmDeleteNotifyToken', { name: esc(b.dataset.name) }))) return;
    await api('/api/notify-tokens/' + b.dataset.tid, { method: 'DELETE' });
    toast(t('deleted'));
    render(true);
  },
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
function bulkKinds(ids = [...S.selected]) {
  const sel = ids.map(dev).filter(Boolean);
  const all = cap => sel.every(d => (d.capabilities || {})[cap] !== false);
  const logins = sel.length && sel.every(d => loginSupport(d) === 'ok');
  return ['web', ...(all('upload') ? ['image', 'video'] : []), ...(all('add_grafana_tag') ? ['collection'] : []), ...(logins ? ['profile'] : [])];
}

function bulkCollection(ids) { collectionDialog(ids[0], null, ids); }
const targetIds = b => (b.dataset.id ? [b.dataset.id] : [...S.selected]);
const detailOf = id => (S.detail && S.detail.id === id ? S.detail : api('/api/devices/' + encodeURIComponent(id)));

document.addEventListener('click', async e => {
  const mb = e.target.closest('[data-menu]');
  if (mb) { e.preventDefault(); e.stopPropagation(); toggleMenu(mb); return; }
  const more = e.target.closest('[data-more]');
  if (more) { e.preventDefault(); e.stopPropagation(); const r = more.getBoundingClientRect(); openContextMenu(more.dataset.more, r.right - 240, r.bottom + 4); return; }
  closeMenus();
  if (e.target.closest('#palette .pi')) closePalette();
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
$('#paletteBtn').onclick = e => { e.stopPropagation(); openPalette(); };
$('#userBtn').onclick = e => {
  e.stopPropagation();
  const d = $('#userDrop');
  d.innerHTML = `<div class="dd-head"><b>${esc(S.me.username)}</b><small>${t('role_' + S.me.role)}</small></div><a href="#/settings">${t('nav_settings')}</a><button data-do="showShortcuts">${t('shortcuts')}</button><button id="logoutBtn">${t('signOut')}</button>`;
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

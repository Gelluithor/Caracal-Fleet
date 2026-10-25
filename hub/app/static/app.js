'use strict';
/* CARACAL Fleet Controller UI - no build step, vanilla JS. Translations live in i18n.js. */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const store = {
  get(k, d = null) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch { /* ignore */ } },
};

const S = {
  openCols: new Set(),   // Grafana collections shown expanded (their dashboards)
  shots: {},             // device id -> {at, url} of the last picture of its screen
  shotBusy: new Set(),
  token: store.get('caracalToken', ''),
  me: null,
  lang: store.get('caracalLang', 'cs'),
  theme: store.get('caracalTheme', 'auto'),
  devices: [],
  agentVersion: '',
  attentionCount: 0,
  org: { groups: [], locations: [] },
  selected: new Set(),
  filters: { q: '', status: '', group: '', location: '', tag: '', ...JSON.parse(store.get('caracalFilters', '{}') || '{}') },
  takeovers: [], approvalsPending: 0, approvalEnabled: false, tags: [],
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
// what the user may do with one device (a user can be limited to some locations, groups or devices)
const canOn = (d, p) => (d && Array.isArray(d.can) ? d.can.includes(p) : can(p));
// a permission with the whole fleet (system settings, staged updates, groups and locations)
const canAll = p => !!S.me && (S.me.everywhere || []).includes(p);
const dev = id => S.devices.find(d => d.id === id);
const num = v => (v === null || v === undefined || v === '' ? null : Number(v));

function errText(e) {
  const m = e && e.message ? e.message : String(e);
  const tr = k => I18N[S.lang]['err_' + k] || I18N.en['err_' + k];
  if (tr(m)) return tr(m);
  // "code: detail" (e.g. the permissions a user cannot grant, or what a backup destination answered)
  const i = m.indexOf(':'), code = m.slice(0, i), rest = m.slice(i + 1).trim();
  if (i > 0 && code === 'cannot_grant') return tr(code) + ' ' + rest.split(',').map(p => t('perm_' + p)).join(', ');
  return i > 0 && tr(code) ? tr(code) + ': ' + rest : m;
}

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
  templates: 'M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM16.5 13v7M13 16.5h7',
  wall: 'M3 4h8v6H3zM13 4h8v6h-8zM3 14h8v6H3zM13 14h8v6h-8z',
  camera: 'M3 8h4l2-3h6l2 3h4v12H3zM12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
  schedule: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2',
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
  send: 'M22 2 11 13M22 2l-7 20-4-9-9-4z',
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
  library: 'M4 4h6v16H4zM10 4h4v16h-4zM15 5l4-1 3 15-4 1z',
  timeline: 'M3 6h8M3 12h14M3 18h10M15 4v4M19 10v4M15 16v4',
  plays: 'M4 20V10M10 20V4M16 20v-8M22 20H2M7 7l5-3 5 6 4-3',
  approvals: 'M9 12l2 2 4-4M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z',
  takeover: 'M3 11v2a1 1 0 0 0 1 1h2l5 4V6L6 10H4a1 1 0 0 0-1 1zM16 8a5 5 0 0 1 0 8M19 5a9 9 0 0 1 0 14',
  widget: 'M4 4h7v7H4zM13 4h7v4h-7zM13 10h7v10h-7zM4 13h7v7H4z',
  tag: 'M3 12V3h9l9 9-9 9zM7.5 7.5h.01',
  map: 'M9 4 3 6v14l6-2 6 2 6-2V4l-6 2zM9 4v14M15 6v14',
  customer: 'M3 21V7l9-4 9 4v14M9 21v-6h6v6M8 10h.01M12 10h.01M16 10h.01',
};
const icon = (n, cls = '') => `<svg class="ic ${cls}" viewBox="0 0 24 24" aria-hidden="true"><path d="${ICONS[n] || ICONS.web}"/></svg>`;
const isTag = k => k === 'grafana-tag';
const kindLabel = k => t((k || '').includes('grafana') ? 'kind_grafana' : 'kind_' + k);
const kindIcon = k => icon(k === 'image' ? 'image' : k === 'video' ? 'video' : k === 'camera' ? 'camera' : (k || '').includes('grafana') ? 'grafana' : 'web');

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

// Slovak, German and Polish are loaded when chosen (missing texts fall back to English)
const LANG_NAMES = [['cs', 'Čeština'], ['sk', 'Slovenčina'], ['en', 'English'], ['de', 'Deutsch'], ['pl', 'Polski']];
function loadLang(l) {
  if (I18N[l] || !['sk', 'de', 'pl'].includes(l)) return Promise.resolve();
  return new Promise(resolve => {
    const el = document.createElement('script');
    el.src = `/static/i18n-${l}.js`;
    el.onload = el.onerror = () => resolve();
    document.head.append(el);
  });
}
async function setLang(l) {
  await loadLang(l);
  S.lang = l;
  store.set('caracalLang', l);
  applyStatic();
  if (S.me) { api('/api/me', { method: 'PATCH', json: { language: l } }).catch(() => {}); buildNav(); render(true); }
}

async function doLogin(e) {
  e.preventDefault();
  $('#loginError').textContent = '';
  try {
    const code = $('#loginCodeRow').hidden ? undefined : $('#loginCode').value.trim();
    const r = await fetch('/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: $('#loginUser').value.trim(), password: $('#loginPass').value, code }) });
    const d = await r.json().catch(() => ({}));
    if (d.detail === 'totp_required' || d.detail === 'invalid_totp') { $('#loginCodeRow').hidden = false; $('#loginCode').value = ''; $('#loginCode').focus(); }
    if (!r.ok) throw new Error(d.detail || r.statusText);
    $('#loginCodeRow').hidden = true;
    $('#loginCode').value = '';
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
  $('#changeForm').hidden = true;
  $('#loginCodeRow').hidden = true;
  applyStatic();
  const sso = PUBLIC.sso || {};
  $('#ssoBtn').hidden = !sso.enabled;
  $('#ssoBtn').textContent = t('ssoSignIn', { name: sso.label || 'SSO' });
}

async function doChangePassword(e) {
  e.preventDefault();
  $('#changeError').textContent = '';
  try {
    if ($('#chNew').value !== $('#chNew2').value) throw new Error('admin_password_mismatch');
    const r = await api('/api/me', { method: 'PATCH', json: { current_password: $('#chOld').value, new_password: $('#chNew').value } });
    S.token = r.token;
    store.set('caracalToken', S.token);
    $$('#changeForm input').forEach(i => { i.value = ''; });
    toast(t('pwChanged'));
    await start();
  } catch (err) { $('#changeError').textContent = errText(err); }
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
  await loadLang(S.lang);
  S.agentVersion = S.me.agent_version;
  if (S.me.must_change_password) {
    // a one-time password from an administrator: a password of one's own first, nothing else
    $('#shell').hidden = true;
    $('#login').hidden = false;
    $('#loginForm').hidden = $('#setupForm').hidden = true;
    $('#changeForm').hidden = false;
    applyStatic();
    $('#chOld').focus();
    return;
  }
  $('#changeForm').hidden = true;
  $('#login').hidden = true;
  $('#shell').hidden = false;
  applyStatic();
  buildNav();
  $('#addDeviceBtn').hidden = !can('install') && !can('settings');
  $('#userBtn').textContent = S.me.username.slice(0, 2).toUpperCase();
  if (!can('view')) {   // an account without a role and without a user group
    $('#view').innerHTML = `<section class="card">${emptyState('lock', t('noAccessTitle'), t('noAccessHint'), '')}</section>`;
    return;
  }
  await Promise.all([refresh(), loadTenants()]);
  route();
}

// ------------------------------------------------------------------ data

async function refresh() {
  try {
    const [d, org] = await Promise.all([api('/api/devices'), api('/api/org')]);
    S.devices = d.devices;
    S.attentionCount = d.attention_count;
    S.takeovers = d.takeovers || [];
    S.approvalsPending = d.approvals_pending || 0;
    S.approvalEnabled = !!d.approval_enabled;
    S.tags = d.tags || [];
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
  renderTakeoverBar();
}

// ------------------------------------------------------------------ navigation

// The menu in groups that can be folded; the simple mode leaves out the pages for advanced work (they stay reachable
// through the search and links). Both choices are kept with the user's account.
const NAV_GROUPS = [
  ['screens', [['overview', 'view'], ['devices', 'view'], ['wall', 'view'], ['map', 'view'], ['attention', 'view']]],
  ['content', [['playlists', 'view'], ['global', 'view'], ['library', 'view'], ['approvals', 'view'], ['takeover', 'control']]],
  ['planning', [['schedule', 'view'], ['timeline', 'view'], ['templates', 'view'], ['plays', 'view']]],
  ['admin', [['organization', 'view'], ['operations', 'view'], ['updates', 'updates'], ['audit', 'audit'], ['users', 'users'], ['settings', 'view']]],
];
const NAV = NAV_GROUPS.flatMap(([, items]) => items);
const PRO_PAGES = new Set(['templates', 'timeline', 'plays', 'organization', 'operations', 'updates', 'audit']);
const prefs = () => (S.me && S.me.prefs) || { ui_mode: 'pro', nav_closed: [], guide_hidden: false };
const simpleMode = () => prefs().ui_mode === 'simple';
async function savePrefs(change) {
  S.me.prefs = { ...prefs(), ...change };
  await api('/api/me', { method: 'PATCH', json: { prefs: change } }).catch(() => {});
}

function buildNav() {
  const closed = new Set(prefs().nav_closed);
  $('#nav').innerHTML = NAV_GROUPS.map(([g, items]) => {
    const list = items.filter(([v, p]) => can(p) && !(simpleMode() && PRO_PAGES.has(v)));
    if (!list.length) return '';
    return `<div class="nav-group ${closed.has(g) ? 'closed' : ''}"><button class="nav-head" data-do="navGroup" data-g="${g}" aria-expanded="${closed.has(g) ? 'false' : 'true'}"><span>${t('navGroup_' + g)}</span>${icon('chev', 'chev')}</button>
      <div class="nav-items">${list.map(([v]) => `<a href="#/${v}" data-nav="${v}" class="${v === 'takeover' ? 'nav-takeover' : ''}">${icon(v)}<span>${t('nav_' + v)}</span>${v === 'attention' ? '<em id="attBadge"></em>' : v === 'approvals' ? '<em id="apprBadge"></em>' : ''}</a>`).join('')}</div></div>`;
  }).join('');
  const mode = $('#modeSwitch');
  if (mode) mode.innerHTML = ['simple', 'pro'].map(m => `<button data-do="setMode" data-mode="${m}" class="${prefs().ui_mode === m ? 'active' : ''}" title="${esc(t('modeHint_' + m))}">${t('mode_' + m + 'UI')}</button>`).join('');
  updateNavBadges();
}

function updateNavBadges() {
  const b = $('#attBadge');
  if (b) { b.textContent = S.attentionCount || ''; b.hidden = !S.attentionCount; }
  const ab = $('#apprBadge');
  if (ab) { ab.textContent = S.approvalsPending || ''; ab.hidden = !S.approvalsPending; }
  // approvals stay out of the menu while they are off and nothing waits
  const an = $('#nav a[data-nav="approvals"]');
  if (an) an.hidden = !S.approvalEnabled && !S.approvalsPending && S.route.view !== 'approvals';
  const active = S.route.view === 'device' ? 'devices' : S.route.view;
  $$('#nav a').forEach(a => a.classList.toggle('active', a.dataset.nav === active));
  $$('#nav .nav-group').forEach(g => g.classList.toggle('has-active', !!$('a.active', g)));
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
  syncTopbar(true);   // another title (or crumb) may change the toolbar's height
}

// ---------- overview

VIEWS.overview = {
  mount(root) {
    setTitle(t('nav_overview'));
    root.innerHTML = `<div id="ovUpdate"></div><div id="ovGuide"></div><div id="ovWelcome"></div><div class="stats" id="ovStats"></div>
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
    guideCard($('#ovGuide', root), first);
    fleetUpdateNotice($('#ovUpdate', root));
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
    ${can('install') ? `<div class="welcome-opts">${opt('sdCard', 'download', t('sdCardTitle'), t('welcomeSd'))}${opt('discover', 'search', t('discoverTitle'), t('welcomeDiscover'))}${opt('provision', 'ssh', t('sshInstall'), t('welcomeSsh'))}${can('admin') ? opt('oneCommand', 'console', t('oneCommand'), t('welcomeOneCommand')) : ''}</div>`
      : `<p class="muted">${t('welcomeViewer')}</p>`}</section>`;
}

// ---------- devices

function filteredDevices() {
  const f = S.filters, q = f.q.toLowerCase();
  return S.devices.filter(d => {
    if (q && ![d.name, d.id, d.ip, d.location, d.group, d.current_name, d.hostname, ...(d.tags || [])].join(' ').toLowerCase().includes(q)) return false;
    if (f.group && d.group !== f.group) return false;
    if (f.tag && !(d.tags || []).includes(f.tag)) return false;
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
        ${S.tags.length ? `<select id="fTag">${options(S.tags, f.tag, t('allTags'))}</select>` : ''}
        <span class="muted grow" id="fCount"></span><button class="btn sm ghost" id="fReset" hidden>${icon('x')}${t('resetFilters')}</button>
        <button class="btn sm ghost" data-do="devicesExport" title="${esc(t('csvExportHint'))}">${icon('download')}${t('csvExport')}</button>
        ${can('devices') ? `<button class="btn sm ghost" data-do="devicesImport" title="${esc(t('csvImportHint'))}">${icon('upload')}${t('csvImport')}</button>` : ''}
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
    if ($('#fTag', root)) $('#fTag', root).onchange = e => set('tag', e.target.value);
    $('#fReset', root).onclick = () => { S.filters = { q: '', status: '', group: '', location: '', tag: '' }; store.set('caracalFilters', null); this.mount(root); this.update(root); };
    $('#selAll', root).onchange = e => { filteredDevices().forEach(d => e.target.checked ? S.selected.add(d.id) : S.selected.delete(d.id)); upd(); };
  },
  update(root) {
    const list = filteredDevices();
    $('#fCount', root).textContent = t('nOfM', { n: list.length, m: S.devices.length });
    $('#fReset', root).hidden = !Object.values(S.filters).some(Boolean);
    patch($('#devRows', root), list.map(d => `<tr data-href="#/device/${encodeURIComponent(d.id)}" class="${S.selected.has(d.id) ? 'sel' : ''}">
      <td class="cb"><input type="checkbox" data-pick="${esc(d.id)}" ${S.selected.has(d.id) ? 'checked' : ''}></td>
      <td><div class="dev-name">${dot(d)}<div><b>${esc(d.name)}${d.muted_until ? ` <span class="muted-ic" title="${esc(t('mutedUntil', { time: clock(d.muted_until) }))}">${icon('bell')}</span>` : ''}</b><small>${esc(d.id)} · ${esc(d.ip || '—')}</small>${tagChips(d)}</div></div></td>
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

// what the simple mode leaves out of the device menus (still in the pro mode and the search)
const PRO_ACTIONS = new Set(['overlaySettings', 'bulkOverlay', 'webDarkSettings', 'bulkWebDark', 'notifySettings', 'bulkNotifySettings',
  'notifySound', 'bulkNotifySound', 'addWatcher', 'bulkWatcher', 'notifyStyle', 'templateApplyTo', 'templateFromDevice', 'nodeAdmin',
  'bulkNodeAdmin', 'downloadSource', 'bulkDownloadSource', 'update_agent', 'caracalUpdate', 'convertNodes', 'reinstallNode', 'bulkReinstall',
  'diagnostics', 'provision', 'bulkSetHub', 'bulkTags']);

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
  const webDark = has('player_settings');
  // reinstallation and diagnostics are done by the agent itself (4.13 and newer)
  const agent = sel.some(agentFeatures), agentWhy = agent ? t('offline') : t('err_agent_update_required');
  const screen = sel.some(x => verGte(x.version, '4.14.0')), screenWhy = screen ? t('offline') : t('needAgent414');
  const any = p => sel.some(x => canOn(x, p));
  const ctl = any('control'), content = any('content'), manage = any('devices'), upd = any('updates'), install = can('install');
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
      [content && A(d ? 'overlaySettings' : 'bulkOverlay', 'settings', t('overlayBar'), { disabled: !overlay, why: why(overlay) }),
        content && A(d ? 'webDarkSettings' : 'bulkWebDark', 'moon', t('webDark'), { disabled: !webDark, why: why(webDark) }),
        content && A(d ? 'layoutSettings' : 'bulkLayout', 'widget', t('screenLayout'), { disabled: !sel.some(layoutSupport), why: why(sel.some(layoutSupport)) })],
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
        manage && A('bulkTags', 'tag', t('tagsEdit')),
        manage && A('templateApplyTo', 'templates', t('templateApply')),
        manage && d && A('templateFromDevice', 'copy', t('saveAsTemplate')),
        manage && A(d ? 'nodeAdmin' : 'bulkNodeAdmin', 'lock', t('webAdmin'), { disabled: off || !admin, why: why(admin) || t('offline') }),
        manage && (!d || d.download_source) && A(d ? 'downloadSource' : 'bulkDownloadSource', 'download', t('downloadSource'))],
      [upd && A(cmd, 'agent', t('act_update_agent'), { act: 'update_agent', disabled: off }),
        upd && A('caracalUpdate', 'updates', t('act_update_caracal'), { disabled: off }),
        upd && d && d.runtime === 'host' && A('convertNodes', 'upload', t('convertToDocker'), { disabled: off }),
        upd && A(d ? 'reinstallNode' : 'bulkReinstall', 'restart', t('reinstall'), { disabled: off || !agent, why: agentWhy })],
      [manage && d && A('diagnostics', 'audit', t('diagnostics'), { disabled: off || !agent, why: agentWhy })],
      [install && d && A('provision', 'ssh', t('sshInstall'), { data: { host: d.ip || '', name: d.name } }),
        d && canOn(d, 'console') && A('', 'console', t('console'), { href: '#/console/' + encodeURIComponent(d.id) })],
      [ctl && (!d || d.screen !== 'off') && A('screenSet', 'moon', t('screenSwitchOff'), { data: { state: 'off' }, disabled: off || !screen, why: screenWhy }),
        ctl && (!d || d.screen === 'off') && A('screenSet', 'sun', t('screenSwitchOn'), { data: { state: 'on' }, disabled: off || !screen, why: screenWhy })],
      [manage && A('bulkSetHub', 'upload', t('act_set_hub')),
        ctl && A(cmd, 'power', t('act_reboot'), { act: 'reboot', cls: 'danger', disabled: off })],
    ] },
  ].map(m => ({ ...m, sections: m.sections.map(x => x.filter(a => a && !(simpleMode() && PRO_ACTIONS.has(a.act || a.todo)))).filter(x => x.length) })).filter(m => m.sections.length);
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
    can('install') && { html: `<button class="pi" data-do="provision">${icon('plus')}<span>${t('addDevice')}</span></button>`, text: norm(t('addDevice')) },
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
// The toolbar gets lower once the page scrolls. A bottom margin of the difference keeps the page below it in place:
// without it the page got shorter by the same amount, which moved the scroll position back over the threshold and
// the toolbar flickered between both states (and short pages could not be scrolled at all).
function topbarGap(tb) {
  const c = tb.cloneNode(true);
  [c, ...c.querySelectorAll('[id]')].forEach(x => x.removeAttribute('id'));
  c.setAttribute('aria-hidden', 'true');
  c.style.cssText = `position:absolute;visibility:hidden;left:0;top:0;width:${tb.offsetWidth}px;margin:0`;
  [c, ...c.querySelectorAll('*')].forEach(x => { x.style.transition = 'none'; });
  c.classList.remove('scrolled');
  tb.parentNode.append(c);
  const full = c.offsetHeight;
  c.classList.add('scrolled');
  const gap = full - c.offsetHeight;
  c.remove();
  return Math.max(0, gap);
}
function syncTopbar(remeasure = false) {
  const tb = $('.topbar');
  if (!tb) return;
  const on = scrollY > 6;
  if (on === tb.classList.contains('scrolled') && !(remeasure && on)) return;
  tb.style.marginBottom = on ? topbarGap(tb) + 'px' : '';
  tb.classList.toggle('scrolled', on);
}
window.addEventListener('scroll', () => syncTopbar(), { passive: true });
window.addEventListener('resize', () => syncTopbar(true));

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
  // fixed position, so the menu is not cut off by a scrolling or clipped panel. It opens on the side with more room
  // and never reaches past the window: a long menu scrolls itself (scrolling the page would close it, and a page that
  // does not scroll left its last items unreachable).
  pop.style.maxHeight = '';
  const r = btn.getBoundingClientRect(), w = pop.offsetWidth, h = pop.offsetHeight;
  const below = innerHeight - r.bottom - 12, above = r.top - 12, up = h > below && above > below;
  const room = Math.max(120, up ? above : below);
  if (h > room) pop.style.maxHeight = room + 'px';
  pop.style.left = Math.max(8, Math.min(r.left, innerWidth - w - 8)) + 'px';
  pop.style.top = (up ? r.top - 4 - Math.min(h, room) : r.bottom + 4) + 'px';
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
  if (a.code === 'player' && canOn(d, 'control')) return btn('restart_player', t('act_restart_player'));
  if (a.code === 'agent_outdated' && canOn(d, 'updates') && d.online) return btn('update_agent', t('act_update_agent'));
  if (a.code === 'caracal_outdated' && canOn(d, 'updates') && d.online) return `<button class="btn sm" data-do="caracalUpdate" data-id="${esc(d.id)}">${t('act_update_caracal')}</button>`;
  if (a.code === 'local_api' && canOn(d, 'control')) return btn('reboot', t('act_reboot'));
  if (a.code === 'admin_missing' && canOn(d, 'devices') && d.online && adminSupport(d)) return `<button class="btn sm" data-do="nodeAdmin" data-id="${esc(d.id)}">${t('webAdminCreate')}</button>`;
  return inDetail ? '' : `<a class="btn sm" href="#/device/${encodeURIComponent(d.id)}">${t('open')}</a>`;
}

// ---------- device detail

const DEVICE_TABS = ['overview', 'playlist', 'collections', 'logins', 'notify', 'timeline', 'plays', 'history', 'settings'];
const PRO_TABS = new Set(['logins', 'timeline', 'plays', 'history']);
const TAB_COUNTS = { playlist: 'playlist_count', collections: 'collection_count', logins: 'profile_count', notify: 'watcher_count' };

function deviceTabs(d, tab) {
  return DEVICE_TABS.filter(x => (x !== 'settings' || canOn(d, 'devices')) && (x === tab || !simpleMode() || !PRO_TABS.has(x)) && (x !== 'collections' || !simpleMode() || d.collection_count)).map(x => `<a href="#/device/${encodeURIComponent(d.id)}/${x}" class="${x === tab ? 'active' : ''}">${t('tab_' + x)}${TAB_COUNTS[x] && d[TAB_COUNTS[x]] ? ` <span class="count">${d[TAB_COUNTS[x]]}</span>` : ''}</a>`).join('');
}

VIEWS.device = {
  mount(root) {
    const d = S.detail;
    if (!d) { setTitle(t('device')); root.innerHTML = `<div class="card empty">${t('err_device_not_found')}</div>`; return; }
    setTitle(d.name, t('nav_devices'), '#/devices');
    root.innerHTML = `<section class="card device-hero"><div id="devHero"></div><div id="devActions"></div></section>
      <div id="devFreeze"></div><div id="devAtt"></div>
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
    patch($('#devFreeze', root), freezeBanner(d));
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
    else if (tab === 'timeline') deviceTimeline(el, d);
    else if (tab === 'plays') devicePlays(el, d);
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
    if (!d || !canOn(d, 'console')) { setTitle(t('console')); root.innerHTML = `<div class="card empty">${t(d ? 'err_forbidden' : 'err_device_not_found')}</div>`; return; }
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

// A frozen screen is said loud and clear, with the way back to the playlist right next to it.
function freezeBanner(d) {
  // an emergency message holds the screen: it is ended on its page, not by resuming playback
  if (d.takeover && d.online) {
    return `<div class="freeze-banner takeover-banner"><span class="freeze-ic">${icon('takeover')}</span><div><b>${t('takeoverOnScreen', { name: esc(d.takeover.title || d.takeover.message || '') })}</b>
      <small>${d.takeover.until ? t('takeoverUntil', { time: clock(d.takeover.until) }) : t('takeoverUntilEnded')}</small></div><a class="btn" href="#/takeover">${t('open')}</a></div>`;
  }
  if (!d.frozen || !d.online) return '';
  const until = d.frozen_until ? t('frozenUntil', { time: dt(d.frozen_until) }) : t('frozenIndef');
  return `<div class="freeze-banner"><span class="freeze-ic">${icon('snow')}</span><div><b>${t('frozenOn', { name: esc(d.current_name || t('nothingPlaying')) })}</b><small>${until}</small></div>
    ${canOn(d, 'control') ? `<button class="btn primary" data-do="cmd" data-id="${esc(d.id)}" data-act="unfreeze">${icon('play')}${t('resumePlayback')}</button>` : ''}</div>`;
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
        ${d.screen ? `<dt>${t('act_screen')}</dt><dd>${t(d.screen === 'on' ? 'screenOn' : 'screenOff')}</dd>` : ''}
        <dt>${t('localApi')}</dt><dd>${d.api_ok === false ? `<span class="tag critical">${t('unavailable')}</span>` : d.api_ok ? `<span class="tag ok">OK</span>` : '—'}</dd>
        <dt>${t('downloadSource')}</dt><dd>${d.download_source ? t('downloadSource_' + d.download_source) : '—'}${canOn(d, 'devices') && d.download_source ? ` <button class="btn sm ghost" data-do="downloadSource" data-id="${esc(d.id)}">${icon('edit')}${t('edit')}</button>` : ''}</dd>
        <dt>${t('pendingCommands')}</dt><dd>${d.pending_commands}</dd>${d.notes ? `<dt>${t('notes')}</dt><dd>${esc(d.notes)}</dd>` : ''}</dl></section>
    </div>${nodeAdminCard(d)}${screenCard(d)}`;
}

// What the TV shows right now: the node's overlay takes a picture of the screen when asked (agent 4.11 and
// CARACAL 2026.10.10.4). The picture is fetched with the session token and shown from a blob URL.
const screenSupport = d => (d.capabilities || {}).screenshot === true;
async function screenBlob(id) {
  const r = await fetch(`/api/devices/${encodeURIComponent(id)}/screenshot`, { headers: { Authorization: 'Bearer ' + S.token } });
  if (!r.ok) throw new Error('not_found');
  return URL.createObjectURL(await r.blob());
}
async function captureScreen(id) {
  const c = await api(`/api/devices/${encodeURIComponent(id)}/commands`, { method: 'POST', json: { action: 'screenshot', payload: {} } });
  const until = Date.now() + 60000;
  while (Date.now() < until) {
    await new Promise(res => setTimeout(res, 1200));
    const row = await api('/api/commands/' + c.id);
    if (row.state === 'completed') return screenBlob(id);
    if (!['queued', 'delivered'].includes(row.state)) throw new Error(row.result || 'act_failed');
  }
  throw new Error('screenshot_timeout');
}
async function loadShot(id, at) {
  const old = S.shots[id];
  S.shots[id] = { at, url: old && old.url, loading: true };
  try {
    const url = await screenBlob(id);
    if (old && old.url) URL.revokeObjectURL(old.url);
    S.shots[id] = { at, url };
  } catch { S.shots[id] = { at, url: old && old.url }; }
  render();
}
function screenCard(d) {
  if (!screenSupport(d) && !d.screenshot_at) return '';
  const shot = S.shots[d.id], busy = S.shotBusy.has(d.id), hidden = store.get('caracalScreenHidden') === '1';
  if (!hidden && d.screenshot_at && (!shot || (shot.at !== d.screenshot_at && !shot.loading))) setTimeout(() => loadShot(d.id, d.screenshot_at));
  return `<section class="card screen-card ${hidden ? 'collapsed' : ''}"><div class="card-head"><h2>${t('onScreen')}</h2>${d.screenshot_at && !hidden ? `<span class="muted">${t('capturedAgo', { ago: ago(d.screenshot_at) })}</span>` : '<span class="muted"></span>'}
      ${screenSupport(d) && can('view') && !hidden ? `<button class="btn sm" data-do="screenshot" data-id="${esc(d.id)}" ${d.online && !busy ? '' : 'disabled'}>${icon('refresh')}${busy ? t('capturing') : t(d.screenshot_at ? 'refresh' : 'showScreen')}</button>` : ''}
      <button class="btn sm ghost" data-do="screenToggle" aria-expanded="${hidden ? 'false' : 'true'}">${t(hidden ? 'show' : 'hide')}</button></div>
    ${hidden ? '' : `<div class="shot ${shot && shot.url ? 'has' : ''}">${shot && shot.url ? `<img src="${esc(shot.url)}" alt="${esc(t('onScreen'))}">` : `<span>${busy ? t('capturing') : t('onScreenHint')}</span>`}</div>`}</section>`;
}

// The node's own web administration (CARACAL on port 8080): its administrator and the countdown on the TV.
const adminSupport = d => 'admin' in (d.capabilities || {});
const nodeAdminUrl = d => d.ip ? `http://${d.ip.includes(':') ? `[${d.ip}]` : d.ip}:8080` : '';

function nodeAdminCard(d) {
  if (!adminSupport(d) && !d.admin && !d.overlay && !d.player_settings) return '';
  const a = d.admin, o = d.overlay, url = nodeAdminUrl(d);
  return `<section class="card"><div class="card-head"><h2>${t('webAdmin')}</h2>${url ? `<a class="btn sm ghost" href="${esc(url)}" target="_blank" rel="noopener">${icon('web')}${t('webAdminOpen')}</a>` : ''}</div>
    <dl class="kv"><dt>${t('webAdminAccount')}</dt><dd>${!a ? '—' : a.configured ? `<b>${esc(a.username)}</b>` : `<span class="tag critical">${t('webAdminMissing')}</span>`}
        ${canOn(d, 'devices') && adminSupport(d) ? ` <button class="btn sm ghost" data-do="nodeAdmin" data-id="${esc(d.id)}" ${d.online ? '' : 'disabled'}>${icon('lock')}${t(a && a.configured ? 'webAdminChange' : 'webAdminCreate')}</button>` : ''}</dd>
      <dt>${t('overlayBar')}</dt><dd>${o ? `${t(o.enabled ? 'overlayOn' : 'overlayOff')}${o.enabled ? ` · ${esc(o.size)} px` : ''}` : '—'}
        ${canOn(d, 'content') && (d.capabilities || {}).overlay ? ` <button class="btn sm ghost" data-do="overlaySettings" data-id="${esc(d.id)}">${icon('edit')}${t('edit')}</button>` : ''}</dd>
      ${d.player_settings ? `<dt>${t('webDark')}</dt><dd>${t('dark_' + d.player_settings.web_dark)}
        ${canOn(d, 'content') && (d.capabilities || {}).player_settings ? ` <button class="btn sm ghost" data-do="webDarkSettings" data-id="${esc(d.id)}">${icon('edit')}${t('edit')}</button>` : ''}</dd>` : ''}</dl>
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

// Websites in dark mode: the node's setting for every website and Grafana collection (an item may choose otherwise).
const DARK_MODES = ['off', 'dark', 'force'];
const darkField = (v = '', grafana = false) => `<label>${t('darkMode')}<select name="dark"><option value="">${t('darkAsNode')}</option>
  ${DARK_MODES.filter(m => !grafana || m !== 'force').map(m => `<option value="${m}" ${v === m ? 'selected' : ''}>${t('dark_' + m)}</option>`).join('')}</select>
  <small class="muted">${t(grafana ? 'darkGrafanaHint' : 'darkItemHint')}</small></label>`;
function webDarkDialog(ids, current) {
  const v = (current && current.web_dark) || 'off';
  modal({
    title: t('webDark'),
    body: `<div class="form"><p class="muted">${t('webDarkHint')}</p>${ids.length > 1 ? `<p class="muted">${t('notifySettingsToSelected', { n: ids.length })}</p>` : ''}
      <div class="mode-pick">${DARK_MODES.map(m => `<label class="check"><input type="radio" name="web_dark" value="${m}" ${m === v ? 'checked' : ''}><span><b>${t('dark_' + m)}</b><small>${t('darkHint_' + m)}</small></span></label>`).join('')}</div></div>`,
    onSubmit: data => (ids.length === 1 ? sendCommand(ids[0], 'player_settings', { web_dark: data.web_dark }) : sendBulk(ids, 'player_settings', { web_dark: data.web_dark })),
  });
}

const verGte = (v, min) => {
  const a = (String(v || '').match(/\d+/g) || []).map(Number), b = min.split('.').map(Number);
  for (let i = 0; i < 3; i++) if ((a[i] || 0) !== (b[i] || 0)) return (a[i] || 0) > (b[i] || 0);
  return true;
};
const agentFeatures = d => !!d && verGte(d.version, '4.13.0');

// CARACAL installed again from scratch by the agent, optionally without its data (as a new node).
async function reinstallDialog(ids) {
  const [img] = await Promise.all([imageInfo(), loadTemplates()]);
  if (!img) return;
  modal({
    title: t('reinstall'), submit: t('reinstallNow'), danger: true,
    body: `<div class="form"><p class="muted">${t('reinstallHint')}</p>${ids.length > 1 ? `<p class="muted">${t('addToSelected', { n: ids.length })}</p>` : ''}
      ${versionField(img)}
      <label class="check"><input type="checkbox" name="wipe_data"><span><b>${t('reinstallWipe')}</b><br><small class="muted">${t('reinstallWipeHint')}</small></span></label>
      <details class="admin-pick wipe-only" hidden><summary>${t('webAdminInstall')}</summary><p class="muted">${t('webAdminInstallHint')}</p>${adminFields('admin_', false)}</details>
      ${templatePick('')}
      <div class="note warn">${t('reinstallWarning')}</div></div>`,
    onOpen: form => {
      const sync = () => { $('.wipe-only', form).hidden = !form.wipe_data.checked; };
      form.wipe_data.onchange = sync;
      sync();
    },
    onSubmit: data => {
      const wipe = !!data.wipe_data, admin = wipe ? adminFromForm(data, 'admin_') : null;
      const payload = { version: data.version.trim(), wipe_data: wipe, ...(admin || {}), ...(data.template ? { template: Number(data.template) } : {}) };
      return ids.length === 1 ? sendCommand(ids[0], 'reinstall_node', payload) : sendBulk(ids, 'reinstall_node', payload);
    },
  });
}

// What the node's screen cannot show when it stays black: the agent's report, with the usual causes pointed out.
function diagFindings(sections) {
  const out = [], of = title => { const o = (sections.find(s => s.title === title) || {}).output || ''; return o === '(empty)' ? '' : o; };
  if (/\s(9\d|100)%\s+\/(var\S*)?$/m.test(of('Disk'))) out.push('diagDiskFull');   // the system or Docker's disk
  const thr = /throttled=(0x[0-9a-f]+)/i.exec(of('Power and temperature (Raspberry Pi)'));
  if (thr && parseInt(thr[1], 16) & 0x50005) out.push('diagPower');
  const outputs = of('Display outputs');
  if (outputs && !/: connected/.test(outputs)) out.push('diagNoDisplay');
  if (/oom-kill|out of memory/i.test(of('Out of memory'))) out.push('diagMemory');
  if (/inactive|failed|activating \(auto-restart\)/.test(of('Display service'))) out.push('diagDisplayDown');
  if (/Restarting|Exited|unhealthy/.test(of('Containers'))) out.push('diagContainers');
  return out;
}
async function diagnosticsDialog(id) {
  const d = dev(id);
  const dlg = modal({ title: `${t('diagnostics')} · ${esc(d ? d.name : id)}`, wide: true, body: `<div id="diagBox"><div class="note info">${t('loadingFromNode')}</div></div>` });
  const el = $('#diagBox', dlg);
  let r;
  try { r = await commandResult(id, 'diagnostics', {}, 120000); } catch (e) { if (el.isConnected) el.innerHTML = `<div class="note warn">${esc(errText(e))}</div>`; return; }
  if (!el.isConnected) return;
  const sections = r.sections || [], found = diagFindings(sections);
  const head = `${d ? d.name : id} · ${r.model || ''} · ${r.os || ''} · CARACAL ${r.caracal_version || '?'} (${r.runtime || '?'}) · agent ${r.version || '?'} · ${dt(r.taken)}`;
  el.innerHTML = `<p class="muted">${t('diagnosticsHint')}</p><p><small class="muted">${esc(head)}</small></p>
    ${found.length ? `<div class="note warn"><div>${found.map(k => `<b>${t(k)}</b><br><small>${t(k + 'Hint')}</small>`).join('<br>')}</div></div>` : `<div class="note ok">${t('diagNothing')}</div>`}
    <div class="form-actions start"><button type="button" class="btn sm" data-copy-diag>${icon('copy')}${t('diagnosticsCopy')}</button></div>
    ${sections.map(s => `<details class="diag"><summary>${esc(s.title)}</summary><pre>${esc(s.output)}</pre></details>`).join('')}`;
  $('[data-copy-diag]', el).onclick = () => navigator.clipboard.writeText(head + '\n\n' + sections.map(s => `## ${s.title}\n${s.output}`).join('\n\n')).then(() => toast(t('copied')));
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
      <td>${c.state === 'queued' && canOn(dev(c.device_id) || S.detail, 'control') ? `<button class="btn sm ghost" data-do="cancelCmd" data-cid="${c.id}">${t('cancel')}</button>` : ''}</td></tr>`).join('')
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
        ${c.state === 'queued' && canOn(dev(c.device_id) || S.detail, 'control') ? `<button class="btn sm ghost" data-do="cancelCmd" data-cid="${c.id}">${t('cancel')}</button>` : ''}</div><time>${clock(c.created)}</time></div>` }))),
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
      <label>${t('tags')}<input name="tags" value="${esc((d.tags || []).join(', '))}" list="dlTags" placeholder="${esc(t('tagsPlaceholder'))}"><small class="muted">${t('tagsHint')}</small></label>
      <label>${t('notes')}<textarea name="notes" rows="3">${esc(d.notes)}</textarea></label>
      ${orgDatalists()}<datalist id="dlTags">${S.tags.map(x => `<option value="${esc(x)}">`).join('')}</datalist>
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
  const edit = canOn(d, 'content'), ctl = canOn(d, 'control') && d.online;
  const assets = orderedAssets(d);
  const html = `<section class="card flush">
    <div class="toolbar">
      <h2 class="grow">${t('playlist')} <span class="muted">(${assets.length})</span></h2>
      ${edit ? `<button class="btn" data-do="addAsset" data-kind="web" data-id="${esc(d.id)}">${icon('web')}${t('addWeb')}</button>
      ${d.capabilities.upload === false ? '' : `<button class="btn" data-do="addAsset" data-kind="image" data-id="${esc(d.id)}">${icon('image')}${t('addImage')}</button>
      <button class="btn" data-do="addAsset" data-kind="video" data-id="${esc(d.id)}">${icon('video')}${t('addVideo')}</button>`}
      ${d.capabilities.add_camera === true ? `<button class="btn" data-do="addCamera" data-id="${esc(d.id)}">${icon('camera')}${t('add_camera')}</button>` : ''}
      ${d.capabilities.widgets === true ? `<button class="btn" data-do="addWidget" data-id="${esc(d.id)}">${icon('widget')}${t('widget')}</button>` : ''}
      ${d.capabilities.upload === false ? '' : `<button class="btn" data-do="addDocument" data-target="device" data-id="${esc(d.id)}">${icon('audit')}${t('document')}</button>`}
      <button class="btn" data-do="copyContent" data-id="${esc(d.id)}">${icon('copy')}${t('copyPlaylist')}</button>` : ''}
    </div>
    ${S.pendingOrder ? `<div class="savebar">${t('orderChanged')}<button class="btn primary sm" data-do="saveOrder" data-id="${esc(d.id)}">${t('saveOrder')}</button><button class="btn sm ghost" data-do="resetOrder">${t('cancel')}</button></div>` : ''}
    ${!d.online ? `<div class="note">${t('offlineQueued')}</div>` : ''}
    ${d.pending_commands ? `<div class="note info">${t('pendingNote', { n: d.pending_commands })}</div>` : ''}
    ${d.capabilities.upload === false && edit ? `<div class="note">${t('noMediaApi')}</div>` : ''}
    <ol class="playlist" id="plist">${assets.map((a, i) => {
      // a Grafana collection plays its dashboards one by one; nodes from 2026.10.10.4 report them
      const dash = isTag(a.kind) ? (d.collections.find(c => String(c.id) === String(a.id)) || {}).dashboards : null;
      const nowId = String(d.current_asset_id ?? d.current_id);
      const playing = d.online && (String(a.id) === nowId || !!(dash && dash.some(x => String(x.id) === nowId)));
      const open = dash && dash.length && S.openCols.has(String(a.id));
      return `<li class="pl-row ${playing ? 'playing' : ''} ${a.enabled ? '' : 'disabled'}" data-aid="${esc(a.id)}" ${edit ? 'draggable="true"' : ''}>
        ${edit ? `<span class="handle" title="${t('dragToReorder')}">${icon('drag')}</span>` : ''}<span class="idx">${i + 1}</span>
        <span class="kind k-${esc(a.kind)}">${isWidget(a) ? icon('widget') : kindIcon(a.kind)}</span>
        <div class="pl-main"><b>${esc(a.name)}</b>${validityTag(a)}<small>${isWidget(a) ? esc(widgetLabel(a.source)) : esc(kindLabel(a.kind))}${isTag(a.kind) ? ` · ${esc(t('grafanaTag'))} ${esc(a.tag)}` : a.source && a.kind === 'web' && !isWidget(a) ? ' · ' + esc(a.source) : ''}</small>
          ${dash && dash.length ? `<button class="pl-expand ${open ? 'open' : ''}" data-do="toggleCol" data-col="${esc(a.id)}" aria-expanded="${open ? 'true' : 'false'}">${icon('chev')}${t('dashboardsCount', { n: dash.length })}</button>` : ''}</div>
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
        </span></li>${open ? dashboardRows(d, dash, ctl && a.enabled, 'pl-dash') : ''}`;
    }).join('') || `<li class="empty">${d.api_ok === false ? t('playlistUnavailable') : t('playlistEmpty')}</li>`}</ol></section>`;
  if (el._dragging) return;
  patch(el, html);
  if (edit) bindDrag($('#plist', el));
  if (edit && d.capabilities.upload !== false) {
    dropFiles(el, files => uploadMany(files, (f, up) => sendCommand(S.detail.id, 'add_media', mediaItem(f, up))));
  }
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
  const edit = canOn(d, 'content'), ctl = canOn(d, 'control') && d.online;
  const canAdd = edit && d.capabilities.add_grafana_tag !== false;
  return `<section class="card flush"><div class="toolbar"><h2 class="grow">${t('grafanaCollections')} <span class="muted">(${d.collections.length})</span></h2>
    ${canAdd ? `<button class="btn" data-do="addCollection" data-id="${esc(d.id)}">${icon('plus')}${t('addCollection')}</button>
    <button class="btn" data-do="copyCollections" data-id="${esc(d.id)}">${icon('copy')}${t('copyCollections')}</button>` : ''}</div>
    ${edit && !canAdd ? `<div class="note">${t('tagCollectionsNote')}</div>` : ''}
    <div class="col-grid">${d.collections.map(c => `<div class="col-card">
      <div class="col-head">${icon('grafana')}<b>${esc(c.name)}</b><span class="muted">${t('grafanaTag')}: ${esc(c.tag || '—')}${c.duration ? ' · ' + dur(c.duration) : ''}</span></div>
      <ul><li title="${esc(c.grafana_url)}">${esc(c.grafana_url || '—')}</li></ul>
      ${c.dashboards ? `<details class="col-dash" ${S.openCols.has(String(c.id)) ? 'open' : ''} data-col="${esc(c.id)}"><summary>${icon('chev')}${t('dashboardsCount', { n: c.dashboards.length })}</summary>
        ${c.dashboards_error ? `<p class="note">${esc(c.dashboards_error)}</p>` : ''}<ul class="dash-list">${dashboardRows(d, c.dashboards, ctl, 'dash-row')}</ul></details>` : ''}
      <div class="col-actions">
        ${ctl ? `<button class="btn sm" data-do="cmd" data-id="${esc(d.id)}" data-act="show_collection" data-col="${esc(c.id)}">${icon('eye')}${t('showNow')}</button>
        <button class="btn sm" data-do="freeze" data-id="${esc(d.id)}" data-col="${esc(c.id)}" data-name="${esc(c.name)}">${icon('snow')}${t('freezeItem')}</button>` : ''}
        ${edit ? `<button class="icon-btn" title="${t('edit')}" data-do="editCollection" data-id="${esc(d.id)}" data-col="${esc(c.id)}">${icon('edit')}</button>
        <button class="icon-btn" title="${t('copyTo')}" data-do="copyContent" data-id="${esc(d.id)}" data-col="${esc(c.id)}">${icon('copy')}</button>
        <button class="icon-btn danger" title="${t('delete')}" data-do="deleteCollection" data-id="${esc(d.id)}" data-col="${esc(c.id)}" data-name="${esc(c.name)}">${icon('trash')}</button>` : ''}
      </div></div>`).join('') || `<div class="empty">${t('noCollections')}</div>`}</div></section>`;
}

// The dashboards of a Grafana collection, each can be shown or frozen on its own like a playlist item.
// an expanded collection card stays expanded when the view is redrawn
document.addEventListener('toggle', e => {
  const el = e.target;
  if (!el.matches || !el.matches('details.col-dash')) return;
  el.open ? S.openCols.add(el.dataset.col) : S.openCols.delete(el.dataset.col);
}, true);
function dashboardRows(d, list, ctl, cls) {
  const nowId = String(d.current_asset_id ?? d.current_id);
  return (list || []).map(x => {
    const playing = d.online && String(x.id) === nowId;
    return `<li class="${cls} ${playing ? 'playing' : ''}"><span class="dash-name">${icon('grafana')}<span title="${esc(x.source)}">${esc(x.name)}</span></span>
      ${playing ? `<span class="tag ok">${d.frozen ? icon('snow') + t('frozen') : icon('play') + t('playing')}</span>` : ''}
      <span class="pl-actions">${ctl ? `<button class="icon-btn" title="${t('showNow')}" data-do="cmd" data-id="${esc(d.id)}" data-act="show" data-item="${esc(x.id)}">${icon('eye')}</button>
        <button class="icon-btn" title="${t('freezeItem')}" data-do="freeze" data-id="${esc(d.id)}" data-item="${esc(x.id)}" data-name="${esc(x.name)}">${icon('snow')}</button>` : ''}</span></li>`;
  }).join('') || `<li class="${cls} muted">${t('noDashboards')}</li>`;
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
  const support = loginSupport(d), edit = canOn(d, 'content') && support === 'ok';
  const profiles = d.profiles || [];
  return `<section class="card flush"><div class="toolbar"><h2 class="grow">${t('loginProfiles')} <span class="muted">(${profiles.length})</span></h2>
    ${edit ? `<button class="btn primary" data-do="addProfile" data-id="${esc(d.id)}">${icon('plus')}${t('addLogin')}</button>` : ''}</div>
    ${support !== 'ok' && canOn(d, 'content') ? `<div class="note">${t(support === 'node' ? 'loginsNeedCaracal' : 'loginsNeedAgent')}</div>` : ''}
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
    canOn(d, 'updates') && d.online ? (support === 'node' ? `<button class="btn primary" data-do="caracalUpdate" data-id="${esc(d.id)}">${icon('updates')}${t('act_update_caracal')}</button>`
      : `<button class="btn primary" data-do="cmd" data-id="${esc(d.id)}" data-act="update_agent">${icon('agent')}${t('act_update_agent')}</button>`) : '')}</section>`;
  const s = { ...NOTIFY_DEFAULTS, ...n.settings }, edit = canOn(d, 'content'), ctl = canOn(d, 'control'), caps = d.capabilities || {};
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
  ${notifyQueueCard(d, n, ctl && caps.notify_remove)}${nodeTokensCard(d, n, canOn(d, 'devices') && caps.notify_token_update)}
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
  const content = canOn(dev(id), 'content'), manage = canOn(dev(id), 'devices');
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
  // the picture of the notification look, placed and sized for this one notification (nodes from 2026.10.10.4)
  const picture = ids.map(dev).some(x => x && (x.capabilities || {}).notify_image === true);
  modal({
    title: t('notifySend'), submit: t('notifySendSubmit'),
    body: `<div class="form">${ids.length > 1 ? `<p class="muted">${t('notifyToSelected', { n: ids.length })}</p>` : ''}
      <label>${t('notifyTitle')}<input name="title" maxlength="120"></label>
      <label>${t('notifyMessage')}<textarea name="message" rows="3" maxlength="600"></textarea></label>
      <div class="row2"><label>${t('notifyLevel')}<select name="level">${choice(NOTIFY_LEVELS, 'info', 'notifyLevel_')}</select></label>
      <label>${t('notifyDuration')}<input name="duration" type="number" min="3" max="120" placeholder="${esc(t('notifyDurationDefault'))}"></label></div>
      <label>${t('notifySound')}<select name="sound"><option value="">${t('notifySoundBySettings')}</option><option value="1">${t('notifySoundPlay')}</option><option value="0">${t('notifySoundSilent')}</option></select></label>
      ${picture ? `<div class="row2"><label>${t('notifyPicture')}<select name="image"><option value="">${t('notifyPictureByLook')}</option>${['none', 'left', 'right', 'top', 'bottom', 'background'].map(v => `<option value="${v}">${t('notifyPicture_' + v)}</option>`).join('')}</select></label>
      <label>${t('notifyPictureSize')}<input name="image_size" type="number" min="10" max="60" step="5" placeholder="${esc(t('notifyPictureByLook'))}"></label></div>` : ''}</div>`,
    onSubmit: data => {
      if (!data.title.trim() && !data.message.trim()) throw new Error('notification_text_required');
      const payload = { title: data.title.trim(), message: data.message.trim(), level: data.level };
      if (data.duration) payload.duration = Number(data.duration);
      if (data.sound) payload.sound = data.sound === '1';
      if (data.image) payload.image = data.image;
      if (data.image_size) payload.image_size = Number(data.image_size);
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
function styleDialog(ids, settings, one = null) {
  let editor = null, picture = null;   // picture: {file_id} of a new upload or {reset: true}, sent when saving
  const devs = ids.map(dev).filter(Boolean);
  const canPicture = devs.length && devs.every(x => (x.capabilities || {}).notify_image === true);
  const canCapture = one && screenSupport(one) && one.online;
  const dlg = modal({
    title: t('notifyLook'), wide: true, submit: t('save'),
    body: `${ids.length > 1 ? `<p class="muted">${t('notifyLookToSelected', { n: ids.length })}</p>` : ''}<p class="muted">${t('notifyLookHint')}</p>
      ${!canPicture ? `<p class="muted">${t('notifyPictureNeedsUpdate')}</p>` : one?.notifications?.image ? `<p class="muted">${t('notifyPictureOnDevice', { name: esc(one.notifications.image.name || '') })}</p>` : ''}<div id="lookEditor"></div>`,
    onOpen: form => {
      editor = NotifyStyle.editor($('#lookEditor', form), settings.style, {
        t: (cs, en) => (S.lang === 'cs' ? cs : en), position: settings.position, scale: settings.scale,
        onImage: canPicture ? async file => {
          if (file.size > 5 * 1024 * 1024) { toast(errText(new Error('file_too_large')), 'err'); throw new Error('file_too_large'); }
          try { const up = await uploadFile(file, () => {}); picture = { file_id: up.id }; } catch (err) { toast(errText(err), 'err'); throw err; }
          return URL.createObjectURL(file);
        } : null,
        onImageRemove: canPicture ? async () => { picture = { reset: true }; } : null,
        capture: canCapture ? () => captureScreen(one.id) : null,
      });
      // the picture the device has, when it was sent through Fleet (the hub keeps the file)
      if (one && one.notifications && one.notifications.image) {
        fetch(`/api/devices/${encodeURIComponent(one.id)}/notify-image`, { headers: { Authorization: 'Bearer ' + S.token } })
          .then(r => (r.ok ? r.blob() : null)).then(b => { if (b && editor && !picture) editor.setImage(URL.createObjectURL(b)); }).catch(() => {});
      }
    },
    onSubmit: async () => {
      if (picture) await (ids.length === 1 ? sendCommand(ids[0], 'notify_image', picture) : sendBulk(ids, 'notify_image', picture));
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
async function tryCommand(form, btn, run, show, target = '.try-result') {
  const out = $(target, form);
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
    patch($('#pHead', ed), freezeBanner(d) + `<section class="card dev-hero slim"><div class="hero-main"><div class="hero-title">${statusBadge(d)}<h2><a href="#/device/${encodeURIComponent(d.id)}">${esc(d.name)}</a></h2></div>
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
        ${canAll('devices') ? `<button class="btn sm primary" data-do="orgEdit" data-kind="${kind}">${icon('plus')}${t('add')}</button>` : ''}</div>
        <div class="org-list">${S.org[kind].map(o => `<div class="org-row">
          <a href="#/devices?${field}=${encodeURIComponent(o.name)}"><b>${esc(o.name)}</b><small class="muted">${esc([o.address, o.description].filter(Boolean).join(' · '))}</small></a>
          <span class="muted nowrap">${o.online}/${o.total} ${t('online').toLowerCase()}</span>
          ${canAll('devices') ? `<span class="row-actions"><button class="icon-btn" data-do="orgEdit" data-kind="${kind}" data-name="${esc(o.name)}" title="${t('edit')}">${icon('edit')}</button>
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
    root.innerHTML = `<nav class="tabs"><a href="#/operations/commands" class="${tab === 'commands' ? 'active' : ''}">${t('commandHistory')}</a>${can('install') ? `<a href="#/operations/jobs" class="${tab === 'jobs' ? 'active' : ''}">${t('sshJobs')}</a>` : ''}</nav>
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

// ---------- users, roles and user groups

const BUILTIN_ROLES = ['admin', 'manager', 'operator', 'viewer'];
const DEVICE_PERMS = ['view', 'control', 'content', 'approve', 'schedule', 'devices', 'updates', 'console'];
const SYSTEM_PERMS = ['install', 'notify_api', 'audit', 'users', 'settings'];
const roleLabel = (key, name) => (BUILTIN_ROLES.includes(key) ? t('role_' + key) : name || (S.roles || []).find(r => r.key === key)?.name || (key ? key : t('roleNone')));
// "all devices" or the locations, groups and devices a role holds for
function scopeText(s) {
  if (!s || s.all) return t('scopeAll');
  const parts = [...(s.locations || []).map(x => `${icon('location')}${esc(x)}`), ...(s.groups || []).map(x => `${icon('group')}${esc(x)}`),
    ...(s.devices || []).map(x => `${icon('devices')}${esc(dev(x)?.name || x)}`)];
  return parts.length ? `<span class="scope-list">${parts.join('')}</span>` : `<span class="muted">${t('scopeNothing')}</span>`;
}

VIEWS.users = {
  mount(root) {
    setTitle(t('nav_users'));
    const tab = ['roles', 'groups', 'tenants'].includes(S.route.id) ? S.route.id : 'people';
    if (tab === 'tenants') { VIEWS.tenants.mount(root); return; }
    const add = { people: ['userEdit', 'newUser'], roles: ['roleEdit', 'roleNew'], groups: ['userGroupEdit', 'userGroupNew'] }[tab];
    root.innerHTML = `${usersTabs(tab)}
      <section class="card flush"><div class="toolbar"><p class="muted grow">${t(tab === 'people' ? 'usersHint' : tab === 'roles' ? 'rolesHint' : 'userGroupsHint')}</p><button class="btn primary" data-do="${add[0]}">${icon('plus')}${t(add[1])}</button></div><div id="uTable"></div></section>`;
  },
  async update(root) {
    if (S.route.id === 'tenants') return VIEWS.tenants.update(root);
    const [users, roles, groups, tenants] = await Promise.all([api('/api/users'), api('/api/roles'), api('/api/user-groups'), api('/api/tenants')].map(p => p.catch(() => null)));
    if (!users || !roles || !groups) return;
    S.tenants = tenants || [];
    Object.assign(S, { users, roles: roles.roles, userGroups: groups });
    const tab = ['roles', 'groups'].includes(S.route.id) ? S.route.id : 'people';
    const groupName = id => groups.find(g => g.id === id)?.name || '';
    if (tab === 'people') {
      patch($('#uTable', root), `<div class="table-wrap"><table class="table"><thead><tr><th>${t('username')}</th><th>${t('role')}</th><th>${t('scope')}</th><th>${t('userGroupsTab')}</th><th>${t('state')}</th><th></th></tr></thead>
        <tbody>${users.map(u => `<tr><td><b>${esc(u.username)}</b>${u.username === S.me.username ? ` <span class="tag">${t('you')}</span>` : ''}<br><small class="muted">${(LANG_NAMES.find(x => x[0] === u.language) || LANG_NAMES[0])[1]} · ${dt(u.created)}</small></td>
        <td>${u.role ? `<span class="badge role">${esc(roleLabel(u.role))}</span>` : `<span class="muted">${t('roleNone')}</span>`}${u.tenant_id ? ` <span class="tag info">${icon('customer')}${esc(tenantName(u.tenant_id))}</span>` : ''}</td><td>${u.role ? scopeText(u.scope) : '—'}</td>
        <td>${u.groups.map(g => `<span class="tag">${esc(groupName(g))}</span>`).join(' ') || '—'}</td>
        <td>${u.enabled ? `<span class="tag ok">${t('active')}</span>` : `<span class="tag">${t('disabled')}</span>`}${u.totp ? ` <span class="tag info">2FA</span>` : ''}${u.must_change_password ? ` <span class="tag frozen">${t('pwOneTime')}</span>` : ''}${u.sso ? ` <span class="tag">SSO</span>` : ''}</td>
        <td class="actions-cell">${u.totp ? `<button class="btn sm ghost" data-do="totpReset" data-uid="${u.id}" data-name="${esc(u.username)}">${t('totpReset')}</button>` : ''}<button class="icon-btn" data-do="userEdit" data-uid="${u.id}" title="${t('edit')}">${icon('edit')}</button>
        ${u.username !== S.me.username ? `<button class="icon-btn danger" data-do="userDelete" data-uid="${u.id}" data-name="${esc(u.username)}" title="${t('delete')}">${icon('trash')}</button>` : ''}</td></tr>`).join('')}</tbody></table></div>`);
    } else if (tab === 'roles') {
      patch($('#uTable', root), `<div class="role-list">${roles.roles.map(r => `<div class="role-card">
        <div class="role-head"><b>${esc(roleLabel(r.key, r.name))}</b>${r.builtin ? ` <span class="tag">${t('roleBuiltin')}</span>` : ''}<span class="grow"></span>
          <small class="muted">${t('roleUsedBy', { u: r.users, g: r.groups })}</small>
          <span class="row-actions"><button class="btn sm ghost" data-do="roleEdit" data-copy="${esc(r.key)}">${icon('copy')}${t('duplicate')}</button>${r.builtin ? '' : `<button class="icon-btn" data-do="roleEdit" data-key="${esc(r.key)}" title="${t('edit')}">${icon('edit')}</button><button class="icon-btn danger" data-do="roleDelete" data-key="${esc(r.key)}" data-name="${esc(r.name)}" title="${t('delete')}">${icon('trash')}</button>`}</span></div>
        <p class="muted">${esc(r.builtin ? t('roleDesc_' + r.key) : r.description || '')}</p>
        <div class="perm-chips">${r.permissions.map(p => `<span class="perm ${SYSTEM_PERMS.includes(p) ? 'system' : ''}" title="${esc(t('permHint_' + p))}">${t('perm_' + p)}</span>`).join('')}</div></div>`).join('')}</div>`);
    } else {
      patch($('#uTable', root), groups.length ? `<div class="table-wrap"><table class="table"><thead><tr><th>${t('name')}</th><th>${t('role')}</th><th>${t('scope')}</th><th>${t('members')}</th><th></th></tr></thead>
        <tbody>${groups.map(g => `<tr><td><b>${esc(g.name)}</b>${g.description ? `<br><small class="muted">${esc(g.description)}</small>` : ''}</td><td><span class="badge role">${esc(roleLabel(g.role))}</span></td><td>${scopeText(g.scope)}</td>
        <td>${g.members.map(id => esc(users.find(u => u.id === id)?.username || '')).filter(Boolean).join(', ') || '—'}</td>
        <td class="actions-cell"><button class="icon-btn" data-do="userGroupEdit" data-gid="${g.id}" title="${t('edit')}">${icon('edit')}</button><button class="icon-btn danger" data-do="userGroupDelete" data-gid="${g.id}" data-name="${esc(g.name)}" title="${t('delete')}">${icon('trash')}</button></td></tr>`).join('')}</tbody></table></div>`
        : emptyState('users', t('noUserGroups'), t('userGroupsEmptyHint'), `<button class="btn primary" data-do="userGroupEdit">${icon('plus')}${t('userGroupNew')}</button>`));
    }
  },
};

// The scope of a role: all devices, or chosen locations, device groups and devices.
function scopeFields(scope) {
  const s = scope && !scope.all ? scope : null;
  const box = (kind, items) => `<details class="scope-box" ${s && (s[kind] || []).length ? 'open' : ''}><summary>${t('scope_' + kind)} <span class="muted" data-count="${kind}"></span></summary>
    <div class="scope-items">${items.map(([v, l]) => `<label class="check"><input type="checkbox" data-scope="${kind}" value="${esc(v)}" ${s && (s[kind] || []).includes(v) ? 'checked' : ''}>${esc(l)}</label>`).join('') || `<span class="muted">${t('noneYet')}</span>`}</div></details>`;
  return `<div class="scope-pick"><span class="label">${t('scope')}</span>
    <div class="radios"><label class="check"><input type="radio" name="scope_all" value="1" ${s ? '' : 'checked'}>${t('scopeAll')}</label>
    <label class="check"><input type="radio" name="scope_all" value="0" ${s ? 'checked' : ''}>${t('scopeChosen')}</label></div>
    <div class="scope-chosen" ${s ? '' : 'hidden'}><p class="muted">${t('scopeHint')}</p>
      ${box('locations', (S.org.locations || []).map(x => [x.name, x.name]))}${box('groups', (S.org.groups || []).map(x => [x.name, x.name]))}${box('devices', S.devices.map(d => [d.id, d.name]))}</div></div>`;
}
function bindScope(form) {
  const sync = () => {
    $('.scope-chosen', form).hidden = form.querySelector('[name=scope_all]:checked').value === '1';
    for (const k of ['locations', 'groups', 'devices']) { const n = $$(`[data-scope="${k}"]:checked`, form).length; $(`[data-count="${k}"]`, form).textContent = n ? `(${n})` : ''; }
  };
  form.addEventListener('change', sync);
  sync();
}
function readScope(form) {
  if (form.querySelector('[name=scope_all]:checked').value === '1') return { all: true };
  const out = {};
  for (const k of ['locations', 'groups', 'devices']) out[k] = $$(`[data-scope="${k}"]:checked`, form).map(x => x.value);
  return out;
}
const roleOptions = (value, none = false) => (none ? `<option value="" ${value ? '' : 'selected'}>${t('roleNone')}</option>` : '')
  + (S.roles || BUILTIN_ROLES.map(key => ({ key }))).map(r => `<option value="${esc(r.key)}" ${value === r.key ? 'selected' : ''}>${esc(roleLabel(r.key, r.name))}</option>`).join('');

function userDialog(uid) {
  const u = uid ? S.users.find(x => x.id === uid) : null;
  const groups = S.userGroups || [];
  modal({
    title: u ? t('editUser') + ': ' + esc(u.username) : t('newUser'), wide: true,
    body: `<div class="form">${u ? '' : `<label>${t('username')}<input name="username" required pattern="[A-Za-z0-9._@\\-]{2,64}"></label>`}
      <label>${u ? t('resetPassword') : t('password')}<span class="pw-row"><input name="password" type="password" minlength="10" autocomplete="new-password" ${u ? `placeholder="${t('leaveEmpty')}"` : 'required'}>
        <button type="button" class="btn sm" data-gen-pw>${icon('refresh')}${t('pwGenerate')}</button></span></label>
      <label class="check must-change" ${u ? 'hidden' : ''}><input type="checkbox" name="must_change" ${u ? '' : 'checked'}><span>${t('pwMustChange')}<br><small class="muted">${t('pwMustChangeHint')}</small></span></label>
      <div class="row2"><label>${t('role')}<select name="role">${roleOptions(u ? u.role : 'viewer', true)}</select></label>
      <label>${t('language')}<select name="language">${LANG_NAMES.map(([k, n]) => `<option value="${k}" ${(u?.language || 'cs') === k ? 'selected' : ''}>${n}</option>`).join('')}</select></label></div>
      ${(S.tenants || []).length && !S.me.tenant_id ? `<label>${t('tenant')}<select name="tenant_id"><option value="">${t('tenantNone')}</option>${S.tenants.map(x => `<option value="${x.id}" ${u && u.tenant_id === x.id ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}</select><small class="muted">${t('tenantUserHint')}</small></label>` : ''}
      ${scopeFields(u ? u.scope : { all: true })}
      ${groups.length ? `<div><span class="label">${t('userGroupsTab')}</span><div class="check-grid">${groups.map(g => `<label class="check"><input type="checkbox" data-group value="${g.id}" ${u && u.groups.includes(g.id) ? 'checked' : ''}><span>${esc(g.name)}<br><small class="muted">${esc(roleLabel(g.role))}</small></span></label>`).join('')}</div><small class="muted">${t('userGroupsMemberHint')}</small></div>` : ''}
      ${u ? `<label class="check"><input type="checkbox" name="enabled" ${u.enabled ? 'checked' : ''}>${t('accountEnabled')}</label>` : ''}</div>`,
    onOpen: form => {
      bindScope(form);
      const pw = form.password, must = $('.must-change', form);
      // a new password for an existing user: the one-time choice appears (checked)
      pw.oninput = () => { if (u) { must.hidden = !pw.value; if (pw.value && !must.dataset.touched) form.must_change.checked = true; } };
      form.must_change.onchange = () => { must.dataset.touched = '1'; };
      $('[data-gen-pw]', form).onclick = () => {
        const chars = 'abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789';
        const rnd = crypto.getRandomValues(new Uint32Array(14));
        pw.value = Array.from(rnd, x => chars[x % chars.length]).join('');
        pw.type = 'text';
        pw.select();
        pw.oninput();
      };
    },
    onSubmit: async (data, form) => {
      const common = { role: data.role, language: data.language, scope: readScope(form), groups: $$('[data-group]:checked', form).map(x => Number(x.value)) };
      if (u) {
        const body = { ...common, enabled: !!data.enabled };
        if (data.password) Object.assign(body, { password: data.password, must_change: !!data.must_change });
        await api('/api/users/' + u.id, { method: 'PATCH', json: body });
      } else {
        await api('/api/users', { method: 'POST', json: { ...common, username: data.username, password: data.password, must_change: !!data.must_change } });
      }
      toast(t('saved'));
      render();
    },
  });
}

function roleDialog(key, copyFrom) {
  const r = key ? S.roles.find(x => x.key === key) : null;
  const src = r || (copyFrom ? S.roles.find(x => x.key === copyFrom) : null);
  const perms = new Set(src ? src.permissions : ['view']);
  const row = p => `<label class="check perm-row"><input type="checkbox" name="perm_${p}" ${perms.has(p) ? 'checked' : ''} ${p === 'view' ? 'disabled checked' : ''}><span><b>${t('perm_' + p)}</b><br><small class="muted">${t('permHint_' + p)}</small></span></label>`;
  modal({
    title: r ? t('roleEdit') : t('roleNew'), wide: true,
    body: `<div class="form"><div class="row2"><label>${t('name')}<input name="name" required maxlength="80" value="${esc(r ? r.name : src ? roleLabel(src.key, src.name) + ' 2' : '')}"></label>
      <label>${t('description')}<input name="description" maxlength="500" value="${esc(r ? r.description : '')}"></label></div>
      <h3>${t('permDevices')}</h3><p class="muted">${t('permDevicesHint')}</p><div class="check-grid">${DEVICE_PERMS.map(row).join('')}</div>
      <h3>${t('permSystem')}</h3><p class="muted">${t('permSystemHint')}</p><div class="check-grid">${SYSTEM_PERMS.map(row).join('')}</div></div>`,
    onSubmit: async (data, form) => {
      const body = { name: data.name.trim(), description: data.description.trim(), permissions: [...DEVICE_PERMS, ...SYSTEM_PERMS].filter(p => p === 'view' || form['perm_' + p].checked) };
      await api(r ? '/api/roles/' + r.key : '/api/roles', { method: r ? 'PATCH' : 'POST', json: body });
      toast(t('saved'));
      render();
    },
  });
}

function userGroupDialog(gid) {
  const g = gid ? S.userGroups.find(x => x.id === gid) : null;
  modal({
    title: g ? t('userGroupEdit') : t('userGroupNew'), wide: true,
    body: `<div class="form"><p class="muted">${t('userGroupsHint')}</p>
      <div class="row2"><label>${t('name')}<input name="name" required maxlength="80" value="${esc(g ? g.name : '')}" placeholder="${esc(t('userGroupNameHint'))}"></label>
      <label>${t('role')}<select name="role">${roleOptions(g ? g.role : 'operator')}</select></label></div>
      <label>${t('description')}<input name="description" maxlength="500" value="${esc(g ? g.description : '')}"></label>
      ${scopeFields(g ? g.scope : { all: true })}
      <div><span class="label">${t('members')}</span><div class="check-grid">${S.users.map(u => `<label class="check"><input type="checkbox" data-member value="${u.id}" ${g && g.members.includes(u.id) ? 'checked' : ''}>${esc(u.username)}</label>`).join('')}</div></div></div>`,
    onOpen: form => bindScope(form),
    onSubmit: async (data, form) => {
      const body = { name: data.name.trim(), description: data.description.trim(), role: data.role, scope: readScope(form), members: $$('[data-member]:checked', form).map(x => Number(x.value)) };
      await api(g ? '/api/user-groups/' + g.id : '/api/user-groups', { method: g ? 'PATCH' : 'POST', json: body });
      toast(t('saved'));
      render();
    },
  });
}

// ---------- settings

// Settings are split into sections with their own navigation (#/settings/<section>).
const SETTINGS = [
  ['account', 'view', 'users'], ['update', 'settings', 'updates'], ['alerts', 'settings', 'bell'], ['install', 'settings', 'plus'], ['notifyapi', 'notify_api', 'web'],
  ['integrations', 'users', 'console'], ['reports', 'settings', 'plays'],
  ['proxy', 'updates', 'download'], ['sso', 'users', 'lock'], ['branding', 'settings', 'image'], ['backup', 'settings', 'upload'],
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

// ---------- updates of Fleet itself

// a little Markdown for release notes: headings, lists, bold, code, links (everything escaped first)
function mdLite(text) {
  const inline = x => esc(x).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\[([^\]]+)\]\((https:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  let html = '', list = false;
  for (const line of String(text || '').split(/\r?\n/)) {
    const li = /^\s*[-*] (.*)/.exec(line), h = /^(#{1,4}) (.*)/.exec(line);
    if (!li && list) { html += '</ul>'; list = false; }
    if (li) { if (!list) { html += '<ul>'; list = true; } html += `<li>${inline(li[1])}</li>`; }
    else if (h) html += `<h${Math.min(5, h[1].length + 2)}>${inline(h[2])}</h${Math.min(5, h[1].length + 2)}>`;
    else if (line.trim()) html += `<p>${inline(line)}</p>`;
  }
  return html + (list ? '</ul>' : '');
}
async function fleetUpdateNotice(el) {
  if (!el || !can('settings')) return;
  if (!S.fleetUpdate) S.fleetUpdate = api('/api/system/update').catch(() => null);
  const u = await S.fleetUpdate;
  if (!u || !el.isConnected) return;
  patch(el, u.newer && u.newer.length ? `<div class="note info update-note"><div>${icon('updates')}<b>${t('fleetUpdateAvailable', { version: esc(u.newer[0]) })}</b> <span class="muted">${t('fleetUpdateCurrent', { version: esc(u.current) })}</span></div>
    <a class="btn sm primary" href="#/settings/update">${t('fleetUpdateShow')}</a></div>` : '');
}
async function fleetUpdateCard(root, refresh = false) {
  const u = await api('/api/system/update' + (refresh ? '?refresh=1' : '')).catch(e => ({ error: errText(e) }));
  if (!root.isConnected) return;
  S.fleetUpdate = Promise.resolve(u);
  const newest = (u.newer || [])[0], job = u.job, last = u.last, d = u.docker || {};
  const running = job && job.state === 'running';
  const manual = d.swarm ? `docker service update --image ${u.image}:${newest || 'latest'} <service>`
    : `cd <folder with compose.yml>\ndocker compose pull\ndocker compose up -d`;
  root.innerHTML = `<section class="card"><div class="card-head"><h2>${t('fleetUpdate')}</h2><button class="btn sm ghost" id="updCheck">${icon('refresh')}${t('fleetUpdateCheck')}</button></div>
    <dl class="kv"><dt>${t('fleetUpdateVersion')}</dt><dd><b>${esc(u.current || S.me.hub_version)}</b> ${newest ? `<span class="tag info">${t('outdated')}</span>` : u.latest ? `<span class="tag ok">${t('latest')}</span>` : ''}</dd>
      <dt>${t('fleetUpdateLatest')}</dt><dd>${esc(u.latest || '—')}</dd><dt>${t('imageName')}</dt><dd><code>${esc(u.image || '')}</code></dd></dl>
    ${u.error ? `<div class="note warn">${esc(errText(new Error(String(u.error).split(':')[0])))}</div>` : ''}
    ${last && last.state === 'failed' ? `<div class="note warn"><div><b>${t('fleetUpdateFailed', { version: esc(String(last.to).split(':').pop()) })}</b><br><small>${esc(last.error || '')}</small></div></div>` : ''}
    ${last && last.state === 'done' && Date.now() / 1000 - last.at < 86400 ? `<div class="note ok">${t('fleetUpdateDone', { version: esc(String(last.to).split(':').pop()) })}</div>` : ''}
    ${running ? `<div class="note info"><div><b>${t('fleetUpdateRunning', { version: esc(job.version) })}</b><br><small>${t('fleetUpdateStep_' + job.step)}</small></div></div>` : ''}
    ${job && job.state === 'failed' ? `<div class="note warn">${esc(job.error || '')}</div>` : ''}
    ${newest && !running ? (d.available ? `<p class="muted">${t('fleetUpdateHow')}</p><div class="form-actions start"><button class="btn primary" id="updRun" data-version="${esc(newest)}">${icon('updates')}${t('fleetUpdateNow', { version: esc(newest) })}</button></div>`
      : `<p class="muted">${t('fleetUpdateManual')}</p><pre class="code" id="updCmd">${esc(manual)}</pre><div class="form-actions start"><button class="btn sm" id="updCopy">${icon('copy')}${t('copy')}</button></div>
        <details><summary>${t('fleetUpdateSocket')}</summary><p class="muted">${t('fleetUpdateSocketHint')}</p><pre class="code">volumes:\n  - /var/run/docker.sock:/var/run/docker.sock</pre></details>`) : ''}</section>
    ${(u.older || []).length && !running ? `<section class="card"><h2>${t('fleetRollback')}</h2><p class="muted">${t('fleetRollbackHint')}</p>
      <div class="form"><div class="row2"><label>${t('fleetRollbackTo')}<select id="rbVersion">${u.older.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join('')}</select></label><span></span></div>
      <div id="rbData"></div>
      <div class="form-actions start">${d.available ? `<button class="btn" id="rbRun">${icon('restart')}${t('fleetRollbackRun')}</button>` : `<small class="muted">${t('fleetRollbackManual')}</small>`}</div></div></section>` : ''}
    ${(u.notes || []).map(n => `<section class="card release-notes"><div class="card-head"><h2>${esc(n.name)}</h2><span class="muted">${n.published ? esc(String(n.published).slice(0, 10)) : ''}</span></div>${mdLite(n.body)}
      ${n.url ? `<p><a href="${esc(n.url)}" target="_blank" rel="noopener">${t('fleetUpdateOnGithub')}</a></p>` : ''}</section>`).join('')}`;
  $('#updCheck', root).onclick = () => fleetUpdateCard(root, true);
  const copy = $('#updCopy', root);
  if (copy) copy.onclick = () => navigator.clipboard.writeText($('#updCmd', root).textContent).then(() => toast(t('copied')));
  const run = $('#updRun', root);
  if (run) run.onclick = async () => {
    if (!await confirmBox(t('fleetUpdateConfirm', { version: esc(run.dataset.version) }), false, t('fleetUpdateNow', { version: esc(run.dataset.version) }))) return;
    try { await api('/api/system/update', { method: 'POST', json: { version: run.dataset.version } }); } catch (e) { return toast(errText(e), 'err'); }
    fleetUpdateCard(root);
    waitForNewVersion(run.dataset.version);
  };
  // going back: the backups that version made can bring its data back too
  const rbVersion = $('#rbVersion', root);
  if (rbVersion) {
    const showData = async () => {
      const v = rbVersion.value, box = $('#rbData', root);
      const b = await api('/api/system/update/backups?version=' + encodeURIComponent(v)).catch(() => ({ backups: [] }));
      if (!box.isConnected || rbVersion.value !== v) return;
      box.innerHTML = `<div class="radios"><label class="check"><input type="radio" name="rbData" value="" ${b.backups.length ? '' : 'checked'}><span>${t('fleetRollbackKeep')}<br><small class="muted">${t('fleetRollbackKeepHint')}</small></span></label>
        ${b.backups.map((x, i) => `<label class="check"><input type="radio" name="rbData" value="${esc(x.name)}" ${i ? '' : 'checked'}><span>${t('fleetRollbackRestore', { date: dt(x.created) })}<br><small class="muted">${t('fleetRollbackRestoreHint')}</small></span></label>`).join('')}</div>
        ${b.backups.length ? '' : `<div class="note warn">${t('fleetRollbackNoBackup', { version: esc(v) })}</div>`}`;
    };
    rbVersion.onchange = showData;
    showData();
    const rb = $('#rbRun', root);
    if (rb) rb.onclick = async () => {
      const v = rbVersion.value, restore = (root.querySelector('[name=rbData]:checked') || {}).value || '';
      if (!await confirmBox(t(restore ? 'fleetRollbackConfirmRestore' : 'fleetRollbackConfirm', { version: esc(v) }), true, t('fleetRollbackRun'))) return;
      try { await api('/api/system/update', { method: 'POST', json: { version: v, restore } }); } catch (e) { return toast(errText(e), 'err'); }
      fleetUpdateCard(root);
      waitForNewVersion(v);
    };
  }
  if (running) setTimeout(() => root.isConnected && fleetUpdateCard(root), 3000);
}
// the hub restarts during the update: the page waits for the new version and reloads itself
function waitForNewVersion(version) {
  const until = Date.now() + 15 * 60000;
  const poll = async () => {
    const p = await fetch('/api/public', { cache: 'no-store' }).then(r => r.json()).catch(() => null);
    if (p && p.version === version) { toast(t('fleetUpdateDone', { version })); setTimeout(() => location.reload(), 1500); return; }
    if (Date.now() < until) setTimeout(poll, 3000);
  };
  setTimeout(poll, 5000);
}

// Backups the hub makes itself every day, kept in its data folder.
async function backupScheduleCard(el) {
  const b = await api('/api/backups').catch(() => null);
  if (!b || !el.isConnected) return;
  const s = b.settings;
  el.innerHTML = `<h2>${t('backupScheduled')}</h2><p class="muted">${t('backupScheduledHint', { folder: esc(b.folder) })}</p>
    <form class="form" id="bkForm"><label class="check"><input type="checkbox" class="switch" name="enabled" ${s.enabled ? 'checked' : ''}>${t('backupDaily')}</label>
    <div class="row2"><label>${t('autoUpdateHour')}<input name="hour" type="number" min="0" max="23" value="${s.hour}"></label><label>${t('backupKeep')}<input name="keep" type="number" min="1" max="90" value="${s.keep}"></label></div>
    <div class="form-actions start"><button class="btn primary">${t('save')}</button><button type="button" class="btn" id="bkNow">${icon('upload')}${t('backupNow')}</button></div></form>
    <div class="table-wrap"><table class="table"><thead><tr><th>${t('file')}</th><th>${t('created')}</th><th></th></tr></thead><tbody>
    ${b.backups.map(x => `<tr><td><b>${esc(x.name)}</b><br><small class="muted">${fmtSize(x.size)}</small></td><td class="nowrap">${dt(x.created)}</td>
      <td class="actions-cell"><button class="btn sm" data-bk-get="${esc(x.name)}">${icon('download')}${t('download')}</button><button class="icon-btn danger" data-bk-del="${esc(x.name)}" title="${t('delete')}">${icon('trash')}</button></td></tr>`).join('')
      || `<tr><td colspan="3" class="empty">${t('backupNone')}</td></tr>`}</tbody></table></div>`;
  $('#bkForm', el).onsubmit = async e => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    try { await api('/api/backups/settings', { method: 'PUT', json: { enabled: !!f.enabled, hour: Number(f.hour), keep: Number(f.keep) } }); toast(t('saved')); } catch (err) { toast(errText(err), 'err'); }
  };
  $('#bkNow', el).onclick = async () => {
    try { await api('/api/backups/run', { method: 'POST' }); toast(t('saved')); backupScheduleCard(el); } catch (err) { toast(errText(err), 'err'); }
  };
  $$('[data-bk-get]', el).forEach(btn => { btn.onclick = async () => {
    const r = await fetch('/api/backups/' + encodeURIComponent(btn.dataset.bkGet), { headers: { Authorization: 'Bearer ' + S.token } });
    if (!r.ok) return toast(t('err_not_found'), 'err');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(await r.blob());
    a.download = btn.dataset.bkGet;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  }; });
  $$('[data-bk-del]', el).forEach(btn => { btn.onclick = async () => {
    if (!await confirmBox(t('confirmBackupDelete', { name: esc(btn.dataset.bkDel) }))) return;
    await api('/api/backups/' + encodeURIComponent(btn.dataset.bkDel), { method: 'DELETE' });
    backupScheduleCard(el);
  }; });
}

const SETTINGS_VIEW = {
  update(root) { root.innerHTML = '<section class="card"><div class="empty">…</div></section>'; fleetUpdateCard(root); },
  async sso(root) {
    root.innerHTML = `<section class="card"><div class="empty">…</div></section>`;
    const o = await api('/api/sso/settings').catch(() => null);
    if (!o || !root.isConnected) return;
    root.innerHTML = `<section class="card"><h2>${t('ssoTitle')}</h2><p class="muted">${t('ssoHint')}</p>
      <form id="ssoForm" class="form"><label class="check"><input type="checkbox" class="switch" name="enabled" ${o.enabled ? 'checked' : ''}>${t('ssoEnable')}</label>
      <label>${t('ssoIssuer')}<input name="issuer" type="url" value="${esc(o.issuer)}" placeholder="https://login.example.com/realms/firma" spellcheck="false"><small class="muted">${t('ssoIssuerHint')}</small></label>
      <div class="row2"><label>Client ID<input name="client_id" value="${esc(o.client_id)}" spellcheck="false"></label><label>Client secret<input name="client_secret" type="password" value="${esc(o.client_secret)}" autocomplete="off"></label></div>
      <label>${t('ssoRedirect')}<input value="${esc(o.redirect_uri)}" readonly><small class="muted">${t('ssoRedirectHint')}</small></label>
      <div class="row2"><label>${t('ssoClaim')}<select name="username_claim">${['preferred_username', 'email', 'upn', 'name', 'sub'].map(c => `<option ${c === o.username_claim ? 'selected' : ''}>${c}</option>`).join('')}</select></label>
      <label>${t('ssoLabel')}<input name="label" value="${esc(o.label)}" placeholder="Microsoft, Google, Keycloak…"></label></div>
      <label>${t('ssoDomains')}<input name="allowed_domains" value="${esc(o.allowed_domains)}" placeholder="firma.cz, firma.com"><small class="muted">${t('ssoDomainsHint')}</small></label>
      <label class="check"><input type="checkbox" class="switch" name="auto_create" ${o.auto_create ? 'checked' : ''}>${t('ssoAutoCreate')}</label>
      <label>${t('ssoRole')}<select name="default_role">${['admin', 'manager', 'operator', 'viewer'].map(r => `<option value="${r}" ${r === o.default_role ? 'selected' : ''}>${t('role_' + r)}</option>`).join('')}</select></label>
      <details><summary>${t('advanced')}</summary><label>${t('ssoScopes')}<input name="scopes" value="${esc(o.scopes)}"></label>
        <label>${t('ssoPublicUrl')}<input name="public_url" type="url" value="${esc(o.public_url)}" placeholder="${esc(location.origin)}"><small class="muted">${t('ssoPublicUrlHint')}</small></label></details>
      <div class="form-actions"><button class="btn primary">${t('save')}</button></div></form></section>`;
    $('#ssoForm', root).onsubmit = async e => {
      e.preventDefault();
      const f = Object.fromEntries(new FormData(e.target));
      try {
        await api('/api/sso/settings', { method: 'PUT', json: { ...f, enabled: !!f.enabled, auto_create: !!f.auto_create } });
        toast(t('saved'));
        PUBLIC = await loadPublic();
      } catch (err) { toast(errText(err), 'err'); }
    };
  },
  account(root) {
    root.innerHTML = `<section class="card"><h2>${t('myAccount')}</h2>
      <dl class="kv"><dt>${t('username')}</dt><dd>${esc(S.me.username)}</dd><dt>${t('role')}</dt><dd>${esc(roleLabel(S.me.role, S.me.role_name))}${S.me.scoped ? ` <span class="tag">${t('scopeLimited')}</span>` : ''}</dd></dl>
      <form id="pwForm" class="form"><h3>${t('changePassword')}</h3>
        <label>${t('currentPassword')}<input type="password" name="current_password" autocomplete="current-password" required></label>
        <label>${t('newPassword')}<input type="password" name="new_password" minlength="10" autocomplete="new-password" required></label>
        <div class="form-actions"><button class="btn primary">${t('changePassword')}</button></div></form></section>
      ${S.me.sso ? `<section class="card"><h2>${t('totpTitle')}</h2><p class="muted">${t('totpSso')}</p></section>`
        : `<section class="card"><div class="card-head"><h2>${t('totpTitle')}</h2><span class="tag ${S.me.totp ? 'ok' : ''}">${t(S.me.totp ? 'totpOn' : 'totpOff')}</span></div>
        <p class="muted">${t('totpHint')}</p><div class="form-actions start">${S.me.totp ? `<button class="btn danger" data-do="totpDisable">${t('totpDisable')}</button>` : `<button class="btn primary" data-do="totpSetup">${icon('lock')}${t('totpEnable')}</button>`}</div></section>`}
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
      <section class="card"><h2>${t('oneCommand')}</h2><p class="muted">${t('oneCommandHint')}</p>
        <div class="form-actions start"><button class="btn" data-do="oneCommand">${icon('console')}${t('oneCommand')}</button></div></section>
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
      <section class="card" id="backupSchedule"><div class="empty">…</div></section>
      <section class="card flush" id="backupOffsite"><div class="empty">…</div></section>
      <section class="card"><h2>${t('restoreTitle')}</h2><p class="muted">${t('restoreHint')}</p>
        <div class="form"><label>${t('backupFile')}<input type="file" id="restoreFile" accept=".zip,.enc,application/zip"></label>
        <label id="restorePassRow" hidden>${t('offsitePassphrase')}<input type="password" id="restorePass" autocomplete="off"><small>${t('restorePassHint')}</small></label>
        <div class="form-actions"><button class="btn danger" id="restoreRun">${icon('upload')}${t('restoreRun')}</button></div></div></section>
      <section class="card"><h2>${t('migrateTitle')}</h2><ol class="steps">${t('migrateSteps')}</ol></section>`;
    this.handlers(root);
    backupScheduleCard($('#backupSchedule', root));
    offsiteCard($('#backupOffsite', root));
    $('#restoreFile', root).onchange = e => { $('#restorePassRow', root).hidden = !/\.enc$/i.test(e.target.files[0]?.name || ''); };
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
        const r = await fetch('/api/backup/restore', { method: 'POST', body: f, headers: { Authorization: 'Bearer ' + S.token, 'Content-Type': 'application/octet-stream',
          ...($('#restorePass', root).value ? { 'X-Backup-Passphrase': $('#restorePass', root).value } : {}) } });
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

// ---------- backups outside the server (Settings → Backup)

const OFFSITE_TYPES = ['s3', 'webdav', 'sftp', 'smb', 'nfs', 'rclone', 'folder'];
const OFFSITE_ICON = { s3: 'upload', webdav: 'web', sftp: 'ssh', smb: 'group', nfs: 'download', rclone: 'web', folder: 'download' };
// [field, input kind, placeholder]; labels are t('ofs_' + field)
const OFFSITE_FIELDS = {
  s3: [['endpoint', 'text', 'https://s3.eu-central-1.amazonaws.com'], ['region', 'text', 'eu-central-1'], ['bucket', 'text', 'caracal-backups'], ['prefix', 'text', 'fleet'],
    ['access_key', 'text', ''], ['secret_key', 'password', ''], ['style', 'check'], ['verify_tls', 'check']],
  webdav: [['url', 'text', 'https://cloud.example.com/remote.php/dav/files/USER/Backups/'], ['username', 'text', ''], ['password', 'password', ''], ['verify_tls', 'check']],
  sftp: [['host', 'text', 'nas.local'], ['port', 'number', '22'], ['username', 'text', ''], ['password', 'password', ''], ['private_key', 'textarea', '-----BEGIN OPENSSH PRIVATE KEY-----'],
    ['key_passphrase', 'password', ''], ['path', 'text', '/backups/caracal']],
  smb: [['server', 'text', 'nas.local'], ['share', 'text', 'backups'], ['path', 'text', 'caracal'], ['username', 'text', ''], ['password', 'password', ''], ['domain', 'text', ''], ['port', 'number', '445']],
  nfs: [['server', 'text', 'nas.local'], ['export', 'text', '/volume1/backups'], ['path', 'text', 'caracal']],
  rclone: [['config', 'textarea', '[gdrive]\ntype = drive\nscope = drive.file\ntoken = {...}'], ['path', 'text', 'Backups/caracal']],
  folder: [['path', 'text', '/backups']],
};
// S3-compatible services: endpoint and region
const S3_PRESETS = {
  aws: ['https://s3.eu-central-1.amazonaws.com', 'eu-central-1', 'virtual'], b2: ['https://s3.eu-central-003.backblazeb2.com', 'eu-central-003', 'path'],
  r2: ['https://ACCOUNT_ID.r2.cloudflarestorage.com', 'auto', 'path'], wasabi: ['https://s3.eu-central-2.wasabisys.com', 'eu-central-2', 'path'],
  hetzner: ['https://fsn1.your-objectstorage.com', 'fsn1', 'path'], minio: ['https://minio.example.com', 'us-east-1', 'path'],
};
const offsiteWhere = x => ({ s3: `${x.bucket}${x.prefix ? '/' + x.prefix : ''} · ${hostOf(x.endpoint)}`, webdav: hostOf(x.url), sftp: `${x.host}:${x.path || '~'}`,
  smb: `\\\\${x.server}\\${x.share}${x.path ? '\\' + x.path : ''}`, nfs: `${x.server}:${x.export}${x.path ? '/' + x.path : ''}`, rclone: x.path || '/', folder: x.path })[x.type] || '';

async function offsiteCard(el) {
  const [d, sched] = await Promise.all([api('/api/backup-targets'), api('/api/backups')].map(p => p.catch(() => null)));
  if (!d || !el.isConnected) return;
  S.offsite = d;
  const row = x => {
    const st = x.status;
    const state = x.running ? `<span class="tag info">${t('offsiteRunning')}</span>`
      : !st ? `<span class="tag">${t('offsiteNever')}</span>`
      : st.ok ? `<span class="tag ok" title="${esc(st.name || '')}">${t('offsiteOk', { time: ago(st.ts), size: fmtSize(st.size || 0) })}</span>`
      : `<span class="tag critical" title="${esc(st.error)}">${t('chFailed')} · ${ago(st.ts)}</span>`;
    return `<div class="channel">${icon(OFFSITE_ICON[x.type])}<div class="channel-main"><b>${esc(x.name)}</b><small class="muted">${t('ofsType_' + x.type)} · ${esc(offsiteWhere(x))} · ${t('offsiteKeep', { n: x.keep })}</small>
      ${st && !st.ok ? `<small class="err-line">${esc(errText(st.error))}</small>` : ''}</div>
      ${x.enabled === false ? `<span class="tag">${t('chPaused')}</span>` : ''}${state}
      <span class="row-actions"><button class="btn sm" data-ofs-up="${esc(x.id)}" ${x.running ? 'disabled' : ''}>${icon('upload')}${t('offsiteUploadNow')}</button>
      <button class="btn sm ghost" data-ofs-files="${esc(x.id)}">${icon('download')}${t('offsiteFiles')}</button>
      <button class="icon-btn" data-ofs-edit="${esc(x.id)}" title="${t('edit')}">${icon('edit')}</button>
      <button class="icon-btn danger" data-ofs-del="${esc(x.id)}" title="${t('delete')}">${icon('trash')}</button></span></div>`;
  };
  el.innerHTML = `<div class="toolbar"><h2 class="grow">${t('offsiteTitle')}</h2><button class="btn sm" id="ofsCrypt">${icon('lock')}${t(d.encrypted ? 'offsiteEncOn' : 'offsiteEncOff')}</button><button class="btn sm primary" id="ofsAdd">${icon('plus')}${t('offsiteAdd')}</button></div>
    <div class="card-pad"><p class="muted">${t('offsiteHint')}</p>${d.targets.length && sched && !sched.settings.enabled ? `<div class="note warn">${t('offsiteNoDaily')}</div>` : ''}</div>
    ${d.targets.length ? `<div class="channel-list">${d.targets.map(row).join('')}</div>` : emptyState('upload', t('offsiteNone'), t('offsiteNoneHint'), '')}`;
  const again = () => offsiteCard(el);
  $('#ofsAdd', el).onclick = () => offsiteDialog(null, again);
  $('#ofsCrypt', el).onclick = () => offsiteCryptDialog(d.encrypted, again);
  $$('[data-ofs-edit]', el).forEach(b => { b.onclick = () => offsiteDialog(d.targets.find(x => x.id === b.dataset.ofsEdit), again); });
  $$('[data-ofs-del]', el).forEach(b => { b.onclick = async () => {
    const x = d.targets.find(y => y.id === b.dataset.ofsDel);
    if (!await confirmBox(t('offsiteDeleteConfirm', { name: esc(x.name) }))) return;
    await api('/api/backup-targets/' + x.id, { method: 'DELETE' });
    again();
  }; });
  $$('[data-ofs-up]', el).forEach(b => { b.onclick = async () => {
    try { await api(`/api/backup-targets/${b.dataset.ofsUp}/upload`, { method: 'POST' }); toast(t('offsiteStarted')); } catch (err) { toast(errText(err), 'err'); }
    again();
    // the copy runs in the background: the list shows it when it is done
    const poll = setInterval(async () => {
      if (!el.isConnected) return clearInterval(poll);
      const now = await api('/api/backup-targets').catch(() => null);
      if (now && !now.targets.some(x => x.running)) { clearInterval(poll); again(); }
    }, 3000);
  }; });
  $$('[data-ofs-files]', el).forEach(b => { b.onclick = () => offsiteFilesDialog(d.targets.find(x => x.id === b.dataset.ofsFiles), d.encrypted); });
}

function offsiteDialog(x, done) {
  let type = x ? x.type : 's3';
  const cur = x || { keep: 14, enabled: true, verify_tls: true };
  const field = ([name, kind, ph]) => {
    const v = cur[name] ?? (name === 'port' ? ph : '');
    if (kind === 'check') return `<label class="check"><input type="checkbox" name="${name}" ${(name === 'style' ? cur.style === 'virtual' : cur[name] !== false) ? 'checked' : ''}>${t('ofs_' + name)}</label>`;
    const hint = I18N[S.lang]['ofsHint_' + type + '_' + name] || I18N[S.lang]['ofsHint_' + name];
    const input = kind === 'textarea' ? `<textarea name="${name}" rows="${name === 'config' ? 8 : 4}" spellcheck="false" placeholder="${esc(ph)}">${esc(v)}</textarea>`
      : `<input name="${name}" type="${kind}" value="${esc(v)}" placeholder="${esc(ph || '')}" autocomplete="off" spellcheck="false">`;
    return `<label>${t('ofs_' + name)}${input}${hint ? `<small>${hint}</small>` : ''}</label>`;
  };
  const fields = () => `<p class="muted">${t('ofsTypeHint_' + type)}</p>
    ${type === 's3' ? `<label>${t('ofsPreset')}<select data-preset><option value="">—</option>${Object.keys(S3_PRESETS).map(k => `<option value="${k}">${t('ofsPreset_' + k)}</option>`).join('')}</select></label>` : ''}
    ${OFFSITE_FIELDS[type].map(field).join('')}`;
  const read = form => {
    const out = { id: x ? x.id : '', type, name: form.name.value.trim(), keep: Number(form.keep.value) || 14, enabled: form.enabled.checked };
    for (const [name, kind] of OFFSITE_FIELDS[type]) {
      const el = form.elements[name];
      out[name] = kind === 'check' ? (name === 'style' ? (el.checked ? 'virtual' : 'path') : el.checked) : el.value;
    }
    return out;
  };
  modal({
    title: x ? t('offsiteEdit') : t('offsiteAdd'), wide: true,
    body: `<div class="form">${x ? '' : `<div class="ch-types">${OFFSITE_TYPES.map(k => `<label class="ch-type"><input type="radio" name="type" value="${k}" ${k === type ? 'checked' : ''}>${icon(OFFSITE_ICON[k])}<span>${t('ofsType_' + k)}</span></label>`).join('')}</div>`}
      <div class="row2"><label>${t('name')}<input name="name" value="${esc(cur.name || '')}" placeholder="${esc(t('offsiteNameHint'))}"></label>
      <label>${t('offsiteKeepLabel')}<input name="keep" type="number" min="1" max="365" value="${cur.keep || 14}"></label></div>
      <div id="ofsFields" class="form">${fields()}</div>
      <label class="check"><input type="checkbox" name="enabled" ${cur.enabled !== false ? 'checked' : ''}>${t('offsiteEnabled')}</label>
      <div class="form-actions start"><button type="button" class="btn" id="ofsTry">${icon('refresh')}${t('offsiteTest')}</button></div><div class="try-result"></div></div>`,
    onOpen: form => {
      const wire = () => {
        const preset = $('[data-preset]', form);
        if (preset) preset.onchange = () => { const p = S3_PRESETS[preset.value]; if (!p) return; form.endpoint.value = p[0]; form.region.value = p[1]; form.style.checked = p[2] === 'virtual'; };
      };
      $$('[name=type]', form).forEach(r => { r.onchange = () => { type = r.value; $('#ofsFields', form).innerHTML = fields(); wire(); }; });
      wire();
      $('#ofsTry', form).onclick = e => tryCommand(form, e.currentTarget, () => api('/api/backup-targets/test', { method: 'POST', json: read(form) }),
        r => (r.ok ? `<div class="note info">${icon('check')}${t('offsiteTestOk')}${r.host_key ? `<br><small class="muted">${esc(r.host_key.slice(0, 60))}…</small>` : ''}</div>` : `<div class="note warn">${esc(errText(r.error))}</div>`));
    },
    onSubmit: async (data, form) => {
      const body = read(form);
      await api(x ? '/api/backup-targets/' + x.id : '/api/backup-targets', { method: x ? 'PATCH' : 'POST', json: body });
      toast(t('saved'));
      done();
    },
  });
}

function offsiteCryptDialog(on, done) {
  modal({
    title: t('offsiteEncTitle'), submit: t('save'),
    body: `<div class="form"><p class="muted">${t('offsiteEncHint')}</p>
      <label>${t('offsitePassphrase')}<input type="password" name="passphrase" minlength="12" autocomplete="new-password" ${on ? `placeholder="${t('leaveEmptyOff')}"` : 'required'}></label>
      <label>${t('offsitePassphrase2')}<input type="password" name="passphrase2" autocomplete="new-password"></label>
      <div class="note warn">${t('offsiteEncWarn')}</div></div>`,
    onSubmit: async data => {
      if (data.passphrase !== data.passphrase2) throw new Error('passphrase_mismatch');
      if (!data.passphrase && on && !await confirmBox(t('offsiteEncDisable'))) return false;
      await api('/api/backup-targets/encryption', { method: 'PUT', json: { passphrase: data.passphrase } });
      toast(t('saved'));
      done();
    },
  });
}

async function offsiteFilesDialog(x, encrypted) {
  const dlg = modal({ title: t('offsiteFilesTitle', { name: esc(x.name) }), wide: true, body: `<div id="ofsList"><div class="note info">${t('loading')}</div></div>` });
  let r;
  try { r = await api(`/api/backup-targets/${x.id}/files`); } catch (e) { $('#ofsList', dlg).innerHTML = `<div class="note warn">${esc(errText(e))}</div>`; return; }
  const list = $('#ofsList', dlg);
  list.innerHTML = r.files.length ? `<p class="muted">${t('offsiteFilesHint')}</p><div class="table-wrap"><table class="table"><thead><tr><th>${t('file')}</th><th>${t('created')}</th><th></th></tr></thead><tbody>
    ${r.files.map(f => `<tr><td><b>${esc(f.name)}</b>${f.name.endsWith('.enc') ? ` <span class="tag">${icon('lock')}${t('encrypted')}</span>` : ''}<br><small class="muted">${fmtSize(f.size)}</small></td><td class="nowrap">${f.mtime ? dt(f.mtime) : '—'}</td>
    <td class="actions-cell"><button class="btn sm danger" data-restore="${esc(f.name)}">${icon('upload')}${t('restoreRun')}</button></td></tr>`).join('')}</tbody></table></div>`
    : `<div class="note">${t('offsiteFilesNone')}</div>`;
  $$('[data-restore]', list).forEach(b => { b.onclick = () => {
    const name = b.dataset.restore, enc = name.endsWith('.enc');
    modal({
      title: t('restoreTitle'), submit: t('restoreRun'), danger: true,
      body: `<div class="form"><p>${t('confirmRestore')}</p><p class="muted">${esc(x.name)}: <b>${esc(name)}</b></p>
        ${enc ? `<label>${t('offsitePassphrase')}<input type="password" name="passphrase" autocomplete="off" ${encrypted ? `placeholder="${t('restorePassStored')}"` : 'required'}></label>` : ''}</div>`,
      onSubmit: async data => {
        await api(`/api/backup-targets/${x.id}/restore`, { method: 'POST', json: { name, passphrase: data.passphrase || '' } });
        toast(t('restoreDone'));
        waitForRestart();
      },
    });
  }; });
}

// ---------- alerts for administrators (Settings → Alerts)

const CH_ICON = { email: 'bell', slack: 'web', teams: 'users', discord: 'web', telegram: 'send', ntfy: 'attention', webhook: 'upload' };
const CH_TYPES = ['email', 'slack', 'teams', 'discord', 'telegram', 'ntfy', 'webhook'];

function renderAlerts(root, conf) {
  const st = conf;
  const save = async patch => {
    try { const next = await api('/api/alerts', { method: 'PUT', json: { ...st, ...patch } }); toast(t('saved')); renderAlerts(root, next); }
    catch (err) { toast(errText(err), 'err'); }
  };
  const on = x => (x ? 'checked' : '');
  const chRow = ch => `<div class="channel">${icon(CH_ICON[ch.type])}<div class="channel-main"><b>${esc(ch.name || t('ch_' + ch.type))}</b>
      <small class="muted">${esc(t('ch_' + ch.type))} · ${esc(ch.to || (ch.type === 'telegram' ? t('chChat') + ' ' + ch.chat : hostOf(ch.url)))}</small></div>
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
          <label>${t('alertsLanguage')}<select name="language">${LANG_NAMES.map(([k, n]) => `<option value="${k}" ${st.language === k ? 'selected' : ''}>${n}</option>`).join('')}</select></label></div>
        <label class="check"><input type="checkbox" name="critical" ${on(st.levels.includes('critical'))}>${t('alertsCritical')}</label>
        <label class="check"><input type="checkbox" name="warning" ${on(st.levels.includes('warning'))}>${t('alertsWarning')}</label>
        <label class="check"><input type="checkbox" name="recovery" ${on(st.recovery)}>${t('alertsRecovery')}</label>
        <label class="check"><input type="checkbox" name="fleet_updates" ${on(st.fleet_updates)}>${t('alertsFleetUpdates')}</label>
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
    save({ enabled: f.enabled.checked, delay: Number(f.delay.value), language: f.language.value, recovery: f.recovery.checked, fleet_updates: f.fleet_updates.checked,
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
    to: form.to.value.trim(), url: form.url.value.trim(), token: form.token.value.trim(), chat: form.chat.value.trim(), enabled: form.enabled.checked });
  modal({
    title: ch ? t('chEdit') : t('chAdd'),
    body: `<div class="form">
      ${ch ? '' : `<div class="ch-types">${CH_TYPES.map(x => `<label class="ch-type"><input type="radio" name="type" value="${x}" ${x === cur.type ? 'checked' : ''}>${icon(CH_ICON[x])}<span>${t('ch_' + x)}</span></label>`).join('')}</div>`}
      <label>${t('name')}<input name="name" value="${esc(cur.name || '')}" placeholder="${esc(t('chNameHint'))}"></label>
      <label data-ch="to">${t('chTo')}<input name="to" value="${esc(cur.to || '')}" placeholder="it@firma.cz, servis@firma.cz"><small>${t('chToHint')}</small></label>
      <label data-ch="url">${t('chUrl')}<input name="url" value="${esc(cur.url || '')}" placeholder="https://"><small class="ch-hint"></small></label>
      <label data-ch="token"><span class="ch-token-label">${t('chToken')}</span><input name="token" value="${esc(cur.token || '')}" autocomplete="off"><small class="ch-token-hint">${t('chTokenHint')}</small></label>
      <div data-ch="chat"><label>${t('chChatId')}<input name="chat" value="${esc(cur.chat || '')}" placeholder="-1001234567890" autocomplete="off"><small>${t('chChatHint')}</small></label>
        <div class="form-actions start find-chat"><button type="button" class="btn sm" id="chFindChat">${icon('search')}${t('chFindChat')}</button></div><div class="chat-result"></div></div>
      <label class="check"><input type="checkbox" name="enabled" ${cur.enabled !== false ? 'checked' : ''}>${t('chEnabled')}</label>
      <div class="form-actions start"><button type="button" class="btn" id="chTry">${icon('bell')}${t('chTest')}</button></div><div class="try-result"></div></div>`,
    onOpen: form => {
      const sync = () => {
        const type = ch ? ch.type : form.querySelector('[name=type]:checked').value;
        $('[data-ch="to"]', form).hidden = type !== 'email';
        $('[data-ch="url"]', form).hidden = ['email', 'telegram'].includes(type);
        $('[data-ch="token"]', form).hidden = !['ntfy', 'webhook', 'telegram'].includes(type);
        $('[data-ch="chat"]', form).hidden = type !== 'telegram';
        $('.ch-token-label', form).textContent = t(type === 'telegram' ? 'chBotToken' : 'chToken');
        $('.ch-token-hint', form).textContent = t(type === 'telegram' ? 'chBotTokenHint' : 'chTokenHint');
        $('.ch-hint', form).textContent = type === 'email' ? '' : t('chHint_' + type);
      };
      $$('[name=type]', form).forEach(r => { r.onchange = sync; });
      sync();
      // the chats that wrote to the bot: one click fills in the chat id
      $('#chFindChat', form).onclick = e => tryCommand(form, e.currentTarget,
        () => api('/api/alerts/telegram/chats', { method: 'POST', json: { id: ch ? ch.id : '', token: form.token.value.trim() } }),
        r => {
          if (!r.ok) return `<div class="note warn">${esc(alertError(r.error))}</div>`;
          if (!r.chats.length) return `<div class="note info">${t('chNoChats')}</div>`;
          setTimeout(() => $$('[data-chat]', form).forEach(b => { b.onclick = () => { form.chat.value = b.dataset.chat; $('.chat-result', form).innerHTML = ''; }; }));
          return `<div class="chat-list">${r.chats.map(c => `<button type="button" class="btn sm" data-chat="${esc(c.id)}">${esc(c.name)} <span class="muted">${esc(c.id)}</span></button>`).join('')}</div>`;
        }, '.chat-result');
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
        <form id="imgForm" class="form"><label>${t('imageName')}<input name="image" placeholder="ghcr.io/OWNER/caracal-node" ${canAll('updates') ? '' : 'readonly'}></label>
        ${canAll('updates') ? `<div class="form-actions"><button class="btn">${t('save')}</button></div>` : ''}</form>
        <h3>${t('availableVersions')}</h3><div id="imgVersions"></div></section>
      <section class="card"><h2>${t('howUpdateWorks')}</h2><ol class="steps">${t('updateStepsDocker')}</ol></section></div>
      <section class="card flush"><div class="toolbar"><h2 class="grow">${t('nodeVersions')}</h2>
        <button class="btn" data-do="convertNodes">${icon('upload')}${t('convertToDocker')}</button>
        <button class="btn primary" data-do="caracalUpdate" data-outdated="1">${icon('updates')}${t('updateOutdated')}</button></div><div id="relNodes"></div></section>
      <section class="card flush"><div class="toolbar"><h2 class="grow">${t('rollouts')}</h2>${canAll('updates') ? `<button class="btn" data-do="rolloutAuto">${icon('settings')}${t('autoUpdate')}</button>
        <button class="btn primary" data-do="rolloutNew">${icon('updates')}${t('rolloutNew')}</button>` : ''}</div><p class="muted card-intro">${t('rolloutsHint')}</p><div id="rollouts"></div></section>
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
    const [img, rels, ro] = await Promise.all([api('/api/node-image').catch(() => null), api('/api/node-releases').catch(() => null), api('/api/rollouts').catch(() => null)]);
    if (!$('#relNodes', root)) return;
    if (ro) { S.rollouts = ro; patch($('#rollouts', root), rolloutsHtml(ro)); }
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
        const action = !canOn(d, 'updates') || !d.online ? '' : d.runtime === 'docker'
          ? `<button class="btn sm" data-do="caracalUpdate" data-id="${esc(d.id)}">${icon('updates')}${t('act_update_caracal')}</button>`
          : `<button class="btn sm" data-do="convertNodes" data-id="${esc(d.id)}">${icon('upload')}${t('convertToDocker')}</button>`;
        return `<tr data-href="#/device/${encodeURIComponent(d.id)}"><td><div class="dev-name">${dot(d)}<b>${esc(d.name)}</b></div></td>
        <td><small>${esc([d.location, d.group].filter(Boolean).join(' · ') || '—')}</small></td><td>${runtimeLabel(d)}</td>
        <td>${esc(d.caracal_version || '?')} ${want && d.caracal_version !== want ? `<span class="tag info">${t('outdated')}</span>` : want ? `<span class="tag ok">${t('latest')}</span>` : ''}</td>
        <td>${esc(d.version || '—')}</td><td>${d.maintenance ? `<span class="badge info">${t('att_maintenance')}</span>` : statusBadge(d)}</td><td class="actions-cell">${action}</td></tr>`;
      }).join('') || `<tr><td colspan="7" class="empty">${t('noDevicesHint')}</td></tr>`}</tbody></table></div>`);
  },
};

// Staged updates: pilots first, then batches after a healthy wait; a failure stops the rollout.
const ROLLOUT_STAGE = { waiting: '', updating: 'info', checking: 'info', done: 'ok', failed: 'critical' };
function rolloutsHtml(ro) {
  const edit = canAll('updates'), a = ro.auto;
  const auto = `<div class="note ${a.enabled ? 'info' : ''}">${a.enabled ? t('autoUpdateOn', { hour: a.hour, group: esc(a.pilot_group || t('autoPilotFirst', { n: a.pilot_count })), batch: a.batch, wait: a.wait }) : t('autoUpdateOff')}</div>`;
  const names = ids => ids.map(id => esc(dev(id)?.name || id)).join(', ');
  return auto + (ro.rollouts.map(r => {
    const done = r.stages.filter(s => s.state === 'done').length;
    const problems = r.stages.flatMap(s => Object.entries({ ...(s.failed || {}), ...(s.unhealthy || {}), ...(s.skipped || {}) }));
    return `<div class="rollout"><div class="rollout-head"><b>CARACAL ${esc(r.version)}</b><span class="badge st-${r.state === 'stopped' ? 'failed' : r.state === 'done' ? 'completed' : r.state === 'running' ? 'running' : 'queued'}">${t('rollout_' + r.state)}</span>
      <small class="muted">${r.auto ? t('autoUpdate') : esc(r.username)} · ${dt(r.created)} · ${t('rolloutProgress', { done, n: r.stages.length })} · ${t('rolloutWait', { n: r.wait })}</small>
      ${edit && ['running', 'paused', 'stopped'].includes(r.state) ? `<span class="rollout-actions">${r.state === 'running' ? `<button class="btn sm" data-do="rolloutCtl" data-act="pause" data-id="${r.id}">${icon('pause')}${t('pause')}</button>`
        : `<button class="btn sm" data-do="rolloutCtl" data-act="resume" data-id="${r.id}">${icon('play')}${t('resume')}</button>`}<button class="btn sm danger" data-do="rolloutCtl" data-act="cancel" data-id="${r.id}">${icon('x')}${t('cancel')}</button></span>` : ''}</div>
      <ol class="rollout-stages">${r.stages.map((s, i) => `<li class="${i === r.current && ['running', 'paused', 'stopped'].includes(r.state) ? 'current' : ''}"><span class="tag ${ROLLOUT_STAGE[s.state] || ''}">${t(i === 0 && r.stages.length > 1 && s.devices.length <= 3 ? 'rolloutPilot' : 'rolloutStage', { n: i + 1 })} · ${t('stage_' + s.state)}</span> <small>${names(s.devices)}</small></li>`).join('')}</ol>
      ${problems.length ? `<div class="note warn"><div>${problems.map(([id, why]) => `<b>${esc(dev(id)?.name || id)}</b>: ${esc(errText(new Error(String(why))).slice(0, 200))}`).join('<br>')}</div></div>` : ''}</div>`;
  }).join('') || `<div class="empty">${t('noRollouts')}</div>`);
}
async function rolloutDialog() {
  const img = await imageInfo();
  if (!img) return;
  const latest = latestOf(img.versions);
  const pre = S.devices.filter(d => d.runtime === 'docker' && d.caracal_version !== latest).map(d => d.id);
  modal({
    title: t('rolloutNew'), submit: t('rolloutStart'), wide: true,
    body: `<div class="form"><p class="muted">${t('rolloutsHint')}</p>${versionField(img)}
      <label>${t('targetDevices')}</label>${targetPicker(null, pre, d => d.runtime === 'docker')}
      <details open><summary>${t('rolloutPilots')}</summary><p class="muted">${t('rolloutPilotsHint')}</p><div class="target-list pilot-list">${S.devices.filter(d => d.runtime === 'docker').map(d => `<label class="check"><input type="checkbox" name="p:${esc(d.id)}">${dot(d)}<span>${esc(d.name)}</span></label>`).join('') || `<div class="muted">${t('noOtherDevices')}</div>`}</div></details>
      <div class="row2"><label>${t('rolloutBatch')}<input name="batch" type="number" min="1" max="500" value="5" required></label><label>${t('rolloutWaitMin')}<input name="wait" type="number" min="0" max="1440" value="30" required></label></div></div>`,
    onOpen: bindTargetPicker,
    onSubmit: async data => {
      const ids = pickedTargets(data);
      if (!ids.length) throw new Error('no_devices');
      const pilots = Object.keys(data).filter(k => k.startsWith('p:')).map(k => k.slice(2));
      await api('/api/rollouts', { method: 'POST', json: { version: data.version.trim(), device_ids: ids, pilot_ids: pilots, batch: Number(data.batch), wait: Number(data.wait) } });
      toast(t('rolloutStarted'));
      render();
    },
  });
}
function rolloutAutoDialog() {
  const a = S.rollouts?.auto || { enabled: false, hour: 3, pilot_group: '', pilot_count: 1, batch: 5, wait: 30 };
  modal({
    title: t('autoUpdate'),
    body: `<div class="form"><p class="muted">${t('autoUpdateHint')}</p><label class="check"><input type="checkbox" class="switch" name="enabled" ${a.enabled ? 'checked' : ''}>${t('autoUpdateEnable')}</label>
      <div class="row2"><label>${t('autoUpdateHour')}<input name="hour" type="number" min="0" max="23" value="${a.hour}"></label>
      <label>${t('autoPilotGroup')}<select name="pilot_group">${options(orgNames('groups'), a.pilot_group, t('autoPilotNone'))}</select></label></div>
      <div class="row2"><label>${t('autoPilotCount')}<input name="pilot_count" type="number" min="0" max="50" value="${a.pilot_count}"></label><label>${t('rolloutBatch')}<input name="batch" type="number" min="1" max="500" value="${a.batch}"></label></div>
      <label>${t('rolloutWaitMin')}<input name="wait" type="number" min="0" max="1440" value="${a.wait}"></label></div>`,
    onSubmit: async data => {
      await api('/api/rollouts/auto', { method: 'PUT', json: { enabled: !!data.enabled, hour: Number(data.hour), pilot_group: data.pilot_group, pilot_count: Number(data.pilot_count), batch: Number(data.batch), wait: Number(data.wait) } });
      toast(t('saved'));
      render();
    },
  });
}

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
  burst();
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
async function sdCardDialog(template = '') {
  const [img] = await Promise.all([api('/api/node-image').catch(() => ({ image: '' })), loadTemplates()]);
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
  const country = ((navigator.language || '').split('-')[1] || (S.lang === 'cs' ? 'CZ' : '')).toUpperCase();
  modal({
    title: t('sdCardTitle'), submit: t('sdCardDownload'), wide: true,
    body: `<div class="form"><p class="muted">${t('sdCardHint')}</p>
      ${img.image ? '' : `<div class="note warn">${t('err_node_image_missing')} <a href="#/updates">${t('nav_updates')}</a></div>`}
      <h3>${t('sdBoardPi')}</h3><div class="mode-pick"><label class="check"><input type="radio" name="os" value="raspios" checked><span><b>${t('osRaspios')}</b><small>${t('osRaspiosHint')}</small></span></label>
      <label class="check"><input type="radio" name="os" value="dietpi"><span><b>${t('osDietpi')}</b><small>${t('osDietpiHint')}</small></span></label>
      <label class="check"><input type="radio" name="os" value="alpine-rpi"><span><b>${t('osAlpineRpi')}</b><small>${t('osAlpineRpiHint')}</small></span></label></div>
      <h3>${t('sdBoardPc')}</h3><div class="mode-pick"><label class="check"><input type="radio" name="os" value="alpine"><span><b>${t('osAlpine')}</b><small>${t('osAlpineHint')}</small></span></label>
      <label class="check"><input type="radio" name="os" value="ubuntu"><span><b>${t('osUbuntu')}</b><small>${t('osUbuntuHint')}</small></span></label></div>
      <p class="muted pc-only">${t('sdPcOther')} <button type="button" class="link" data-do="oneCommand">${t('oneCommand')}</button></p>
      <div class="note warn pc-only">${t('sdPcErase')}</div>
      <label class="alpine-only">${t('installDisk')}<input name="disk" value="auto" pattern="auto|/dev/[a-z][a-z0-9]{1,15}" spellcheck="false"><small class="muted">${t('installDiskHint')}</small></label>
      <label class="dietpi-only">${t('dietpiTxt')}<input type="file" id="sdDietpi" accept=".txt,text/plain"><small class="muted">${t('dietpiTxtHint')}</small></label>
      ${templatePick(template)}
      <label>${t('hubUrl')}<input name="hub_url" value="${esc(location.origin)}" required></label>
      <div class="row2"><label>${t('namePrefix')}<input name="name_prefix" value="caracal" pattern="[a-z0-9][a-z0-9\\-]{0,30}" required></label>
      <label>${t('timezone')}<input name="timezone" value="${esc(tz)}"></label></div>
      <details class="no-alpine"><summary>${t('wifi')}</summary><div class="row2"><label>${t('wifiSsid')}<input name="wifi_ssid" maxlength="32"></label>
        <label>${t('wifiPassword')}<input name="wifi_password" type="password" autocomplete="off" minlength="8" maxlength="63"></label></div>
        <label>${t('wifiCountry')}<input name="wifi_country" value="${esc(country)}" maxlength="2"></label></details>
      <details><summary>${t('deviceLogin')}</summary><p class="muted">${t('deviceLoginHint')}</p>
        <div class="row2"><label class="user-field">${t('username')}<input name="user" value="admin"></label>
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
        const os = form.os.value, pc = ['alpine', 'ubuntu'].includes(os);
        $$('.dietpi-only', form).forEach(x => { x.hidden = os !== 'dietpi'; });
        $$('.user-field', form).forEach(x => { x.hidden = os === 'dietpi'; });
        $$('.alpine-only', form).forEach(x => { x.hidden = os !== 'alpine'; });
        $$('.no-alpine', form).forEach(x => { x.hidden = os === 'alpine' || os === 'alpine-rpi'; });
        $$('.pc-only', form).forEach(x => { x.hidden = !pc; });
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
      if (data.os.startsWith('alpine')) delete body.wifi_ssid;
      // the Raspberry Pi image is built on the hub (the first time it downloads Alpine): it takes a moment
      if (data.os === 'alpine-rpi') toast(t('sdImagePreparing'));
      const r = await fetch('/api/sdcard', { method: 'POST', body: JSON.stringify(body), headers: { Authorization: 'Bearer ' + S.token, 'Content-Type': 'application/json' } });
      await downloadResponse(r, data.os === 'alpine-rpi' ? 'caracal-alpine-rpi.img.gz' : `caracal-sdcard-${data.os}.zip`);
      const steps = { dietpi: 'sdCardStepsDietpi', alpine: 'usbStepsAlpine', ubuntu: 'usbStepsUbuntu', 'alpine-rpi': 'sdCardStepsAlpineRpi' }[data.os] || 'sdCardSteps';
      setTimeout(() => modal({ title: t(['alpine', 'ubuntu'].includes(data.os) ? 'usbTitle' : 'sdCardTitle'), body: `<ol class="steps">${t(steps)}</ol>` }), 50);
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
      root.innerHTML = `<div id="gHead"></div><div id="gItems"></div><div id="gVers"></div><div id="gDeps"></div>`;
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
      ${edit ? `<button class="btn" data-do="addDocument" data-target="global">${icon('audit')}${t('document')}</button>` : ''}
      ${edit ? ['web', 'image', 'video', 'camera', 'widget', 'grafana-tag'].map(k => `<button class="btn" data-do="globalAddItem" data-kind="${k}">${k === 'widget' ? icon('widget') : kindIcon(k)}${t({ web: 'addWeb', image: 'addImage', video: 'addVideo', camera: 'add_camera', widget: 'widget' }[k] || 'grafanaCollection')}</button>`).join('') : ''}</div>
      <ol class="playlist" id="gList">${p.items.map((it, i) => `<li class="g-row" ${edit ? `draggable="true" data-iid="${it.id}"` : ''}>
        <span class="idx">${i + 1}</span><span class="kind k-${esc(it.kind)}">${isWidget(it) ? icon('widget') : kindIcon(it.kind)}</span>
        <div class="pl-main"><b>${esc(it.name)}</b>${validityTag(it)}<small>${esc(kindLabel(it.kind))} · ${esc(isWidget(it) ? widgetLabel(it.source) : it.kind === 'web' ? it.source : it.kind === 'camera' ? t('camStreamsN', { n: (it.streams || []).length }) : isTag(it.kind) ? `${t('grafanaTag')} ${it.tag} · ${it.grafana_url}` : (it.file_name || '') + (it.file_size ? ' · ' + fmtSize(it.file_size) : ''))}</small></div>
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
    globalVersions($('#gVers', root), p);
    if (edit) {
      bindGlobalDrag($('#gList', root), p.id);
      dropFiles($('#gItems', root), files => uploadMany(files, async (f, up) => {
        await api('/api/library/' + up.id, { method: 'POST', json: {} });
        await api(`/api/global-playlists/${p.id}/items`, { method: 'POST', json: mediaItem(f, up) });
      }).then(() => { toast(t('saved')); render(); }));
    }
  },
};

function fmtSize(n) {
  if (n >= 1073741824) return (n / 1073741824).toFixed(1) + ' GB';
  return n > 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' kB';
}

// An item shown only from / until a time: the browser's local time, sent as Unix time (CARACAL 2026.10.14 and newer)
const toLocalInput = ts => (ts ? new Date(ts * 1000 - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16) : '');
const fromLocalInput = v => (v ? Math.round(new Date(v).getTime() / 1000) : null);
const validitySupport = ids => ids.every(id => (dev(id)?.capabilities || {}).validity === true);
function validityFields(it = {}) {
  return `<details class="validity" ${it.valid_from || it.valid_until ? 'open' : ''}><summary>${t('validity')}${it.valid_from || it.valid_until ? ` <span class="muted">${validityText(it)}</span>` : ''}</summary>
    <p class="muted">${t('validityHint')}</p><div class="row2"><label>${t('validFrom')}<input type="datetime-local" name="valid_from" value="${toLocalInput(it.valid_from)}"></label>
    <label>${t('validUntil')}<input type="datetime-local" name="valid_until" value="${toLocalInput(it.valid_until)}"></label></div></details>`;
}
const validityPayload = data => ({ valid_from: fromLocalInput(data.valid_from), valid_until: fromLocalInput(data.valid_until) });
function validityText(it) {
  const f = x => new Date(x * 1000).toLocaleString(S.lang === 'en' ? 'en-GB' : 'cs-CZ', { day: 'numeric', month: 'numeric', hour: '2-digit', minute: '2-digit' });
  return it.valid_from && it.valid_until ? `${f(it.valid_from)} – ${f(it.valid_until)}` : it.valid_from ? t('validFromText', { t: f(it.valid_from) }) : t('validUntilText', { t: f(it.valid_until) });
}
// a tag in the playlist: waiting for its time, playing until..., or over
function validityTag(it) {
  if (!it.valid_from && !it.valid_until) return '';
  const now = Date.now() / 1000;
  const state = it.valid_until && now >= it.valid_until ? 'over' : it.valid_from && now < it.valid_from ? 'waiting' : 'on';
  return ` <span class="tag validity-${state}" title="${esc(validityText(it))}">${icon('schedule')}${t('validity_' + state)} · ${esc(validityText(it))}</span>`;
}

function globalItemDialog(pid, kind, item = null) {
  const isMedia = kind === 'image' || kind === 'video';
  const it = item || { name: '', source: '', duration: kind === 'grafana-tag' ? 60 : kind === 'web' ? 30 : 15, scale: 1, kiosk: true };
  modal({
    title: item ? t('editItem') : isTag(kind) ? t('addCollection') : t('add_' + kind),
    body: `<div class="form">${isMedia && !item ? `<div class="seg wide-seg" id="srcSeg"><button type="button" class="active" data-src="upload">${t('uploadNew')}</button><button type="button" data-src="library">${t('fromLibrary')}</button></div>
      <div data-src-box="upload"><label>${t('file')}<input type="file" name="file" accept="${kind}/*" required></label><div class="upload-prog" hidden><i></i></div>
      <label class="check"><input type="checkbox" name="to_library" checked>${t('saveToLibrary')}</label></div>
      <div data-src-box="library" hidden><label>${t('libraryFile')}<select name="library_id"><option value="">${t('loading')}</option></select></label></div>` : ''}
      <label>${t('name')}<input name="name" value="${esc(it.name)}" ${isMedia ? '' : 'required'}></label>
      ${kind === 'web' ? `<label>URL<input name="source" type="url" value="${esc(it.source)}" placeholder="https://" required></label>` : ''}
      ${isTag(kind) ? grafanaFields(it) : ''}
      <div class="row2"><label>${t(isTag(kind) ? 'durationPerDashboard' : 'durationSec')}<input name="duration" type="number" min="5" value="${esc(it.duration)}" required></label>
      ${kind === 'web' || isTag(kind) ? `<label>${t('scale')}<input name="scale" type="number" step="0.05" min="0.5" max="3" value="${esc(it.scale ?? 1)}"></label>` : '<span></span>'}</div>
      ${kind === 'video' ? `<small class="muted">${t('videoDurationHint')}</small>` : ''}
      ${kind === 'web' || isTag(kind) ? darkField(it.dark || '', isTag(kind)) : ''}
      ${validityFields(it)}</div>`,
    onOpen: form => {
      const f = $('input[name=file]', form);
      if (f) f.onchange = () => { if (f.files[0] && !form.name.value) form.name.value = f.files[0].name.replace(/\.[^.]+$/, ''); };
      const seg = $('#srcSeg', form);
      if (!seg) return;
      seg.onclick = e => {
        const b = e.target.closest('[data-src]');
        if (!b) return;
        $$('[data-src]', seg).forEach(x => x.classList.toggle('active', x === b));
        $$('[data-src-box]', form).forEach(x => { x.hidden = x.dataset.srcBox !== b.dataset.src; });
        form.file.required = b.dataset.src === 'upload';
        form.library_id.required = b.dataset.src === 'library';
      };
      api('/api/library').then(list => {
        const files = list.filter(x => x.kind === kind);
        form.library_id.innerHTML = files.length ? files.map(x => `<option value="${esc(x.id)}">${esc(x.name)} · ${fmtSize(x.size)}</option>`).join('') : `<option value="">${t('libraryEmptyKind')}</option>`;
      }).catch(() => {});
    },
    onSubmit: async (data, form) => {
      const payload = { kind, name: data.name.trim(), duration: Number(data.duration || 0), scale: Number(data.scale || 1) };
      if (isMedia && !item && $('[data-src-box="library"]', form) && !$('[data-src-box="library"]', form).hidden) {
        if (!data.library_id) throw new Error('file_not_found');
        payload.file_id = data.library_id;
        Object.assign(payload, validityPayload(data));
        await api(`/api/global-playlists/${pid}/items`, { method: 'POST', json: payload });
        toast(t('saved'));
        return render();
      }
      if (kind === 'web') payload.source = data.source.trim();
      if (isTag(kind)) Object.assign(payload, grafanaPayload(data));
      if (kind === 'web' || isTag(kind)) payload.dark = data.dark || '';
      Object.assign(payload, validityPayload(data));
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
          if (data.to_library) await api('/api/library/' + up.id, { method: 'POST', json: {} });
        }
        await api(`/api/global-playlists/${pid}/items`, { method: 'POST', json: payload });
      }
      toast(t('saved'));
      render();
    },
  });
}

function globalDeployDialog(p) {
  const media = p.items.some(it => it.kind === 'image' || it.kind === 'video');
  const approval = S.approvalEnabled && !S.devices.every(d => canOn(d, 'approve'));
  modal({
    title: t('deployTitle', { name: esc(p.name) }), submit: approval ? t('requestApproval') : t('deploy'), wide: true,
    body: `<div class="form"><p class="muted">${t('deployHint')}</p>${targetPicker(null)}
      <div class="radios"><label class="check"><input type="radio" name="mode" value="append" checked>${t('mode_append')}</label>
      <label class="check"><input type="radio" name="mode" value="replace">${t('mode_replace')}</label></div>
      <div class="note warn">${t('deployReplaceWarning')}</div>
      ${media ? `<div class="note">${t('deployMediaNote')}</div>` : ''}
      ${approval ? `<div class="note info">${icon('approvals')}${t('approvalNeededHint')}</div><label>${t('approvalNote')}<input name="note" maxlength="500" placeholder="${esc(t('approvalNotePlaceholder'))}"></label>` : ''}
      <details class="diff-box" id="diffBox"><summary>${icon('eye')}${t('deployPreview')}</summary><div id="diffOut" class="muted">${t('deployPreviewPick')}</div></details></div>`,
    onOpen: form => {
      bindTargetPicker(form);
      let timer = null;
      const load = async () => {
        const targets = pickedTargets(Object.fromEntries(new FormData(form)));
        const out = $('#diffOut', form);
        if (!targets.length) { out.innerHTML = t('deployPreviewPick'); return; }
        out.innerHTML = t('loading');
        try {
          const r = await api(`/api/global-playlists/${p.id}/preview`, { method: 'POST', json: { target_ids: targets, mode: form.mode.value } });
          out.innerHTML = r.devices.map(x => diffHtml(x)).join('');
        } catch (e) { out.innerHTML = `<div class="note warn">${esc(errText(e))}</div>`; }
      };
      const later = () => { clearTimeout(timer); if ($('#diffBox', form).open) timer = setTimeout(load, 300); };
      form.addEventListener('change', later);
      form.addEventListener('click', e => { if (e.target.closest('[data-tsel]')) later(); });
      $('#diffBox', form).addEventListener('toggle', later);
    },
    onSubmit: async data => {
      const targets = pickedTargets(data);
      if (!targets.length) throw new Error('no_devices');
      const r = await api(`/api/global-playlists/${p.id}/deploy`, { method: 'POST', json: { target_ids: targets, mode: data.mode, note: data.note || '' } });
      if (r.pending_approval) { toast(t('approvalRequested')); S.approvalsPending += 0; render(); return; }
      toast(t('deployQueued', { n: targets.length }));
      if (r.media_unsupported.length) toast(t('deployMediaSkipped', { list: r.media_unsupported.join(', ') }), 'warn');
      if ((r.items_unsupported || []).length) toast(t('deployCameraSkipped', { list: r.items_unsupported.join(', ') }), 'warn');
      render();
    },
  });
}

// ---------- node templates: parts of a node's setup defined once, applied to nodes or baked into zero-touch media

const tplParts = p => [p.playlist_id && 'playlist', p.notify && 'notify', p.image && 'picture', p.overlay && 'overlay', p.player && 'player', (p.group || p.location) && 'org'].filter(Boolean);
const tplChips = p => tplParts(p).map(x => `<span>${t('tplPart_' + x)}</span>`).join('') || `<span>${t('tplEmpty')}</span>`;

// a template choice for new nodes (SD card, USB stick, installation); S.templates is loaded by the caller
const templatePick = (sel = '') => `<label>${t('nodeTemplate')}<select name="template"><option value="">${t('noTemplate')}</option>${(S.templates || []).map(p => `<option value="${p.id}" ${String(p.id) === String(sel) ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}</select><small class="muted">${t('nodeTemplateHint')}</small></label>`;
async function loadTemplates() { S.templates = await api('/api/templates').catch(() => S.templates || []); return S.templates; }

VIEWS.templates = {
  mount(root) {
    setTitle(t('nav_templates'));
    if (S.route.id) { root.innerHTML = `<div id="tHead"></div><div id="tParts"></div><div id="tUsed"></div>`; return; }
    root.innerHTML = `<section class="card flush"><div class="toolbar"><h2 class="grow">${t('nav_templates')}</h2>
      ${can('devices') ? `<button class="btn" data-do="templateFromDevice">${icon('copy')}${t('templateFromDevice')}</button><button class="btn primary" data-do="templateNew">${icon('plus')}${t('templateNew')}</button>` : ''}</div>
      <p class="muted card-intro">${t('templatesHint')}</p><div id="tList"></div></section>`;
  },
  async update(root) {
    if (!S.route.id) {
      const rows = await loadTemplates();
      if (!$('#tList', root)) return;
      patch($('#tList', root), `<div class="table-wrap"><table class="table"><thead><tr><th>${t('name')}</th><th>${t('tplContains')}</th><th>${t('updated')}</th></tr></thead>
        <tbody>${rows.map(p => `<tr data-href="#/templates/${p.id}"><td><b>${esc(p.name)}</b>${p.description ? `<br><small class="muted">${esc(p.description)}</small>` : ''}</td>
        <td><div class="chips">${tplChips(p)}</div></td><td class="nowrap">${dt(p.updated)}<br><small class="muted">${esc(p.updated_by)}</small></td></tr>`).join('')
        || `<tr><td colspan="3">${emptyState('templates', t('noTemplates'), t('templatesHint'), can('devices') ? `<button class="btn primary" data-do="templateNew">${icon('plus')}${t('templateNew')}</button>` : '')}</td></tr>`}</tbody></table></div>`);
      return;
    }
    const p = await api('/api/templates/' + encodeURIComponent(S.route.id)).catch(() => null);
    if (!$('#tHead', root)) return;
    if (!p) { patch($('#tHead', root), `<div class="card empty">${t('err_not_found')}</div>`); return; }
    S.template = p;
    setTitle(p.name, t('nav_templates'), '#/templates');
    const edit = can('devices'), n = p.notify || {};
    const editBtn = (todo, label = t('edit')) => (edit ? `<button class="btn sm" data-do="${todo}">${icon('edit')}${label}</button>` : '');
    const off = `<span class="muted">${t('tplKeep')}</span>`;
    patch($('#tHead', root), `<section class="card dev-hero"><div class="hero-main"><div class="hero-title">${icon('templates')}<h2>${esc(p.name)}</h2></div>
      <div class="chips">${tplChips(p)}<span>${t('updated')} ${dt(p.updated)} · ${esc(p.updated_by)}</span></div>
      ${p.description ? `<p class="muted">${esc(p.description)}</p>` : ''}</div>
      ${edit ? `<div class="hero-actions"><button class="btn primary" data-do="templateApply" ${tplParts(p).length ? '' : 'disabled'}>${icon('upload')}${t('templateApply')}</button>
        <button class="btn" data-do="sdCard" data-template="${p.id}">${icon('download')}${t('zeroTouchMedia')}</button>
        <button class="btn" data-do="oneCommand" data-template="${p.id}">${icon('console')}${t('oneCommand')}</button>
        <button class="btn" data-do="templateEdit">${icon('edit')}${t('edit')}</button><button class="btn danger" data-do="templateDelete">${icon('trash')}${t('delete')}</button></div>` : ''}</section>`);
    const part = (ic, title, hint, body, actions) => `<section class="card tpl-part"><div class="card-head"><h2>${icon(ic)}${title}</h2><div class="tpl-actions">${actions}</div></div><p class="muted">${hint}</p>${body}</section>`;
    patch($('#tParts', root), `<div class="tpl-grid">
      ${part('playlists', t('playlist'), t('tplPlaylistHint'), p.playlist_id ? (p.playlist ? `<dl class="kv"><dt>${t('globalPlaylist')}</dt><dd><a href="#/global/${p.playlist_id}">${esc(p.playlist.name)}</a> <span class="muted">(${t('nItems', { n: p.playlist.items })})</span></dd><dt>${t('mode')}</dt><dd>${t('mode_' + (p.playlist_mode || 'replace'))}</dd></dl>` : `<div class="note warn">${t('tplPlaylistMissing')}</div>`) : off, editBtn('templatePlaylist'))}
      ${part('bell', t('notifySettings'), t('tplNotifyHint'), p.notify ? `<div class="tpl-look"><div id="tplPreview"></div><dl class="kv">
        ${'enabled' in n ? `<dt>${t('notifyEnabled')}</dt><dd>${n.enabled ? t('yes') : t('no')}</dd>` : ''}${n.position ? `<dt>${t('notifyPosition')}</dt><dd>${t('notifyPos_' + n.position.replace(/-/g, '_'))}</dd>` : ''}
        ${n.duration ? `<dt>${t('notifyDuration')}</dt><dd>${n.duration} s</dd>` : ''}${n.sound ? `<dt>${t('notifySound')}</dt><dd>${t('notifySound_' + n.sound)}</dd>` : ''}
        <dt>${t('notifyLook')}</dt><dd>${n.style ? t('tplCustomLook') : t('tplDefaultLook')}${p.image ? ` · ${t('tplWithPicture')}` : ''}</dd></dl></div>` : off,
        editBtn('templateNotify', t('notifySettings')) + editBtn('templateLook', t('notifyLook')) + (edit && p.notify ? `<button class="btn sm ghost" data-do="templateClear" data-part="notify">${icon('x')}${t('tplRemovePart')}</button>` : ''))}
      ${part('settings', t('overlayBar'), t('overlayHint'), p.overlay ? `<dl class="kv"><dt>${t('overlayEnabled')}</dt><dd>${p.overlay.enabled ? t('yes') : t('no')}</dd>${p.overlay.size ? `<dt>${t('overlaySize')}</dt><dd>${p.overlay.size} px</dd>` : ''}</dl>` : off, editBtn('templateOverlay'))}
      ${part('moon', t('webDark'), t('webDarkHint'), p.player ? `<dl class="kv"><dt>${t('webDark')}</dt><dd>${t('dark_' + p.player.web_dark)}</dd></dl>` : off, editBtn('templatePlayer'))}
      ${part('group', t('groupAndLocation'), t('tplOrgHint'), p.group || p.location ? `<dl class="kv">${p.group ? `<dt>${t('group')}</dt><dd>${esc(p.group)}</dd>` : ''}${p.location ? `<dt>${t('location')}</dt><dd>${esc(p.location)}</dd>` : ''}</dl>` : off, editBtn('templateOrg'))}
    </div>`);
    const prev = $('#tplPreview', root);
    if (prev && !prev.childElementCount) {
      NotifyStyle.preview(prev, n.style, { t: (cs, en) => (S.lang === 'cs' ? cs : en), position: n.position, scale: n.scale });
      if (p.image) fetch(`/api/templates/${p.id}/image`, { headers: { Authorization: 'Bearer ' + S.token } }).then(r => (r.ok ? r.blob() : null))
        .then(b => { if (b && prev.isConnected) NotifyStyle.preview(prev, n.style, { t: (cs, en) => (S.lang === 'cs' ? cs : en), position: n.position, scale: n.scale, image: URL.createObjectURL(b) }); }).catch(() => {});
    }
    const skipped = x => x.skipped.map(s => `${t('act_' + s.action)}: ${errText(new Error(s.reason))}`).join(', ');
    patch($('#tUsed', root), `<section class="card flush"><div class="toolbar"><h2 class="grow">${t('tplApplied')}</h2></div>
      <div class="table-wrap"><table class="table"><thead><tr><th>${t('time')}</th><th>${t('device')}</th><th>${t('user')}</th><th>${t('result')}</th></tr></thead>
      <tbody>${p.applied.map(x => `<tr><td class="nowrap">${dt(x.created)}</td><td><a href="#/device/${encodeURIComponent(x.device_id)}">${esc(x.device_name || x.device_id)}</a></td>
        <td>${x.automatic ? t('tplAutomatic') : esc(x.username)}</td><td>${x.queued.map(a => `<span class="badge st-completed">${esc(a === 'organization' ? t('groupAndLocation') : t('act_' + a))}</span>`).join(' ')}
        ${x.skipped.length ? `<small class="muted" title="${esc(skipped(x))}">${t('tplSkippedN', { n: x.skipped.length })}</small>` : ''}</td></tr>`).join('')
        || `<tr><td colspan="4" class="empty">${t('tplNeverApplied')}${p.devices ? ' ' + t('tplWaitingN', { n: p.devices }) : ''}</td></tr>`}</tbody></table></div></section>`);
  },
};

const tplSave = async (patchData, msg = t('saved')) => { await api('/api/templates/' + S.template.id, { method: 'PATCH', json: patchData }); toast(msg); render(); };

function templateApplyDialog(p, preselect = []) {
  modal({
    title: t('templateApplyTitle', { name: esc(p.name) }), submit: t('templateApply'), wide: true,
    body: `<div class="form"><p class="muted">${t('templateApplyHint')}</p><div class="chips">${tplChips(p)}</div>${targetPicker(null, preselect)}
      ${p.playlist_id && p.playlist_mode !== 'append' ? `<div class="note warn">${t('tplReplaceWarning')}</div>` : ''}</div>`,
    onOpen: bindTargetPicker,
    onSubmit: async data => {
      const targets = pickedTargets(data);
      if (!targets.length) throw new Error('no_devices');
      const r = await api(`/api/templates/${p.id}/apply`, { method: 'POST', json: { target_ids: targets } });
      const skipped = r.results.filter(x => x.skipped.length);
      toast(t('templateApplied', { n: targets.length }));
      if (skipped.length) toast(t('templatePartsSkipped', { list: skipped.map(x => `${x.device_name}: ${x.skipped.map(s => t('act_' + s.action)).join(', ')}`).join('; ') }), 'warn');
      render();
    },
  });
}

function templateLookDialog(p) {
  let editor = null;
  const n = p.notify || {};
  const dlg = modal({
    title: t('notifyLook'), wide: true, submit: t('save'),
    body: `<p class="muted">${t('tplLookHint')}</p><div id="lookEditor"></div>`,
    onOpen: form => {
      editor = NotifyStyle.editor($('#lookEditor', form), n.style, {
        t: (cs, en) => (S.lang === 'cs' ? cs : en), position: n.position, scale: n.scale,
        // the picture is stored with the template right away (sent to the nodes when the template is applied)
        onImage: async file => {
          if (file.size > 5 * 1024 * 1024) { toast(errText(new Error('file_too_large')), 'err'); throw new Error('file_too_large'); }
          try { const up = await uploadFile(file, () => {}); await api(`/api/templates/${p.id}/image`, { method: 'POST', json: { file_id: up.id } }); } catch (err) { toast(errText(err), 'err'); throw err; }
          return URL.createObjectURL(file);
        },
        onImageRemove: async () => { await api(`/api/templates/${p.id}/image`, { method: 'POST', json: { reset: true } }); },
      });
      if (p.image) fetch(`/api/templates/${p.id}/image`, { headers: { Authorization: 'Bearer ' + S.token } })
        .then(r => (r.ok ? r.blob() : null)).then(b => { if (b && editor) editor.setImage(URL.createObjectURL(b)); }).catch(() => {});
    },
    onSubmit: () => tplSave({ notify: { ...n, style: editor.get() } }),
  });
  dlg.addEventListener('close', () => render(), { once: true });
  dlg.classList.add('xwide');
}

function templateNotifyDialog(p) {
  const s = { ...NOTIFY_DEFAULTS, ...(p.notify || {}) };
  const numIn = (name, min, max, step = 1) => `<input name="${name}" type="number" min="${min}" max="${max}" step="${step}" value="${esc(s[name])}" required>`;
  modal({
    title: t('notifySettings'), wide: true,
    body: `<div class="form"><p class="muted">${t('tplNotifyHint')}</p>
      <label class="check"><input type="checkbox" name="enabled" ${s.enabled ? 'checked' : ''}>${t('notifyEnabled')}</label>
      <div class="row2"><label>${t('notifyPosition')}<select name="position">${choice(NOTIFY_POSITIONS, s.position, 'notifyPos_')}</select></label>
      <label>${t('notifyDuration')}${numIn('duration', 3, 120)}</label></div>
      <div class="row2"><label>${t('notifySize')}${numIn('scale', 50, 300, 10)}</label><label>${t('notifyMaxQueue')}${numIn('max_queue', 1, 200)}</label></div>
      <div class="row2"><label>${t('notifySound')}<select name="sound">${choice(NOTIFY_SOUNDS, s.sound, 'notifySound_')}</select></label><label>${t('notifyVolume')}${numIn('volume', 0, 100, 10)}</label></div>
      <div class="row2"><label>${t('notifyHistoryMax')}${numIn('history_max', 50, 5000, 50)}</label><label>${t('notifyHistoryDays')}${numIn('history_days', 1, 90)}</label></div></div>`,
    onSubmit: data => {
      const notify = { ...(p.notify || {}), enabled: !!data.enabled, position: data.position, sound: data.sound };
      for (const k of ['duration', 'scale', 'max_queue', 'volume', 'history_max', 'history_days']) notify[k] = Number(data[k]);
      return tplSave({ notify });
    },
  });
}

// one command for a running Linux (Debian, Ubuntu, Raspberry Pi OS, Alpine): needs the enrollment token (admin)
async function oneCommandDialog(template = '') {
  const [s] = await Promise.all([api('/api/settings'), loadTemplates()]);
  const command = (tpl, fleet, wget) => `${wget ? `wget -qO- ${location.origin}/api/bootstrap/caracal-install.sh | sh -s --` : `curl -fsSL ${location.origin}/api/bootstrap/caracal-install.sh | sudo sh -s --`} --hub ${location.origin} --token ${s.enroll_token}${tpl ? ` --template ${tpl}` : ''}${fleet ? ' --via-fleet' : ''}`;
  modal({
    title: t('oneCommand'), wide: true,
    body: `<div class="form"><p class="muted">${t('oneCommandHint')}</p>${templatePick(template)}
      <label class="check"><input type="checkbox" name="via_fleet">${t('viaFleet')}</label>
      <h3>Debian · Ubuntu · Raspberry Pi OS</h3><pre class="code" id="ocCurl"></pre><div class="form-actions start"><button type="button" class="btn sm" data-copy="ocCurl">${icon('copy')}${t('copy')}</button></div>
      <h3>Alpine Linux <small class="muted">(root)</small></h3><pre class="code" id="ocWget"></pre><div class="form-actions start"><button type="button" class="btn sm" data-copy="ocWget">${icon('copy')}${t('copy')}</button></div>
      <div class="note info">${t('oneCommandToken')}</div></div>`,
    onOpen: form => {
      const sync = () => { $('#ocCurl', form).textContent = command(form.template.value, form.via_fleet.checked, false); $('#ocWget', form).textContent = command(form.template.value, form.via_fleet.checked, true); };
      form.template.onchange = form.via_fleet.onchange = sync;
      $$('[data-copy]', form).forEach(b => { b.onclick = () => navigator.clipboard.writeText($('#' + b.dataset.copy, form).textContent).then(() => toast(t('copied'))); });
      sync();
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
  el.innerHTML = `<div class="card-head"><h2>${t('proxyTitle')}</h2>${can('settings') ? `<button class="btn sm ghost" data-do="proxyClear">${icon('trash')}${t('proxyClear')}</button>` : ''}</div>
    <p class="muted">${t('proxyHint')}</p>
    <dl class="kv"><dt>${t('proxyNodes')}</dt><dd>${nodes}</dd>
      <dt>${t('proxyCache')}</dt><dd>${fmtSize(p.cache_bytes)} / ${fmtSize(p.cache_limit)} (${t('proxyAptPart', { size: fmtSize(p.apt_bytes) })})</dd>
      <dt>${t('proxyImages')}</dt><dd>${p.images.map(x => `<div>${esc(x.name)} <span class="muted">${fmtSize(x.size)}</span></div>`).join('') || '—'}</dd>
      ${p.jobs.length ? `<dt>${t('proxyPreparing')}</dt><dd>${p.jobs.map(j => `<div>${esc(j.image)} · ${esc(j.arch)} ${j.state === 'error' ? `<span class="tag critical">${esc(j.error)}</span>` : `<span class="tag info">${t('proxyDownloading')}</span>`}</div>`).join('')}</dd>` : ''}
      <dt>${t('proxyAptHosts')}</dt><dd><small>${p.apt_hosts.map(esc).join(', ')}</small></dd></dl>
    <div id="mirrorBox"></div>`;
  const m = await api('/api/proxy/mirrors').catch(() => null);
  if (!m || !el.isConnected) return;
  $('#mirrorBox', el).innerHTML = `<h3>${t('mirrorsTitle')}</h3><p class="muted">${t('mirrorsHint')}</p>
    <form class="form" id="mirrorForm"><label>${t('mirrorsLabel')}<textarea name="mirrors" rows="4" spellcheck="false" placeholder="mirror.example.cz\nhttp://apt.firma.lan:8080" ${can('settings') ? '' : 'readonly'}>${esc(m.mirrors.map(x => (x.scheme === 'http' ? 'http://' : '') + x.host).join('\n'))}</textarea>
      <small class="muted">${t('mirrorsFormat')}</small></label>
    ${can('settings') ? `<div class="form-actions"><button class="btn primary">${t('save')}</button></div>` : ''}</form>`;
  $('#mirrorForm', el).onsubmit = async e => {
    e.preventDefault();
    const lines = e.target.mirrors.value.split(/\s+/).map(x => x.trim()).filter(Boolean);
    try { await api('/api/proxy/mirrors', { method: 'PUT', json: { mirrors: lines } }); toast(t('saved')); proxyCard(el); } catch (err) { toast(errText(err), 'err'); }
  };
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
  burst();
}

async function sendBulk(ids, action, payload = {}) {
  const r = await api('/api/bulk/commands', { method: 'POST', json: { device_ids: ids, action, payload } });
  toast(t('bulkQueued', { n: r.command_ids.length, action: t('act_' + action) }));
  if (r.skipped.length) toast(t('bulkSkipped', { n: r.skipped.length, list: r.skipped.map(s => (dev(s.device_id)?.name || s.device_id) + ' (' + errText(new Error(s.reason)) + ')').join(', ') }), 'warn');
  burst();
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
    ${kind === 'web' ? darkField(a.dark || '') : ''}
    ${showEnabled ? `<label class="check"><input type="checkbox" name="enabled" ${a.enabled ? 'checked' : ''}>${t('enabledInPlaylist')}</label>` : ''}
    ${validitySupport(targets || [deviceId]) ? validityFields(a) : ''}
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
      if (kind === 'web') { payload.source = data.source.trim(); payload.scale = Number(data.scale || 1); payload.dark = data.dark || ''; }
      if (profiles) payload.auth_profile_id = data.auth_profile_id ? Number(data.auth_profile_id) : null;
      if ('valid_from' in data) Object.assign(payload, validityPayload(data));
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
      ${darkField(c.dark || '', true)}
      ${validitySupport(targets || [deviceId]) ? validityFields(c) : ''}
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
      const payload = { name: data.name.trim(), ...grafanaPayload(data), duration: Number(data.duration || 60), scale: Number(data.scale || 1), dark: data.dark || '' };
      if ('valid_from' in data) Object.assign(payload, validityPayload(data));
      if (col) return sendCommand(deviceId, 'update_collection', { id: col.id, ...payload });
      const ids = targets || [deviceId, ...pickedTargets(data)];
      return ids.length === 1 ? sendCommand(ids[0], 'add_collection', payload) : sendBulk(ids, 'add_collection', payload);
    },
  });
}

// A camera: 1-9 streams (RTSP, HTTP) as a grid, played by the node with mpv. ONVIF cameras are found by the node.
const camStreamRow = (st = {}, edit = false) => `<div class="stream-row" data-stream>
  <input data-f="name" placeholder="${esc(t('camStreamName'))}" value="${esc(st.name || '')}">
  <input data-f="url" placeholder="rtsp://192.168.1.20:554/stream1" value="${esc(st.url || '')}" required spellcheck="false">
  <input data-f="username" placeholder="${esc(edit ? t('camUnchanged') : t('username'))}" value="${esc(st.username || '')}" autocomplete="off">
  <input data-f="password" type="password" placeholder="${esc(edit ? t('camUnchanged') : t('password'))}" autocomplete="new-password">
  <button type="button" class="icon-btn" data-preview-row title="${t('camPreview')}">${icon('eye')}</button>
  <button type="button" class="icon-btn danger" data-del-stream title="${t('delete')}">${icon('x')}</button></div>`;
// A picture of a stream, taken by the node (CARACAL 2026.10.14 and agent 4.16)
async function cameraPreview(deviceId, payload, box) {
  box.innerHTML = `<div class="note">${t('camPreviewLoading')}</div>`;
  try {
    const r = await commandResult(deviceId, 'camera_preview', payload);
    box.innerHTML = r.image ? `<img class="cam-preview" alt="" src="data:${esc(r.type || 'image/jpeg')};base64,${r.image}">` : `<div class="note warn">${esc(r.text || r.detail || t('camPreviewNone'))}</div>`;
  } catch (e) { box.innerHTML = `<div class="note warn">${esc(errText(e))}</div>`; }
}
// pid: a camera of a global playlist (stored in Fleet); finding and previews then go through a node chosen in the dialog
function cameraDialog(deviceId, cam = null, targets = null, pid = null) {
  let src = { streams: [{}], layout: 'auto' };
  if (cam) { try { src = { ...src, ...JSON.parse(cam.source || '{}') }; } catch { /* ignore */ } }
  if (pid && cam) src = { ...src, streams: cam.streams, layout: cam.layout, audio: cam.audio };
  const via = pid ? S.devices.filter(d => d.online && canOn(d, 'content') && (d.capabilities || {}).camera_onvif === true) : [];
  if (pid) deviceId = via.length ? via[0].id : null;
  const layouts = [['auto', t('camLayoutAuto')], ['2', t('camLayout2')], ['2x2', '2 × 2'], ['3x3', '3 × 3']];
  modal({
    title: cam ? t('camEdit') : t('add_camera'), wide: true, submit: cam ? t('save') : t('add'),
    body: `<div class="form"><p class="muted">${t(pid ? 'camHintGlobal' : 'camHint')}</p>
      <div class="row2"><label>${t('name')}<input name="name" required value="${esc(cam ? cam.name : '')}"></label>
      <label>${t('durationSec')}<input name="duration" type="number" min="5" value="${esc(cam ? cam.duration : 30)}" required></label></div>
      <div class="row2"><label>${t('camLayout')}<select name="layout">${layouts.map(([v, l]) => `<option value="${v}" ${src.layout === v ? 'selected' : ''}>${l}</option>`).join('')}</select></label>
      <label class="check"><input type="checkbox" name="audio" ${src.audio ? 'checked' : ''}>${t('camAudio')}</label></div>
      ${pid || validitySupport(targets || [deviceId]) ? validityFields(cam || {}) : ''}
      <h3>${t('camStreams')}</h3>${cam ? `<p class="muted">${t(pid ? 'camEditHintGlobal' : 'camEditHint')}</p>` : ''}<div id="camRows">${src.streams.map(x => camStreamRow(x, !!cam)).join('')}</div>
      <div class="form-actions start"><button type="button" class="btn sm" data-add-stream>${icon('plus')}${t('camAddStream')}</button></div>
      ${targets || !deviceId ? '' : `<details class="onvif"><summary>${t('camOnvif')}</summary><p class="muted">${t('camOnvifHint')}</p>
        ${pid ? `<label>${t('camVia')}<select data-via>${via.map(d => `<option value="${esc(d.id)}">${esc(d.name)}</option>`).join('')}</select><small class="muted">${t('camViaHint')}</small></label>` : ''}
        <div class="form-actions start"><button type="button" class="btn sm" data-discover>${icon('search')}${t('camDiscover')}</button></div><div class="cam-found"></div>
        <div class="row2"><label>${t('camHost')}<input data-onvif="host" placeholder="192.168.1.20"></label><label>Port<input data-onvif="port" type="number" value="80"></label></div>
        <div class="row2"><label>${t('username')}<input data-onvif="username" autocomplete="off"></label><label>${t('password')}<input data-onvif="password" type="password" autocomplete="new-password"></label></div>
        <input type="hidden" data-onvif="xaddr"><div class="form-actions start"><button type="button" class="btn sm primary" data-onvif-load>${icon('camera')}${t('camLoad')}</button></div><div class="cam-streams"></div></details>`}
      ${targets ? `<p class="muted">${t('addToSelected', { n: targets.length })}</p>` : cam || pid ? '' : `<details><summary>${t('alsoAddTo')}</summary>${targetPicker(deviceId, [], d => (d.capabilities || {}).add_camera === true)}</details>`}</div>`,
    onOpen: form => {
      bindTargetPicker(form);
      const rows = $('#camRows', form), ov = k => $(`[data-onvif="${k}"]`, form);
      const viaSel = $('[data-via]', form);
      if (viaSel) viaSel.onchange = () => { deviceId = viaSel.value; };
      const preview = pid ? via.some(d => (d.capabilities || {}).camera_preview === true) : (dev(deviceId)?.capabilities || {}).camera_preview === true;
      if (!preview) $$('[data-preview-row]', form).forEach(b => b.remove());
      form.addEventListener('click', async e => {
        if (e.target.closest('[data-add-stream]')) {
          rows.insertAdjacentHTML('beforeend', camStreamRow());
          if (!preview) $$('[data-preview-row]', form).forEach(b => b.remove());
        }
        const pr = e.target.closest('[data-preview-row]');
        if (pr) {
          const row = pr.closest('[data-stream]'), f = k => $(`[data-f=${k}]`, row).value.trim();
          const index = $$('[data-stream]', form).indexOf(row);
          // an edited camera without a new password: the node uses the stored one
          const payload = cam && !pid && !f('password') && !f('username') ? { id: cam.id, index } : { url: f('url'), username: f('username'), password: $('[data-f=password]', row).value };
          let box = row.nextElementSibling;
          if (!box || !box.classList.contains('cam-preview-box')) { row.insertAdjacentHTML('afterend', '<div class="cam-preview-box"></div>'); box = row.nextElementSibling; }
          if (pid && (dev(deviceId)?.capabilities || {}).camera_preview !== true) box.innerHTML = `<div class="note warn">${t('camPreviewNoNode')}</div>`;
          else cameraPreview(deviceId, payload, box);
        }
        const del = e.target.closest('[data-del-stream]');
        if (del && $$('[data-stream]', form).length > 1) del.closest('[data-stream]').remove();
        const disc = e.target.closest('[data-discover]');
        if (disc) {
          tryCommand(form, disc, () => commandResult(deviceId, 'camera_discover'), r => (r.cameras || []).length
            ? `<div class="cam-list">${r.cameras.map(x => `<button type="button" class="btn sm" data-pick-cam="${esc(x.address)}" data-xaddr="${esc(x.xaddr)}">${icon('camera')}${esc(x.name || x.hardware || x.address)} <small class="muted">${esc(x.address)}${x.kind === 'recorder' ? ' · ' + t('camRecorder') : ''}</small></button>`).join('')}</div>`
            : `<div class="note">${t('camNoneFound')}</div>`, '.cam-found');
        }
        const pick = e.target.closest('[data-pick-cam]');
        if (pick) { ov('host').value = pick.dataset.pickCam; ov('xaddr').value = pick.dataset.xaddr; ov('username').focus(); }
        const load = e.target.closest('[data-onvif-load]');
        if (load) {
          tryCommand(form, load, () => commandResult(deviceId, 'camera_onvif', { host: ov('host').value.trim(), port: Number(ov('port').value) || 80,
            username: ov('username').value, password: ov('password').value, xaddr: ov('xaddr').value }), r => {
            const use = i => {
              const x = r.streams[i], empty = $$('[data-stream]', form).find(row => !$('[data-f=url]', row).value);
              if ($$('[data-stream]', form).some(row => $('[data-f=url]', row).value === x.url)) return;
              if (!empty && $$('[data-stream]', form).length >= 9) return toast(t('camMaxStreams'), 'err');
              const html = camStreamRow({ name: x.name, url: x.url, username: ov('username').value });
              if (empty) empty.outerHTML = html; else rows.insertAdjacentHTML('beforeend', html);
              if (!preview) $$('[data-preview-row]', form).forEach(b => b.remove());
              const added = $$('[data-stream]', form).find(row => $('[data-f=url]', row).value === x.url);
              if (added) $('[data-f=password]', added).value = ov('password').value;
              if (!form.name.value) form.name.value = x.name || ov('host').value;
              const btn = $(`[data-use-stream="${i}"]`, form);
              if (btn) btn.disabled = true;
            };
            setTimeout(() => {
              $$('[data-use-stream]', form).forEach(b => { b.onclick = () => use(Number(b.dataset.useStream)); });
              $$('[data-preview-stream]', form).forEach(b => {
                b.onclick = () => {
                  const x = r.streams[Number(b.dataset.previewStream)];
                  cameraPreview(deviceId, { url: x.url, username: ov('username').value, password: ov('password').value }, $('.cam-found-preview', form));
                };
              });
              const mains = $('[data-use-mains]', form);
              // a recorder: the first stream of every channel (its main stream), as many as fit
              if (mains) mains.onclick = () => { const seen = new Set(); r.streams.forEach((x, i) => { if (x.source && !seen.has(x.source)) { seen.add(x.source); use(i); } }); };
            });
            const channels = new Set((r.streams || []).map(x => x.source).filter(Boolean));
            return `${channels.size > 1 ? `<div class="note info"><div>${t('camRecorderFound', { n: channels.size, s: r.streams.length })}</div><button type="button" class="btn sm" data-use-mains>${icon('plus')}${t('camAddMains')}</button></div>` : ''}
              <div class="stream-list">${(r.streams || []).map((x, i) => `<div class="stream-pick"><div><b>${esc(x.name || x.url)}</b><small class="muted">${esc(x.url)}</small></div>
              ${preview ? `<button type="button" class="btn sm ghost" data-preview-stream="${i}">${icon('eye')}${t('camPreview')}</button>` : ''}<button type="button" class="btn sm" data-use-stream="${i}">${icon('plus')}${t('add')}</button></div>`).join('')}</div>
              <div class="cam-found-preview"></div>`;
          }, '.cam-streams');
        }
      });
    },
    onSubmit: data => {
      const streams = $$('#camRows [data-stream]').map(row => Object.fromEntries($$('[data-f]', row).map(i => [i.dataset.f, i.value.trim()])));
      const payload = { name: data.name.trim(), duration: Number(data.duration || 30), layout: data.layout, audio: !!data.audio, streams };
      if ('valid_from' in data) Object.assign(payload, validityPayload(data));
      if (pid) {
        return api(cam ? `/api/global-playlists/${pid}/items/${cam.id}` : `/api/global-playlists/${pid}/items`,
          { method: cam ? 'PATCH' : 'POST', json: { kind: 'camera', ...payload } }).then(() => { toast(t('saved')); render(); });
      }
      if (cam) return sendCommand(deviceId, 'update_camera', { id: cam.id, ...payload });
      const ids = targets || [deviceId, ...pickedTargets(data)];
      return ids.length === 1 ? sendCommand(ids[0], 'add_camera', payload) : sendBulk(ids, 'add_camera', payload);
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
  const [img] = await Promise.all([api('/api/node-image').catch(() => ({ image: '', versions: [] })), loadTemplates()]);
  modal({
    title: t('addDevice'), submit: t('install'), wide: true,
    body: `<div class="form">
      <div class="mode-pick"><label class="check"><input type="radio" name="mode" value="node" ${mode === 'node' ? 'checked' : ''}><span><b>${t('modeNode')}</b><small>${t('modeNodeHint')}</small></span></label>
      <label class="check"><input type="radio" name="mode" value="agent" ${mode === 'agent' ? 'checked' : ''}><span><b>${t('modeAgent')}</b><small>${t('modeAgentHint')}</small></span></label></div>
      <div class="node-only">${img.image ? versionField(img) : `<div class="note warn">${t('err_node_image_missing')} <a href="#/updates">${t('nav_updates')}</a></div>`}</div>
      <div class="row2"><label>${t('hostIp')}<input name="host" value="${esc(host)}" required></label><label>${t('sshPort')}<input name="port" type="number" value="22"></label></div>
      <div class="form-actions start"><button type="button" class="btn sm ghost" data-do="discover">${icon('refresh')}${t('discoverTitle')}</button>${can('settings') ? `<button type="button" class="btn sm ghost" data-do="sdCard">${icon('download')}${t('sdCardTitle')}</button>` : ''}</div>
      <div class="row2"><label>${t('sshUser')}<input name="username" value="pi" required></label><label>${t('sshPassword')}<input name="password" type="password" autocomplete="off"></label></div>
      <details><summary>${t('sshKeyAuth')}</summary><label>${t('privateKey')}<textarea name="private_key" rows="4" placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"></textarea></label><label>${t('passphrase')}<input name="passphrase" type="password"></label></details>
      <label>${t('deviceName')}<input name="name" value="${esc(name)}"></label>
      <div class="row2"><label>${t('group')}<input name="group" list="dlGroups"></label><label>${t('location')}<input name="location" list="dlLocations"></label></div>${orgDatalists()}
      ${templatePick()}
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
      ${kind === 'locations' ? `<label>${t('address')}<span class="pw-row"><input name="address" value="${esc(o.address || '')}"><button type="button" class="btn sm" data-geocode>${icon('location')}${t('findOnMap')}</button></span></label>
        <div class="geo-found"></div><div class="row2"><label>${t('latitude')}<input name="lat" value="${o.lat ?? ''}" inputmode="decimal"></label><label>${t('longitude')}<input name="lon" value="${o.lon ?? ''}" inputmode="decimal"></label></div>` : ''}
      <label>${t('description')}<textarea name="description" rows="2">${esc(o.description || '')}</textarea></label></div>`,
    onOpen: form => {
      const b = $('[data-geocode]', form);
      if (b) b.onclick = () => tryCommand(form, b, () => api('/api/org/geocode', { method: 'POST', json: { address: form.address.value } }), r => {
        setTimeout(() => $$('[data-place]', form).forEach(x => { x.onclick = () => { form.lat.value = x.dataset.lat; form.lon.value = x.dataset.lon; $('.geo-found', form).innerHTML = ''; }; }));
        return r.places.length ? `<div class="cam-list">${r.places.map(x => `<button type="button" class="btn sm" data-place data-lat="${x.lat}" data-lon="${x.lon}">${icon('location')}${esc(x.name.slice(0, 90))}</button>`).join('')}</div>` : `<div class="note">${t('noMatch')}</div>`;
      }, '.geo-found');
    },
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
          if (data.kind === 'camera') cameraDialog(ids[0], null, one ? null : ids);
          else if (one) data.kind === 'collection' ? collectionDialog(one) : data.kind === 'profile' ? profileDialog(one) : assetDialog(one, data.kind);
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
  sdCard(b) { sdCardDialog(b.dataset.template || ''); },
  oneCommand(b) { oneCommandDialog(b.dataset.template || ''); },
  templateNew() {
    modal({
      title: t('templateNew'),
      body: `<div class="form"><p class="muted">${t('templatesHint')}</p><label>${t('name')}<input name="name" required></label><label>${t('description')}<textarea name="description" rows="2"></textarea></label></div>`,
      onSubmit: async data => { const r = await api('/api/templates', { method: 'POST', json: data }); location.hash = '#/templates/' + r.id; },
    });
  },
  templateFromDevice(b) {
    const here = b.dataset.id || (S.route.view === 'device' ? S.route.id : '');
    modal({
      title: t('templateFromDevice'),
      body: `<div class="form"><p class="muted">${t('templateFromDeviceHint')}</p><label>${t('sourceDevice')}<select name="device_id" required><option value="">—</option>${S.devices.map(d => `<option value="${esc(d.id)}" ${d.id === here ? 'selected' : ''}>${esc(d.name)}</option>`).join('')}</select></label>
        <label>${t('name')}<input name="name"></label><label class="check"><input type="checkbox" name="playlist" checked>${t('templateWithPlaylist')}</label></div>`,
      onSubmit: async data => {
        const r = await api('/api/templates/from-device', { method: 'POST', json: { ...data, playlist: !!data.playlist } });
        if (r.skipped.length) toast(t('copySkipped', { list: r.skipped.join(', ') }), 'warn');
        if (r.image_missing) toast(t('templateImageMissing'), 'warn');
        location.hash = '#/templates/' + r.id;
      },
    });
  },
  templateEdit() {
    const p = S.template;
    modal({
      title: t('edit'),
      body: `<div class="form"><label>${t('name')}<input name="name" value="${esc(p.name)}" required></label><label>${t('description')}<textarea name="description" rows="2">${esc(p.description)}</textarea></label></div>`,
      onSubmit: data => tplSave(data),
    });
  },
  async templateDelete() {
    if (!await confirmBox(t('confirmDeleteItem', { name: esc(S.template.name) }))) return;
    await api('/api/templates/' + S.template.id, { method: 'DELETE' });
    toast(t('deleted'));
    location.hash = '#/templates';
  },
  templateApply() { templateApplyDialog(S.template); },
  async templateApplyTo(b) {
    const rows = await loadTemplates();
    if (!rows.length) { toast(t('noTemplates'), 'warn'); location.hash = '#/templates'; return; }
    const ids = targetIds(b);
    modal({
      title: t('templateApply'), submit: t('nextStep'),
      body: `<div class="form"><label>${t('nodeTemplate')}<select name="template" required>${rows.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join('')}</select></label></div>`,
      onSubmit: data => { setTimeout(() => templateApplyDialog(rows.find(p => String(p.id) === data.template), ids), 50); },
    });
  },
  async templatePlaylist() {
    const p = S.template, lists = await api('/api/global-playlists');
    modal({
      title: t('playlist'),
      body: `<div class="form"><p class="muted">${t('tplPlaylistHint')}</p><label>${t('globalPlaylist')}<select name="playlist_id"><option value="">${t('tplKeep')}</option>${lists.map(x => `<option value="${x.id}" ${x.id === p.playlist_id ? 'selected' : ''}>${esc(x.name)} (${x.items})</option>`).join('')}</select></label>
        <div class="radios"><label class="check"><input type="radio" name="playlist_mode" value="replace" ${p.playlist_mode !== 'append' ? 'checked' : ''}>${t('mode_replace')}</label>
        <label class="check"><input type="radio" name="playlist_mode" value="append" ${p.playlist_mode === 'append' ? 'checked' : ''}>${t('mode_append')}</label></div>
        ${lists.length ? '' : `<div class="note">${t('noGlobalPlaylists')} <a href="#/global">${t('nav_global')}</a></div>`}</div>`,
      onSubmit: data => tplSave({ playlist_id: data.playlist_id ? Number(data.playlist_id) : null, playlist_mode: data.playlist_mode }),
    });
  },
  templateNotify() { templateNotifyDialog(S.template); },
  templateLook() { templateLookDialog(S.template); },
  templateOverlay() {
    const o = { enabled: true, size: 16, ...(S.template.overlay || {}) };
    modal({
      title: t('overlayBar'),
      body: `<div class="form"><p class="muted">${t('overlayHint')}</p><label class="check"><input type="checkbox" name="keep" ${S.template.overlay ? '' : 'checked'}>${t('tplKeep')}</label>
        <label class="check"><input type="checkbox" name="enabled" ${o.enabled ? 'checked' : ''}>${t('overlayEnabled')}</label>
        <label>${t('overlaySize')}<input name="size" type="number" min="4" max="200" value="${esc(o.size)}" required></label></div>`,
      onSubmit: data => tplSave({ overlay: data.keep ? null : { enabled: !!data.enabled, size: Number(data.size) } }),
    });
  },
  templatePlayer() {
    const v = (S.template.player || {}).web_dark || '';
    modal({
      title: t('webDark'),
      body: `<div class="form"><p class="muted">${t('webDarkHint')}</p><div class="mode-pick">
        <label class="check"><input type="radio" name="web_dark" value="" ${v ? '' : 'checked'}><span><b>${t('tplKeep')}</b></span></label>
        ${DARK_MODES.map(m => `<label class="check"><input type="radio" name="web_dark" value="${m}" ${m === v ? 'checked' : ''}><span><b>${t('dark_' + m)}</b><small>${t('darkHint_' + m)}</small></span></label>`).join('')}</div></div>`,
      onSubmit: data => tplSave({ player: data.web_dark ? { web_dark: data.web_dark } : null }),
    });
  },
  templateOrg() {
    const p = S.template;
    modal({
      title: t('groupAndLocation'),
      body: `<div class="form"><p class="muted">${t('tplOrgHint')}</p><div class="row2"><label>${t('group')}<input name="group" list="dlGroups" value="${esc(p.group || '')}"></label>
        <label>${t('location')}<input name="location" list="dlLocations" value="${esc(p.location || '')}"></label></div>${orgDatalists()}</div>`,
      onSubmit: data => tplSave({ group: data.group.trim(), location: data.location.trim() }),
    });
  },
  templateClear(b) { tplSave({ [b.dataset.part]: null }); },
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
      body: `<div class="form"><label>${t('name')}<input name="name" required></label><label>${t('description')}<textarea name="description" rows="2"></textarea></label>${tenantField()}</div>`,
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
  globalAddItem(b) {
    if (b.dataset.kind === 'camera') cameraDialog(null, null, null, S.global.id);
    else if (b.dataset.kind === 'widget') widgetDialog({ pid: S.global.id });
    else globalItemDialog(S.global.id, b.dataset.kind);
  },
  globalEditItem(b) {
    const it = S.global.items.find(x => String(x.id) === b.dataset.iid);
    if (!it) return;
    if (it.kind === 'camera') cameraDialog(null, it, null, S.global.id);
    else if (isWidget(it)) widgetDialog({ pid: S.global.id, item: it });
    else globalItemDialog(S.global.id, it.kind, it);
  },
  addCamera(b) { cameraDialog(b.dataset.id); },
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
    styleDialog(ids, one?.notifications?.settings || {}, one);
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
  webDarkSettings(b) { webDarkDialog([b.dataset.id], dev(b.dataset.id)?.player_settings); },
  bulkWebDark() { webDarkDialog([...S.selected], null); },
  reinstallNode(b) { reinstallDialog([b.dataset.id]); },
  bulkReinstall() { reinstallDialog([...S.selected]); },
  diagnostics(b) { diagnosticsDialog(b.dataset.id); },
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
    else if (a && a.kind === 'camera') cameraDialog(b.dataset.id, a);
    else if (a && isWidget(a)) widgetDialog({ deviceId: b.dataset.id, item: a });
    else if (a) assetDialog(b.dataset.id, ['image', 'video'].includes(a.kind) ? a.kind : 'web', a);
  },
  async deleteAsset(b) {
    if (await confirmBox(t('confirmDeleteItem', { name: esc(b.dataset.name) }))) await sendCommand(b.dataset.id, 'delete_asset', { id: b.dataset.item });
  },
  freeze(b) { freezeDialog(b.dataset.id, b.dataset.item, b.dataset.col, b.dataset.name); },
  screenToggle() { store.set('caracalScreenHidden', store.get('caracalScreenHidden') === '1' ? '0' : '1'); render(); },
  toggleCol(b) { const k = String(b.dataset.col); S.openCols.has(k) ? S.openCols.delete(k) : S.openCols.add(k); render(); },
  async screenshot(b) {
    const id = b.dataset.id;
    S.shotBusy.add(id); render();
    try {
      const url = await captureScreen(id);
      const old = S.shots[id];
      if (old && old.url) URL.revokeObjectURL(old.url);
      S.shots[id] = { at: Date.now() / 1000, url, fresh: true };
    } catch (err) { toast(errText(err), 'err'); }
    S.shotBusy.delete(id); render(); tick();
  },
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
  roleEdit(b) { roleDialog(b.dataset.key || null, b.dataset.copy || null); },
  async roleDelete(b) {
    if (!await confirmBox(t('confirmDeleteItem', { name: esc(b.dataset.name) }))) return;
    await api('/api/roles/' + encodeURIComponent(b.dataset.key), { method: 'DELETE' });
    toast(t('deleted'));
    render();
  },
  userGroupEdit(b) { userGroupDialog(b.dataset.gid ? Number(b.dataset.gid) : null); },
  async userGroupDelete(b) {
    if (!await confirmBox(t('confirmDeleteItem', { name: esc(b.dataset.name) }))) return;
    await api('/api/user-groups/' + b.dataset.gid, { method: 'DELETE' });
    toast(t('deleted'));
    render();
  },
};

// Offer only content types every selected node can receive.
function bulkKinds(ids = [...S.selected]) {
  const sel = ids.map(dev).filter(Boolean);
  const all = cap => sel.every(d => (d.capabilities || {})[cap] !== false);
  const logins = sel.length && sel.every(d => loginSupport(d) === 'ok');
  const cams = sel.length && sel.every(d => (d.capabilities || {}).add_camera === true);
  return ['web', ...(all('upload') ? ['image', 'video'] : []), ...(all('add_grafana_tag') ? ['collection'] : []), ...(cams ? ['camera'] : []), ...(logins ? ['profile'] : [])];
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

// After a command the page follows the device every second for a while, so its result shows up right away.
let burstTimer = null;
function burst(seconds = 12) {
  clearInterval(burstTimer);
  let n = 0;
  burstTimer = setInterval(() => { tick(); if (++n >= seconds) clearInterval(burstTimer); }, 1000);
}

async function tick() {
  if (!S.me || document.hidden) return;
  await refresh();
  const focus = document.activeElement;
  if (S.route.view === 'device' && S.route.tab === 'settings' && focus && focus.closest('#devForm')) return;
  render();
}

$('#loginForm').addEventListener('submit', doLogin);
$('#changeForm').addEventListener('submit', doChangePassword);
$('#chLogout').onclick = () => logout();
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
  d.innerHTML = `<div class="dd-head"><b>${esc(S.me.username)}</b><small>${esc(roleLabel(S.me.role, S.me.role_name))}</small></div><a href="#/settings">${t('nav_settings')}</a><button data-do="showShortcuts">${t('shortcuts')}</button><button id="logoutBtn">${t('signOut')}</button>`;
  d.hidden = !d.hidden;
  $('#logoutBtn').onclick = logout;
};
document.addEventListener('click', () => { $('#userDrop').hidden = true; });
window.addEventListener('hashchange', route);
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyStatic);

$('#setupForm').addEventListener('submit', doSetup);
applyStatic();
loadLang(S.lang).then(applyStatic);
// single sign-on comes back with the session (or an error) in the address
let ssoError = '';
if (location.hash.startsWith('#sso=')) {
  S.token = decodeURIComponent(location.hash.slice(5));
  store.set('caracalToken', S.token);
  history.replaceState(null, '', location.pathname);
} else if (location.hash.startsWith('#sso_error=')) {
  ssoError = decodeURIComponent(location.hash.slice(11));
  history.replaceState(null, '', location.pathname);
}
loadPublic().then(() => {
  if (S.token && !PUBLIC.setup_required) start(); else logout();
  if (ssoError) $('#loginError').textContent = errText(new Error(ssoError));
});
setInterval(tick, 5000);

// ---------- schedules: playlists by time, screen hours and actions at a given time

const DAYS = [0, 1, 2, 3, 4, 5, 6];
const daysText = days => (days.length === 7 ? t('everyDay') : days.join() === '0,1,2,3,4' ? t('workdays') : days.join() === '5,6' ? t('weekend') : days.map(x => t('day_' + x)).join(' '));
const windowText = w => `${daysText(w.days)} ${w.start}–${w.end}`;
const targetsText = tg => [...(tg.groups || []).map(g => `${icon('group')}${esc(g)}`), ...(tg.locations || []).map(l => `${icon('location')}${esc(l)}`),
  ...(tg.devices || []).map(id => `${icon('devices')}${esc(dev(id)?.name || id)}`)].map(x => `<span>${x}</span>`).join('');
const daysPick = (days = [0, 1, 2, 3, 4]) => `<div class="day-pick">${DAYS.map(x => `<label><input type="checkbox" data-day="${x}" ${days.includes(x) ? 'checked' : ''}><span>${t('day_' + x)}</span></label>`).join('')}</div>`;
const readDays = el => $$('[data-day]', el).filter(i => i.checked).map(i => Number(i.dataset.day));
const playlistOptions = (sel, empty = '') => (empty ? `<option value="">${empty}</option>` : '') + (S.globalLists || []).map(p => `<option value="${p.id}" ${String(p.id) === String(sel) ? 'selected' : ''}>${esc(p.name)}</option>`).join('');
const playlistName = id => (S.globalLists || []).find(p => p.id === id)?.name || (id ? '#' + id : '—');

function targetsField(tg = {}) {
  const pick = (kind, prefix, names, sel) => names.map(n => `<label class="check"><input type="checkbox" name="${prefix}${esc(n)}" ${(sel || []).includes(n) ? 'checked' : ''}>${icon(kind === 'groups' ? 'group' : 'location')}<span>${esc(n)}</span></label>`).join('');
  return `<div class="sched-targets"><label>${t('schedTargets')}</label><small class="muted">${t('schedTargetsHint')}</small>
    ${orgNames('groups').length || orgNames('locations').length ? `<div class="target-org">${pick('groups', 'g:', orgNames('groups'), tg.groups)}${pick('locations', 'l:', orgNames('locations'), tg.locations)}</div>` : ''}
    <details ${(tg.devices || []).length || !(orgNames('groups').length || orgNames('locations').length) ? 'open' : ''}><summary>${t('schedDevices')}</summary>${targetPicker(null, tg.devices || [])}</details></div>`;
}
const readTargets = data => ({
  devices: pickedTargets(data),
  groups: Object.keys(data).filter(k => k.startsWith('g:')).map(k => k.slice(2)),
  locations: Object.keys(data).filter(k => k.startsWith('l:')).map(k => k.slice(2)),
});
async function loadScheduleData() {
  const [d, lists] = await Promise.all([api('/api/schedules'), api('/api/global-playlists').catch(() => []), loadTemplates()]);
  S.globalLists = lists;
  S.sched = d;
  return d;
}

// A schedule as a week: one bar per day, coloured by the playlist on screen, hatched where the screens are off.
const TL_COLORS = ['#ff7a45', '#4c8bf5', '#34c759', '#af52de', '#f5b400', '#00b8d9', '#ff5e8a', '#8e8e93'];
const toMin = hhmm => { const [h, m] = hhmm.split(':').map(Number); return h * 60 + m; };
const fromMin = m => `${String(Math.floor(m / 60) % 24).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
function coversAt(w, day, min) {
  const s = toMin(w.start), e = toMin(w.end);
  if (s < e) return w.days.includes(day) && min >= s && min < e;
  return (w.days.includes(day) && min >= s) || (w.days.includes((day + 6) % 7) && min < e);
}
const playlistColor = id => TL_COLORS[Math.max(0, (S.globalLists || []).findIndex(p => p.id === id)) % TL_COLORS.length];
function scheduleAt(s, day, min) {
  const slot = s.slots.find(w => coversAt(w, day, min));
  return { pid: slot ? slot.playlist_id : s.default_playlist_id, on: !s.screen.length || s.screen.some(w => coversAt(w, day, min)) };
}
function weekTimeline(s, nowDay, nowMin) {
  const STEP = 15;
  const rows = DAYS.map(day => {
    const segs = [];
    for (let m = 0; m < 1440; m += STEP) {
      const v = scheduleAt(s, day, m), last = segs[segs.length - 1];
      if (last && last.pid === v.pid && last.on === v.on) last.to = m + STEP;
      else segs.push({ ...v, from: m, to: m + STEP });
    }
    return `<div class="tl-row"><span class="tl-day ${day === nowDay ? 'today' : ''}">${t('day_' + day)}</span><div class="tl-bar">
      ${segs.map(g => `<i class="${g.on ? '' : 'off'} ${g.pid ? '' : 'keep'}" style="width:${(g.to - g.from) / 14.4}%;${g.pid ? `--c:${playlistColor(g.pid)}` : ''}" title="${fromMin(g.from)}–${fromMin(g.to)} · ${esc(g.pid ? playlistName(g.pid) : t('schedKeep'))}${g.on ? '' : ' · ' + t('screenOff')}"></i>`).join('')}
      ${day === nowDay ? `<b class="tl-now" style="left:${nowMin / 14.4}%"></b>` : ''}</div></div>`;
  }).join('');
  const used = [...new Set([...s.slots.map(w => w.playlist_id), s.default_playlist_id].filter(Boolean))];
  return `<div class="timeline">${rows}<div class="tl-axis"><span></span><div>${[0, 6, 12, 18, 24].map(h => `<small style="left:${h / 24 * 100}%">${h}:00</small>`).join('')}</div></div>
    <div class="tl-legend">${used.map(id => `<span><i style="--c:${playlistColor(id)}"></i>${esc(playlistName(id))}</span>`).join('')}
    ${s.screen.length ? `<span><i class="off"></i>${t('screenOff')}</span>` : ''}${s.slots.length && !s.default_playlist_id ? `<span><i class="keep"></i>${t('schedKeep')}</span>` : ''}</div></div>`;
}

// What a scheduled action does, as a sentence: "Every day at 03:00 restarts the player on 2 devices."
function targetCount(tg) {
  const ids = new Set(tg.devices || []);
  S.devices.forEach(d => { if ((tg.groups || []).includes(d.group) || (tg.locations || []).includes(d.location)) ids.add(d.id); });
  return ids.size;
}
function taskSentence(x) {
  const when = x.date ? t('taskOn', { date: esc(x.date), time: esc(x.time) }) : t('taskEvery', { days: daysText(x.days), time: esc(x.time) });
  return `${when} ${t('taskDoes_' + x.action)}${taskDetail(x, true)} ${t('taskOnN', { n: targetCount(x.targets) })}`;
}

VIEWS.schedule = {
  mount(root) {
    setTitle(t('nav_schedule'));
    root.innerHTML = '<div id="schHead"></div><div id="schList"></div><div id="schTasks"></div>';
  },
  async update(root) {
    const d = await loadScheduleData();
    if (!$('#schHead', root)) return;
    const edit = can('schedule'), tzEdit = can('settings'), mine = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
    const nowMin = toMin(d.now.slice(11, 16));
    patch($('#schHead', root), `<div class="sched-kinds">
      <section class="card sched-kind"><div class="kind-ic">${icon('schedule')}</div><div><h2>${t('regimeTitle')}</h2><p class="muted">${t('regimeWhat')}</p>
        <ul class="kind-points"><li>${t('regimePoint1')}</li><li>${t('regimePoint2')}</li></ul>
        ${edit ? `<div class="form-actions start"><button class="btn primary" data-do="schedEdit">${icon('plus')}${t('schedNew')}</button><button class="btn" data-do="schedEdit" data-preset="office">${t('presetOffice')}</button></div>` : ''}</div></section>
      <section class="card sched-kind"><div class="kind-ic">${icon('play')}</div><div><h2>${t('tasksTitle')}</h2><p class="muted">${t('tasksWhat')}</p>
        <ul class="kind-points"><li>${t('tasksPoint1')}</li><li>${t('tasksPoint2')}</li></ul>
        ${edit ? `<div class="form-actions start"><button class="btn primary" data-do="taskEdit">${icon('plus')}${t('taskNew')}</button><button class="btn" data-do="taskEdit" data-preset="restart">${t('presetRestart')}</button></div>` : ''}</div></section></div>
      <p class="muted sched-clock">${icon('schedule')}${t('schedClock', { now: esc(d.now), tz: esc(d.timezone) })}${tzEdit ? ` · <button class="link" data-do="schedTz" data-tz="${esc(d.timezone)}">${t('schedTimezoneChange')}</button>` : ''}</p>
      ${tzEdit && mine && mine !== d.timezone ? `<div class="note warn"><div>${t('schedTzOther', { tz: esc(mine) })} <button class="btn sm" data-do="schedTz" data-tz="${esc(mine)}" data-direct="1">${t('schedTzUse', { tz: esc(mine) })}</button></div></div>` : ''}`);
    patch($('#schList', root), d.schedules.length ? `<h2 class="sched-section">${t('regimeTitle')}</h2>` + d.schedules.map(s => `<section class="card regime ${s.enabled ? '' : 'disabled'}">
      <div class="card-head"><div><h2>${esc(s.name)}${s.enabled ? '' : ` <span class="tag">${t('disabled')}</span>`}</h2><div class="chips">${targetsText(s.targets)}<span>${t('nDevices', { n: s.devices.length })}</span></div></div>
        ${edit ? `<div class="tpl-actions"><button class="btn sm" data-do="schedEdit" data-id="${s.id}">${icon('edit')}${t('edit')}</button><button class="icon-btn danger" title="${t('delete')}" data-do="schedDelete" data-id="${s.id}">${icon('trash')}</button></div>` : ''}</div>
      ${s.enabled ? `<p class="regime-now"><b>${t('schedNowCol')}:</b> ${s.now.playlist_id ? esc(playlistName(s.now.playlist_id)) : t('schedKeep')}${s.now.screen ? ` · <span class="tag ${s.now.screen === 'on' ? 'ok' : ''}">${t(s.now.screen === 'on' ? 'screenOn' : 'screenOff')}</span>` : ''}</p>` : ''}
      ${weekTimeline(s, d.weekday, nowMin)}</section>`).join('') : '');
    const result = r => (r ? `${t('nQueued', { n: r.queued.length })}${r.skipped.length ? ` · <span title="${esc(r.skipped.map(s => (dev(s.device_id)?.name || s.device_id) + ': ' + errText(new Error(s.reason))).join('\n'))}">${t('nSkipped', { n: r.skipped.length })}</span>` : ''}` : '');
    patch($('#schTasks', root), d.tasks.length ? `<section class="card flush"><div class="toolbar"><h2 class="grow">${t('tasksTitle')}</h2></div>
      <div class="table-wrap"><table class="table"><thead><tr><th>${t('name')}</th><th>${t('taskWhatHappens')}</th><th>${t('lastRun')}</th><th></th></tr></thead>
      <tbody>${d.tasks.map(x => `<tr><td><b>${esc(x.name)}</b>${x.enabled ? '' : ` <span class="tag">${t('disabled')}</span>`}<div class="chips">${targetsText(x.targets)}</div></td>
      <td>${taskSentence(x)}</td>
      <td>${x.last_run ? `${dt(x.last_run)}<br><small class="muted">${result(x.last_result)}</small>` : `<span class="muted">${t('never')}</span>`}</td>
      <td class="actions-cell">${edit ? `<button class="btn sm" data-do="taskRun" data-id="${x.id}">${icon('play')}${t('runNow')}</button><button class="btn sm" data-do="taskEdit" data-id="${x.id}">${icon('edit')}${t('edit')}</button><button class="icon-btn danger" title="${t('delete')}" data-do="taskDelete" data-id="${x.id}">${icon('trash')}</button>` : ''}</td></tr>`).join('')}</tbody></table></div></section>` : '');
  },
};
function taskDetail(x, inline = false) {
  const p = x.payload || {}, wrap = v => (inline ? ` <b>${v}</b>` : `<br><small class="muted">${v}</small>`);
  if (x.action === 'deploy_playlist') return wrap(`${esc(playlistName(p.playlist_id))}`);
  if (x.action === 'apply_template') return wrap(esc((S.templates || []).find(tp => tp.id === p.template_id)?.name || '#' + p.template_id));
  if (x.action === 'notify') return wrap(`„${esc(p.title || p.message || '')}“`);
  return '';
}

const windowRow = (w = {}, withPlaylist = false) => `<div class="sched-row" data-row>${daysPick(w.days)}
  <span class="sched-times"><input type="time" data-f="start" value="${esc(w.start || '08:00')}" required><span>–</span><input type="time" data-f="end" value="${esc(w.end || '17:00')}" required></span>
  ${withPlaylist ? `<select data-f="playlist" required>${playlistOptions(w.playlist_id, t('choosePlaylist'))}</select>` : ''}
  <button type="button" class="icon-btn danger" data-del-row title="${t('delete')}">${icon('x')}</button></div>`;
function readRows(box, withPlaylist) {
  return $$('[data-row]', box).map(r => {
    const w = { days: readDays(r), start: $('[data-f=start]', r).value, end: $('[data-f=end]', r).value };
    if (!w.days.length) throw new Error('invalid_days');
    if (withPlaylist) { w.playlist_id = Number($('[data-f=playlist]', r).value) || null; if (!w.playlist_id) throw new Error('playlist_not_found'); }
    return w;
  });
}

const SCHEDULE_PRESETS = {
  office: () => ({ name: t('presetOffice'), enabled: true, targets: {}, default_playlist_id: (S.globalLists || [])[0]?.id || null, slots: [],
    screen: [{ days: [0, 1, 2, 3, 4], start: '07:00', end: '18:00' }] }),
};
async function scheduleDialog(id = null, preset = '') {
  if (!S.sched) await loadScheduleData();
  const s = id ? S.sched.schedules.find(x => String(x.id) === String(id))
    : (SCHEDULE_PRESETS[preset] || (() => ({ name: '', enabled: true, targets: {}, slots: [], screen: [], default_playlist_id: null })))();
  if (!s) return;
  modal({
    title: id ? t('schedEdit') : t('schedNew'), wide: true, submit: id ? t('save') : t('add'),
    body: `<div class="form"><p class="muted">${t('regimeWhat')}</p>
      <div class="row2"><label>${t('name')}<input name="name" value="${esc(s.name)}" required placeholder="${esc(t('schedNamePlaceholder'))}"></label>
      <label class="check sched-enabled"><input type="checkbox" class="switch" name="enabled" ${s.enabled ? 'checked' : ''}>${t('enabled')}</label></div>
      ${targetsField(s.targets)}
      <h3>${icon('playlists')}${t('schedSlots')}</h3><p class="muted">${t('schedSlotsHint')}</p>
      <label>${t('schedDefault')}<select name="default_playlist_id">${playlistOptions(s.default_playlist_id, t('schedNoDefault'))}</select><small class="muted">${t('schedDefaultHint')}</small></label>
      <div id="slotRows">${s.slots.map(w => windowRow(w, true)).join('')}</div>
      <div class="form-actions start"><button type="button" class="btn sm" data-add-row="slot">${icon('plus')}${t('schedAddSlot')}</button></div>
      <h3>${icon('power')}${t('schedScreen')}</h3><p class="muted">${t('schedScreenHint')}</p>
      <div id="screenRows">${s.screen.map(w => windowRow(w)).join('')}</div>
      <div class="form-actions start"><button type="button" class="btn sm" data-add-row="screen">${icon('plus')}${t('schedAddScreen')}</button></div>
      <h3>${t('schedPreview')}</h3><div id="schedPreview"></div>
      ${(S.globalLists || []).length ? '' : `<div class="note">${t('noGlobalPlaylists')} <a href="#/global">${t('nav_global')}</a></div>`}</div>`,
    onOpen: form => {
      bindTargetPicker(form);
      // the week below the form follows every change
      const preview = () => {
        let rows;
        try { rows = { slots: readRows($('#slotRows', form), true), screen: readRows($('#screenRows', form), false) }; } catch { return; }
        $('#schedPreview', form).innerHTML = weekTimeline({ ...rows, default_playlist_id: Number(form.default_playlist_id.value) || null }, -1, 0);
      };
      form.addEventListener('click', e => {
        const add = e.target.closest('[data-add-row]'), del = e.target.closest('[data-del-row]');
        if (add) $(add.dataset.addRow === 'slot' ? '#slotRows' : '#screenRows', form).insertAdjacentHTML('beforeend', windowRow({}, add.dataset.addRow === 'slot'));
        if (del) del.closest('[data-row]').remove();
        if (add || del || e.target.closest('.day-pick')) setTimeout(preview);
      });
      form.addEventListener('change', preview);
      preview();
    },
    onSubmit: async (data, form) => {
      const body = { name: data.name.trim(), enabled: !!data.enabled, targets: readTargets(data), slots: readRows($('#slotRows', form), true),
        screen: readRows($('#screenRows', form), false), default_playlist_id: data.default_playlist_id ? Number(data.default_playlist_id) : null };
      await api(id ? `/api/schedules/${id}` : '/api/schedules', { method: id ? 'PATCH' : 'POST', json: body });
      toast(t('saved'));
      render();
    },
  });
}

const TASK_ACTIONS = ['restart_player', 'reboot', 'notify', 'deploy_playlist', 'apply_template', 'screen_off', 'screen_on', 'update_agent', 'update_caracal'];
function taskPayloadFields(action, p = {}) {
  if (action === 'deploy_playlist') return `<label>${t('globalPlaylist')}<select name="playlist_id" required>${playlistOptions(p.playlist_id, t('choosePlaylist'))}</select></label>
    <div class="radios"><label class="check"><input type="radio" name="mode" value="replace" ${p.mode !== 'append' ? 'checked' : ''}>${t('mode_replace')}</label><label class="check"><input type="radio" name="mode" value="append" ${p.mode === 'append' ? 'checked' : ''}>${t('mode_append')}</label></div>`;
  if (action === 'apply_template') return `<label>${t('nodeTemplate')}<select name="template_id" required>${(S.templates || []).map(x => `<option value="${x.id}" ${x.id === p.template_id ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}</select></label>`;
  if (action === 'notify') return `<label>${t('notifyTitle')}<input name="title" value="${esc(p.title || '')}" maxlength="200"></label><label>${t('notifyMessage')}<textarea name="message" rows="2" maxlength="2000">${esc(p.message || '')}</textarea></label>
    <label>${t('notifyLevel')}<select name="level">${NOTIFY_LEVELS.map(l => `<option value="${l}" ${l === (p.level || 'info') ? 'selected' : ''}>${t('notifyLevel_' + l)}</option>`).join('')}</select></label>`;
  if (action === 'update_caracal') return `<small class="muted">${t('taskUpdateHint')}</small>`;
  if (action.startsWith('screen_')) return `<small class="muted">${t('taskScreenHint')}</small>`;
  return '';
}

const TASK_PRESETS = { restart: () => ({ name: t('presetRestart'), enabled: true, targets: {}, days: DAYS, time: '03:00', date: '', action: 'restart_player', payload: {} }) };
async function taskDialog(id = null, preset = '') {
  if (!S.sched) await loadScheduleData();
  const x = id ? S.sched.tasks.find(v => String(v.id) === String(id))
    : (TASK_PRESETS[preset] || (() => ({ name: '', enabled: true, targets: {}, days: DAYS, time: '03:00', date: '', action: 'restart_player', payload: {} })))();
  if (!x) return;
  modal({
    title: id ? t('taskEdit') : t('taskNew'), wide: true, submit: id ? t('save') : t('add'),
    body: `<div class="form"><p class="muted">${t('tasksWhat')}</p>
      <h3>1 · ${t('taskStepWhat')}</h3>
      <label>${t('action')}<select name="action">${TASK_ACTIONS.map(a => `<option value="${a}" ${a === x.action ? 'selected' : ''}>${t('task_' + a)}</option>`).join('')}</select></label>
      <div id="taskPayload">${taskPayloadFields(x.action, x.payload)}</div>
      <h3>2 · ${t('taskStepWhen')}</h3>
      <div class="radios"><label class="check"><input type="radio" name="when" value="days" ${x.date ? '' : 'checked'}>${t('taskRepeat')}</label><label class="check"><input type="radio" name="when" value="date" ${x.date ? 'checked' : ''}>${t('taskOnce')}</label></div>
      <div class="row2"><div id="taskDays">${daysPick(x.days.length ? x.days : DAYS)}</div><label id="taskDate">${t('date')}<input type="date" name="date" value="${esc(x.date)}"></label>
      <label>${t('time')}<input type="time" name="time" value="${esc(x.time)}" required></label></div>
      <h3>3 · ${t('taskStepWho')}</h3>
      ${targetsField(x.targets)}
      <div class="row2"><label>${t('name')}<input name="name" value="${esc(x.name)}" required placeholder="${esc(t('taskNamePlaceholder'))}"></label>
      <label class="check sched-enabled"><input type="checkbox" class="switch" name="enabled" ${x.enabled ? 'checked' : ''}>${t('enabled')}</label></div>
      <div class="note info" id="taskSummary"></div></div>`,
    onOpen: form => {
      bindTargetPicker(form);
      const summary = () => {
        const data = Object.fromEntries(new FormData(form)), once = form.when.value === 'date';
        const draft = { action: form.action.value, time: form.time.value || '--:--', date: once ? (form.date.value || '…') : '', days: once ? [] : readDays($('#taskDays', form)),
          targets: readTargets(data), payload: { title: data.title, playlist_id: Number(data.playlist_id), template_id: Number(data.template_id) } };
        $('#taskSummary', form).innerHTML = taskSentence(draft);
      };
      const sync = () => { const once = form.when.value === 'date'; $('#taskDays', form).hidden = once; $('#taskDate', form).hidden = !once; form.date.required = once; summary(); };
      $$('input[name=when]', form).forEach(i => { i.onchange = sync; });
      form.action.onchange = () => {
        $('#taskPayload', form).innerHTML = taskPayloadFields(form.action.value);
        if (!form.name.dataset.touched) form.name.value = t('task_' + form.action.value);
        summary();
      };
      form.name.oninput = () => { form.name.dataset.touched = '1'; };
      if (x.name) form.name.dataset.touched = '1';
      else form.name.value = t('task_' + x.action);
      form.addEventListener('change', summary);
      form.addEventListener('input', summary);
      form.addEventListener('click', e => { if (e.target.closest('.day-pick, [data-tsel]')) setTimeout(summary); });
      sync();
    },
    onSubmit: async (data, form) => {
      const action = data.action, payload = {};
      if (action === 'deploy_playlist') Object.assign(payload, { playlist_id: Number(data.playlist_id) || null, mode: data.mode });
      if (action === 'apply_template') payload.template_id = Number(data.template_id) || null;
      if (action === 'notify') Object.assign(payload, { title: data.title.trim(), message: data.message.trim(), level: data.level });
      const once = data.when === 'date';
      const body = { name: data.name.trim(), enabled: !!data.enabled, action, payload, time: data.time, targets: readTargets(data),
        date: once ? data.date : '', days: once ? [] : readDays($('#taskDays', form)) };
      await api(id ? `/api/scheduled-tasks/${id}` : '/api/scheduled-tasks', { method: id ? 'PATCH' : 'POST', json: body });
      toast(t('saved'));
      render();
    },
  });
}

Object.assign(ACTIONS_UI, {
  schedEdit(b) { scheduleDialog(b.dataset.id || null, b.dataset.preset || ''); },
  async totpSetup() {
    const s = await api('/api/me/totp/setup', { method: 'POST' });
    modal({
      title: t('totpEnable'), submit: t('totpConfirm'),
      body: `<div class="form"><p class="muted">${t('totpScan')}</p><div class="totp-qr">${s.qr}</div>
        <details><summary>${t('totpManual')}</summary><code class="secret">${esc(s.secret.match(/.{1,4}/g).join(' '))}</code></details>
        <label>${t('totpCode')}<input name="code" inputmode="numeric" autocomplete="one-time-code" required></label></div>`,
      onSubmit: async data => {
        const r = await api('/api/me/totp/enable', { method: 'POST', json: { code: data.code } });
        S.me.totp = true;
        modal({ title: t('totpRecovery'), body: `<div class="form"><p class="muted">${t('totpRecoveryHint')}</p><pre class="code" id="recCodes">${r.recovery_codes.join('\n')}</pre>
          <div class="form-actions start"><button type="button" class="btn sm" data-copy="recCodes">${icon('copy')}${t('copy')}</button></div></div>`,
          onOpen: form => { $('[data-copy]', form).onclick = () => navigator.clipboard.writeText($('#recCodes', form).textContent).then(() => toast(t('copied'))); } });
        render(true);
        return false;
      },
    });
  },
  totpDisable() {
    modal({
      title: t('totpDisable'), danger: true, submit: t('totpDisable'),
      body: `<div class="form"><p class="muted">${t('totpDisableHint')}</p><label>${t('currentPassword')}<input type="password" name="password" autocomplete="current-password" required></label></div>`,
      onSubmit: async data => { await api('/api/me/totp/disable', { method: 'POST', json: { password: data.password } }); S.me.totp = false; toast(t('saved')); render(true); },
    });
  },
  async totpReset(b) {
    if (!await confirmBox(t('confirmTotpReset', { name: esc(b.dataset.name) }))) return;
    await api(`/api/users/${b.dataset.uid}/totp/reset`, { method: 'POST' });
    toast(t('saved'));
    render();
  },
  rolloutNew() { rolloutDialog(); },
  rolloutAuto() { rolloutAutoDialog(); },
  async rolloutCtl(b) {
    if (b.dataset.act === 'cancel' && !await confirmBox(t('confirmRolloutCancel'))) return;
    await api(`/api/rollouts/${b.dataset.id}/${b.dataset.act}`, { method: 'POST' });
    render();
  },
  screenSet(b) {
    const ids = b.dataset.id ? [b.dataset.id] : [...S.selected], payload = { state: b.dataset.state };
    return ids.length === 1 ? sendCommand(ids[0], 'screen', payload) : sendBulk(ids, 'screen', payload);
  },
  async schedDelete(b) {
    const s = S.sched.schedules.find(x => String(x.id) === b.dataset.id);
    if (!s || !await confirmBox(t('confirmDeleteSchedule', { name: esc(s.name) }))) return;
    await api('/api/schedules/' + s.id, { method: 'DELETE' });
    render();
  },
  taskEdit(b) { taskDialog(b.dataset.id || null, b.dataset.preset || ''); },
  async taskDelete(b) {
    const x = S.sched.tasks.find(v => String(v.id) === b.dataset.id);
    if (!x || !await confirmBox(t('confirmDeleteTask', { name: esc(x.name) }))) return;
    await api('/api/scheduled-tasks/' + x.id, { method: 'DELETE' });
    render();
  },
  async taskRun(b) {
    const x = S.sched.tasks.find(v => String(v.id) === b.dataset.id);
    if (!x || !await confirmBox(t('confirmRunTask', { name: esc(x.name), action: t('task_' + x.action) }), x.action === 'reboot')) return;
    const r = await api(`/api/scheduled-tasks/${x.id}/run`, { method: 'POST' });
    reportQueued({ command_ids: r.queued, skipped: r.skipped }, t('task_' + x.action));
    render();
  },
  schedTz(b) {
    const save = async tz => { await api('/api/schedules/settings', { method: 'PUT', json: { timezone: tz } }); toast(t('saved')); render(); };
    if (b.dataset.direct) return save(b.dataset.tz);
    modal({
      title: t('schedTimezone'), body: `<div class="form"><label>${t('timezone')}<input name="timezone" value="${esc(b.dataset.tz)}" required placeholder="Europe/Prague" spellcheck="false"></label><small class="muted">${t('schedTimezoneHint')}</small></div>`,
      onSubmit: data => save(data.timezone.trim()),
    });
  },
});

// ---------- screen wall: what every screen shows, refreshed while the page is open

let wallTimer = null, wallRound = 0;
const WALL_EVERY = [30, 60, 120, 0];
function wallDevices() {
  const g = store.get('caracalWallGroup', ''), l = store.get('caracalWallLocation', '');
  return S.devices.filter(d => (screenSupport(d) || d.screenshot_at) && (!g || d.group === g) && (!l || d.location === l));
}
function wallCapture() {
  if (S.route.view !== 'wall' || document.visibilityState !== 'visible') return;
  wallRound = Date.now();
  // fire and forget: the picture is loaded when the device list reports its new time
  wallDevices().filter(d => d.online && screenSupport(d)).slice(0, 60).forEach(d => {
    api(`/api/devices/${encodeURIComponent(d.id)}/commands`, { method: 'POST', json: { action: 'screenshot', payload: {} } }).catch(() => {});
  });
}
function wallSchedule() {
  clearInterval(wallTimer);
  const every = Number(store.get('caracalWallEvery', '60'));
  if (every) wallTimer = setInterval(() => (S.route.view === 'wall' ? wallCapture() : clearInterval(wallTimer)), every * 1000);
}
VIEWS.wall = {
  mount(root) {
    setTitle(t('nav_wall'));
    const every = store.get('caracalWallEvery', '60');
    root.innerHTML = `<section class="card wall-head"><div class="toolbar"><h2 class="grow">${t('nav_wall')}</h2>
      <select id="wGroup">${options(orgNames('groups'), store.get('caracalWallGroup', ''), t('allGroups'))}</select>
      <select id="wLoc">${options(orgNames('locations'), store.get('caracalWallLocation', ''), t('allLocations'))}</select>
      <select id="wEvery">${WALL_EVERY.map(s => `<option value="${s}" ${String(s) === every ? 'selected' : ''}>${s ? t('wallEvery', { s }) : t('wallManual')}</option>`).join('')}</select>
      <button class="btn" id="wNow">${icon('refresh')}${t('refresh')}</button></div><p class="muted card-intro">${t('wallHint')}</p></section><div class="screen-wall" id="wall"></div>`;
    const upd = () => this.update(root);
    $('#wGroup', root).onchange = e => { store.set('caracalWallGroup', e.target.value || null); upd(); };
    $('#wLoc', root).onchange = e => { store.set('caracalWallLocation', e.target.value || null); upd(); };
    $('#wEvery', root).onchange = e => { store.set('caracalWallEvery', e.target.value); wallSchedule(); };
    $('#wNow', root).onclick = wallCapture;
    wallSchedule();
    wallCapture();
  },
  update(root) {
    const box = $('#wall', root);
    if (!box) return;
    const list = wallDevices();
    for (const d of list) {
      const shot = S.shots[d.id];
      if (d.screenshot_at && (!shot || (shot.at !== d.screenshot_at && !shot.loading))) setTimeout(() => loadShot(d.id, d.screenshot_at));
    }
    patch(box, list.map(d => {
      const shot = S.shots[d.id];
      const state = !d.online ? t('offline') : d.screen === 'off' ? t('screenOff') : '';
      return `<a class="wall-tile ${d.online ? '' : 'off'}" href="#/device/${encodeURIComponent(d.id)}">
        <div class="wall-shot">${shot && shot.url ? `<img src="${shot.url}" alt="">` : `<span>${screenSupport(d) ? t('capturing') : t('needNewerCaracal')}</span>`}${state ? `<em>${state}</em>` : ''}</div>
        <div class="wall-meta">${dot(d)}<b>${esc(d.name)}</b><small class="muted">${esc(d.current_name || '')}${d.screenshot_at ? ` · ${ago(d.screenshot_at)}` : ''}</small></div></a>`;
    }).join('') || `<div class="card">${emptyState('wall', t('wallEmpty'), t('wallHint'))}</div>`);
  },
};

// ================================================================== emergency messages, approvals, library, proof
// of play, what plays when, widgets, device labels and tables

// ---------- emergency message: a bar on every page while one runs

function renderTakeoverBar() {
  const el = $('#takeoverBar');
  if (!el) return;
  patch(el, (S.takeovers || []).map(x => `<div class="takeover-bar lvl-${esc(x.level)}">${icon('takeover')}<b>${esc(x.title)}</b>
    <span>${t('takeoverOnN', { n: x.devices })}${x.until ? ' · ' + t('takeoverUntil', { time: clock(x.until) }) : ' · ' + t('takeoverUntilEnded')}</span><span class="grow"></span>
    <a class="btn sm" href="#/takeover">${t('open')}</a>${can('control') ? `<button class="btn sm" data-do="takeoverEnd" data-tid="${x.id}">${icon('x')}${t('takeoverEnd')}</button>` : ''}</div>`).join(''));
}

const TAKEOVER_MINUTES = [15, 30, 60, 120, 240, 480, 0];
VIEWS.takeover = {
  mount(root) {
    setTitle(t('nav_takeover'));
    const locs = orgNames('locations'), groups = orgNames('groups');
    root.innerHTML = `<div class="grid two takeover-grid">
      <section class="card"><h2>${t('takeoverNew')}</h2><p class="muted">${t('takeoverHint')}</p>
      <form id="toForm" class="form">
        <label>${t('takeoverTitle')}<input name="title" maxlength="200" placeholder="${esc(t('takeoverTitlePlaceholder'))}" required></label>
        <label>${t('takeoverMessage')}<textarea name="message" rows="4" maxlength="2000" placeholder="${esc(t('takeoverMessagePlaceholder'))}"></textarea></label>
        <div class="row2"><label>${t('takeoverLevel')}<select name="level">${['critical', 'warning', 'info'].map(l => `<option value="${l}">${t('takeoverLevel_' + l)}</option>`).join('')}</select></label>
        <label>${t('takeoverDuration')}<select name="minutes">${TAKEOVER_MINUTES.map(m => `<option value="${m}" ${m === 60 ? 'selected' : ''}>${m ? (m < 60 ? t('minutesN', { n: m }) : t('hoursN', { n: m / 60 })) : t('takeoverUntilEnded')}</option>`).join('')}</select></label></div>
        <div class="radios"><label class="check"><input type="radio" name="scope" value="all" checked>${t('takeoverAll')}</label><label class="check"><input type="radio" name="scope" value="pick">${t('takeoverPick')}</label></div>
        <div id="toPick" hidden>
          ${locs.length ? `<h3>${t('locations')}</h3><div class="chip-picks">${locs.map(x => `<label class="check chip-pick"><input type="checkbox" name="loc:${esc(x)}">${icon('location')}${esc(x)}</label>`).join('')}</div>` : ''}
          ${groups.length ? `<h3>${t('groups')}</h3><div class="chip-picks">${groups.map(x => `<label class="check chip-pick"><input type="checkbox" name="grp:${esc(x)}">${icon('group')}${esc(x)}</label>`).join('')}</div>` : ''}
          <h3>${t('nav_devices')}</h3>${targetPicker(null, [], d => canOn(d, 'control'))}</div>
        <div class="form-actions start"><button class="btn danger big">${icon('takeover')}${t('takeoverStart')}</button></div>
      </form></section>
      <section class="card"><h2>${t('preview')}</h2><div class="takeover-preview lvl-critical" id="toPrev"><small></small><b></b><p></p><footer><span></span><span></span></footer></div>
      <p class="muted">${t('takeoverOldNodes')}</p></section></div>
      <section class="card flush"><div class="toolbar"><h2 class="grow">${t('takeoverHistory')}</h2></div><div id="toList"><div class="empty">…</div></div></section>`;
    const form = $('#toForm', root);
    bindTargetPicker(form);
    const prev = () => {
      const box = $('#toPrev', root), lvl = form.level.value;
      box.className = 'takeover-preview lvl-' + lvl;
      $('small', box).textContent = t('takeoverLevel_' + lvl).toUpperCase();
      $('b', box).textContent = form.title.value || t('takeoverTitlePlaceholder');
      $('p', box).textContent = form.message.value;
      const now = new Date();
      $$('footer span', box)[0].textContent = now.toLocaleDateString(S.lang === 'cs' ? 'cs-CZ' : 'en-GB', { weekday: 'long', day: 'numeric', month: 'long' });
      $$('footer span', box)[1].textContent = now.toLocaleTimeString(S.lang === 'cs' ? 'cs-CZ' : 'en-GB', { hour: '2-digit', minute: '2-digit' });
    };
    form.addEventListener('input', prev);
    form.addEventListener('change', () => { $('#toPick', root).hidden = form.scope.value !== 'pick'; prev(); });
    prev();
    form.onsubmit = async e => {
      e.preventDefault();
      const data = Object.fromEntries(new FormData(form));
      const targets = data.scope === 'all' ? { all: true } : {
        locations: Object.keys(data).filter(k => k.startsWith('loc:')).map(k => k.slice(4)),
        groups: Object.keys(data).filter(k => k.startsWith('grp:')).map(k => k.slice(4)), devices: pickedTargets(data),
      };
      const n = data.scope === 'all' ? S.devices.filter(d => canOn(d, 'control')).length : null;
      if (!await confirmBox(t('takeoverConfirm', { n: n ?? '…' }), true, t('takeoverStart'))) return;
      try {
        const r = await api('/api/takeovers', { method: 'POST', json: { title: data.title, message: data.message, level: data.level, minutes: Number(data.minutes), targets } });
        const old = r.devices.filter(x => x.how !== 'takeover').length;
        toast(t('takeoverStarted', { n: r.devices.length }));
        if (old) toast(t('takeoverFallback', { n: old }), 'warn');
        form.reset();
        prev();
        await tick();
        this.load(root);
      } catch (err) { toast(errText(err), 'err'); }
    };
    this.load(root);
  },
  async load(root) {
    const list = await api('/api/takeovers').catch(() => []);
    const el = $('#toList', root);
    if (!el) return;
    patch(el, list.length ? `<div class="table-wrap"><table class="table"><thead><tr><th>${t('takeoverTitle')}</th><th>${t('time')}</th><th>${t('nav_devices')}</th><th>${t('state')}</th><th></th></tr></thead><tbody>
      ${list.map(x => `<tr><td><span class="lvl-dot lvl-${esc(x.level)}"></span><b>${esc(x.title || '—')}</b><br><small class="muted">${esc((x.message || '').slice(0, 140))}</small></td>
        <td class="nowrap">${dt(x.created)}<br><small class="muted">${esc(x.username)}</small></td>
        <td>${t('nDevices', { n: x.devices.length })}${x.devices.some(d => d.how === 'notify') ? `<br><small class="muted">${t('takeoverAsNotify', { n: x.devices.filter(d => d.how === 'notify').length })}</small>` : ''}</td>
        <td>${x.active ? `<span class="tag bad">${t('takeoverActive')}</span>${x.until ? `<br><small class="muted">${t('takeoverUntil', { time: clock(x.until) })}</small>` : ''}` : `<span class="tag">${t('takeoverEnded')}</span>${x.ended_by ? `<br><small class="muted">${esc(x.ended_by)}</small>` : ''}`}</td>
        <td>${x.active && can('control') ? `<button class="btn sm" data-do="takeoverEnd" data-tid="${x.id}">${t('takeoverEnd')}</button>` : ''}</td></tr>`).join('')}</tbody></table></div>`
      : emptyState('takeover', t('takeoverNone'), t('takeoverNoneHint')));
  },
};

// ---------- approvals

VIEWS.approvals = {
  mount(root) {
    setTitle(t('nav_approvals'));
    root.innerHTML = `<div id="apSet"></div><div id="apPending"><div class="card empty">…</div></div><div id="apDone"></div>`;
    this.load(root);
  },
  async load(root) {
    const [pending, done] = await Promise.all([api('/api/approvals'), api('/api/approvals?state=done')]).catch(() => [null, null]);
    if (!pending || !$('#apPending', root)) return;
    S.approvalEnabled = pending.enabled;
    patch($('#apSet', root), canAll('settings') ? `<section class="card"><div class="card-head"><h2>${t('approvalSetting')}</h2>
      <label class="switch-row"><input type="checkbox" id="apToggle" ${pending.enabled ? 'checked' : ''}><span>${pending.enabled ? t('on') : t('off')}</span></label></div>
      <p class="muted">${t('approvalSettingHint')}</p></section>` : pending.enabled ? '' : `<div class="note">${t('approvalOff')}</div>`);
    const tg = $('#apToggle', root);
    if (tg) tg.onchange = async () => {
      try { await api('/api/approvals/settings', { method: 'PUT', json: { enabled: tg.checked } }); toast(t('saved')); await tick(); this.load(root); } catch (e) { toast(errText(e), 'err'); }
    };
    patch($('#apPending', root), `<section class="card flush"><div class="toolbar"><h2 class="grow">${t('approvalPending')} <span class="muted">(${pending.approvals.length})</span></h2></div>
      ${pending.approvals.map(a => `<div class="approval">
        <div class="approval-head"><div><b><a href="#/global/${a.playlist_id}">${esc(a.playlist)}</a></b> <span class="tag">${t('mode_' + a.mode)}</span>
          <br><small class="muted">${t('approvalBy', { user: esc(a.username), time: dt(a.created) })}</small></div>
          <div class="approval-actions">${a.can_decide ? `<button class="btn primary sm" data-do="approvalApprove" data-aid="${a.id}">${icon('check')}${t('approve')}</button>
            <button class="btn sm" data-do="approvalReject" data-aid="${a.id}">${icon('x')}${t('reject')}</button>` : ''}
            ${a.mine ? `<button class="btn sm ghost" data-do="approvalCancel" data-aid="${a.id}">${t('cancelRequest')}</button>` : ''}</div></div>
        ${a.note ? `<p class="approval-note">${esc(a.note)}</p>` : ''}
        <div class="approval-body"><div><h3>${t('approvalItems', { n: a.items.length })}</h3><ol class="mini-list">${a.items.map(it => `<li>${isWidget(it) ? icon('widget') : kindIcon(it.kind)}<span>${esc(it.name)}</span><small class="muted">${it.duration ? dur(it.duration) : ''}</small></li>`).join('')}</ol></div>
        <div><h3>${t('approvalTargets', { n: a.targets.length })}</h3><div class="chips">${a.targets.map(d => `<span>${esc(d.name)}</span>`).join('')}</div></div></div></div>`).join('')
        || `<div class="empty">${emptyState('approvals', t('approvalNone'), pending.enabled ? t('approvalNoneHint') : t('approvalOff'))}</div>`}</section>`);
    patch($('#apDone', root), done && done.approvals.length ? `<section class="card flush"><div class="toolbar"><h2 class="grow">${t('approvalDecided')}</h2></div>
      <div class="table-wrap"><table class="table"><thead><tr><th>${t('nav_global')}</th><th>${t('approvalRequestedBy')}</th><th>${t('result')}</th><th>${t('approvalComment')}</th></tr></thead><tbody>
      ${done.approvals.map(a => `<tr><td><b>${esc(a.playlist)}</b><br><small class="muted">${t('nDevices', { n: a.targets.length })} · ${t('mode_' + a.mode)}</small></td>
        <td>${esc(a.username)}<br><small class="muted">${dt(a.created)}</small></td>
        <td><span class="tag ${a.state === 'approved' ? 'ok' : a.state === 'rejected' ? 'bad' : ''}">${t('approval_' + a.state)}</span><br><small class="muted">${esc(a.decided_by)} · ${dt(a.decided)}</small></td>
        <td>${esc(a.comment || '')}</td></tr>`).join('')}</tbody></table></div></section>` : '');
  },
};

// ---------- media library

VIEWS.library = {
  mount(root) {
    setTitle(t('nav_library'));
    root.innerHTML = `<section class="card flush"><div class="toolbar"><h2 class="grow">${t('nav_library')}</h2>
      <input id="libQ" type="search" placeholder="${esc(t('search'))}"><select id="libKind"><option value="">${t('libraryAll')}</option><option value="image">${t('kind_image')}</option><option value="video">${t('kind_video')}</option></select>
      ${can('content') ? `<button class="btn" data-do="addDocument" data-target="library">${icon('audit')}${t('document')}</button><label class="btn primary">${icon('upload')}${t('libraryUpload')}<input type="file" id="libUp" accept="image/*,video/*" multiple hidden></label>` : ''}</div>
      <p class="muted card-intro">${t('libraryHint')}</p><div class="upload-prog" id="libProg" hidden><i></i></div><div class="lib-grid" id="libGrid"><div class="empty">…</div></div></section>`;
    if (can('content')) {
      dropFiles($('#libGrid', root).parentElement, files => uploadMany(files, (f, up) => api('/api/library/' + up.id, { method: 'POST', json: {} }))
        .then(() => { toast(t('saved')); this.load(root); }));
    }
    $('#libQ', root).oninput = () => this.draw(root);
    $('#libKind', root).onchange = () => this.draw(root);
    const up = $('#libUp', root);
    if (up) up.onchange = async () => {
      const prog = $('#libProg', root);
      prog.hidden = false;
      try {
        for (const [i, file] of [...up.files].entries()) {
          const r = await uploadFile(file, x => { $('i', prog).style.width = ((i + x) / up.files.length * 100) + '%'; });
          await api('/api/library/' + r.id, { method: 'POST', json: {} });
        }
        toast(t('saved'));
      } catch (e) { toast(errText(e), 'err'); }
      prog.hidden = true;
      up.value = '';
      this.load(root);
    };
    this.load(root);
  },
  async load(root) {
    S.library = await api('/api/library').catch(() => []);
    this.draw(root);
  },
  draw(root) {
    const grid = $('#libGrid', root);
    if (!grid) return;
    const q = ($('#libQ', root).value || '').toLowerCase(), kind = $('#libKind', root).value;
    const list = (S.library || []).filter(x => (!kind || x.kind === kind) && (!q || x.name.toLowerCase().includes(q)));
    patch(grid, list.map(x => `<div class="lib-card">
      <div class="lib-thumb" data-thumb="${esc(x.id)}" data-kind="${x.kind}">${x.kind === 'video' ? icon('video') : icon('image')}</div>
      <div class="lib-meta"><b title="${esc(x.name)}">${esc(x.name)}</b><small class="muted">${t('kind_' + x.kind)} · ${fmtSize(x.size)} · ${dt(x.created)}</small>
        <div class="lib-usage">${x.usage.length ? x.usage.map(u => u.type === 'global' ? `<a class="tag info" href="#/global/${u.id}">${icon('global')}${esc(u.name)}</a>` : `<a class="tag" href="#/templates/${u.id}">${icon('templates')}${esc(u.name)}</a>`).join('') : `<span class="tag">${t('libraryUnused')}</span>`}</div></div>
      ${can('content') ? `<div class="lib-actions"><button class="icon-btn" title="${t('libraryUse')}" data-do="libUse" data-fid="${esc(x.id)}">${icon('plus')}</button>
        <button class="icon-btn" title="${t('rename')}" data-do="libRename" data-fid="${esc(x.id)}">${icon('edit')}</button>
        <button class="icon-btn" title="${t('libraryReplace')}" data-do="libReplace" data-fid="${esc(x.id)}">${icon('refresh')}</button>
        <button class="icon-btn danger" title="${x.usage.length ? t('libraryInUse') : t('delete')}" data-do="libDelete" data-fid="${esc(x.id)}" ${x.usage.length ? 'disabled' : ''}>${icon('trash')}</button></div>` : ''}</div>`).join('')
      || emptyState('library', t('libraryEmpty'), t('libraryHint')));
    $$('[data-thumb]', grid).forEach(el => libThumb(el));
  },
};
S.thumbs = {};
async function libThumb(el) {
  const id = el.dataset.thumb;
  if (!S.thumbs[id]) {
    S.thumbs[id] = fetch('/api/library/' + id + '/file', { headers: { Authorization: 'Bearer ' + S.token } })
      .then(r => (r.ok ? r.blob() : null)).then(b => (b ? URL.createObjectURL(b) : null)).catch(() => null);
  }
  const url = await S.thumbs[id];
  if (!url || !el.isConnected) return;
  el.innerHTML = el.dataset.kind === 'video' ? `<video src="${url}" muted preload="metadata"></video>` : `<img src="${url}" alt="">`;
}

function libReplaceDialog(file) {
  modal({
    title: t('libraryReplaceTitle', { name: esc(file.name) }), submit: t('libraryReplace'),
    body: `<div class="form"><p class="muted">${t('libraryReplaceHint')}</p>
      <label>${t('file')}<input type="file" name="file" accept="${file.kind}/*" required></label><div class="upload-prog" hidden><i></i></div>
      ${file.usage.length ? `<p>${t('libraryUsedIn')}</p><div class="chips">${file.usage.map(u => `<span>${esc(u.name)}</span>`).join('')}</div>` : ''}</div>`,
    onSubmit: async (data, form) => {
      const pr = $('.upload-prog', form);
      pr.hidden = false;
      const up = await uploadFile(form.file.files[0], x => { $('i', pr).style.width = (x * 100) + '%'; });
      const r = await api(`/api/library/${file.id}/replace`, { method: 'POST', json: { file_id: up.id } });
      toast(t('libraryReplaced', { n: r.playlists.length }));
      const again = r.playlists.filter(x => x.last_deployment);
      if (again.length) setTimeout(() => redeployDialog(again), 50);
      render();
    },
  });
}

function redeployDialog(list) {
  modal({
    title: t('redeployTitle'), submit: t('deploy'),
    body: `<div class="form"><p class="muted">${t('redeployHint')}</p>${list.map(x => `<label class="check"><input type="checkbox" name="p:${x.id}" checked><span><b>${esc(x.name)}</b><br>
      <small class="muted">${t('nDevices', { n: x.last_deployment.targets.length })} · ${t('mode_' + x.last_deployment.mode)} · ${dt(x.last_deployment.created)}</small></span></label>`).join('')}</div>`,
    onSubmit: async data => {
      let pending = 0, done = 0;
      for (const x of list) {
        if (!data['p:' + x.id]) continue;
        const r = await api(`/api/global-playlists/${x.id}/deploy`, { method: 'POST', json: { target_ids: x.last_deployment.targets, mode: x.last_deployment.mode } });
        r.pending_approval ? pending++ : done++;
      }
      if (done) toast(t('redeployDone', { n: done }));
      if (pending) toast(t('approvalRequested'));
    },
  });
}

async function libUseDialog(file) {
  const lists = await api('/api/global-playlists');
  modal({
    title: t('libraryUse'), submit: t('add'),
    body: `<div class="form"><label>${t('nav_global')}<select name="pid">${lists.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join('')}</select></label>
      <label>${t('name')}<input name="name" value="${esc(file.name.replace(/\.[^.]+$/, ''))}"></label>
      <label>${t('durationSec')}<input name="duration" type="number" min="5" value="${file.kind === 'video' ? 30 : 15}"></label></div>`,
    onSubmit: async data => {
      if (!data.pid) throw new Error('not_found');
      await api(`/api/global-playlists/${data.pid}/items`, { method: 'POST', json: { kind: file.kind, file_id: file.id, name: data.name, duration: Number(data.duration || 15) } });
      toast(t('saved'));
      render();
    },
  });
}

// ---------- versions of a global playlist and what a deployment changes

async function globalVersions(el, p) {
  if (!el) return;
  const list = await api(`/api/global-playlists/${p.id}/versions`).catch(() => []);
  if (!el.isConnected) return;
  const note = v => (v.note.startsWith('restore:') ? t('version_restore') : t('version_' + (v.note || 'edit')));
  patch(el, list.length ? `<section class="card flush"><details class="pver"><summary class="toolbar"><h2 class="grow">${t('versions')} <span class="muted">(${list.length})</span></h2></summary>
    <ol class="pver-list">${list.map(v => `<li class="${v.current ? 'current' : ''}"><div><b>${dt(v.created)}</b> <small class="muted">${v.username ? esc(v.username) + ' · ' : ''}${esc(note(v))}</small>
      <br><small class="muted">${t('nItems', { n: v.count })}: ${esc(v.names.join(', '))}${v.count > v.names.length ? '…' : ''}</small></div>
      <div class="pver-actions">${v.current ? `<span class="tag ok">${t('versionCurrent')}</span>` : `<button class="btn sm ghost" data-do="versionShow" data-vid="${v.id}">${icon('eye')}${t('show')}</button>
      ${can('content') ? `<button class="btn sm" data-do="versionRestore" data-vid="${v.id}">${icon('restart')}${t('restore')}</button>` : ''}`}</div></li>`).join('')}</ol></details></section>` : '');
}

function diffHtml(x) {
  const list = (cls, names) => names.map(n => `<li class="${cls}">${esc(n)}</li>`).join('');
  return `<div class="diff-dev"><b>${esc(x.name)}</b>${x.online ? '' : ` <small class="muted">${t('offlineQueuedShort')}</small>`}
    <ul class="diff">${list('add', x.added)}${list('same', x.same)}${list('del', x.removed)}${list('keep', x.kept)}${list('skip', x.skipped)}</ul>
    ${x.duplicates.length ? `<small class="muted">${t('diffDuplicates', { list: esc(x.duplicates.join(', ')) })}</small>` : ''}
    ${!x.added.length && !x.removed.length && !x.same.length ? `<small class="muted">${t('diffNothing')}</small>` : ''}</div>`;
}

// ---------- proof of play

function playsQuery(f) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) if (v) q.set(k, v);
  return q.toString();
}
const isoDay = d => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
function playsRange(preset) {
  const today = new Date(), from = new Date();
  if (preset === 'today') return [isoDay(today), isoDay(today)];
  if (preset === 'month') { from.setDate(1); return [isoDay(from), isoDay(today)]; }
  from.setDate(today.getDate() - (preset === '30' ? 29 : 6));
  return [isoDay(from), isoDay(today)];
}
async function downloadAuthed(path, name) {
  const r = await fetch(path, { headers: { Authorization: 'Bearer ' + S.token } });
  if (!r.ok) throw new Error(r.statusText);
  const a = document.createElement('a');
  a.href = URL.createObjectURL(await r.blob());
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

function playsReport(r, by) {
  const max = Math.max(1, ...r.days.map(d => d.seconds));
  const cols = by === 'item' ? [t('playsItem'), t('playsCount'), t('playsTime'), t('nav_devices')] : by === 'device' ? [t('device'), t('playsCount'), t('playsTime'), t('playsItems')] : [t('playsDay'), t('playsCount'), t('playsTime'), t('nav_devices')];
  const cell = x => (by === 'item' ? `<td>${kindIcon(x.kind)} <b>${esc(x.name || '—')}</b></td>` : by === 'device'
    ? `<td><a href="#/device/${encodeURIComponent(x.device_id)}"><b>${esc(x.name)}</b></a><br><small class="muted">${esc(x.location || '')}</small></td>`
    : `<td>${new Date(x.day + 'T12:00').toLocaleDateString(S.lang === 'cs' ? 'cs-CZ' : 'en-GB', { weekday: 'short', day: 'numeric', month: 'numeric' })}</td>`);
  return `<div class="stats plays-stats"><div class="stat"><span>${t('playsCount')}</span><b>${r.total.count.toLocaleString(S.lang === 'cs' ? 'cs-CZ' : 'en-GB')}</b></div><div class="stat"><span>${t('playsTime')}</span><b>${dur(r.total.seconds)}</b></div>
    <div class="stat"><span>${t('playsItems')}</span><b>${r.total.items}</b></div><div class="stat"><span>${t('playsScreens')}</span><b>${r.total.devices}</b></div></div>
    ${r.days.length > 1 ? `<div class="plays-chart" role="img" aria-label="${esc(t('playsChart'))}">${r.days.map(d => `<div class="pc-col" title="${esc(d.day)}: ${d.count} × · ${esc(dur(d.seconds))}"><i style="height:${Math.round(d.seconds / max * 100)}%"></i><small>${new Date(d.day + 'T12:00').getDate()}.</small></div>`).join('')}</div>` : ''}
    <div class="table-wrap"><table class="table"><thead><tr>${cols.map(c => `<th>${c}</th>`).join('')}</tr></thead><tbody>
    ${r.rows.map(x => `<tr>${cell(x)}<td>${x.count.toLocaleString(S.lang === 'cs' ? 'cs-CZ' : 'en-GB')}</td><td>${dur(x.seconds)}</td><td>${by === 'device' ? x.items : x.devices}</td></tr>`).join('') || `<tr><td colspan="4">${emptyState('plays', t('playsNone'), t('playsNoneHint'))}</td></tr>`}</tbody></table></div>`;
}

VIEWS.plays = {
  mount(root) {
    setTitle(t('nav_plays'));
    const [from, to] = playsRange('7');
    S.playsF = S.playsF || { from, to, by: 'item', location: '', group: '', tag: '', q: '', preset: '7' };
    const f = S.playsF;
    root.innerHTML = `<section class="card flush"><div class="toolbar wrap">
      <div class="seg" id="plPreset">${['today', '7', '30', 'month'].map(x => `<button data-p="${x}" class="${f.preset === x ? 'active' : ''}">${t('playsPreset_' + x)}</button>`).join('')}</div>
      <input type="date" id="plFrom" value="${f.from}"><input type="date" id="plTo" value="${f.to}">
      <select id="plLoc">${options(orgNames('locations'), f.location, t('allLocations'))}</select>
      <select id="plGroup">${options(orgNames('groups'), f.group, t('allGroups'))}</select>
      ${S.tags.length ? `<select id="plTag">${options(S.tags, f.tag, t('allTags'))}</select>` : ''}
      <input type="search" id="plQ" placeholder="${esc(t('playsSearch'))}" value="${esc(f.q)}"><span class="grow"></span>
      <button class="btn sm" id="plCsv">${icon('download')}CSV</button><button class="btn sm" data-do="monthlyReport">${icon('audit')}${t('monthlyReport')}</button></div>
      <div class="toolbar"><div class="seg" id="plBy">${['item', 'device', 'day'].map(x => `<button data-by="${x}" class="${f.by === x ? 'active' : ''}">${t('playsBy_' + x)}</button>`).join('')}</div></div>
      <div id="plOut" class="plays-out"><div class="empty">…</div></div></section>`;
    const go = () => this.load(root);
    $('#plPreset', root).onclick = e => { const b = e.target.closest('[data-p]'); if (!b) return; [f.from, f.to] = playsRange(b.dataset.p); f.preset = b.dataset.p; this.mount(root); };
    $('#plBy', root).onclick = e => { const b = e.target.closest('[data-by]'); if (!b) return; f.by = b.dataset.by; $$('[data-by]', root).forEach(x => x.classList.toggle('active', x === b)); go(); };
    $('#plFrom', root).onchange = e => { f.from = e.target.value; f.preset = ''; go(); };
    $('#plTo', root).onchange = e => { f.to = e.target.value; f.preset = ''; go(); };
    $('#plLoc', root).onchange = e => { f.location = e.target.value; go(); };
    $('#plGroup', root).onchange = e => { f.group = e.target.value; go(); };
    if ($('#plTag', root)) $('#plTag', root).onchange = e => { f.tag = e.target.value; go(); };
    let timer;
    $('#plQ', root).oninput = e => { f.q = e.target.value; clearTimeout(timer); timer = setTimeout(go, 300); };
    $('#plCsv', root).onclick = () => downloadAuthed('/api/plays/export.csv?' + playsQuery({ ...f, by: '', preset: '' }), `caracal-plays-${f.from}-${f.to}.csv`).catch(e => toast(errText(e), 'err'));
    go();
  },
  async load(root) {
    const f = S.playsF, out = $('#plOut', root);
    try {
      const r = await api('/api/plays/report?' + playsQuery({ ...f, preset: '' }));
      if (out.isConnected) patch(out, playsReport(r, f.by));
    } catch (e) { out.innerHTML = `<div class="note warn">${esc(errText(e))}</div>`; }
  },
};

async function devicePlays(el, d) {
  if (el._for === d.id && el._at > Date.now() - 60000) return;
  el._for = d.id;
  el._at = Date.now();
  const [from, to] = playsRange('7');
  const r = await api('/api/plays/report?' + playsQuery({ device: d.id, from, to, by: 'item' })).catch(() => null);
  if (!r || !el.isConnected) return;
  patch(el, `<section class="card flush"><div class="toolbar"><h2 class="grow">${t('playsLast7')}</h2><a class="btn sm" href="#/plays">${t('playsAll')}</a></div>
    ${d.capabilities.plays === true || r.total.count ? playsReport(r, 'item') : `<div class="note">${t('playsNeedUpdate')}</div>`}</section>`);
}

// ---------- what plays when

const TIMELINE_COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#8b5cf6', '#ec4899', '#14b8a6', '#f97316', '#6366f1'];
const tlColor = id => TIMELINE_COLORS[Math.abs(Number(id) || 0) % TIMELINE_COLORS.length];
const hhmm = m => `${String(Math.floor(m / 60) % 24).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
const tlPct = m => (m / 1440 * 100).toFixed(3) + '%';
function tlTrack(row, nowMinute = null) {
  const seg = (s, cls, style, title, label = '') => `<i class="${cls}" style="left:${tlPct(s.start)};width:${tlPct(s.end - s.start)};${style}" title="${esc(title)}">${label}</i>`;
  return `<div class="wk-track">
    ${row.playlists.map(p => seg(p, 'wk-pl', `background:${tlColor(p.playlist_id)}`, `${hhmm(p.start)}–${hhmm(p.end)} · ${p.name}`, p.end - p.start > 90 ? `<span>${esc(p.name)}</span>` : '')).join('')}
    ${row.playlists.length ? '' : `<em class="wk-own">${row.schedule ? t('tlDefault') : t('tlNoSchedule')}</em>`}
    ${row.screen_off.map(s => seg(s, 'wk-off', '', `${hhmm(s.start)}–${hhmm(s.end)} · ${t('screenOff')}`)).join('')}
    ${row.takeovers.map(s => seg(s, 'wk-alert', '', `${hhmm(s.start)}–${hhmm(s.end)} · ${s.title}`)).join('')}
    ${row.tasks.map(x => `<b class="wk-task" style="left:${tlPct(x.minute)}" title="${esc(hhmm(x.minute) + ' · ' + x.name + ' (' + t('task_' + x.action) + ')')}"></b>`).join('')}
    ${nowMinute !== null ? `<b class="wk-now" style="left:${tlPct(nowMinute)}"></b>` : ''}</div>
    ${row.items.length ? `<div class="wk-items">${row.items.map(it => `<i style="left:${tlPct(it.start)};width:${tlPct(Math.max(4, it.end - it.start))}" title="${esc(`${hhmm(it.start)}–${hhmm(it.end)} · ${it.name}${it.from === 'device' ? '' : ' (' + it.from + ')'}`)}"><span>${esc(it.name)}</span></i>`).join('')}</div>` : ''}`;
}
const tlRuler = () => `<div class="wk-ruler">${[0, 3, 6, 9, 12, 15, 18, 21].map(h => `<span style="left:${tlPct(h * 60)}">${h}:00</span>`).join('')}</div>`;
function tlLegend(days) {
  const seen = new Map();
  days.forEach(d => d.devices.forEach(r => r.playlists.forEach(p => seen.set(p.playlist_id, p.name))));
  return `<div class="wk-legend">${[...seen].map(([id, name]) => `<span><i style="background:${tlColor(id)}"></i>${esc(name)}</span>`).join('')}
    <span><i class="wk-off-sw"></i>${t('screenOff')}</span><span><i class="wk-task-sw"></i>${t('tlTask')}</span><span><i class="wk-alert-sw"></i>${t('nav_takeover')}</span><span><i class="wk-item-sw"></i>${t('tlValidity')}</span></div>`;
}
function nowMinuteOf(r, date) {
  const [d, hm] = r.now.split(' ');
  return d === date ? Number(hm.slice(0, 2)) * 60 + Number(hm.slice(3)) : null;
}

VIEWS.timeline = {
  mount(root) {
    setTitle(t('nav_timeline'));
    S.tlF = S.tlF || { date: isoDay(new Date()), location: '', group: '' };
    const f = S.tlF;
    root.innerHTML = `<section class="card flush"><div class="toolbar wrap"><button class="icon-btn" id="tlPrev">${icon('back')}</button>
      <input type="date" id="tlDate" value="${f.date}"><button class="icon-btn" id="tlNext" style="transform:scaleX(-1)">${icon('back')}</button>
      <button class="btn sm ghost" id="tlToday">${t('today')}</button>
      <select id="tlLoc">${options(orgNames('locations'), f.location, t('allLocations'))}</select>
      <select id="tlGroup">${options(orgNames('groups'), f.group, t('allGroups'))}</select><span class="grow muted" id="tlTz"></span></div>
      <p class="muted card-intro">${t('tlHint')}</p><div id="tlOut"><div class="empty">…</div></div></section>`;
    const shift = n => { const d = new Date(f.date + 'T12:00'); d.setDate(d.getDate() + n); f.date = isoDay(d); $('#tlDate', root).value = f.date; this.load(root); };
    $('#tlPrev', root).onclick = () => shift(-1);
    $('#tlNext', root).onclick = () => shift(1);
    $('#tlToday', root).onclick = () => { f.date = isoDay(new Date()); $('#tlDate', root).value = f.date; this.load(root); };
    $('#tlDate', root).onchange = e => { f.date = e.target.value; this.load(root); };
    $('#tlLoc', root).onchange = e => { f.location = e.target.value; this.load(root); };
    $('#tlGroup', root).onchange = e => { f.group = e.target.value; this.load(root); };
    this.load(root);
  },
  async load(root) {
    const f = S.tlF, out = $('#tlOut', root);
    const r = await api('/api/timeline?' + playsQuery(f)).catch(e => ({ error: errText(e) }));
    if (!out.isConnected) return;
    if (r.error) { out.innerHTML = `<div class="note warn">${esc(r.error)}</div>`; return; }
    $('#tlTz', root).textContent = t('tlTimezone', { tz: r.timezone });
    const day = r.days[0], now = nowMinuteOf(r, day.date);
    patch(out, day.devices.length ? `${tlLegend(r.days)}<div class="wk"><div class="wk-row wk-head"><span></span>${tlRuler()}</div>
      ${day.devices.map(x => `<div class="wk-row"><a class="wk-label" href="#/device/${encodeURIComponent(x.id)}/timeline"><b>${esc(x.name)}</b><small class="muted">${esc(x.schedule ? x.schedule.name : t('tlNoSchedule'))}</small></a><div>${tlTrack(x, now)}</div></div>`).join('')}</div>`
      : emptyState('timeline', t('noMatch'), ''));
  },
};

async function deviceTimeline(el, d) {
  if (el._for === d.id && el._at > Date.now() - 60000) return;
  el._for = d.id;
  el._at = Date.now();
  const r = await api('/api/timeline?' + playsQuery({ device: d.id, days: 7 })).catch(() => null);
  if (!r || !el.isConnected) return;
  const loc = S.lang === 'cs' ? 'cs-CZ' : 'en-GB';
  patch(el, `<section class="card flush"><div class="toolbar"><h2 class="grow">${t('tlWeek')}</h2><span class="muted">${t('tlTimezone', { tz: r.timezone })}</span><a class="btn sm" href="#/schedule">${t('nav_schedule')}</a></div>
    ${tlLegend(r.days)}<div class="wk"><div class="wk-row wk-head"><span></span>${tlRuler()}</div>
    ${r.days.map(day => { const x = day.devices[0]; return x ? `<div class="wk-row"><span class="wk-label"><b>${new Date(day.date + 'T12:00').toLocaleDateString(loc, { weekday: 'long' })}</b><small class="muted">${new Date(day.date + 'T12:00').toLocaleDateString(loc, { day: 'numeric', month: 'numeric' })}</small></span><div>${tlTrack(x, nowMinuteOf(r, day.date))}</div></div>` : ''; }).join('')}</div></section>`);
}

// ---------- widgets

const WIDGET_TYPES = ['clock', 'weather', 'rss', 'table', 'text', 'calendar', 'room', 'qr', 'ticker'];
const WIDGET_PREFIX = 'caracal-widget:';
function widgetParams(src) {
  src = String(src || '');
  if (src.startsWith(WIDGET_PREFIX)) return new URLSearchParams(src.slice(WIDGET_PREFIX.length));
  const i = src.indexOf('/widgets?');
  return i >= 0 ? new URLSearchParams(src.slice(i + 9)) : null;
}
const isWidget = a => !!a && a.kind === 'web' && !!widgetParams(a.source) && !!widgetParams(a.source).get('type');
const widgetLabel = src => { const q = widgetParams(src); return t('widget') + ' · ' + t('widget_' + (q && q.get('type') || 'text')); };

// the preview frame asks the page for feeds (weather, RSS, CSV); Fleet reads them with the user's sign-in
window.addEventListener('message', async e => {
  if (e.origin !== location.origin || !e.data || !e.data.caracalFeed || !e.source) return;
  try {
    const r = await api('/api/widgets/feed?url=' + encodeURIComponent(e.data.url));
    e.source.postMessage({ caracalFeed: e.data.caracalFeed, text: r.text }, location.origin);
  } catch (err) { e.source.postMessage({ caracalFeed: e.data.caracalFeed, error: errText(err) }, location.origin); }
});

// deviceId: a device's playlist (also more devices through "also add"), pid: a global playlist; item: editing;
// pick: a zone of a screen layout gets the widget (onPick(query)), nothing is saved here
function widgetDialog({ deviceId = null, pid = null, item = null, pick = null, query = '' }) {
  const q = item ? widgetParams(item.source) : query ? new URLSearchParams(query) : new URLSearchParams({ type: pick && pick.zone === 'ticker' ? 'ticker' : 'clock' });
  const v = k => esc(q.get(k) || '');
  const type0 = q.get('type') || 'clock';
  modal({
    title: pick ? t('zoneWidget_' + pick.zone) : item ? t('widgetEdit') : t('widgetAdd'), wide: true, submit: item || pick ? t('save') : t('add'),
    body: `<div class="widget-dlg"><div class="form">
      <div class="widget-types">${WIDGET_TYPES.map(x => `<label class="wt"><input type="radio" name="type" value="${x}" ${x === type0 ? 'checked' : ''}><span>${t('widget_' + x)}</span><small>${t('widgetHint_' + x)}</small></label>`).join('')}</div>
      <div class="row2" ${pick ? 'hidden' : ''}><label>${t('name')}<input name="name" value="${esc(item ? item.name : '')}" placeholder="${esc(t('widget_' + type0))}"></label>
      <label>${t('durationSec')}<input name="duration" type="number" min="5" value="${esc(item ? item.duration : 20)}" required></label></div>
      <label>${t('widgetTitle')}<input name="title" value="${v('title')}" maxlength="120"></label>
      <div data-w="clock"><label class="check"><input type="checkbox" name="seconds" ${q.get('seconds') === '1' ? 'checked' : ''}>${t('widgetSeconds')}</label>
        <label>${t('widgetTz')}<input name="tz" value="${v('tz')}" placeholder="Europe/Prague"><small class="muted">${t('widgetTzHint')}</small></label></div>
      <div data-w="weather" class="row2"><label>${t('widgetCity')}<input name="city" value="${v('city')}" placeholder="Praha"></label>
        <label>${t('widgetDays')}<input name="days" type="number" min="0" max="6" value="${v('days') || 4}"></label></div>
      <div data-w="rss"><label>${t('widgetRss')}<input name="url_rss" type="url" value="${q.get('type') === 'rss' ? v('url') : ''}" placeholder="https://www.example.com/rss"></label>
        <label>${t('widgetCount')}<input name="count" type="number" min="1" max="12" value="${v('count') || 6}"></label></div>
      <div data-w="table"><label>${t('widgetCsv')}<input name="url_table" type="url" value="${q.get('type') === 'table' ? v('url') : ''}" placeholder="https://docs.google.com/spreadsheets/d/…"><small class="muted">${t('widgetCsvHint')}</small></label>
        <div class="row2"><label>${t('widgetRows')}<input name="rows" type="number" min="3" max="30" value="${v('rows') || 10}"></label><label>${t('widgetPageSeconds')}<input name="page_seconds" type="number" min="4" max="120" value="${v('page_seconds') || 10}"></label></div></div>
      <div data-w="text"><label>${t('widgetText')}<textarea name="text" rows="4" maxlength="1500">${v('text')}</textarea></label>
        <label class="check"><input type="checkbox" name="align" ${q.get('align') === 'center' ? 'checked' : ''}>${t('widgetCenter')}</label></div>
      <div data-w="calendar"><label>${t('widgetIcs')}<input name="url_calendar" type="url" value="${q.get('type') === 'calendar' ? v('url') : ''}" placeholder="https://outlook.office365.com/owa/calendar/…/calendar.ics"><small class="muted">${t('widgetIcsHint')}</small></label>
        <label>${t('widgetCalDays')}<input name="cal_days" type="number" min="1" max="14" value="${v('days') || 3}"></label></div>
      <div data-w="room"><label>${t('widgetIcs')}<input name="url_room" type="url" value="${q.get('type') === 'room' ? v('url') : ''}" placeholder="https://calendar.google.com/calendar/ical/…/basic.ics"><small class="muted">${t('widgetRoomHint')}</small></label></div>
      <div data-w="qr"><label>${t('widgetQrUrl')}<input name="url_qr" value="${q.get('type') === 'qr' ? v('url') : ''}" placeholder="https://example.com/menu"></label>
        <label>${t('widgetQrText')}<input name="qr_text" value="${q.get('type') === 'qr' ? v('text') : ''}" maxlength="200"></label></div>
      <div data-w="ticker"><label>${t('widgetTickerText')}<textarea name="ticker_text" rows="3" maxlength="1500" placeholder="${esc(t('widgetTickerTextHint'))}">${q.get('type') === 'ticker' ? v('text') : ''}</textarea></label>
        <label>${t('widgetTickerRss')}<input name="url_ticker" type="url" value="${q.get('type') === 'ticker' ? v('url') : ''}" placeholder="https://www.example.com/rss"></label>
        <label>${t('widgetSpeed')}<input name="speed" type="range" min="40" max="300" value="${v('speed') || 120}"></label></div>
      <div class="row2"><label>${t('widgetTheme')}<select name="theme"><option value="dark">${t('widgetDark')}</option><option value="light" ${q.get('theme') === 'light' ? 'selected' : ''}>${t('widgetLight')}</option></select></label>
        <label>${t('widgetAccent')}<input name="accent" type="color" value="#${(q.get('accent') || 'f26b1d').replace(/[^0-9a-f]/gi, '')}"></label></div>
      ${deviceId && !item && !pick ? `<details><summary>${t('alsoAddTo')}</summary>${targetPicker(deviceId, [], x => (x.capabilities || {}).widgets === true)}</details>` : ''}
      ${!deviceId && !item && !pick ? `<small class="muted">${t('widgetGlobalHint')}</small>` : ''}</div>
      <div class="widget-preview"><div class="wp-frame"><iframe id="wpFrame" title="${esc(t('preview'))}" sandbox="allow-scripts allow-same-origin"></iframe></div><small class="muted">${t('widgetPreviewHint')}</small></div></div>`,
    onOpen: form => {
      bindTargetPicker(form);
      let timer;
      const sync = () => {
        const type = form.type.value;
        $$('[data-w]', form).forEach(el => { el.hidden = el.dataset.w !== type; });
        form.name.placeholder = t('widget_' + type);
        clearTimeout(timer);
        timer = setTimeout(() => { $('#wpFrame', form).src = '/static/widgets.html?preview=1&' + widgetQuery(Object.fromEntries(new FormData(form))); }, 500);
      };
      form.addEventListener('input', sync);
      form.addEventListener('change', sync);
      sync();
    },
    onSubmit: async data => {
      if (pick) return pick.onPick(widgetQuery(data));
      const source = WIDGET_PREFIX + widgetQuery(data);
      const payload = { name: (data.name || '').trim() || t('widget_' + data.type), duration: Number(data.duration || 20), source };
      if (pid) {
        await api(item ? `/api/global-playlists/${pid}/items/${item.id}` : `/api/global-playlists/${pid}/items`, { method: item ? 'PATCH' : 'POST', json: { kind: 'web', ...payload, scale: 1 } });
        toast(t('saved'));
        return render();
      }
      if (item) return sendCommand(deviceId, 'update_asset', { id: item.id, ...payload });
      const ids = [deviceId, ...pickedTargets(data)];
      return ids.length === 1 ? sendCommand(ids[0], 'add_web', { ...payload, scale: 1 }) : sendBulk(ids, 'add_web', { ...payload, scale: 1 });
    },
  });
}
function widgetQuery(d) {
  const q = new URLSearchParams({ type: d.type, lang: S.lang });
  const put = (k, v) => { if (v !== undefined && v !== null && String(v).trim() !== '') q.set(k, String(v).trim()); };
  put('title', d.title);
  put('theme', d.theme === 'light' ? 'light' : '');
  put('accent', (d.accent || '').replace('#', '') === 'f26b1d' ? '' : (d.accent || '').replace('#', ''));
  if (d.type === 'clock') { put('seconds', d.seconds ? '1' : ''); put('tz', d.tz); }
  if (d.type === 'weather') { put('city', d.city); put('days', d.days); }
  if (d.type === 'rss') { put('url', d.url_rss); put('count', d.count); }
  if (d.type === 'table') { put('url', d.url_table); put('rows', d.rows); put('page_seconds', d.page_seconds); }
  if (d.type === 'text') { put('text', d.text); put('align', d.align ? 'center' : ''); }
  if (d.type === 'calendar') { put('url', d.url_calendar); put('days', d.cal_days); }
  if (d.type === 'room') put('url', d.url_room);
  if (d.type === 'qr') { put('url', d.url_qr); put('text', d.qr_text); }
  if (d.type === 'ticker') { put('text', d.ticker_text); put('url', d.url_ticker); put('speed', d.speed); }
  return q.toString();
}

// ---------- device labels and tables

const tagChips = d => ((d.tags || []).length ? `<span class="tag-chips">${d.tags.map(x => `<span class="tchip">${esc(x)}</span>`).join('')}</span>` : '');

function bulkTagsDialog(ids) {
  modal({
    title: t('tagsEdit'), submit: t('save'),
    body: `<div class="form"><p class="muted">${t('selectedN', { n: ids.length })}</p>
      <label>${t('tagsAdd')}<input name="add" list="dlTags2" placeholder="${esc(t('tagsPlaceholder'))}"></label>
      <label>${t('tagsRemove')}<input name="remove" list="dlTags2"></label><datalist id="dlTags2">${S.tags.map(x => `<option value="${esc(x)}">`).join('')}</datalist>
      ${S.tags.length ? `<div class="chips">${S.tags.map(x => `<span class="tchip">${esc(x)}</span>`).join('')}</div>` : ''}</div>`,
    onSubmit: async data => {
      await api('/api/devices/tags', { method: 'POST', json: { device_ids: ids, add: data.add, remove: data.remove } });
      toast(t('saved'));
      await tick();
    },
  });
}

function importDialog() {
  let rows = null;
  modal({
    title: t('csvImport'), wide: true, submit: t('csvApply'),
    body: `<div class="form"><p class="muted">${t('csvImportHelp')}</p>
      <pre class="code">id;name;location;group;tags;notes\nCRCL-1A2B3C4D;Recepce;Praha;Vstupy;lobby, 4K;</pre>
      <label>${t('file')}<input type="file" name="file" accept=".csv,text/csv,text/plain"></label>
      <label>${t('csvPaste')}<textarea name="csv" rows="6" spellcheck="false"></textarea></label>
      <div class="form-actions start"><button type="button" class="btn" id="csvCheck">${icon('eye')}${t('csvCheck')}</button></div>
      <div id="csvOut"></div></div>`,
    onOpen: form => {
      form.file.onchange = async () => { if (form.file.files[0]) { form.csv.value = await form.file.files[0].text(); } };
      $('#csvCheck', form).onclick = async () => {
        const out = $('#csvOut', form);
        try {
          const r = await api('/api/devices/import', { method: 'POST', json: { csv: form.csv.value, apply: false } });
          rows = r.rows;
          out.innerHTML = `<div class="table-wrap"><table class="table"><thead><tr><th>#</th><th>${t('device')}</th><th>${t('csvChanges')}</th></tr></thead><tbody>
            ${r.rows.map(x => `<tr class="${x.error ? 'row-err' : ''}"><td>${x.line}</td><td>${esc(x.device || x.key)}</td><td>${x.error ? `<span class="tag bad">${esc(errText(new Error(x.error)))}</span>`
              : Object.keys(x.changes).length ? Object.entries(x.changes).map(([k, c]) => `<div><small class="muted">${t('csvCol_' + k)}:</small> <s>${esc(Array.isArray(c.from) ? c.from.join(', ') : c.from || '—')}</s> → <b>${esc(Array.isArray(c.to) ? c.to.join(', ') : c.to || '—')}</b></div>`).join('') : `<span class="muted">${t('csvNoChange')}</span>`}</td></tr>`).join('')}</tbody></table></div>`;
        } catch (e) { out.innerHTML = `<div class="note warn">${esc(errText(e))}</div>`; }
      };
    },
    onSubmit: async data => {
      if (!rows) throw new Error('csv_check_first');
      const r = await api('/api/devices/import', { method: 'POST', json: { csv: data.csv, apply: true } });
      toast(t('csvApplied', { n: r.rows.filter(x => !x.error && Object.keys(x.changes || {}).length).length }));
      await tick();
    },
  });
}

Object.assign(ACTIONS_UI, {
  async takeoverEnd(b) {
    if (!await confirmBox(t('takeoverEndConfirm'), false, t('takeoverEnd'))) return;
    await api(`/api/takeovers/${b.dataset.tid}/end`, { method: 'POST' });
    toast(t('takeoverEndedToast'));
    await tick();
    if (S.route.view === 'takeover') VIEWS.takeover.load($('#view'));
  },
  async approvalApprove(b) {
    modal({
      title: t('approve'), submit: t('approve'), body: `<div class="form"><label>${t('approvalComment')}<input name="comment" maxlength="500"></label></div>`,
      onSubmit: async data => {
        const r = await api(`/api/approvals/${b.dataset.aid}/approve`, { method: 'POST', json: { comment: data.comment } });
        toast(t('approvalApproved'));
        if ((r.items_unsupported || []).length) toast(t('deployCameraSkipped', { list: r.items_unsupported.join(', ') }), 'warn');
        await tick();
        VIEWS.approvals.load($('#view'));
      },
    });
  },
  async approvalReject(b) {
    modal({
      title: t('reject'), submit: t('reject'), danger: true, body: `<div class="form"><label>${t('approvalReason')}<input name="comment" maxlength="500" required></label></div>`,
      onSubmit: async data => {
        await api(`/api/approvals/${b.dataset.aid}/reject`, { method: 'POST', json: { comment: data.comment } });
        toast(t('approvalRejected'));
        await tick();
        VIEWS.approvals.load($('#view'));
      },
    });
  },
  async approvalCancel(b) {
    await api(`/api/approvals/${b.dataset.aid}`, { method: 'DELETE' });
    await tick();
    VIEWS.approvals.load($('#view'));
  },
  libUse(b) { const f = S.library.find(x => x.id === b.dataset.fid); if (f) libUseDialog(f); },
  libReplace(b) { const f = S.library.find(x => x.id === b.dataset.fid); if (f) libReplaceDialog(f); },
  libRename(b) {
    const f = S.library.find(x => x.id === b.dataset.fid);
    if (!f) return;
    modal({
      title: t('rename'), body: `<div class="form"><label>${t('name')}<input name="name" value="${esc(f.name)}" required></label></div>`,
      onSubmit: async data => { await api('/api/library/' + f.id, { method: 'PATCH', json: { name: data.name } }); VIEWS.library.load($('#view')); },
    });
  },
  async libDelete(b) {
    const f = S.library.find(x => x.id === b.dataset.fid);
    if (!f || !await confirmBox(t('confirmDeleteItem', { name: esc(f.name) }))) return;
    await api('/api/library/' + f.id, { method: 'DELETE' });
    VIEWS.library.load($('#view'));
  },
  async versionShow(b) {
    const v = await api(`/api/global-playlists/${S.global.id}/versions/${b.dataset.vid}`);
    const now = S.global.items.map(x => x.name);
    modal({
      title: t('versionOf', { time: dt(v.created) }),
      body: `<p class="muted">${esc(v.username || t('versionSystem'))}</p><ol class="mini-list">${v.items.map(it => `<li class="${now.includes(it.name) ? '' : 'diff-del'}">${isWidget(it) ? icon('widget') : kindIcon(it.kind)}<span>${esc(it.name)}</span><small class="muted">${it.duration ? dur(it.duration) : ''}</small></li>`).join('') || `<li class="empty">${t('playlistEmpty')}</li>`}</ol>
        ${S.global.items.filter(x => !v.items.some(y => y.name === x.name)).length ? `<p class="muted">${t('versionNewSince')}: ${esc(S.global.items.filter(x => !v.items.some(y => y.name === x.name)).map(x => x.name).join(', '))}</p>` : ''}`,
    });
  },
  async versionRestore(b) {
    if (!await confirmBox(t('versionRestoreConfirm'), false, t('restore'))) return;
    const r = await api(`/api/global-playlists/${S.global.id}/versions/${b.dataset.vid}/restore`, { method: 'POST' });
    toast(t('versionRestored'));
    if (r.missing.length) toast(t('versionMissing', { list: r.missing.join(', ') }), 'warn');
    render();
  },
  addWidget(b) { widgetDialog({ deviceId: b.dataset.id }); },
  bulkTags(b) { bulkTagsDialog(b.dataset.id ? [b.dataset.id] : [...S.selected]); },
  devicesExport() { return downloadAuthed('/api/devices/export.csv', 'caracal-devices.csv'); },
  devicesImport() { importDialog(); },
});

// ---------- the first steps, simple / pro mode, dropping files

async function guideCard(el, first) {
  if (!el || prefs().guide_hidden) { patch(el, ''); return; }
  if (!S.guide || S.guide.at < Date.now() - 30000) S.guide = { at: Date.now(), data: await api('/api/onboarding').catch(() => null) };
  const steps = (S.guide.data && S.guide.data.steps) || [];
  if (!el.isConnected || !steps.length || steps.every(x => x.done)) { patch(el, ''); return; }
  const done = steps.filter(x => x.done).length;
  const go = { device: `<button class="btn sm primary" data-do="guideAddDevice">${t('guideDo_device')}</button>`, content: `<a class="btn sm" href="#/global">${t('guideDo_content')}</a>`,
    alerts: `<a class="btn sm" href="#/settings/alerts">${t('guideDo_alerts')}</a>`, backup: `<a class="btn sm" href="#/settings/backup">${t('guideDo_backup')}</a>`, users: `<a class="btn sm" href="#/users">${t('guideDo_users')}</a>` };
  patch(el, `<section class="card guide"><div class="card-head"><h2>${t('guideTitle')}</h2><span class="muted">${t('guideProgress', { n: done, m: steps.length })}</span>
    <button class="btn sm ghost" data-do="guideHide">${t('guideHide')}</button></div>
    <div class="guide-bar"><i style="width:${Math.round(done / steps.length * 100)}%"></i></div>
    <ol class="guide-steps">${steps.map(x => `<li class="${x.done ? 'done' : ''}"><span class="guide-check">${x.done ? icon('check') : ''}</span><div><b>${t('guide_' + x.key)}</b><small class="muted">${t('guideHint_' + x.key)}</small></div>${x.done || (first && x.key === 'device') ? '' : go[x.key]}</li>`).join('')}</ol>
    <div class="guide-mode"><span>${t('guideMode')}</span><div class="seg">${['simple', 'pro'].map(m => `<button data-do="setMode" data-mode="${m}" class="${prefs().ui_mode === m ? 'active' : ''}">${t('mode_' + m + 'UI')}</button>`).join('')}</div><small class="muted">${t('modeHint_' + prefs().ui_mode)}</small></div></section>`);
}

const mediaItem = (f, up) => ({ kind: f.type.split('/')[0], file_id: up.id, name: f.name.replace(/\.[^.]+$/, ''), duration: f.type.startsWith('video') ? 30 : 15 });
async function uploadMany(files, each) {
  for (const [i, f] of files.entries()) {
    toast(t('uploadingN', { n: i + 1, m: files.length, name: f.name }));
    await each(f, await uploadFile(f, () => {}));
  }
}
// pictures and videos dropped on el (the latest onFiles is used, el keeps one set of listeners)
function dropFiles(el, onFiles) {
  if (!el) return;
  el.dataset.dropLabel = t('dropHere');
  if (el._onFiles) { el._onFiles = onFiles; return; }
  el._onFiles = onFiles;
  const files = e => [...((e.dataTransfer && e.dataTransfer.types) || [])].includes('Files');
  el.addEventListener('dragover', e => { if (!files(e)) return; e.preventDefault(); el.classList.add('drop-on'); });
  el.addEventListener('dragleave', e => { if (!el.contains(e.relatedTarget)) el.classList.remove('drop-on'); });
  el.addEventListener('drop', e => {
    if (!files(e)) return;
    e.preventDefault();
    el.classList.remove('drop-on');
    const list = [...e.dataTransfer.files].filter(f => /^(image|video)\//.test(f.type));
    if (!list.length) { toast(t('dropOnlyMedia'), 'warn'); return; }
    Promise.resolve(el._onFiles(list)).catch(err => toast(errText(err), 'err'));
  });
}
// the order of a global playlist by dragging its rows
function bindGlobalDrag(list, pid) {
  if (!list) return;
  let dragged = null, before = '';
  list.ondragstart = e => {
    dragged = e.target.closest('li[data-iid]');
    if (!dragged) return;
    before = $$('li[data-iid]', list).map(x => x.dataset.iid).join();
    dragged.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
  };
  list.ondragover = e => {
    const over = e.target.closest('li[data-iid]');
    if (!dragged || !over || over === dragged) return;
    e.preventDefault();
    const r = over.getBoundingClientRect();
    list.insertBefore(dragged, e.clientY > r.top + r.height / 2 ? over.nextSibling : over);
  };
  list.ondragend = async () => {
    if (!dragged) return;
    dragged.classList.remove('dragging');
    dragged = null;
    const order = $$('li[data-iid]', list).map(x => Number(x.dataset.iid));
    if (order.join() === before) return;
    try { await api(`/api/global-playlists/${pid}/order`, { method: 'PUT', json: { order } }); toast(t('saved')); } catch (err) { toast(errText(err), 'err'); }
    render();
  };
}

Object.assign(ACTIONS_UI, {
  async navGroup(b) {
    const closed = new Set(prefs().nav_closed);
    closed.has(b.dataset.g) ? closed.delete(b.dataset.g) : closed.add(b.dataset.g);
    b.closest('.nav-group').classList.toggle('closed', closed.has(b.dataset.g));
    b.setAttribute('aria-expanded', closed.has(b.dataset.g) ? 'false' : 'true');
    await savePrefs({ nav_closed: [...closed] });
  },
  async setMode(b) {
    await savePrefs({ ui_mode: b.dataset.mode });
    buildNav();
    render(true);
    toast(t('modeSet_' + b.dataset.mode));
  },
  async guideHide() {
    await savePrefs({ guide_hidden: true });
    render(true);
  },
  guideAddDevice() { provisionDialog(); },
});

// ================================================================== screen layouts, documents, integrations, reports,
// customers and the map

const layoutSupport = d => !!(d && d.player_settings && 'layout' in d.player_settings);
const tenantName = id => ((S.tenants || []).find(x => x.id === id) || {}).name || '';
const tenantField = (sel = '') => ((S.tenants || []).length && !S.me.tenant_id ? `<label>${t('tenant')}<select name="tenant_id"><option value="">${t('tenantNone')}</option>${S.tenants.map(x => `<option value="${x.id}" ${String(sel) === String(x.id) ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}</select></label>` : '');
const usersTabs = tab => `<nav class="tabs"><a href="#/users" class="${tab === 'people' ? 'active' : ''}">${t('usersTab')}</a><a href="#/users/roles" class="${tab === 'roles' ? 'active' : ''}">${t('rolesTab')}</a><a href="#/users/groups" class="${tab === 'groups' ? 'active' : ''}">${t('userGroupsTab')}</a>${S.me.tenant_id ? '' : `<a href="#/users/tenants" class="${tab === 'tenants' ? 'active' : ''}">${t('tenantsTab')}</a>`}</nav>`;
async function loadTenants() { S.tenants = await api('/api/tenants').catch(() => S.tenants || []); return S.tenants; }

// ---------- a screen in zones: the playlist, a side panel and a bar at the bottom

function layoutDialog(ids) {
  const d = ids.length === 1 ? dev(ids[0]) || S.detail : null;
  const cur = (d && d.player_settings && d.player_settings.layout) || {};
  const z = { side: cur.side ? { ...cur.side } : null, ticker: cur.ticker ? { ...cur.ticker } : null };
  const summary = q => { const p = new URLSearchParams(q || ''); return p.get('type') ? t('widget_' + p.get('type')) + (p.get('title') ? ' · ' + p.get('title') : '') : t('zoneNoWidget'); };
  modal({
    title: t('screenLayout'), wide: true,
    body: `<div class="layout-dlg"><div class="form"><p class="muted">${t('screenLayoutHint')}</p>
      <label class="check"><input type="checkbox" name="side_on" ${z.side ? 'checked' : ''}>${t('zoneSide')}</label>
      <div data-z="side" class="zone-box"><div class="row2"><label>${t('zoneWidth')}<input name="side_width" type="range" min="15" max="40" value="${z.side ? z.side.width : 25}"></label>
        <label>${t('zonePosition')}<select name="side_position"><option value="right">${t('zoneRight')}</option><option value="left" ${z.side && z.side.position === 'left' ? 'selected' : ''}>${t('zoneLeft')}</option></select></label></div>
        <div class="zone-pick"><span data-sum="side"></span><button type="button" class="btn sm" data-zone="side">${icon('widget')}${t('zoneChoose')}</button></div></div>
      <label class="check"><input type="checkbox" name="ticker_on" ${z.ticker ? 'checked' : ''}>${t('zoneTicker')}</label>
      <div data-z="ticker" class="zone-box"><label>${t('zoneHeight')}<input name="ticker_height" type="range" min="5" max="25" value="${z.ticker ? z.ticker.height : 8}"></label>
        <div class="zone-pick"><span data-sum="ticker"></span><button type="button" class="btn sm" data-zone="ticker">${icon('widget')}${t('zoneChoose')}</button></div></div>
      ${ids.length > 1 ? `<p class="muted">${t('selectedN', { n: ids.length })}</p>` : ''}</div>
      <div class="layout-preview"><div class="lp-screen" id="lpScreen"><div class="lp-main">${t('zoneMain')}</div><iframe data-lp="side" title="side"></iframe><iframe data-lp="ticker" title="ticker"></iframe></div><small class="muted">${t('screenLayoutPreview')}</small></div></div>`,
    onOpen: form => {
      const draw = () => {
        const sideOn = form.side_on.checked && z.side, tickOn = form.ticker_on.checked && z.ticker;
        $('[data-z=side]', form).hidden = !form.side_on.checked;
        $('[data-z=ticker]', form).hidden = !form.ticker_on.checked;
        $('[data-sum=side]', form).textContent = summary(z.side && z.side.widget);
        $('[data-sum=ticker]', form).textContent = summary(z.ticker && z.ticker.widget);
        const sw = sideOn ? Number(form.side_width.value) : 0, th = tickOn ? Number(form.ticker_height.value) : 0, left = form.side_position.value === 'left';
        const box = $('#lpScreen', form);
        box.style.setProperty('--sw', sw + '%'); box.style.setProperty('--th', th + '%'); box.classList.toggle('left', left);
        for (const [k, on] of [['side', sideOn], ['ticker', tickOn]]) {
          const f = $(`[data-lp=${k}]`, form);
          f.hidden = !on;
          const src = on ? '/static/widgets.html?preview=1&' + z[k].widget : '';
          if (f.dataset.src !== src) { f.dataset.src = src; f.src = src || 'about:blank'; }
        }
      };
      form.addEventListener('input', draw);
      form.addEventListener('change', draw);
      form.addEventListener('click', e => {
        const b = e.target.closest('[data-zone]');
        if (!b) return;
        const zone = b.dataset.zone;
        widgetDialog({ pick: { zone, onPick: q => { z[zone] = { ...(z[zone] || {}), widget: q }; setTimeout(() => { draw(); }, 50); } }, query: z[zone] && z[zone].widget });
      });
      draw();
    },
    onSubmit: async data => {
      const layout = {};
      if (data.side_on) {
        if (!z.side || !z.side.widget) throw new Error('zone_widget_required');
        layout.side = { width: Number(data.side_width), position: data.side_position, widget: z.side.widget };
      }
      if (data.ticker_on) {
        if (!z.ticker || !z.ticker.widget) throw new Error('zone_widget_required');
        layout.ticker = { height: Number(data.ticker_height), widget: z.ticker.widget };
      }
      const targets = ids.filter(id => layoutSupport(dev(id) || (S.detail && S.detail.id === id ? S.detail : null)));
      return targets.length === 1 ? sendCommand(targets[0], 'player_settings', { layout }) : sendBulk(targets, 'player_settings', { layout });
    },
  });
}

// ---------- PDF and presentations: one picture per page

function documentDialog(target, id = null) {
  modal({
    title: t('documentTitle'), submit: t('documentConvert'),
    body: `<div class="form"><p class="muted">${t('documentHint')}</p>
      <label>${t('file')}<input type="file" name="file" accept=".pdf,.pptx,.ppt,.odp,.key,.docx,.doc,.odt" required></label>
      ${target === 'library' ? '' : `<label>${t('documentSeconds')}<input name="duration" type="number" min="5" value="10" required></label>`}
      <div class="upload-prog" hidden><i></i></div><div class="doc-state muted"></div></div>`,
    onSubmit: async (data, form) => {
      const file = form.file.files[0];
      const pr = $('.upload-prog', form), st = $('.doc-state', form);
      pr.hidden = false;
      const r = await new Promise((resolve, reject) => {
        const x = new XMLHttpRequest();
        x.open('POST', '/api/documents');
        x.setRequestHeader('Authorization', 'Bearer ' + S.token);
        x.setRequestHeader('X-File-Name', encodeURIComponent(file.name));
        x.upload.onprogress = e => { if (e.lengthComputable) { $('i', pr).style.width = (e.loaded / e.total * 100) + '%'; if (e.loaded === e.total) st.textContent = t('documentConverting'); } };
        x.onload = () => { let d = {}; try { d = JSON.parse(x.responseText); } catch { /* ignore */ } x.status < 300 ? resolve(d) : reject(new Error(d.detail || x.statusText)); };
        x.onerror = () => reject(new Error('network_error'));
        x.send(file);
      });
      const duration = Number(data.duration || 10);
      if (target === 'global') {
        for (const p of r.pages) await api(`/api/global-playlists/${S.global.id}/items`, { method: 'POST', json: { kind: 'image', file_id: p.id, name: p.name, duration } });
        render();
      } else if (target === 'device') {
        for (const p of r.pages) await api(`/api/devices/${encodeURIComponent(id)}/commands`, { method: 'POST', json: { action: 'add_media', payload: { kind: 'image', file_id: p.id, name: p.name, duration } } });
      } else if (S.route.view === 'library') VIEWS.library.load($('#view'));
      toast(t('documentDone', { n: r.pages.length }));
    },
  });
}

// ---------- settings: API tokens and webhooks, monthly reports

Object.assign(SETTINGS_VIEW, {
  async integrations(root) {
    root.innerHTML = '<section class="card"><div class="empty">…</div></section>';
    const [tokens, hooks, roles] = await Promise.all([api('/api/api-tokens'), can('settings') ? api('/api/webhooks') : null, api('/api/roles')].map(p => p && p.catch(() => null)));
    if (!root.isConnected) return;
    S.roles = roles ? roles.roles : S.roles;
    S.hooks = hooks;
    root.innerHTML = `<section class="card flush"><div class="toolbar"><h2 class="grow">${t('apiTokens')}</h2><button class="btn primary sm" data-do="apiTokenNew">${icon('plus')}${t('apiTokenNew')}</button></div>
      <p class="muted card-intro">${t('apiTokensHint')}</p>
      <div class="table-wrap"><table class="table"><thead><tr><th>${t('name')}</th><th>${t('role')}</th><th>${t('lastUsed')}</th><th></th></tr></thead><tbody>
      ${(tokens || []).map(x => `<tr><td><b>${esc(x.name)}</b><br><small class="muted"><code>${esc(x.prefix)}…</code> · ${esc(x.created_by)} · ${dt(x.created)}${x.expires ? ' · ' + t('expires', { time: dt(x.expires) }) : ''}</small></td>
        <td><span class="badge role">${esc(roleLabel(x.role))}</span><br><small class="muted">${scopeText(x.scope)}</small></td><td class="nowrap">${x.last_used ? ago(x.last_used) : '—'}</td>
        <td class="nowrap"><label class="check"><input type="checkbox" data-do="apiTokenToggle" data-tid="${x.id}" ${x.enabled ? 'checked' : ''}>${t('enabled')}</label>
        <button class="icon-btn danger" data-do="apiTokenDelete" data-tid="${x.id}" title="${t('delete')}">${icon('trash')}</button></td></tr>`).join('') || `<tr><td colspan="4" class="empty">${t('apiTokensNone')}</td></tr>`}</tbody></table></div>
      <details class="api-help"><summary>${t('apiExamples')}</summary><pre class="code">curl -H "Authorization: Bearer cfa_…" ${esc(location.origin)}/api/devices

curl -X POST -H "Authorization: Bearer cfa_…" -H "Content-Type: application/json" \\
  -d '{"title":"Evacuation","message":"Leave the building","minutes":30,"targets":{"all":true}}' \\
  ${esc(location.origin)}/api/takeovers

curl -X POST -H "Authorization: Bearer cfa_…" -H "Content-Type: application/json" \\
  -d '{"target_ids":["CRCL-…"],"mode":"replace"}' ${esc(location.origin)}/api/global-playlists/1/deploy</pre><p class="muted">${t('apiDocsHint')}</p></details></section>
    ${hooks ? `<section class="card flush"><div class="toolbar"><h2 class="grow">${t('webhooks')}</h2><button class="btn primary sm" data-do="webhookEdit">${icon('plus')}${t('webhookNew')}</button></div>
      <p class="muted card-intro">${t('webhooksHint')}</p>
      <div class="table-wrap"><table class="table"><thead><tr><th>${t('name')}</th><th>${t('webhookEvents')}</th><th>${t('webhookLast')}</th><th></th></tr></thead><tbody>
      ${hooks.webhooks.map(h => `<tr><td><b>${esc(h.name)}</b><br><small class="muted">${esc(h.url)}</small></td><td><small>${h.events.map(e => esc(t('event_' + e.replace('.', '_')))).join(', ')}</small></td>
        <td>${h.last ? `<span class="tag ${h.last.ok ? 'ok' : 'bad'}">${h.last.ok ? 'OK' : esc(h.last.error || 'error')}</span><br><small class="muted">${ago(h.last.at)}</small>` : '—'}</td>
        <td class="nowrap"><button class="btn sm" data-do="webhookTest" data-hid="${h.id}">${t('test')}</button><button class="icon-btn" data-do="webhookEdit" data-hid="${h.id}" title="${t('edit')}">${icon('edit')}</button><button class="icon-btn danger" data-do="webhookDelete" data-hid="${h.id}" title="${t('delete')}">${icon('trash')}</button></td></tr>`).join('') || `<tr><td colspan="4" class="empty">${t('webhooksNone')}</td></tr>`}</tbody></table></div></section>` : ''}`;
  },
  async reports(root) {
    root.innerHTML = '<section class="card"><div class="empty">…</div></section>';
    const s = await api('/api/reports/settings').catch(() => null);
    if (!s || !root.isConnected) return;
    const last = new Date(); last.setDate(0);
    const month = `${last.getFullYear()}-${String(last.getMonth() + 1).padStart(2, '0')}`;
    root.innerHTML = `<section class="card"><h2>${t('monthlyReport')}</h2><p class="muted">${t('monthlyReportHint')}</p>
      <div class="form"><div class="row2"><label>${t('reportMonth')}<input type="month" id="repMonth" value="${month}"></label><span></span></div>
      <div class="form-actions start"><button class="btn" id="repGet">${icon('download')}${t('reportDownload')}</button><button class="btn" id="repSend">${icon('send')}${t('reportSendNow')}</button></div></div></section>
      <section class="card"><h2>${t('reportAuto')}</h2><form id="repForm" class="form">
      <label class="check"><input type="checkbox" class="switch" name="enabled" ${s.enabled ? 'checked' : ''}>${t('reportAutoOn')}</label>
      <label>${t('reportRecipients')}<input name="recipients" value="${esc(s.recipients.join(', '))}" placeholder="vedeni@firma.cz, it@firma.cz"><small class="muted">${t('reportRecipientsHint')}</small></label>
      <label>${t('language')}<select name="language">${LANG_NAMES.map(([k, n]) => `<option value="${k}" ${s.language === k ? 'selected' : ''}>${n}</option>`).join('')}</select></label>
      <div class="form-actions"><button class="btn primary">${t('save')}</button></div></form></section>`;
    $('#repGet', root).onclick = () => downloadAuthed('/api/reports/monthly.pdf?month=' + $('#repMonth', root).value, `caracal-report-${$('#repMonth', root).value}.pdf`).catch(e => toast(errText(e), 'err'));
    $('#repSend', root).onclick = async () => { try { await api('/api/reports/send', { method: 'POST', json: { month: $('#repMonth', root).value } }); toast(t('reportSent')); } catch (e) { toast(errText(e), 'err'); } };
    $('#repForm', root).onsubmit = async e => {
      e.preventDefault();
      try { await api('/api/reports/settings', { method: 'PUT', json: Object.fromEntries(new FormData(e.target)) }); toast(t('saved')); } catch (err) { toast(errText(err), 'err'); }
    };
  },
});

// ---------- customers

VIEWS.tenants = {
  mount(root) {
    setTitle(t('nav_users'));
    root.innerHTML = `${usersTabs('tenants')}<section class="card flush"><div class="toolbar"><p class="muted grow">${t('tenantsHint')}</p><button class="btn primary" data-do="tenantEdit">${icon('plus')}${t('tenantNew')}</button></div><div id="tnTable"></div></section>`;
  },
  async update(root) {
    const list = await loadTenants();
    patch($('#tnTable', root), `<div class="table-wrap"><table class="table"><thead><tr><th>${t('tenant')}</th><th>${t('locations')}</th><th>${t('usersTab')}</th><th></th></tr></thead><tbody>
      ${list.map(x => `<tr><td><b>${icon('customer')} ${esc(x.name)}</b></td><td>${x.locations.map(l => `<span class="tag">${esc(l)}</span>`).join(' ') || '—'}</td><td>${x.users}</td>
        <td class="nowrap"><button class="icon-btn" data-do="tenantEdit" data-tid="${x.id}" title="${t('edit')}">${icon('edit')}</button><button class="icon-btn danger" data-do="tenantDelete" data-tid="${x.id}" title="${t('delete')}">${icon('trash')}</button></td></tr>`).join('')
      || `<tr><td colspan="4">${emptyState('customer', t('tenantsNone'), t('tenantsHint'))}</td></tr>`}</tbody></table></div>`);
  },
};

// ---------- the map of the locations

let leafletLoading = null;
function loadLeaflet() {
  if (!leafletLoading) {
    const css = document.createElement('link');
    css.rel = 'stylesheet';
    css.href = '/static/vendor/leaflet/leaflet.css';
    document.head.append(css);
    leafletLoading = new Promise((resolve, reject) => {
      const el = document.createElement('script');
      el.src = '/static/vendor/leaflet/leaflet.js';
      el.onload = resolve;
      el.onerror = () => { leafletLoading = null; reject(new Error('map_load_failed')); };
      document.head.append(el);
    });
  }
  return leafletLoading;
}
VIEWS.map = {
  mount(root) {
    setTitle(t('nav_map'));
    root.innerHTML = `<section class="card flush map-card"><div class="toolbar"><h2 class="grow">${t('nav_map')}</h2><span class="map-legend"><i class="ok"></i>${t('mapOk')}<i class="warn"></i>${t('mapProblem')}<i class="bad"></i>${t('mapOffline')}</span>
      ${canAll('devices') ? `<a class="btn sm" href="#/organization">${icon('location')}${t('mapEditPlaces')}</a>` : ''}</div><div id="map" class="map"></div><div id="mapNone"></div></section>`;
    loadLeaflet().then(() => this.draw(root)).catch(e => { $('#map', root).innerHTML = `<div class="note warn">${esc(errText(e))}</div>`; });
  },
  update(root) { if (window.L && this.layer && $('#map', root)) this.markers(root); },
  draw(root) {
    const el = $('#map', root);
    if (!el || !window.L) return;
    this.map = L.map(el, { attributionControl: true }).setView([49.8, 15.5], 7);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, referrerPolicy: 'origin', attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>' }).addTo(this.map);
    this.layer = L.layerGroup().addTo(this.map);
    this.markers(root, true);
  },
  markers(root, fit = false) {
    this.layer.clearLayers();
    const places = (S.org.locations || []).filter(l => l.lat != null && l.lon != null);
    const pts = [];
    for (const l of places) {
      const ds = S.devices.filter(d => d.location === l.name);
      const off = ds.filter(d => !d.online).length, bad = ds.filter(d => d.online && d.needs_attention).length;
      const color = off ? '#dc2626' : bad ? '#f59e0b' : ds.length ? '#16a34a' : '#8e8e93';
      const m = L.circleMarker([l.lat, l.lon], { radius: 9 + Math.min(10, ds.length), color: '#fff', weight: 2, fillColor: color, fillOpacity: .9 }).addTo(this.layer);
      m.bindPopup(`<b>${esc(l.name)}</b><br>${esc(l.address || '')}<br>${t('mapCounts', { n: ds.length, on: ds.length - off, bad })}<br><a href="#/devices?location=${encodeURIComponent(l.name)}">${t('open')}</a>`);
      pts.push([l.lat, l.lon]);
    }
    if (fit && pts.length) this.map.fitBounds(pts, { padding: [40, 40], maxZoom: 13 });
    const none = (S.org.locations || []).length - places.length;
    patch($('#mapNone', root), none ? `<div class="note">${t('mapMissing', { n: none })}</div>` : '');
  },
};

Object.assign(ACTIONS_UI, {
  layoutSettings(b) { layoutDialog([b.dataset.id]); },
  bulkLayout() { layoutDialog([...S.selected]); },
  addDocument(b) { documentDialog(b.dataset.target, b.dataset.id); },
  async monthlyReport() {
    const last = new Date(); last.setDate(0);
    const month = `${last.getFullYear()}-${String(last.getMonth() + 1).padStart(2, '0')}`;
    modal({
      title: t('monthlyReport'), submit: t('reportDownload'),
      body: `<div class="form"><p class="muted">${t('monthlyReportHint')}</p><label>${t('reportMonth')}<input type="month" name="month" value="${month}" required></label>
        <div class="row2"><label>${t('location')}<select name="location">${options(orgNames('locations'), '', t('allLocations'))}</select></label><label>${t('group')}<select name="group">${options(orgNames('groups'), '', t('allGroups'))}</select></label></div></div>`,
      onSubmit: data => downloadAuthed('/api/reports/monthly.pdf?' + playsQuery(data), `caracal-report-${data.month}.pdf`),
    });
  },
  apiTokenNew() {
    modal({
      title: t('apiTokenNew'), wide: true,
      body: `<div class="form"><label>${t('name')}<input name="name" required placeholder="Home Assistant"></label>
        <div class="row2"><label>${t('role')}<select name="role">${roleOptions('operator')}</select></label><label>${t('apiTokenDays')}<input name="days" type="number" min="0" max="3650" value="0"><small class="muted">${t('apiTokenDaysHint')}</small></label></div>
        ${scopeFields({ all: true })}</div>`,
      onSubmit: async (data, form) => {
        const r = await api('/api/api-tokens', { method: 'POST', json: { name: data.name, role: data.role, days: Number(data.days || 0), scope: readScope(form) } });
        setTimeout(() => modal({ title: t('apiTokenCreated'), body: `<p>${t('apiTokenOnce')}</p><pre class="code">${esc(r.token)}</pre>` }), 50);
        render(true);
      },
    });
  },
  async apiTokenToggle(b) { await api('/api/api-tokens/' + b.dataset.tid, { method: 'PATCH', json: { enabled: b.checked } }); toast(t('saved')); },
  async apiTokenDelete(b) {
    if (!await confirmBox(t('apiTokenDeleteConfirm'))) return;
    await api('/api/api-tokens/' + b.dataset.tid, { method: 'DELETE' });
    render(true);
  },
  webhookEdit(b) {
    const h = b.dataset.hid ? S.hooks.webhooks.find(x => x.id === b.dataset.hid) : { name: '', url: '', events: ['device.offline', 'device.online'], enabled: true };
    modal({
      title: b.dataset.hid ? t('webhookEdit') : t('webhookNew'), wide: true,
      body: `<div class="form"><div class="row2"><label>${t('name')}<input name="name" value="${esc(h.name)}"></label><label>URL<input name="url" type="url" value="${esc(h.url)}" required placeholder="https://"></label></div>
        <span class="label">${t('webhookEvents')}</span><div class="check-grid">${S.hooks.events.map(e => `<label class="check"><input type="checkbox" name="ev:${e}" ${h.events.includes(e) ? 'checked' : ''}><span>${t('event_' + e.replace('.', '_'))}<br><small class="muted"><code>${e}</code></small></span></label>`).join('')}</div>
        <label class="check"><input type="checkbox" name="enabled" ${h.enabled ? 'checked' : ''}>${t('enabled')}</label>
        ${b.dataset.hid ? `<label class="check"><input type="checkbox" name="new_secret">${t('webhookNewSecret')}</label>` : ''}
        <p class="muted">${t('webhookSignHint')}</p></div>`,
      onSubmit: async data => {
        const json = { name: data.name, url: data.url, enabled: !!data.enabled, new_secret: !!data.new_secret, events: Object.keys(data).filter(k => k.startsWith('ev:')).map(k => k.slice(3)) };
        const r = await api(b.dataset.hid ? '/api/webhooks/' + b.dataset.hid : '/api/webhooks', { method: b.dataset.hid ? 'PATCH' : 'POST', json });
        if (r.secret) setTimeout(() => modal({ title: t('webhookSecret'), body: `<p>${t('webhookSecretOnce')}</p><pre class="code">${esc(r.secret)}</pre>` }), 50);
        render(true);
      },
    });
  },
  async webhookTest(b) {
    try { await api(`/api/webhooks/${b.dataset.hid}/test`, { method: 'POST' }); toast(t('webhookTestOk')); } catch (e) { toast(errText(e), 'err'); }
    render(true);
  },
  async webhookDelete(b) {
    if (!await confirmBox(t('webhookDeleteConfirm'))) return;
    await api('/api/webhooks/' + b.dataset.hid, { method: 'DELETE' });
    render(true);
  },
  async tenantEdit(b) {
    const x = b.dataset.tid ? (S.tenants || []).find(y => String(y.id) === b.dataset.tid) : { name: '', locations: [] };
    const taken = new Set((S.tenants || []).filter(y => y !== x).flatMap(y => y.locations));
    modal({
      title: b.dataset.tid ? t('tenantEdit') : t('tenantNew'),
      body: `<div class="form"><label>${t('name')}<input name="name" value="${esc(x.name)}" required></label>
        <span class="label">${t('tenantLocations')}</span><div class="check-grid">${orgNames('locations').map(l => `<label class="check"><input type="checkbox" name="loc:${esc(l)}" ${x.locations.includes(l) ? 'checked' : ''} ${taken.has(l) ? 'disabled' : ''}><span>${esc(l)}${taken.has(l) ? `<br><small class="muted">${t('tenantTaken')}</small>` : ''}</span></label>`).join('') || `<span class="muted">${t('tenantNoLocations')}</span>`}</div>
        <p class="muted">${t('tenantHint')}</p></div>`,
      onSubmit: async data => {
        const json = { name: data.name, locations: Object.keys(data).filter(k => k.startsWith('loc:')).map(k => k.slice(4)) };
        await api(b.dataset.tid ? '/api/tenants/' + b.dataset.tid : '/api/tenants', { method: b.dataset.tid ? 'PATCH' : 'POST', json });
        toast(t('saved'));
        render(true);
      },
    });
  },
  async tenantDelete(b) {
    if (!await confirmBox(t('tenantDeleteConfirm'))) return;
    await api('/api/tenants/' + b.dataset.tid, { method: 'DELETE' });
    render(true);
  },
});

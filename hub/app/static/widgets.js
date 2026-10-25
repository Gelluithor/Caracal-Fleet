// Widgets of CARACAL: a clock, the weather, news (RSS / Atom), a table (CSV, Google Sheets) and a text, set by the
// address (?type=clock&title=...). The same file is in CARACAL Fleet for its preview. Feeds from other sites are
// read through the node (/api/widgets/feed); in the preview of Fleet the page asks the window around it.
const Q = new URLSearchParams(location.search);
const LANG = Q.get('lang') === 'en' ? 'en' : 'cs';
const LOCALE = LANG === 'en' ? 'en-GB' : 'cs-CZ';
const W = document.getElementById('w');
const T = {
  cs: { free: 'Volno', busy: 'Obsazeno', until: 'do {t}', freeUntil: 'volno do {t}', freeAllDay: 'volno do konce dne', next: 'Dál dnes', nothing: 'Žádné další schůzky', noEvents: 'Žádné události', allDay: 'celý den', today: 'Dnes', tomorrow: 'Zítra', loading: 'Načítám…', failed: 'Nelze načíst', noCity: 'Zadejte město', cityNotFound: 'Město nebylo nalezeno', wind: 'vítr', feels: 'pocitově', humidity: 'vlhkost', noItems: 'Žádné zprávy', emptyTable: 'Tabulka je prázdná', page: 'strana' },
  en: { free: 'Free', busy: 'Busy', until: 'until {t}', freeUntil: 'free until {t}', freeAllDay: 'free for the rest of the day', next: 'Later today', nothing: 'No more meetings', noEvents: 'No events', allDay: 'all day', today: 'Today', tomorrow: 'Tomorrow', loading: 'Loading…', failed: 'Cannot load', noCity: 'Enter a city', cityNotFound: 'City not found', wind: 'wind', feels: 'feels like', humidity: 'humidity', noItems: 'No news', emptyTable: 'The table is empty', page: 'page' },
}[LANG];

if (Q.get('theme') === 'light') document.body.classList.add('light');
const accent = (Q.get('accent') || '').replace(/[^0-9a-f]/gi, '');
if (accent.length === 6 || accent.length === 3) document.documentElement.style.setProperty('--accent', '#' + accent);

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const titleHtml = () => (Q.get('title') ? `<div class="title">${esc(Q.get('title'))}</div>` : '');
const fail = e => { W.innerHTML = `${titleHtml()}<div class="error">${esc(T.failed)}<br><small>${esc(e && e.message || e)}</small></div>`; };

// ---------- feeds
let seq = 0;
const waiting = {};
window.addEventListener('message', e => {
  if (e.origin !== location.origin || !e.data || !waiting[e.data.caracalFeed]) return;
  const w = waiting[e.data.caracalFeed];
  delete waiting[e.data.caracalFeed];
  if (e.data.error) w.reject(new Error(e.data.error)); else w.resolve(e.data.text);
});
function feed(url) {
  if (Q.get('preview') && window.parent !== window) {
    return new Promise((resolve, reject) => {
      const id = ++seq;
      waiting[id] = { resolve, reject };
      window.parent.postMessage({ caracalFeed: id, url }, location.origin);
      setTimeout(() => { if (waiting[id]) { delete waiting[id]; reject(new Error('timeout')); } }, 25000);
    });
  }
  return fetch('/api/widgets/feed?url=' + encodeURIComponent(url), { cache: 'no-store' }).then(async r => {
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || 'HTTP ' + r.status);
    return d.text;
  });
}
// keep showing the last good content when a refresh fails
function every(ms, fn) {
  let shown = false;
  const run = () => fn().then(() => { shown = true; }).catch(e => { if (!shown) fail(e); });
  run();
  setInterval(run, ms);
}

// ---------- clock
function clock() {
  const tz = Q.get('tz') || undefined, seconds = Q.get('seconds') === '1';
  const draw = () => {
    const now = new Date();
    let hm, s;
    try {
      hm = now.toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit', timeZone: tz });
      s = now.toLocaleTimeString(LOCALE, { second: '2-digit', timeZone: tz }).padStart(2, '0');
    } catch { hm = now.toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit' }); s = String(now.getSeconds()).padStart(2, '0'); }
    let date;
    try { date = now.toLocaleDateString(LOCALE, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric', timeZone: tz }); }
    catch { date = now.toLocaleDateString(LOCALE, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }); }
    W.innerHTML = `${titleHtml()}<div class="clock"><div class="time">${esc(hm)}${seconds ? `<small>${esc(s)}</small>` : ''}</div><div class="date">${esc(date)}</div></div>`;
  };
  draw();
  setInterval(draw, 1000);
}

// ---------- weather (Open-Meteo, no key needed)
const WMO = [
  [[0], '☀️', 'Jasno', 'Clear'], [[1], '🌤️', 'Skoro jasno', 'Mainly clear'], [[2], '⛅', 'Polojasno', 'Partly cloudy'],
  [[3], '☁️', 'Zataženo', 'Overcast'], [[45, 48], '🌫️', 'Mlha', 'Fog'], [[51, 53, 55, 56, 57], '🌦️', 'Mrholení', 'Drizzle'],
  [[61, 63, 65, 66, 67], '🌧️', 'Déšť', 'Rain'], [[71, 73, 75, 77], '🌨️', 'Sněžení', 'Snow'],
  [[80, 81, 82], '🌦️', 'Přeháňky', 'Showers'], [[85, 86], '🌨️', 'Sněhové přeháňky', 'Snow showers'],
  [[95, 96, 99], '⛈️', 'Bouřka', 'Thunderstorm'],
];
const wmo = code => WMO.find(x => x[0].includes(code)) || [[], '🌡️', '', ''];
async function weather() {
  let lat = Number(Q.get('lat')), lon = Number(Q.get('lon')), place = Q.get('city') || '';
  if (!(lat || lon)) {
    if (!place) throw new Error(T.noCity);
    const g = JSON.parse(await feed(`https://geocoding-api.open-meteo.com/v1/search?count=1&format=json&language=${LANG}&name=${encodeURIComponent(place)}`));
    const hit = (g.results || [])[0];
    if (!hit) throw new Error(T.cityNotFound);
    lat = hit.latitude; lon = hit.longitude; place = hit.name;
  }
  const days = Math.min(6, Math.max(0, Number(Q.get('days') || 4)));
  const d = JSON.parse(await feed(`https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&timezone=auto&forecast_days=${days + 1}`
    + '&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m'
    + '&daily=weather_code,temperature_2m_max,temperature_2m_min'));
  const c = d.current, w = wmo(c.weather_code);
  const daily = d.daily.time.slice(1, days + 1).map((day, i) => {
    const x = wmo(d.daily.weather_code[i + 1]);
    const name = new Date(day + 'T12:00').toLocaleDateString(LOCALE, { weekday: 'short' });
    return `<div class="wx-day"><b>${esc(name)}</b><span>${x[1]}</span><i>${Math.round(d.daily.temperature_2m_max[i + 1])}° <em>${Math.round(d.daily.temperature_2m_min[i + 1])}°</em></i></div>`;
  }).join('');
  W.innerHTML = `<div class="title">${esc(Q.get('title') || place)}</div>
    <div class="wx-now"><div class="wx-icon">${w[1]}</div><div><div class="wx-temp">${Math.round(c.temperature_2m)}°</div>
    <div class="wx-text">${esc(w[LANG === 'en' ? 3 : 2])}</div>
    <div class="wx-meta">${T.feels} ${Math.round(c.apparent_temperature)}° · ${T.wind} ${Math.round(c.wind_speed_10m)} km/h · ${T.humidity} ${Math.round(c.relative_humidity_2m)} %</div></div></div>
    ${daily ? `<div class="wx-days">${daily}</div>` : ''}`;
}

// ---------- news (RSS 2.0, Atom)
const plain = html => { const d = document.createElement('div'); d.innerHTML = html || ''; return (d.textContent || '').replace(/\s+/g, ' ').trim(); };
function when(s) {
  const t = new Date(s);
  if (isNaN(t)) return '';
  const today = new Date().toDateString() === t.toDateString();
  return today ? t.toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit' }) : t.toLocaleDateString(LOCALE, { day: 'numeric', month: 'numeric' }) + ' ' + t.toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit' });
}
async function news() {
  const url = Q.get('url');
  if (!url) throw new Error('URL');
  const xml = new DOMParser().parseFromString(await feed(url), 'text/xml');
  const nodes = [...xml.querySelectorAll('item')].length ? [...xml.querySelectorAll('item')] : [...xml.querySelectorAll('entry')];
  const pick = (n, ...sel) => { for (const s of sel) { const e = n.getElementsByTagName(s)[0]; if (e && e.textContent.trim()) return e.textContent.trim(); } return ''; };
  const image = n => {
    for (const tag of ['media:content', 'media:thumbnail', 'enclosure']) {
      const e = [...n.getElementsByTagName(tag)].find(x => /^https:/.test(x.getAttribute('url') || '') && !/video|audio/.test(x.getAttribute('type') || ''));
      if (e) return e.getAttribute('url');
    }
    const m = /<img[^>]+src="(https:[^"]+)"/i.exec(pick(n, 'description', 'content:encoded', 'content', 'summary'));
    return m ? m[1] : '';
  };
  const items = nodes.slice(0, Math.min(12, Math.max(1, Number(Q.get('count') || 6)))).map(n => ({
    title: plain(pick(n, 'title')), text: plain(pick(n, 'description', 'summary', 'content')), time: when(pick(n, 'pubDate', 'updated', 'published', 'dc:date')), img: image(n),
  }));
  const channel = plain(pick(xml.documentElement, 'title'));
  if (!items.length) { W.innerHTML = `${titleHtml()}<div class="error">${T.noItems}</div>`; return; }
  const [lead, ...rest] = items;
  W.innerHTML = `<div class="title">${esc(Q.get('title') || channel)}</div>
    <div class="news ${rest.length ? '' : 'noimg'}"><div class="lead">${lead.img ? `<img src="${esc(lead.img)}" alt="">` : ''}<h2>${esc(lead.title)}</h2><p>${esc(lead.text)}</p><small>${esc(lead.time)}</small></div>
    ${rest.length ? `<ul class="more">${rest.map(x => `<li>${esc(x.title)}<small>${esc(x.time)}</small></li>`).join('')}</ul>` : ''}</div>`;
}

// ---------- table (CSV, a Google Sheets link)
function csvUrl(url) {
  const m = /docs\.google\.com\/spreadsheets\/d\/([\w-]+)/.exec(url);
  if (!m || /output=csv|format=csv/.test(url)) return url;
  const gid = (/[#&?]gid=(\d+)/.exec(url) || [])[1] || '0';
  return `https://docs.google.com/spreadsheets/d/${m[1]}/export?format=csv&gid=${gid}`;
}
function parseCsv(text) {
  const rows = [];
  let row = [], cell = '', q = false;
  const sep = (text.split('\n')[0].match(/;/g) || []).length > (text.split('\n')[0].match(/,/g) || []).length ? ';' : ',';
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (q) {
      if (ch === '"' && text[i + 1] === '"') { cell += '"'; i++; } else if (ch === '"') q = false; else cell += ch;
    } else if (ch === '"') q = true;
    else if (ch === sep) { row.push(cell); cell = ''; }
    else if (ch === '\n' || ch === '\r') { if (ch === '\r' && text[i + 1] === '\n') i++; row.push(cell); rows.push(row); row = []; cell = ''; }
    else cell += ch;
  }
  if (cell || row.length) { row.push(cell); rows.push(row); }
  return rows.filter(r => r.some(c => c.trim()));
}
let page = 0;
async function table() {
  const url = Q.get('url');
  if (!url) throw new Error('URL');
  const rows = parseCsv(await feed(csvUrl(url)));
  if (!rows.length) { W.innerHTML = `${titleHtml()}<div class="error">${T.emptyTable}</div>`; return; }
  const [head, ...body] = rows;
  const per = Math.min(30, Math.max(3, Number(Q.get('rows') || 10)));
  const pages = Math.max(1, Math.ceil(body.length / per));
  const draw = () => {
    page %= pages;
    const part = body.slice(page * per, page * per + per);
    W.innerHTML = `${titleHtml()}<table><thead><tr>${head.map(h => `<th>${esc(h)}</th>`).join('')}</tr></thead>
      <tbody>${part.map(r => `<tr>${head.map((_, i) => `<td>${esc(r[i] || '')}</td>`).join('')}</tr>`).join('')}</tbody></table>
      ${pages > 1 ? `<div class="pager">${T.page} ${page + 1} / ${pages}</div>` : ''}`;
  };
  draw();
  clearInterval(table.timer);
  if (pages > 1) table.timer = setInterval(() => { page++; draw(); }, Math.max(4, Number(Q.get('page_seconds') || 10)) * 1000);
}

// ---------- text
function text() {
  const center = Q.get('align') === 'center';
  W.innerHTML = `<div class="text ${center ? 'center' : ''}"><div class="bar"></div>${Q.get('title') ? `<h1>${esc(Q.get('title'))}</h1>` : ''}<p>${esc(Q.get('text') || '')}</p></div>`;
}

// ---------- QR code (drawn by the node / Fleet, works offline)
function qr() {
  const caption = Q.get('text') || '';
  W.innerHTML = `<div class="qr">${Q.get('title') ? `<h1>${esc(Q.get('title'))}</h1>` : ''}<img src="/api/widgets/qr?text=${encodeURIComponent(Q.get('url') || '')}" alt="">${caption ? `<p>${esc(caption)}</p>` : ''}</div>`;
}

// ---------- calendars (iCalendar / ICS: Microsoft 365, Google, Exchange, Nextcloud…)
const WINDOWS_TZ = { 'Central Europe Standard Time': 'Europe/Prague', 'Central European Standard Time': 'Europe/Warsaw', 'W. Europe Standard Time': 'Europe/Berlin',
  'Romance Standard Time': 'Europe/Paris', 'GMT Standard Time': 'Europe/London', 'E. Europe Standard Time': 'Europe/Bucharest', 'FLE Standard Time': 'Europe/Kiev',
  'Eastern Standard Time': 'America/New_York', 'Central Standard Time': 'America/Chicago', 'Pacific Standard Time': 'America/Los_Angeles', UTC: 'UTC' };
function tzOffset(ms, tz) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-US', { timeZone: tz, hour12: false, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' })
    .formatToParts(new Date(ms)).map(x => [x.type, x.value]));
  return Date.UTC(+parts.year, +parts.month - 1, +parts.day, +parts.hour % 24, +parts.minute, +parts.second) - ms;
}
function icsDate(value, params) {
  const m = /^(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})(Z?))?/.exec(value || '');
  if (!m) return null;
  const [, y, mo, d, h, mi, se, z] = m;
  if (h === undefined) return { ms: new Date(+y, mo - 1, +d).getTime(), allDay: true };
  if (z) return { ms: Date.UTC(+y, mo - 1, +d, +h, +mi, +se), allDay: false };
  const tz = params.TZID && (WINDOWS_TZ[params.TZID] || params.TZID.replace(/^"|"$/g, ''));
  if (tz) {
    try { const guess = Date.UTC(+y, mo - 1, +d, +h, +mi, +se); return { ms: guess - tzOffset(guess, tz), allDay: false }; } catch { /* an unknown zone: local time */ }
  }
  return { ms: new Date(+y, mo - 1, +d, +h, +mi, +se).getTime(), allDay: false };
}
function parseIcs(text, from, to) {
  const lines = text.replace(/\r?\n[ \t]/g, '').split(/\r?\n/);
  const events = [];
  let ev = null;
  for (const line of lines) {
    if (line === 'BEGIN:VEVENT') { ev = { exdates: [] }; continue; }
    if (line === 'END:VEVENT') { if (ev) events.push(ev); ev = null; continue; }
    if (!ev) continue;
    const i = line.indexOf(':');
    if (i < 0) continue;
    const [name, ...rest] = line.slice(0, i).split(';');
    const params = Object.fromEntries(rest.map(x => x.split('=')));
    const value = line.slice(i + 1);
    if (name === 'SUMMARY') ev.title = value.replace(/\\([,;\\])/g, '$1').replace(/\\n/gi, ' ');
    else if (name === 'LOCATION') ev.where = value.replace(/\\([,;\\])/g, '$1');
    else if (name === 'DTSTART') ev.start = icsDate(value, params);
    else if (name === 'DTEND') ev.end = icsDate(value, params);
    else if (name === 'DURATION') ev.duration = value;
    else if (name === 'RRULE') ev.rrule = Object.fromEntries(value.split(';').map(x => x.split('=')));
    else if (name === 'EXDATE') value.split(',').forEach(v => { const x = icsDate(v, params); if (x) ev.exdates.push(x.ms); });
    else if (name === 'STATUS') ev.status = value;
    else if (name === 'RECURRENCE-ID') ev.recurrence = icsDate(value, params);
  }
  const dur = ev => {
    if (ev.end) return ev.end.ms - ev.start.ms;
    const m = /P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?/.exec(ev.duration || '');
    return m ? ((+m[1] || 0) * 86400 + (+m[2] || 0) * 3600 + (+m[3] || 0) * 60) * 1000 : (ev.start.allDay ? 86400000 : 3600000);
  };
  const moved = new Set(events.filter(e => e.recurrence).map(e => e.title + '|' + e.recurrence.ms));
  const out = [];
  const DAYS = ['SU', 'MO', 'TU', 'WE', 'TH', 'FR', 'SA'];
  for (const e of events) {
    if (!e.start || e.status === 'CANCELLED') continue;
    const len = dur(e);
    const push = ms => { if (ms + len > from && ms < to && !e.exdates.includes(ms) && (e.recurrence || !moved.has(e.title + '|' + ms))) out.push({ title: e.title || '', where: e.where || '', start: ms, end: ms + len, allDay: e.start.allDay }); };
    const r = e.rrule;
    if (!r) { push(e.start.ms); continue; }
    const until = r.UNTIL ? icsDate(r.UNTIL, {}).ms : Infinity, count = r.COUNT ? +r.COUNT : Infinity, step = +(r.INTERVAL || 1);
    const byday = (r.BYDAY || '').split(',').filter(Boolean).map(x => DAYS.indexOf(x.slice(-2)));
    let n = 0;
    const base = new Date(e.start.ms);
    for (let k = 0; k < 2000 && n < count; k++) {
      let dates = [];
      const t = new Date(base);
      if (r.FREQ === 'DAILY') { t.setDate(base.getDate() + k * step); dates = [t]; }
      else if (r.FREQ === 'WEEKLY') {
        const week = new Date(base); week.setDate(base.getDate() - base.getDay() + k * 7 * step);
        dates = (byday.length ? byday : [base.getDay()]).sort().map(dw => { const x = new Date(week); x.setDate(week.getDate() + dw); return x; }).filter(x => x >= base);
      } else if (r.FREQ === 'MONTHLY') { t.setMonth(base.getMonth() + k * step); dates = [t]; }
      else if (r.FREQ === 'YEARLY') { t.setFullYear(base.getFullYear() + k * step); dates = [t]; }
      else break;
      for (const x of dates) {
        if (x.getTime() > until || n >= count) { k = 1e9; break; }
        n++;
        push(x.getTime());
      }
      if (dates.length && dates[0].getTime() > to) break;
    }
  }
  return out.sort((a, b) => a.start - b.start);
}
const hm = ms => new Date(ms).toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit' });
async function calendar() {
  const url = Q.get('url');
  if (!url) throw new Error('URL');
  const days = Math.min(14, Math.max(1, Number(Q.get('days') || 3)));
  const start = new Date(); start.setHours(0, 0, 0, 0);
  const events = parseIcs(await feed(url), Date.now(), start.getTime() + days * 86400000).slice(0, 40);
  const groups = {};
  for (const e of events) {
    const d = new Date(Math.max(e.start, Date.now())); d.setHours(0, 0, 0, 0);
    (groups[d.getTime()] = groups[d.getTime()] || []).push(e);
  }
  const label = ms => { const diff = Math.round((ms - start.getTime()) / 86400000); return diff === 0 ? T.today : diff === 1 ? T.tomorrow : new Date(ms).toLocaleDateString(LOCALE, { weekday: 'long', day: 'numeric', month: 'numeric' }); };
  W.innerHTML = `${titleHtml()}<div class="agenda">${Object.entries(groups).map(([day, list]) => `<section><h2>${esc(label(+day))}</h2>
    ${list.map(e => `<div class="ev"><span class="ev-time">${e.allDay ? T.allDay : hm(e.start) + '–' + hm(e.end)}</span><span class="ev-title">${esc(e.title)}${e.where ? `<small>${esc(e.where)}</small>` : ''}</span></div>`).join('')}</section>`).join('')
    || `<div class="error">${T.noEvents}</div>`}</div>`;
}
async function room() {
  const url = Q.get('url');
  if (!url) throw new Error('URL');
  const end = new Date(); end.setHours(23, 59, 59, 999);
  const now = Date.now();
  const events = parseIcs(await feed(url), now - 12 * 3600000, end.getTime()).filter(e => !e.allDay && e.end > now);
  const cur = events.find(e => e.start <= now && now < e.end), next = events.filter(e => e.start > now);
  W.innerHTML = `<div class="room ${cur ? 'busy' : 'free'}"><div class="room-name">${esc(Q.get('title') || '')}</div>
    <div class="room-state">${cur ? T.busy : T.free}</div>
    <div class="room-now">${cur ? `${esc(cur.title)} · ${T.until.replace('{t}', hm(cur.end))}` : next.length ? T.freeUntil.replace('{t}', hm(next[0].start)) : T.freeAllDay}</div>
    <div class="room-next"><h2>${T.next}</h2>${next.slice(0, 4).map(e => `<div class="ev"><span class="ev-time">${hm(e.start)}–${hm(e.end)}</span><span class="ev-title">${esc(e.title)}</span></div>`).join('') || `<p>${T.nothing}</p>`}</div></div>`;
}

// ---------- ticker: a line of news or own text running across a narrow zone
async function ticker() {
  let items = (Q.get('text') || '').split('\n').map(x => x.trim()).filter(Boolean);
  if (Q.get('url')) {
    const xml = new DOMParser().parseFromString(await feed(Q.get('url')), 'text/xml');
    const nodes = [...xml.querySelectorAll('item')].length ? [...xml.querySelectorAll('item')] : [...xml.querySelectorAll('entry')];
    items = items.concat(nodes.slice(0, 20).map(n => plain((n.getElementsByTagName('title')[0] || {}).textContent || '')).filter(Boolean));
  }
  const speed = Math.min(400, Math.max(30, Number(Q.get('speed') || 120)));   // pixels per second
  const label = Q.get('title') ? `<b class="tk-label">${esc(Q.get('title'))}</b>` : '';
  const line = items.map(x => `<span>${esc(x)}</span>`).join('<i>•</i>');
  W.className = 'tk-host';
  W.innerHTML = `${label}<div class="tk"><div class="tk-run">${line}<i>•</i>${line}<i>•</i></div></div>`;
  const run = W.querySelector('.tk-run');
  requestAnimationFrame(() => { run.style.animationDuration = (run.scrollWidth / 2 / speed) + 's'; });
}

const type = Q.get('type');
W.innerHTML = `<div class="error">${T.loading}</div>`;
if (type === 'clock') clock();
else if (type === 'weather') every(15 * 60000, weather);
else if (type === 'rss') every(10 * 60000, news);
else if (type === 'table') every(5 * 60000, table);
else if (type === 'qr') qr();
else if (type === 'calendar') every(5 * 60000, calendar);
else if (type === 'room') every(60000, room);
else if (type === 'ticker') every(10 * 60000, ticker);
else text();

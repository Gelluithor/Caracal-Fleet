// Widgets of CARACAL: a clock, the weather, news (RSS / Atom), a table (CSV, Google Sheets) and a text, set by the
// address (?type=clock&title=...). The same file is in CARACAL Fleet for its preview. Feeds from other sites are
// read through the node (/api/widgets/feed); in the preview of Fleet the page asks the window around it.
const Q = new URLSearchParams(location.search);
const LANG = Q.get('lang') === 'en' ? 'en' : 'cs';
const LOCALE = LANG === 'en' ? 'en-GB' : 'cs-CZ';
const W = document.getElementById('w');
const T = {
  cs: { loading: 'Načítám…', failed: 'Nelze načíst', noCity: 'Zadejte město', cityNotFound: 'Město nebylo nalezeno', wind: 'vítr', feels: 'pocitově', humidity: 'vlhkost', noItems: 'Žádné zprávy', emptyTable: 'Tabulka je prázdná', page: 'strana' },
  en: { loading: 'Loading…', failed: 'Cannot load', noCity: 'Enter a city', cityNotFound: 'City not found', wind: 'wind', feels: 'feels like', humidity: 'humidity', noItems: 'No news', emptyTable: 'The table is empty', page: 'page' },
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

const type = Q.get('type');
W.innerHTML = `<div class="error">${T.loading}</div>`;
if (type === 'clock') clock();
else if (type === 'weather') every(15 * 60000, weather);
else if (type === 'rss') every(10 * 60000, news);
else if (type === 'table') every(5 * 60000, table);
else text();

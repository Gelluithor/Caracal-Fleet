/* Editor of the look of on-screen notifications. The same file is used by the CARACAL node admin UI and by
   CARACAL Fleet. The overlay on the TV (player/overlay.py) draws a notification with Tk; the preview follows its
   layout and sizes on a 1920x1080 screen, so what you see here is what the TV shows (fonts may differ slightly when
   DejaVu is not installed on this computer).
   NotifyStyle.editor(element, style, {t, position, scale, onChange}) -> {get(), set(style)} */
(function () {
  'use strict';
  const LEVELS = ['info', 'success', 'warning', 'critical'];
  const DEFAULT = {
    bg: '#111926', title: '#f8fafc', text: '#cbd5e1', muted: '#94a3b8', fill: 'stripe', stripe: 100,
    font: 'DejaVu Sans', bold: true, opacity: 96, width: 0, align: 'left',
    icon: true, source: true, waiting: true, progress: true, animation: 'slide', speed: 250,
    levels: { info: { color: '#3b82f6', icon: 'ℹ' }, success: { color: '#22c55e', icon: '✓' },
      warning: { color: '#f59e0b', icon: '⚠' }, critical: { color: '#ef4444', icon: '✖' } },
  };
  const clone = o => JSON.parse(JSON.stringify(o));
  // Every value is checked before it is used: the look comes from the device (in Fleet from what the node
  // reports), and it ends up in style attributes, so only known choices, #RRGGBB colours and bounded numbers pass.
  const CHOICES = { fill: ['stripe', 'solid', 'border'], font: ['DejaVu Sans', 'DejaVu Serif', 'DejaVu Sans Mono'], align: ['left', 'center'], animation: ['slide', 'fade', 'none'] };
  const RANGES = { stripe: [0, 300], opacity: [50, 100], width: [0, 100], speed: [100, 1500] };
  const COLOR = /^#[0-9a-fA-F]{6}$/;
  const pick = (k, v, fallback) => {
    if (k in CHOICES) return CHOICES[k].includes(v) ? v : fallback;
    if (k in RANGES) { const n = Math.round(Number(v)); return Number.isFinite(n) ? Math.min(RANGES[k][1], Math.max(RANGES[k][0], n)) : fallback; }
    if (typeof fallback === 'boolean') return typeof v === 'boolean' ? v : fallback;
    if (typeof v === 'string' && COLOR.test(fallback)) return COLOR.test(v) ? v.toLowerCase() : fallback;
    return fallback;
  };
  const merge = (base, d) => {
    const o = clone(base);
    for (const [k, v] of Object.entries(d && typeof d === 'object' ? d : {})) {
      if (k === 'levels' && v && typeof v === 'object') {
        for (const l of LEVELS) {
          const x = v[l] && typeof v[l] === 'object' ? v[l] : {};
          if (typeof x.color === 'string' && COLOR.test(x.color)) o.levels[l].color = x.color.toLowerCase();
          if (typeof x.icon === 'string') o.levels[l].icon = [...x.icon].slice(0, 3).join('');
        }
      } else if (k in o && k !== 'levels') o[k] = pick(k, v, o[k]);
    }
    return o;
  };
  // a few starting points; every value can be changed afterwards
  const PRESETS = {
    caracal: ['CARACAL', 'CARACAL', {}],
    light: ['Světlý', 'Light', { bg: '#ffffff', title: '#111827', text: '#374151', muted: '#6b7280', opacity: 98 }],
    vivid: ['Výrazný', 'Vivid', { fill: 'solid', opacity: 100, animation: 'slide' }],
    minimal: ['Minimalistický', 'Minimal', { bg: '#0b0f14', fill: 'border', stripe: 60, source: false, waiting: false, progress: false, bold: false, opacity: 90, animation: 'fade', speed: 400 }],
    contrast: ['Vysoký kontrast', 'High contrast', { bg: '#000000', title: '#ffff00', text: '#ffffff', muted: '#ffff00', stripe: 250, opacity: 100, animation: 'none',
      levels: { info: { color: '#00e5ff' }, success: { color: '#00ff66' }, warning: { color: '#ffd400' }, critical: { color: '#ff2a2a' } } }],
    banner: ['Banner přes celou šířku', 'Full-width banner', { fill: 'solid', width: 100, align: 'center', progress: true, waiting: false, opacity: 100, speed: 350 }],
  };
  const FONTS = { 'DejaVu Sans': "'DejaVu Sans', Verdana, sans-serif", 'DejaVu Serif': "'DejaVu Serif', Georgia, serif", 'DejaVu Sans Mono': "'DejaVu Sans Mono', Menlo, monospace" };
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  // text on a coloured background: white or near black, whichever reads better (the overlay does the same)
  const onColor = hex => { const n = parseInt(hex.slice(1), 16), r = n >> 16, g = (n >> 8) & 255, b = n & 255; return (r * 299 + g * 587 + b * 114) / 1000 > 160 ? '#111111' : '#ffffff'; };

  const CSS = `
.nse{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.15fr);gap:22px;align-items:start}
.nse h4{margin:16px 0 8px;font-size:12.5px;font-weight:650;opacity:.65}.nse h4:first-child{margin-top:0}
.nse-row{display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px;margin-bottom:8px}
.nse label{display:flex;align-items:center;gap:8px;font-size:13.5px;font-weight:500;margin:0}
.nse select,.nse input[type=text],.nse input[type=number]{width:auto;height:32px;padding:4px 9px;border:1px solid color-mix(in srgb,currentColor 22%,transparent);border-radius:8px;background:transparent;color:inherit;font:inherit;font-size:13.5px}
.nse select option{color:#111;background:#fff}
.nse input[type=color]{width:34px;height:28px;padding:0;border:1px solid color-mix(in srgb,currentColor 22%,transparent);border-radius:7px;background:none;cursor:pointer}
.nse input[type=color]::-webkit-color-swatch-wrapper{padding:2px}.nse input[type=color]::-webkit-color-swatch{border:0;border-radius:5px}
.nse input[type=range]{width:130px;accent-color:var(--accent,#e85d3f)}
.nse input[type=checkbox]{width:16px;height:16px;accent-color:var(--accent,#e85d3f)}
.nse-presets{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
.nse-preset{display:grid;gap:6px;padding:8px;border:1px solid color-mix(in srgb,currentColor 15%,transparent);border-radius:10px;background:transparent;color:inherit;font:inherit;font-size:12.5px;font-weight:500;cursor:pointer;text-align:left}
.nse-preset:hover{border-color:var(--accent,#e85d3f)}
.nse-preset i{display:flex;height:22px;border-radius:4px;overflow:hidden}
.nse-preset i b{flex:none;width:5px}.nse-preset i s{flex:1;margin:6px 8px;border-radius:2px;opacity:.85;text-decoration:none}
.nse-levels{display:grid;grid-template-columns:auto auto 1fr;gap:6px 10px;align-items:center}
.nse-levels input[type=text]{width:52px;text-align:center}
.nse-seg{display:inline-flex;padding:2px;border-radius:9px;background:color-mix(in srgb,currentColor 9%,transparent)}
.nse-seg button{border:0;background:none;color:inherit;font:inherit;font-size:12.5px;font-weight:500;padding:4px 10px;border-radius:7px;cursor:pointer;opacity:.75}
.nse-seg button.on{background:var(--nse-on,#fff);color:#111;opacity:1;box-shadow:0 1px 2px #0002}
.nse-side{position:sticky;top:12px;display:grid;gap:10px}
.nse-tv{position:relative;aspect-ratio:16/9;border:6px solid #050607;border-radius:12px;overflow:hidden;background:#0f172a}
.nse-stage{position:absolute;left:0;top:0;width:1920px;height:1080px;transform-origin:0 0;background:radial-gradient(1200px 700px at 20% 10%,#24324a,#0b1220 70%)}
.nse-stage .nse-fake{position:absolute;border-radius:24px;background:#ffffff0d}
.nse-toast{position:absolute;display:flex;overflow:hidden;box-shadow:0 18px 40px #0006}
.nse-toast .nse-stripe{flex:none}
.nse-body{flex:1;display:flex;flex-direction:column;min-width:0}
.nse-head{display:flex;justify-content:space-between;gap:12px;font-weight:700;white-space:nowrap}
.nse-title,.nse-msg{white-space:pre-wrap;overflow-wrap:anywhere}
.nse-bar{margin-top:auto}.nse-bar i{display:block;height:100%}
.nse-play{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}
@keyframes nse-in{from{opacity:0;transform:var(--from)}}
.nse-anim{animation:nse-in var(--speed) cubic-bezier(.2,.8,.2,1) both}
@media (max-width:900px){.nse{grid-template-columns:minmax(0,1fr)}.nse-side{position:static}}`;
  function injectCss() {
    if (document.getElementById('nse-css')) return;
    const el = document.createElement('style');
    el.id = 'nse-css';
    el.textContent = CSS;
    document.head.append(el);
  }

  // The toast as overlay.py draws it on a 1920x1080 screen.
  function toastHtml(s, o) {
    const sw = 1920, sh = 1080, scale = Math.max(.5, Math.min(3, (o.scale || 100) / 100)), pos = o.position || 'top-right';
    const title = Math.max(14, Math.floor(sh / 38 * scale)), text = Math.max(11, Math.floor(sh / 54 * scale)), small = Math.max(10, Math.floor(sh / 72 * scale)), pad = Math.max(10, Math.floor(sh / 70 * scale));
    const banner = s.width >= 100;
    const w = s.width ? Math.floor(sw * s.width / 100) : Math.floor(Math.min(sw * .9, Math.max(320, sw * (['top', 'bottom', 'center'].includes(pos) ? .42 : .32) * scale)));
    const m = banner ? 0 : Math.floor(Math.min(sw, sh) * .035);
    const lv = s.levels[o.level] || s.levels.info, color = lv.color;
    const solid = s.fill === 'solid', border = s.fill === 'border';
    const fg = solid ? onColor(color) : s.title, fg2 = solid ? onColor(color) : s.text, fgm = solid ? onColor(color) : s.muted, bg = solid ? color : s.bg;
    const stripeW = s.fill === 'stripe' ? Math.floor(Math.max(6, pad * .6) * s.stripe / 100) : 0;
    const bw = border ? Math.max(2, Math.floor(pad * .3 * Math.max(s.stripe, 30) / 100)) : 0;
    const font = FONTS[s.font] || FONTS['DejaVu Sans'], center = s.align === 'center';
    const x = pos.endsWith('right') ? sw - w - m : pos.endsWith('left') ? m : (sw - w) / 2;
    const yTop = pos.startsWith('top') ? m : null, yBottom = pos.startsWith('bottom') ? m + (o.bar || 8) : null;
    const from = s.animation === 'fade' ? 'none' : pos.endsWith('right') ? `translateX(${Math.max(30, w * .15)}px)` : pos.endsWith('left') ? `translateX(-${Math.max(30, w * .15)}px)` : pos.startsWith('top') ? `translateY(-${Math.max(30, w * .15)}px)` : pos.startsWith('bottom') ? `translateY(${Math.max(30, w * .15)}px)` : 'none';
    const place = `left:${x}px;${yTop != null ? `top:${yTop}px;` : yBottom != null ? `bottom:${yBottom}px;` : 'top:50%;margin-top:-90px;'}`;
    const head = s.icon || s.source || s.waiting;
    const bar = Math.max(3, Math.floor(pad * .35)), track = solid ? 'rgba(0,0,0,.22)' : 'color-mix(in srgb,' + s.muted + ' 25%,' + s.bg + ')';
    return `<div class="nse-toast ${o.animate && s.animation !== 'none' ? 'nse-anim' : ''}" style="${place}width:${w}px;background:${border ? color : bg};opacity:${s.opacity / 100};font-family:${font};--from:${from};--speed:${s.speed}ms;${border ? `padding:${bw}px;` : ''}${banner ? 'box-shadow:none;' : ''}">
      ${stripeW ? `<div class="nse-stripe" style="width:${stripeW}px;background:${color}"></div>` : ''}
      <div class="nse-body" style="background:${bg}">
        ${head ? `<div class="nse-head" style="padding:${pad}px ${pad}px ${Math.floor(pad * .3)}px;font-size:${small}px;${center ? 'justify-content:center;' : ''}"><span style="color:${solid ? fg : color}">${s.icon ? esc(lv.icon) + '&nbsp;&nbsp;' : ''}${s.source ? esc(o.source || 'Grafana') : ''}</span>${s.waiting && !center ? `<span style="color:${fgm}">+2</span>` : ''}</div>` : ''}
        <div class="nse-title" style="padding:${head ? 0 : pad}px ${pad}px ${Math.floor(pad * .4)}px;font-size:${title}px;font-weight:${s.bold ? 700 : 400};color:${fg};text-align:${center ? 'center' : 'left'}">${esc(o.title)}</div>
        <div class="nse-msg" style="padding:0 ${pad}px ${pad}px;font-size:${text}px;color:${fg2};text-align:${center ? 'center' : 'left'}">${esc(o.message)}</div>
        ${s.progress ? `<div class="nse-bar" style="height:${bar}px;background:${track}"><i style="width:62%;background:${solid ? fg : color}"></i></div>` : ''}
      </div></div>`;
  }

  function editor(el, style, opts = {}) {
    injectCss();
    const T = opts.t || ((cs, en) => en);
    let s = merge(DEFAULT, style || {});
    let level = 'warning';
    const SAMPLE = {
      info: [T('Nová verze dashboardu', 'New dashboard version'), T('Zobrazí se po další položce playlistu.', 'It shows after the next playlist item.'), 'CARACAL'],
      success: [T('Záloha dokončena', 'Backup finished'), T('Všechny servery jsou zálohované.', 'All servers are backed up.'), T('Zálohy', 'Backups')],
      warning: [T('Vysoké vytížení linky 3', 'High load on line 3'), T('Teplota motoru 78 °C, zkontrolujte chlazení.', 'Motor temperature 78 °C, check the cooling.'), 'Grafana'],
      critical: [T('Výpadek serveru ERP', 'ERP server down'), T('Pracujeme na opravě, odhad 20 minut.', 'We are working on it, about 20 minutes.'), 'Zabbix'],
    };
    const seg = (key, items) => `<span class="nse-seg" data-seg="${key}">${items.map(([v, cs, en]) => `<button type="button" data-v="${v}" class="${String(s[key]) === String(v) ? 'on' : ''}">${T(cs, en)}</button>`).join('')}</span>`;
    const color = (key, cs, en) => `<label><input type="color" data-k="${key}" value="${s[key]}">${T(cs, en)}</label>`;
    const check = (key, cs, en) => `<label><input type="checkbox" data-k="${key}" ${s[key] ? 'checked' : ''}>${T(cs, en)}</label>`;
    const LV = { info: ['Informace', 'Information'], success: ['V pořádku', 'OK'], warning: ['Varování', 'Warning'], critical: ['Kritické', 'Critical'] };
    function controls() {
      return `<div class="nse-controls">
        <h4>${T('Předvolby', 'Presets')}</h4><div class="nse-presets">${Object.entries(PRESETS).map(([k, [cs, en, p]]) => { const x = merge(DEFAULT, p), c = x.levels.warning.color, solid = x.fill === 'solid';
          return `<button type="button" class="nse-preset" data-preset="${k}"><i style="background:${solid ? c : x.bg};${x.fill === 'border' ? `box-shadow:inset 0 0 0 2px ${c};` : ''}"><b style="background:${x.fill === 'stripe' ? c : 'transparent'}"></b><s style="background:${solid ? onColor(c) : x.title}"></s></i>${T(cs, en)}</button>`; }).join('')}</div>
        <h4>${T('Barvy', 'Colours')}</h4><div class="nse-row">${color('bg', 'Pozadí', 'Background')}${color('title', 'Nadpis', 'Title')}${color('text', 'Text', 'Text')}${color('muted', 'Doplňky', 'Details')}</div>
        <h4>${T('Úrovně – barva a ikona', 'Levels – colour and icon')}</h4><div class="nse-levels">${LEVELS.map(l => `<input type="color" data-level="${l}" data-f="color" value="${s.levels[l].color}"><input type="text" maxlength="3" data-level="${l}" data-f="icon" value="${esc(s.levels[l].icon)}" title="${T('Ikona: až 3 znaky, např. ⚠ ✖ ✓ ℹ ★ ●', 'Icon: up to 3 characters, e.g. ⚠ ✖ ✓ ℹ ★ ●')}"><span>${T(...LV[l])}</span>`).join('')}</div>
        <h4>${T('Tvar', 'Shape')}</h4>
        <div class="nse-row">${seg('fill', [['stripe', 'Pruh', 'Stripe'], ['solid', 'Plná barva', 'Solid'], ['border', 'Rámeček', 'Border']])}</div>
        <div class="nse-row"><label>${T('Tloušťka pruhu / rámečku', 'Stripe / border thickness')}<input type="range" min="0" max="300" step="10" data-k="stripe" value="${s.stripe}"></label></div>
        <div class="nse-row"><label>${T('Šířka', 'Width')}<select data-k="width">${[[0, 'Automaticky', 'Automatic'], [30, '30 %', '30 %'], [40, '40 %', '40 %'], [50, '50 %', '50 %'], [60, '60 %', '60 %'], [80, '80 %', '80 %'], [100, 'Celá šířka (banner)', 'Full width (banner)']].map(([v, cs, en]) => `<option value="${v}" ${s.width === v ? 'selected' : ''}>${T(cs, en)}</option>`).join('')}</select></label>
          ${seg('align', [['left', 'Vlevo', 'Left'], ['center', 'Na střed', 'Centre']])}</div>
        <div class="nse-row"><label>${T('Krytí', 'Opacity')}<input type="range" min="50" max="100" step="2" data-k="opacity" value="${s.opacity}"></label></div>
        <h4>${T('Písmo', 'Font')}</h4><div class="nse-row"><select data-k="font">${Object.keys(FONTS).map(f => `<option ${s.font === f ? 'selected' : ''}>${f}</option>`).join('')}</select>${check('bold', 'Tučný nadpis', 'Bold title')}</div>
        <h4>${T('Co zobrazit', 'What to show')}</h4><div class="nse-row">${check('icon', 'Ikona', 'Icon')}${check('source', 'Odesílatel', 'Sender')}${check('waiting', 'Počet čekajících', 'Waiting count')}${check('progress', 'Odpočet', 'Countdown')}</div>
        <h4>${T('Animace', 'Animation')}</h4><div class="nse-row">${seg('animation', [['slide', 'Vysunutí', 'Slide'], ['fade', 'Prolnutí', 'Fade'], ['none', 'Žádná', 'None']])}<label>${T('Rychlost', 'Speed')}<input type="range" min="100" max="1500" step="50" data-k="speed" value="${s.speed}"></label></div>
      </div>`;
    }
    function previewHtml(animate) {
      const [title, message, source] = SAMPLE[level];
      return `<div class="nse-fake" style="left:90px;top:90px;width:1100px;height:520px"></div><div class="nse-fake" style="left:1240px;top:90px;width:590px;height:250px"></div><div class="nse-fake" style="left:1240px;top:370px;width:590px;height:240px"></div><div class="nse-fake" style="left:90px;top:650px;width:1740px;height:330px"></div>
        ${toastHtml(s, { position: opts.position, scale: opts.scale, level, title, message, source, animate })}`;
    }
    el.innerHTML = `<div class="nse">${controls()}<div class="nse-side"><div class="nse-tv"><div class="nse-stage"></div></div>
      <div class="nse-play"><span class="nse-seg" data-lv>${LEVELS.map(l => `<button type="button" data-l="${l}" class="${l === level ? 'on' : ''}">${T(...LV[l])}</button>`).join('')}</span>
      <button type="button" class="nse-seg-btn" data-replay style="border:0;background:none;color:inherit;font:inherit;font-size:12.5px;cursor:pointer;opacity:.8">▶ ${T('Přehrát příchod', 'Replay entrance')}</button></div></div></div>`;
    const stage = el.querySelector('.nse-stage'), tv = el.querySelector('.nse-tv');
    const fit = () => { stage.style.transform = `scale(${tv.clientWidth / 1920})`; };
    const draw = animate => { stage.innerHTML = previewHtml(animate); };
    if (window.ResizeObserver) new ResizeObserver(fit).observe(tv);
    fit();
    draw(true);
    const changed = () => { draw(false); opts.onChange && opts.onChange(clone(s)); };
    const sync = () => { el.querySelector('.nse-controls').outerHTML = controls(); bind(); };
    function bind() {
      el.querySelectorAll('.nse-controls [data-k]').forEach(i => {
        i.oninput = i.onchange = () => {
          const k = i.dataset.k;
          s[k] = pick(k, i.type === 'checkbox' ? i.checked : i.value, s[k]);
          changed();
        };
      });
      el.querySelectorAll('.nse-controls [data-level]').forEach(i => {
        i.oninput = () => { const lv = s.levels[i.dataset.level]; if (i.dataset.f === 'color') { if (COLOR.test(i.value)) lv.color = i.value.toLowerCase(); } else lv.icon = [...i.value].slice(0, 3).join(''); level = i.dataset.level; el.querySelectorAll('[data-l]').forEach(b => b.classList.toggle('on', b.dataset.l === level)); changed(); };
      });
      el.querySelectorAll('.nse-controls [data-seg] button').forEach(b => {
        b.onclick = () => { s[b.parentElement.dataset.seg] = b.dataset.v; b.parentElement.querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b)); changed(); };
      });
      el.querySelectorAll('[data-preset]').forEach(b => {
        b.onclick = () => { s = merge(DEFAULT, PRESETS[b.dataset.preset][2]); sync(); draw(true); opts.onChange && opts.onChange(clone(s)); };
      });
    }
    bind();
    el.querySelectorAll('[data-l]').forEach(b => { b.onclick = () => { level = b.dataset.l; el.querySelectorAll('[data-l]').forEach(x => x.classList.toggle('on', x === b)); draw(true); }; });
    el.querySelector('[data-replay]').onclick = () => draw(true);
    return { get: () => clone(s), set(v) { s = merge(DEFAULT, v || {}); sync(); draw(true); } };
  }

  window.NotifyStyle = { DEFAULT: clone(DEFAULT), PRESETS, LEVELS, merge: d => merge(DEFAULT, d), editor };
})();

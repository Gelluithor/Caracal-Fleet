/* Editor of the look of on-screen notifications. The same file is used by the CARACAL node admin UI and by
   CARACAL Fleet. The overlay on the TV draws a notification as one picture (player/notify_render.py): rounded,
   see-through or blurred, with a gradient, a shadow and an uploaded picture. The preview follows its layout and
   sizes on a 1920x1080 screen, over a sample screen or a picture of what the TV really shows.
   NotifyStyle.editor(element, style, {t, position, scale, onChange, image, onImage, onImageRemove, capture})
     image: URL of the uploaded picture (or ''); onImage(file) -> Promise<url>; onImageRemove() -> Promise;
     capture() -> Promise<url of a screenshot of the TV>
   -> {get(), set(style), setImage(url), setBackground(url)} */
(function () {
  'use strict';
  const LEVELS = ['info', 'success', 'warning', 'critical'];
  const DEFAULT = {
    bg: '#111926', title: '#f8fafc', text: '#cbd5e1', muted: '#94a3b8', fill: 'stripe', stripe: 100,
    font: 'DejaVu Sans', bold: true, opacity: 100, width: 0, align: 'left',
    icon: true, source: true, waiting: true, progress: true, animation: 'slide', speed: 250,
    radius: 14, bg2: '#111926', gradient: 'none', bg_opacity: 100, blur: 0, shadow: 40, border_color: '', stripe_pos: 'left',
    image: false, image_pos: 'left', image_size: 25, image_align: 'center', image_shape: 'rounded', image_dim: 45, title_size: 100, text_size: 100, padding: 100, margin: 100,
    progress_pos: 'bottom', progress_size: 100,
    levels: { info: { color: '#3b82f6', icon: 'ℹ' }, success: { color: '#22c55e', icon: '✓' },
      warning: { color: '#f59e0b', icon: '⚠' }, critical: { color: '#ef4444', icon: '✖' } },
  };
  const clone = o => JSON.parse(JSON.stringify(o));
  // Every value is checked before it is used: the look comes from the device (in Fleet from what the node
  // reports), and it ends up in style attributes, so only known choices, #RRGGBB colours and bounded numbers pass.
  const CHOICES = { fill: ['stripe', 'solid', 'border'], font: ['DejaVu Sans', 'DejaVu Serif', 'DejaVu Sans Mono'], align: ['left', 'center'], animation: ['slide', 'fade', 'none'],
    gradient: ['none', 'vertical', 'horizontal', 'diagonal'], stripe_pos: ['left', 'right', 'top', 'bottom'], image_pos: ['left', 'right', 'top', 'bottom', 'background'], image_align: ['top', 'center', 'bottom'], image_shape: ['square', 'rounded', 'circle'], progress_pos: ['bottom', 'top'] };
  const RANGES = { stripe: [0, 300], opacity: [50, 100], width: [0, 100], speed: [100, 1500], radius: [0, 60], bg_opacity: [0, 100], blur: [0, 100], shadow: [0, 100],
    image_size: [10, 60], image_dim: [0, 90], title_size: [50, 250], text_size: [50, 250], padding: [50, 250], margin: [0, 300], progress_size: [50, 400] };
  const COLOR = /^#[0-9a-fA-F]{6}$/;
  const pick = (k, v, fallback) => {
    if (k in CHOICES) return CHOICES[k].includes(v) ? v : fallback;
    if (k in RANGES) { const n = Math.round(Number(v)); return Number.isFinite(n) ? Math.min(RANGES[k][1], Math.max(RANGES[k][0], n)) : fallback; }
    if (typeof fallback === 'boolean') return typeof v === 'boolean' ? v : fallback;
    if (k === 'border_color') return v === '' || (typeof v === 'string' && COLOR.test(v)) ? String(v).toLowerCase() : fallback;
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
  // starting points; every value can be changed afterwards. A preset sets the whole look.
  const PRESETS = {
    caracal: ['CARACAL', 'CARACAL', {}],
    glass: ['Sklo', 'Glass', { bg: '#0f172a', bg2: '#1e293b', gradient: 'vertical', bg_opacity: 45, blur: 45, radius: 22, shadow: 55, fill: 'border', stripe: 30, border_color: '#ffffff', speed: 350, animation: 'fade' }],
    light: ['Světlý', 'Light', { bg: '#ffffff', bg2: '#f1f5f9', gradient: 'vertical', title: '#111827', text: '#374151', muted: '#6b7280', radius: 18, shadow: 45 }],
    vivid: ['Výrazný', 'Vivid', { fill: 'solid', gradient: 'diagonal', radius: 18, shadow: 60 }],
    dusk: ['Soumrak', 'Dusk', { bg: '#1e1b4b', bg2: '#4c1d95', gradient: 'diagonal', radius: 26, shadow: 70, stripe: 0, title_size: 110, padding: 120 }],
    minimal: ['Minimalistický', 'Minimal', { bg: '#0b0f14', fill: 'border', stripe: 60, radius: 10, shadow: 0, source: false, waiting: false, progress: false, bold: false, animation: 'fade', speed: 400 }],
    contrast: ['Vysoký kontrast', 'High contrast', { bg: '#000000', bg2: '#000000', title: '#ffff00', text: '#ffffff', muted: '#ffff00', stripe: 250, radius: 0, shadow: 0, animation: 'none', title_size: 120, text_size: 115,
      levels: { info: { color: '#00e5ff' }, success: { color: '#00ff66' }, warning: { color: '#ffd400' }, critical: { color: '#ff2a2a' } } }],
    banner: ['Banner přes celou šířku', 'Full-width banner', { fill: 'solid', width: 100, align: 'center', radius: 0, shadow: 0, waiting: false, speed: 350 }],
    poster: ['S obrázkem', 'With picture', { image: true, image_pos: 'top', image_size: 40, radius: 20, shadow: 60, width: 30, stripe: 0 }],
  };
  const FONTS = { 'DejaVu Sans': "'DejaVu Sans', Verdana, sans-serif", 'DejaVu Serif': "'DejaVu Serif', Georgia, serif", 'DejaVu Sans Mono': "'DejaVu Sans Mono', Menlo, monospace" };
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const rgb = hex => { const n = parseInt(hex.slice(1), 16); return [n >> 16, (n >> 8) & 255, n & 255]; };
  const rgba = (hex, a) => `rgba(${rgb(hex).join(',')},${a})`;
  // text on a coloured background: white or near black, whichever reads better (the overlay does the same)
  const onColor = hex => { const [r, g, b] = rgb(hex); return (r * 299 + g * 587 + b * 114) / 1000 > 160 ? '#111111' : '#ffffff'; };
  const darker = hex => '#' + rgb(hex).map(v => Math.floor(v * .72).toString(16).padStart(2, '0')).join('');
  // only picture addresses without quotes, brackets or spaces go into the preview's style attributes
  const safeUrl = u => typeof u === 'string' && /^(blob:|data:image\/|\/|https?:\/\/)/.test(u) && !/["'()\\\s<>]/.test(u) ? u : '';

  const CSS = `
.nse{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.2fr);gap:22px;align-items:start}
.nse h4{margin:14px 0 8px;font-size:12.5px;font-weight:650;opacity:.65}.nse h4:first-child{margin-top:0}
.nse-row{display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px;margin-bottom:8px}
.nse label{display:flex;align-items:center;gap:8px;font-size:13.5px;font-weight:500;margin:0}
.nse-grid{display:grid;grid-template-columns:minmax(9em,13em) minmax(0,1fr) 3.4em;gap:6px 12px;align-items:center;margin-bottom:8px;font-size:13.5px}
.nse-grid output{font-variant-numeric:tabular-nums;opacity:.6;font-size:12.5px;text-align:right}
.nse-grid input[type=range]{width:100%}
.nse select,.nse input[type=text],.nse input[type=number]{width:auto;height:32px;padding:4px 9px;border:1px solid color-mix(in srgb,currentColor 22%,transparent);border-radius:8px;background:transparent;color:inherit;font:inherit;font-size:13.5px}
.nse select option{color:#111;background:#fff}
.nse input[type=color]{width:34px;height:28px;padding:0;border:1px solid color-mix(in srgb,currentColor 22%,transparent);border-radius:7px;background:none;cursor:pointer}
.nse input[type=color]::-webkit-color-swatch-wrapper{padding:2px}.nse input[type=color]::-webkit-color-swatch{border:0;border-radius:5px}
.nse input[type=range]{width:130px;height:auto;padding:0;border:0;background:none;box-shadow:none;accent-color:var(--accent,#e85d3f)}
.nse .nse-grid input[type=range]{width:100%;min-width:0}
.nse input[type=checkbox]{width:16px;height:16px;accent-color:var(--accent,#e85d3f)}
.nse-tabs{display:flex;gap:2px;padding:3px;margin-bottom:14px;border-radius:10px;background:color-mix(in srgb,currentColor 8%,transparent);overflow-x:auto}
.nse-tabs button{flex:1 1 0;min-width:0;overflow:hidden;text-overflow:ellipsis;border:0;background:none;color:inherit;font:inherit;font-size:13px;font-weight:550;padding:6px 10px;border-radius:8px;cursor:pointer;opacity:.7;white-space:nowrap}
.nse-tabs button.on{background:var(--nse-on,#fff);color:#111;opacity:1;box-shadow:0 1px 3px #0002}
/* the tabs share one fixed-height area (scrolled when longer), so the editor and its dialog keep their size */
.nse-panes{height:clamp(360px,58vh,540px);overflow-y:auto;overscroll-behavior:contain;padding-right:6px;margin-right:-6px;scrollbar-gutter:stable}
.nse-pane[hidden]{display:none}
.nse-presets{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
.nse-preset{display:grid;gap:6px;padding:8px;border:1px solid color-mix(in srgb,currentColor 15%,transparent);border-radius:10px;background:transparent;color:inherit;font:inherit;font-size:12.5px;font-weight:500;cursor:pointer;text-align:left}
.nse-preset:hover{border-color:var(--accent,#e85d3f)}
.nse-preset i{display:flex;height:24px;overflow:hidden}
.nse-preset i b{flex:none;width:5px}.nse-preset i s{flex:1;margin:7px 8px;border-radius:2px;opacity:.85;text-decoration:none}
.nse-levels{display:grid;grid-template-columns:auto auto 1fr;gap:6px 10px;align-items:center}
.nse-levels input[type=text]{width:52px;text-align:center}
.nse-seg{display:inline-flex;flex-wrap:wrap;padding:2px;border-radius:9px;background:color-mix(in srgb,currentColor 9%,transparent)}
.nse-seg button{border:0;background:none;color:inherit;font:inherit;font-size:12.5px;font-weight:500;padding:4px 10px;border-radius:7px;cursor:pointer;opacity:.75}
.nse-seg button.on{background:var(--nse-on,#fff);color:#111;opacity:1;box-shadow:0 1px 2px #0002}
.nse-pic{display:flex;align-items:center;gap:12px;margin-bottom:10px}
.nse-pic>span{width:64px;height:64px;flex:none;border-radius:10px;background:color-mix(in srgb,currentColor 8%,transparent) center/cover no-repeat;display:grid;place-items:center;font-size:12px;opacity:.9}
.nse-btn{border:1px solid color-mix(in srgb,currentColor 20%,transparent);background:transparent;color:inherit;font:inherit;font-size:13px;font-weight:550;padding:6px 12px;border-radius:8px;cursor:pointer}
.nse-btn:hover{border-color:var(--accent,#e85d3f)}.nse-btn[disabled]{opacity:.5;cursor:default}
.nse-note{font-size:12.5px;opacity:.65;margin:4px 0 10px;line-height:1.45}
.nse-side{position:sticky;top:12px;display:grid;gap:10px}
.nse-tv{position:relative;aspect-ratio:16/9;border:6px solid #050607;border-radius:12px;overflow:hidden;background:#0f172a}
.nse-stage{position:absolute;left:0;top:0;width:1920px;height:1080px;transform-origin:0 0;background:radial-gradient(1200px 700px at 20% 10%,#24324a,#0b1220 70%);background-size:cover;background-position:center}
.nse-stage .nse-fake{position:absolute;border-radius:24px;background:#ffffff0d}
.nse-stage .nse-fake i{position:absolute;left:40px;right:40px;bottom:40px;height:46%;background:linear-gradient(90deg,#e85d3f66,#3b82f666);clip-path:polygon(0 80%,10% 55%,20% 70%,32% 30%,45% 50%,58% 18%,70% 42%,82% 25%,100% 50%,100% 100%,0 100%)}
.nse-toast{position:absolute;overflow:hidden;isolation:isolate}
.nse-layer{position:absolute;inset:0;z-index:-1}
.nse-in{position:relative;display:flex;gap:var(--gap);align-items:center}
.nse-body{flex:1;display:flex;flex-direction:column;min-width:0}
.nse-head{display:flex;justify-content:space-between;gap:12px;font-weight:700;white-space:nowrap}
.nse-title,.nse-msg{white-space:pre-wrap;overflow-wrap:anywhere}
.nse-bar{position:absolute}.nse-bar i{display:block;height:100%}
.nse-play{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}
@keyframes nse-in{from{opacity:0;translate:var(--from)}}
.nse-anim{animation:nse-in var(--speed) cubic-bezier(.2,.8,.2,1) both}
@media (max-width:900px){.nse{grid-template-columns:minmax(0,1fr)}.nse-side{position:static;order:-1}.nse-panes{height:clamp(320px,50vh,520px)}}
@media (max-width:520px){.nse-tabs button{padding:6px 3px;font-size:12.5px}.nse-presets{grid-template-columns:repeat(2,minmax(0,1fr))}.nse-grid{grid-template-columns:minmax(6.5em,9em) minmax(0,1fr) 3.2em;gap:6px 8px}
.nse-play{flex-direction:column;align-items:stretch}.nse-play .nse-seg{justify-content:center}.nse-play .nse-row{justify-content:flex-end}}`;
  function injectCss() {
    if (document.getElementById('nse-css')) return;
    const el = document.createElement('style');
    el.id = 'nse-css';
    el.textContent = CSS;
    document.head.append(el);
  }

  // The notification as player/notify_render.py draws it on a 1920x1080 screen (the same sizes and rules).
  function toastHtml(s, o) {
    const sw = 1920, sh = 1080, scale = Math.max(.5, Math.min(3, (o.scale || 100) / 100)), pos = o.position || 'top-right';
    const k = key => Math.max(.3, Math.min(3, (s[key] || 100) / 100));
    const title = Math.max(14, Math.floor(sh / 38 * scale * k('title_size'))), text = Math.max(11, Math.floor(sh / 54 * scale * k('text_size'))), small = Math.max(10, Math.floor(sh / 72 * scale)), pad = Math.max(8, Math.floor(sh / 70 * scale * k('padding')));
    const banner = s.width >= 100;
    const w = s.width ? Math.floor(sw * s.width / 100) : Math.floor(Math.min(sw * .9, Math.max(320, sw * (['top', 'bottom', 'center'].includes(pos) ? .42 : .32) * scale)));
    const m = banner ? 0 : Math.floor(Math.min(sw, sh) * .035 * s.margin / 100);
    const r = banner ? 0 : Math.floor(s.radius * sh / 1080 * scale);
    const lv = s.levels[o.level] || s.levels.info, color = lv.color;
    const solid = s.fill === 'solid', border = s.fill === 'border';
    const fg = solid ? onColor(color) : s.title, fg2 = solid ? onColor(color) : s.text, fgm = solid ? onColor(color) : s.muted;
    const stripe = s.fill === 'stripe' ? Math.floor(Math.max(6, pad * .6) * s.stripe / 100) : 0, sp = s.stripe_pos;
    const bw = border ? Math.max(2, Math.floor(pad * .3 * Math.max(s.stripe, 30) / 100)) : 0;
    const font = FONTS[s.font] || FONTS['DejaVu Sans'], center = s.align === 'center';
    const url = safeUrl(o.image);
    const pic = s.image && url ? s.image_pos : '';
    const imgW = ['left', 'right'].includes(pic) ? Math.floor(w * s.image_size / 100) : 0, imgTop = ['top', 'bottom'].includes(pic) ? Math.floor(w * s.image_size / 100 * .45) : 0;
    const shadow = banner ? 0 : Math.floor(pad * 1.6 * s.shadow / 100);
    const x = pos.endsWith('right') ? sw - w - m : pos.endsWith('left') ? m : (sw - w) / 2;
    const yTop = pos.startsWith('top') ? m : null, yBottom = pos.startsWith('bottom') ? m + (o.bar || 8) : null;
    const shift = Math.round(Math.max(30, w * .15));
    const from = s.animation === 'fade' ? '0 0' : pos.endsWith('right') ? `${shift}px 0` : pos.endsWith('left') ? `-${shift}px 0` : pos.startsWith('top') ? `0 -${shift}px` : pos.startsWith('bottom') ? `0 ${shift}px` : '0 0';
    const place = `left:${x}px;${yTop != null ? `top:${yTop}px;` : yBottom != null ? `bottom:${yBottom}px;` : 'top:50%;transform:translateY(-50%);'}`;
    const c1 = solid ? color : s.bg, c2 = solid ? (s.gradient !== 'none' ? darker(color) : color) : s.bg2;
    const a = s.bg_opacity / 100;
    const dir = { vertical: '180deg', horizontal: '90deg', diagonal: '135deg' }[s.gradient];
    const fillCss = dir ? `linear-gradient(${dir},${rgba(c1, a)},${rgba(c2, a)})` : rgba(c1, a);
    const head = s.icon || s.source || s.waiting;
    const bar = s.progress ? Math.max(3, Math.floor(pad * .35 * s.progress_size / 100)) : 0;
    const track = solid ? 'rgba(0,0,0,.25)' : `color-mix(in srgb,${s.muted} 25%,${s.bg})`;
    const barTop = s.progress_pos === 'top';
    const barCss = !bar ? '' : r ? `left:${Math.max(r / 2, pad)}px;right:${Math.max(r / 2, pad)}px;${barTop ? 'top' : 'bottom'}:${bw + Math.floor(pad * (barTop ? .35 : .5))}px;`
      : `left:${bw + (sp === 'left' ? stripe : 0)}px;right:${bw}px;${barTop ? 'top' : 'bottom'}:${bw}px;`;
    const stripeCss = !stripe ? '' : { left: `left:0;top:0;bottom:0;width:${stripe}px`, right: `right:0;left:auto;top:0;bottom:0;width:${stripe}px`, top: `left:0;right:0;top:0;height:${stripe}px`, bottom: `left:0;right:0;top:auto;bottom:0;height:${stripe}px` }[sp];
    const barRoom = bar ? bar + Math.floor(pad * .5) : 0;
    const inPad = [pad + bw + (sp === 'top' ? stripe : 0) + (barTop ? barRoom : 0), pad + bw + (sp === 'right' ? stripe : 0), pad + bw + (sp === 'bottom' ? stripe : 0) + (barTop ? 0 : barRoom), pad + bw + (sp === 'left' ? stripe : 0)];
    const shape = s.image_shape, bandR = shape === 'square' ? 0 : shape === 'circle' ? imgTop / 2 : Math.max(8, Math.floor(r / 2));
    const side = imgW ? `<div style="flex:none;width:${imgW}px;height:${imgW}px;align-self:${{ top: 'flex-start', bottom: 'flex-end' }[s.image_align] || 'center'};order:${pic === 'right' ? 2 : 0};background:url('${url}') center/${shape === 'circle' ? 'cover' : 'contain'} no-repeat;border-radius:${shape === 'circle' ? '50%' : shape === 'rounded' ? '18%' : '0'}"></div>` : '';
    const band = imgTop ? `<div style="height:${imgTop}px;${pic === 'top' ? 'margin-bottom' : 'margin-top'}:${pad}px;border-radius:${bandR}px;background:url('${url}') center/cover"></div>` : '';
    return `<div class="nse-toast ${o.animate && s.animation !== 'none' ? 'nse-anim' : ''}" style="${place}width:${w}px;border-radius:${r}px;opacity:${s.opacity / 100};font-family:${font};--from:${from};--speed:${s.speed}ms;box-shadow:${shadow ? `0 ${Math.floor(shadow / 3)}px ${Math.floor(shadow * 1.1)}px rgba(0,0,0,${(.55 * s.shadow / 100).toFixed(2)})` : 'none'};${s.blur ? `backdrop-filter:blur(${Math.round(s.blur * 1.2)}px);-webkit-backdrop-filter:blur(${Math.round(s.blur * 1.2)}px);` : ''}">
      <div class="nse-layer" style="background:${fillCss}"></div>
      ${pic === 'background' ? `<div class="nse-layer" style="background:url('${url}') center/cover"></div><div class="nse-layer" style="background:${rgba(c1, s.image_dim / 100)}"></div>` : ''}
      ${stripe ? `<div class="nse-layer" style="z-index:0;${stripeCss};background:${color}"></div>` : ''}
      ${bw ? `<div class="nse-layer" style="z-index:1;border-radius:${r}px;box-shadow:inset 0 0 0 ${bw}px ${s.border_color || color}"></div>` : ''}
      <div class="nse-in" style="padding:${inPad.join('px ')}px;--gap:${pad}px">
        ${side}
        <div class="nse-body">
          ${pic === 'top' ? band : ''}
          ${head ? `<div class="nse-head" style="padding-bottom:${Math.floor(pad * .3)}px;font-size:${small}px;${center ? 'justify-content:center;' : ''}"><span style="color:${solid ? fg : color}">${s.icon ? esc(lv.icon) + '&nbsp;&nbsp;' : ''}${s.source ? esc(o.source || 'Grafana') : ''}</span>${s.waiting && !center ? `<span style="color:${fgm}">+2</span>` : ''}</div>` : ''}
          <div class="nse-title" style="font-size:${title}px;line-height:1.22;font-weight:${s.bold ? 700 : 400};color:${fg};text-align:${center ? 'center' : 'left'}">${esc(o.title)}</div>
          ${o.message ? `<div class="nse-msg" style="margin-top:${Math.floor(pad * .4)}px;font-size:${text}px;line-height:1.32;color:${fg2};text-align:${center ? 'center' : 'left'}">${esc(o.message)}</div>` : ''}
          ${pic === 'bottom' ? band : ''}
        </div>
      </div>
      ${bar ? `<div class="nse-bar" style="${barCss}height:${bar}px;background:${track}"><i style="width:62%;background:${solid ? fg : color}"></i></div>` : ''}
    </div>`;
  }

  function editor(el, style, opts = {}) {
    injectCss();
    const T = opts.t || ((cs, en) => en);
    let s = merge(DEFAULT, style || {});
    let level = 'warning', tab = 'style', image = safeUrl(opts.image || ''), backdrop = '', busy = false;
    const SAMPLE = {
      info: [T('Nová verze dashboardu', 'New dashboard version'), T('Zobrazí se po další položce playlistu.', 'It shows after the next playlist item.'), 'CARACAL'],
      success: [T('Záloha dokončena', 'Backup finished'), T('Všechny servery jsou zálohované.', 'All servers are backed up.'), T('Zálohy', 'Backups')],
      warning: [T('Vysoké vytížení linky 3', 'High load on line 3'), T('Teplota motoru 78 °C, zkontrolujte chlazení.', 'Motor temperature 78 °C, check the cooling.'), 'Grafana'],
      critical: [T('Výpadek serveru ERP', 'ERP server down'), T('Pracujeme na opravě, odhad 20 minut.', 'We are working on it, about 20 minutes.'), 'Zabbix'],
    };
    const seg = (key, items) => `<span class="nse-seg" data-seg="${key}">${items.map(([v, cs, en]) => `<button type="button" data-v="${v}" class="${String(s[key]) === String(v) ? 'on' : ''}">${T(cs, en)}</button>`).join('')}</span>`;
    const color = (key, cs, en) => `<label><input type="color" data-k="${key}" value="${s[key] || '#ffffff'}">${T(cs, en)}</label>`;
    const check = (key, cs, en) => `<label><input type="checkbox" data-k="${key}" ${s[key] ? 'checked' : ''}>${T(cs, en)}</label>`;
    const unit = k => k === 'speed' ? ' ms' : k === 'radius' ? ' px' : ' %';
    const range = (key, cs, en, step = 5) => `<span>${T(cs, en)}</span><input type="range" min="${RANGES[key][0]}" max="${RANGES[key][1]}" step="${step}" data-k="${key}" value="${s[key]}"><output data-o="${key}">${s[key]}${unit(key)}</output>`;
    const LV = { info: ['Informace', 'Information'], success: ['V pořádku', 'OK'], warning: ['Varování', 'Warning'], critical: ['Kritické', 'Critical'] };
    const TABS = [['style', 'Styl', 'Style'], ['shape', 'Tvar', 'Shape'], ['picture', 'Obrázek', 'Picture'], ['text', 'Text', 'Text'], ['motion', 'Pohyb', 'Motion']];
    const pane = (id, html) => `<div class="nse-pane" data-pane="${id}" ${tab === id ? '' : 'hidden'}>${html}</div>`;
    function controls() {
      return `<div class="nse-controls">
        <div class="nse-tabs" role="tablist">${TABS.map(([id, cs, en]) => `<button type="button" role="tab" aria-selected="${tab === id}" data-tab="${id}" class="${tab === id ? 'on' : ''}">${T(cs, en)}</button>`).join('')}</div>
        <div class="nse-panes">
        ${pane('style', `
          <h4>${T('Předvolby', 'Presets')}</h4><div class="nse-presets">${Object.entries(PRESETS).map(([key, [cs, en, p]]) => { const x = merge(DEFAULT, p), c = x.levels.warning.color, solid = x.fill === 'solid';
            const bgc = solid ? c : x.gradient !== 'none' ? `linear-gradient(135deg,${x.bg},${x.bg2})` : x.bg;
            return `<button type="button" class="nse-preset" data-preset="${key}"><i style="border-radius:${Math.min(12, Math.round(x.radius / 2))}px;background:${bgc};${x.fill === 'border' ? `box-shadow:inset 0 0 0 2px ${x.border_color || c};` : ''}${x.bg_opacity < 100 ? 'opacity:.75;' : ''}"><b style="background:${x.fill === 'stripe' && x.stripe ? c : 'transparent'}"></b><s style="background:${solid ? onColor(c) : x.title}"></s></i>${T(cs, en)}</button>`; }).join('')}</div>
          <h4>${T('Barvy', 'Colours')}</h4><div class="nse-row">${color('bg', 'Pozadí', 'Background')}${color('title', 'Nadpis', 'Title')}${color('text', 'Text', 'Text')}${color('muted', 'Doplňky', 'Details')}</div>
          <div class="nse-row">${seg('gradient', [['none', 'Jednobarevné', 'Flat'], ['vertical', 'Přechod ↓', 'Gradient ↓'], ['horizontal', 'Přechod →', 'Gradient →'], ['diagonal', 'Přechod ↘', 'Gradient ↘']])}${s.gradient !== 'none' && s.fill !== 'solid' ? color('bg2', 'Druhá barva', 'Second colour') : ''}</div>
          <h4>${T('Úrovně – barva a ikona', 'Levels – colour and icon')}</h4><div class="nse-levels">${LEVELS.map(l => `<input type="color" data-level="${l}" data-f="color" value="${s.levels[l].color}"><input type="text" maxlength="3" data-level="${l}" data-f="icon" value="${esc(s.levels[l].icon)}" title="${T('Ikona: až 3 znaky, např. ⚠ ✖ ✓ ℹ ★ ●', 'Icon: up to 3 characters, e.g. ⚠ ✖ ✓ ℹ ★ ●')}"><span>${T(...LV[l])}</span>`).join('')}</div>`)}
        ${pane('shape', `
          <h4>${T('Zvýraznění úrovně', 'Level accent')}</h4>
          <div class="nse-row">${seg('fill', [['stripe', 'Pruh', 'Stripe'], ['solid', 'Plná barva', 'Solid'], ['border', 'Rámeček', 'Border']])}</div>
          ${s.fill === 'stripe' ? `<div class="nse-row">${seg('stripe_pos', [['left', 'Vlevo', 'Left'], ['right', 'Vpravo', 'Right'], ['top', 'Nahoře', 'Top'], ['bottom', 'Dole', 'Bottom']])}</div>` : ''}
          ${s.fill === 'border' ? `<div class="nse-row"><label><input type="checkbox" data-bc ${s.border_color ? '' : 'checked'}>${T('Rámeček v barvě úrovně', 'Border in the level colour')}</label>${s.border_color ? color('border_color', 'Barva rámečku', 'Border colour') : ''}</div>` : ''}
          <div class="nse-grid">${s.fill !== 'solid' ? range('stripe', 'Tloušťka', 'Thickness', 10) : ''}${range('radius', 'Zaoblení', 'Rounding', 1)}${range('shadow', 'Stín', 'Shadow')}</div>
          <h4>${T('Průhlednost', 'Transparency')}</h4>
          <div class="nse-grid">${range('bg_opacity', 'Krytí pozadí', 'Background opacity')}${range('blur', 'Rozostření pod (sklo)', 'Blur behind (glass)')}${range('opacity', 'Krytí celého oznámení', 'Whole notification', 2)}</div>
          <p class="nse-note">${T('Průhledné pozadí ukazuje, co je na obrazovce pod oznámením v okamžiku, kdy se objeví.', 'A see-through background shows what is on the screen under the notification at the moment it appears.')}</p>
          <h4>${T('Velikost a umístění', 'Size and placement')}</h4>
          <div class="nse-row"><label>${T('Šířka', 'Width')}<select data-k="width">${[[0, 'Automaticky', 'Automatic'], [25, '25 %', '25 %'], [30, '30 %', '30 %'], [40, '40 %', '40 %'], [50, '50 %', '50 %'], [60, '60 %', '60 %'], [80, '80 %', '80 %'], [100, 'Celá šířka (banner)', 'Full width (banner)']].map(([v, cs, en]) => `<option value="${v}" ${s.width === v ? 'selected' : ''}>${T(cs, en)}</option>`).join('')}</select></label></div>
          <div class="nse-grid">${range('padding', 'Vnitřní okraj', 'Padding')}${range('margin', 'Odstup od kraje', 'Distance from the edge', 10)}</div>`)}
        ${pane('picture', `
          <div class="nse-pic"><span style="${image ? `background-image:url('${image}')` : ''}">${image ? '' : T('žádný', 'none')}</span>
            <div class="nse-row" style="margin:0">${opts.onImage ? `<button type="button" class="nse-btn" data-upload ${busy ? 'disabled' : ''}>${busy ? T('Nahrávám…', 'Uploading…') : image ? T('Nahrát jiný', 'Upload another') : T('Nahrát obrázek', 'Upload picture')}</button>` : ''}
            ${image && opts.onImageRemove ? `<button type="button" class="nse-btn" data-unimage>${T('Odebrat', 'Remove')}</button>` : ''}
            <input type="file" accept="image/png,image/jpeg,image/gif,image/webp" data-file hidden></div></div>
          <p class="nse-note">${T('PNG, JPEG, GIF nebo WebP do 5 MB – logo, ikona nebo fotka. Jeden obrázek pro všechna oznámení.', 'PNG, JPEG, GIF or WebP up to 5 MB – a logo, an icon or a photo. One picture for all notifications.')}</p>
          <div class="nse-row">${check('image', 'Zobrazit obrázek v oznámení', 'Show the picture in notifications')}</div>
          ${s.image ? `<h4>${T('Kde', 'Where')}</h4><div class="nse-row">${seg('image_pos', [['left', 'Vlevo', 'Left'], ['right', 'Vpravo', 'Right'], ['top', 'Nahoře', 'Top'], ['bottom', 'Dole', 'Bottom'], ['background', 'Jako pozadí', 'As background']])}</div>
          ${['left', 'right'].includes(s.image_pos) ? `<div class="nse-row">${seg('image_align', [['top', 'Zarovnat nahoru', 'Align to top'], ['center', 'Na střed', 'Centre'], ['bottom', 'Dolů', 'Bottom']])}</div>` : ''}
          ${s.image_pos !== 'background' ? `<h4>${T('Tvar', 'Shape')}</h4><div class="nse-row">${seg('image_shape', [['square', 'Hranatý', 'Square'], ['rounded', 'Zaoblený', 'Rounded'], ['circle', 'Kruh', 'Circle']])}</div>` : ''}
          <h4>${T('Velikost', 'Size')}</h4><div class="nse-grid">${s.image_pos === 'background' ? range('image_dim', 'Ztmavení obrázku', 'Picture dimming') : range('image_size', ['top', 'bottom'].includes(s.image_pos) ? 'Výška pruhu' : 'Šířka obrázku', ['top', 'bottom'].includes(s.image_pos) ? 'Band height' : 'Picture width')}</div>
          <p class="nse-note">${T('Každé oznámení může místo a velikost obrázku změnit, nebo ho skrýt (pole image a image_size při odeslání).', 'Each notification can change the place and size of the picture or hide it (image and image_size when sending).')}</p>` : ''}`)}
        ${pane('text', `
          <h4>${T('Písmo', 'Font')}</h4><div class="nse-row"><select data-k="font">${Object.keys(FONTS).map(f => `<option ${s.font === f ? 'selected' : ''}>${f}</option>`).join('')}</select>${check('bold', 'Tučný nadpis', 'Bold title')}${seg('align', [['left', 'Vlevo', 'Left'], ['center', 'Na střed', 'Centre']])}</div>
          <div class="nse-grid">${range('title_size', 'Velikost nadpisu', 'Title size')}${range('text_size', 'Velikost textu', 'Text size')}</div>
          <h4>${T('Co zobrazit', 'What to show')}</h4><div class="nse-row">${check('icon', 'Ikona', 'Icon')}${check('source', 'Odesílatel', 'Sender')}${check('waiting', 'Počet čekajících', 'Waiting count')}${check('progress', 'Odpočet', 'Countdown')}</div>
          ${s.progress ? `<div class="nse-row">${seg('progress_pos', [['bottom', 'Odpočet dole', 'Countdown at the bottom'], ['top', 'Nahoře', 'At the top']])}</div><div class="nse-grid">${range('progress_size', 'Tloušťka odpočtu', 'Countdown thickness', 10)}</div>` : ''}`)}
        ${pane('motion', `
          <h4>${T('Příchod a odchod', 'Entrance and exit')}</h4><div class="nse-row">${seg('animation', [['slide', 'Vysunutí', 'Slide'], ['fade', 'Prolnutí', 'Fade'], ['none', 'Žádná', 'None']])}</div>
          ${s.animation !== 'none' ? `<div class="nse-grid">${range('speed', 'Délka', 'Length', 50)}</div>` : ''}
          <p class="nse-note">${T('Na slabších zařízeních zvolte kratší animaci nebo prolnutí.', 'On slower devices choose a shorter animation or the fade.')}</p>`)}
        </div>
      </div>`;
    }
    function previewHtml(animate) {
      const [title, message, source] = SAMPLE[level];
      return `${backdrop ? '' : `<div class="nse-fake" style="left:90px;top:90px;width:1100px;height:520px"><i></i></div><div class="nse-fake" style="left:1240px;top:90px;width:590px;height:250px"></div><div class="nse-fake" style="left:1240px;top:370px;width:590px;height:240px"></div><div class="nse-fake" style="left:90px;top:650px;width:1740px;height:330px"><i style="background:linear-gradient(90deg,#22c55e55,#f59e0b55)"></i></div>`}
        ${toastHtml(s, { position: opts.position, scale: opts.scale, level, title, message, source, animate, image })}`;
    }
    el.innerHTML = `<div class="nse">${controls()}<div class="nse-side"><div class="nse-tv"><div class="nse-stage"></div></div>
      <div class="nse-play"><span class="nse-seg" data-lv>${LEVELS.map(l => `<button type="button" data-l="${l}" class="${l === level ? 'on' : ''}">${T(...LV[l])}</button>`).join('')}</span>
      <span class="nse-row" style="margin:0">${opts.capture ? `<button type="button" class="nse-btn" data-capture title="${T('Náhled nad tím, co je teď opravdu na obrazovce', 'Preview over what the screen really shows now')}">${T('Skutečná obrazovka', 'Real screen')}</button>` : ''}
      <button type="button" class="nse-btn" data-replay>▶ ${T('Přehrát', 'Replay')}</button></span></div></div></div>`;
    const stage = el.querySelector('.nse-stage'), tv = el.querySelector('.nse-tv');
    const fit = () => { stage.style.transform = `scale(${tv.clientWidth / 1920})`; };
    const draw = animate => { stage.style.backgroundImage = backdrop ? `url('${backdrop}')` : ''; stage.innerHTML = previewHtml(animate); };
    if (window.ResizeObserver) new ResizeObserver(fit).observe(tv);
    fit();
    draw(true);
    const changed = () => { draw(false); opts.onChange && opts.onChange(clone(s)); };
    const sync = (top = true) => {
      const keep = top ? 0 : (el.querySelector('.nse-panes') || {}).scrollTop || 0;
      el.querySelector('.nse-controls').outerHTML = controls(); bind();
      el.querySelector('.nse-panes').scrollTop = keep;
    };
    // choices that show or hide other controls redraw the panel
    const STRUCTURAL = ['fill', 'gradient', 'image', 'image_pos', 'progress', 'animation'];
    function bind() {
      el.querySelectorAll('.nse-controls [data-tab]').forEach(b => { b.onclick = () => { tab = b.dataset.tab; sync(); }; });
      el.querySelectorAll('.nse-controls [data-k]').forEach(i => {
        i.oninput = i.onchange = e => {
          const k = i.dataset.k;
          s[k] = pick(k, i.type === 'checkbox' ? i.checked : i.value, s[k]);
          const out = el.querySelector(`[data-o="${k}"]`);
          if (out) out.textContent = s[k] + unit(k);
          changed();
          if (e.type !== 'change') return;
          if (STRUCTURAL.includes(k)) sync(false);
          if (k === 'image' && s.image && !image && opts.onImage) el.querySelector('[data-file]')?.click();
        };
      });
      el.querySelectorAll('.nse-controls [data-level]').forEach(i => {
        i.oninput = () => { const lv = s.levels[i.dataset.level]; if (i.dataset.f === 'color') { if (COLOR.test(i.value)) lv.color = i.value.toLowerCase(); } else lv.icon = [...i.value].slice(0, 3).join(''); level = i.dataset.level; el.querySelectorAll('[data-l]').forEach(b => b.classList.toggle('on', b.dataset.l === level)); changed(); };
      });
      el.querySelectorAll('.nse-controls [data-seg] button').forEach(b => {
        b.onclick = () => {
          const key = b.parentElement.dataset.seg;
          s[key] = pick(key, b.dataset.v, s[key]);
          changed();
          if (STRUCTURAL.includes(key)) sync(false); else b.parentElement.querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b));
          if (key === 'animation') draw(true);
        };
      });
      const bc = el.querySelector('[data-bc]');
      if (bc) bc.onchange = () => { s.border_color = bc.checked ? '' : '#ffffff'; changed(); sync(false); };
      el.querySelectorAll('[data-preset]').forEach(b => {
        b.onclick = () => { s = merge(DEFAULT, PRESETS[b.dataset.preset][2]); sync(false); draw(true); opts.onChange && opts.onChange(clone(s)); };
      });
      const file = el.querySelector('[data-file]'), up = el.querySelector('[data-upload]'), rm = el.querySelector('[data-unimage]');
      if (up) up.onclick = () => file.click();
      if (file) file.onchange = async () => {
        const f = file.files[0];
        if (!f) return;
        busy = true; sync(false);
        try { image = safeUrl(await opts.onImage(f)) || image; s.image = true; opts.onChange && opts.onChange(clone(s)); } catch (e) { /* the caller reports the error */ }
        busy = false; sync(false); draw(true);
      };
      if (rm) rm.onclick = async () => {
        try { await opts.onImageRemove(); image = ''; s.image = false; opts.onChange && opts.onChange(clone(s)); } catch (e) { /* reported by the caller */ }
        sync(false); draw(false);
      };
    }
    bind();
    el.querySelectorAll('[data-l]').forEach(b => { b.onclick = () => { level = b.dataset.l; el.querySelectorAll('[data-l]').forEach(x => x.classList.toggle('on', x === b)); draw(true); }; });
    el.querySelector('[data-replay]').onclick = () => draw(true);
    const cap = el.querySelector('[data-capture]');
    if (cap) cap.onclick = async () => {
      if (backdrop) { backdrop = ''; cap.textContent = T('Skutečná obrazovka', 'Real screen'); draw(false); return; }
      cap.disabled = true; cap.textContent = T('Snímám obrazovku…', 'Capturing the screen…');
      try { backdrop = safeUrl(await opts.capture()); } catch (e) { backdrop = ''; }
      cap.disabled = false; cap.textContent = backdrop ? T('Ukázková obrazovka', 'Sample screen') : T('Skutečná obrazovka', 'Real screen');
      draw(true);
    };
    return {
      get: () => clone(s),
      set(v) { s = merge(DEFAULT, v || {}); sync(); draw(true); },
      setImage(url) { image = safeUrl(url || ''); sync(false); draw(false); },
      setBackground(url) { backdrop = safeUrl(url || ''); draw(false); },
    };
  }

  // A still preview of one notification on a small screen (e.g. in a list of templates)
  function preview(el, style, opts = {}) {
    injectCss();
    const T = opts.t || ((cs, en) => en);
    el.innerHTML = `<div class="nse-tv"><div class="nse-stage"><div class="nse-fake" style="left:90px;top:90px;width:1100px;height:520px"><i></i></div><div class="nse-fake" style="left:1240px;top:90px;width:590px;height:520px"></div><div class="nse-fake" style="left:90px;top:650px;width:1740px;height:330px"></div>
      ${toastHtml(merge(DEFAULT, style || {}), { position: opts.position, scale: opts.scale, level: opts.level || 'warning', title: T('Vysoké vytížení linky 3', 'High load on line 3'), message: T('Teplota motoru 78 °C, zkontrolujte chlazení.', 'Motor temperature 78 °C, check the cooling.'), source: 'Grafana', image: safeUrl(opts.image || '') })}</div></div>`;
    const stage = el.querySelector('.nse-stage'), tv = el.querySelector('.nse-tv');
    const fit = () => { stage.style.transform = `scale(${tv.clientWidth / 1920})`; };
    if (window.ResizeObserver) new ResizeObserver(fit).observe(tv);
    fit();
  }

  window.NotifyStyle = { DEFAULT: clone(DEFAULT), PRESETS, LEVELS, merge: d => merge(DEFAULT, d), editor, preview };
})();

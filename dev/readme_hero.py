"""The animated banner of the README (docs/brand/hero-dark.svg, hero-light.svg) and the plain logo (caracal.svg).

    pip install fonttools brotli
    python dev/readme_hero.py docs/brand/caracal.svg docs/brand hub/app/static/vendor/fonts

The logo paths come from a traced logo (potrace of hub/app/static/brand/logo.png, or docs/brand/caracal.svg itself).
The texts are drawn as outlines: GitHub shows SVG images without web fonts.
"""
import base64, re, sys
from pathlib import Path
SRC = Path(sys.argv[1]); OUT = Path(sys.argv[2]); FONTS = Path(sys.argv[3])
traced = SRC.read_text()
paths = re.findall(r'<path d="([^"]+)"', traced)
group = re.search(r'<g transform="([^"]+)"', traced).group(1)
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
_fonts = {}
def outline(text, weight, size, x, y, spacing=0.0, anchor='start'):
    """The text as one SVG path (GitHub shows SVG images without web fonts)."""
    f = _fonts.setdefault(weight, TTFont(str(FONTS / f'barlow-semi-condensed-latin-{weight}.woff2')))
    gs, cmap, upm = f.getGlyphSet(), f.getBestCmap(), f['head'].unitsPerEm
    k = size / upm
    width = sum(gs[cmap[ord(ch)]].width * k + spacing for ch in text) - spacing
    pen = SVGPathPen(gs)
    cx = x - (width / 2 if anchor == 'middle' else 0)
    for ch in text:
        g = gs[cmap[ord(ch)]]
        g.draw(TransformPen(pen, (k, 0, 0, -k, cx, y)))
        cx += g.width * k + spacing
    return re.sub(r'-?\d+\.\d+', lambda m: f'{float(m.group()):.1f}'.rstrip('0').rstrip('.'), pen.getCommands()), width

def logo_paths(cls):
    return '\n'.join(f'<path class="{cls}" pathLength="1" d="{d}"/>' for d in paths)

# the static logo for other uses
(OUT / 'caracal.svg').write_text(f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1756 2272" width="439" height="568">
<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ffa51c"/><stop offset=".55" stop-color="#f98a12"/><stop offset="1" stop-color="#f4400a"/></linearGradient></defs>
<g transform="{group}" fill="url(#g)">{''.join(f'<path d="{d}"/>' for d in paths)}</g></svg>
''')

def tx(text, weight, size, x, y, fill, spacing=0.0):
    d, _ = outline(text, weight, size, x, y, spacing, 'middle')
    return f'<path d="{d}" fill="{fill}"/>'


SCREENS = [
    # (content svg inside a 96x54 screen, progress duration)
    ('' + tx('12:30', 700, 21, 48, 31, '#f4f6fa') + tx('Friday 23 October', 600, 7.5, 48, 42, '#9aa3b5') + '', 7),
    ('<rect x="10" y="30" width="9" height="14" rx="1.5" fill="#3b82f6"/><rect x="23" y="20" width="9" height="24" rx="1.5" fill="#3b82f6"/><rect x="36" y="26" width="9" height="18" rx="1.5" fill="#3b82f6"/><rect x="49" y="13" width="9" height="31" rx="1.5" fill="#f98a12"/><rect x="62" y="22" width="9" height="22" rx="1.5" fill="#3b82f6"/><rect x="75" y="17" width="9" height="27" rx="1.5" fill="#3b82f6"/>', 9),
    ('<circle cx="30" cy="25" r="10" fill="#fbbf24"/>' + tx('21°', 700, 20, 66, 31, '#f4f6fa') + tx('Prague · clear', 600, 7.5, 48, 45, '#9aa3b5') + '', 6),
    ('<rect x="8" y="9" width="38" height="36" rx="3" fill="url(#img)"/><rect x="52" y="12" width="36" height="5" rx="2" fill="#f4f6fa" opacity=".85"/><rect x="52" y="22" width="30" height="3" rx="1.5" fill="#9aa3b5"/><rect x="52" y="29" width="33" height="3" rx="1.5" fill="#9aa3b5"/><rect x="52" y="36" width="24" height="3" rx="1.5" fill="#9aa3b5"/>', 8),
    ('<rect x="6" y="6" width="41" height="20" rx="2" fill="#2a3b55"/><rect x="49" y="6" width="41" height="20" rx="2" fill="#33405a"/><rect x="6" y="28" width="41" height="20" rx="2" fill="#2f3d52"/><rect x="49" y="28" width="41" height="20" rx="2" fill="#283447"/><circle cx="12" cy="11" r="2" fill="#ef4444" class="rec"/>', 10),
    ('<rect x="0" y="0" width="96" height="54" fill="#b91c1c"/>' + tx('EVACUATE', 700, 15, 48, 27, '#ffffff', 1) + tx('Leave by the nearest exit', 600, 7.5, 48, 40, '#fde2e2') + '', 0),
]

def hero(theme):
    ink, muted = ('#f4f6fa', '#9aa3b5') if theme == 'dark' else ('#141821', '#5d6677')
    title_d, w = outline('CARACAL', 700, 96, 292, 128, 6)
    fleet_d, fw = outline('Fleet', 600, 96, 292 + w + 22, 128)
    assert 292 + w + 22 + fw < 950, w + fw
    tag_d, _ = outline('Every signage screen of the company in one place, self-hosted', 600, 24, 296, 172, .3)
    screens = []
    for i, (inner, secs) in enumerate(SCREENS):
        x = 300 + i * 104
        bar = f'<rect class="bar" x="0" y="51" width="96" height="3" fill="#f98a12" style="animation-duration:{secs}s"/>' if secs else ''
        screens.append(f'''<g transform="translate({x} 214)"><g class="screen" style="animation-delay:{2.2 + i * 0.18:.2f}s">
  <rect x="-4" y="-4" width="104" height="62" rx="7" fill="#0b0e14"/>
  <svg width="96" height="54" viewBox="0 0 96 54"><rect width="96" height="54" fill="#141a26"/>{inner}{bar}</svg>
  <circle cx="92" cy="-8" r="3" fill="#22c55e" class="dot" style="animation-delay:{i * 0.7:.1f}s"/>
  <rect x="38" y="58" width="20" height="4" rx="1" fill="#0b0e14" opacity=".5"/>
</g></g>''')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 300" width="960" height="300" role="img" aria-label="CARACAL Fleet">
<style>
text {{ font-family: "Barlow Semi Condensed", "Segoe UI", "Helvetica Neue", Arial, sans-serif; }}
.title {{ font-weight: 700; font-size: 104px; letter-spacing: 7px; fill: {ink}; }}
.fleet {{ font-weight: 600; font-size: 104px; fill: url(#g); }}
.tag {{ font-weight: 600; font-size: 25px; fill: {muted}; letter-spacing: .3px; }}
.s-clock {{ font-weight: 700; font-size: 21px; fill: #f4f6fa; text-anchor: middle; }}
.s-temp {{ font-weight: 700; font-size: 20px; fill: #f4f6fa; text-anchor: middle; }}
.s-alert {{ font-weight: 700; font-size: 15px; fill: #fff; text-anchor: middle; letter-spacing: 1px; }}
.s-small {{ font-weight: 600; font-size: 7.5px; fill: #9aa3b5; text-anchor: middle; }}
.s-white {{ fill: #fde2e2; }}
.outline {{ fill: url(#g); fill-opacity: 0; stroke: #f98a12; stroke-width: 150; stroke-dasharray: 1; stroke-dashoffset: 1;
  animation: draw 1.7s cubic-bezier(.6,0,.3,1) forwards, fill .9s 1.25s ease-out forwards; }}
.glow {{ opacity: 0; animation: glow 5s 2.4s ease-in-out infinite; }}
.words {{ opacity: 0; animation: rise .8s 1.3s cubic-bezier(.2,.8,.2,1) forwards; }}
.tagline {{ opacity: 0; animation: rise .8s 1.6s cubic-bezier(.2,.8,.2,1) forwards; }}
.screen {{ opacity: 0; animation: pop .6s cubic-bezier(.2,.9,.3,1.2) forwards; }}
.bar {{ transform-origin: 0 0; animation: progress linear infinite; }}
.dot {{ animation: blink 2.8s ease-in-out infinite; }}
.rec {{ animation: blink 1.2s steps(2) infinite; }}
@keyframes draw {{ to {{ stroke-dashoffset: 0; }} }}
@keyframes fill {{ to {{ fill-opacity: 1; stroke-opacity: 0; }} }}
@keyframes glow {{ 0%, 100% {{ opacity: 0; }} 50% {{ opacity: .55; }} }}
@keyframes rise {{ from {{ opacity: 0; transform: translateY(14px); }} to {{ opacity: 1; transform: none; }} }}
@keyframes pop {{ from {{ opacity: 0; transform: translateY(16px) scale(.92); }} to {{ opacity: 1; transform: none; }} }}
@keyframes progress {{ from {{ transform: scaleX(0); }} to {{ transform: scaleX(1); }} }}
@keyframes blink {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: .25; }} }}
@media (prefers-reduced-motion: reduce) {{
  .outline {{ animation: none; fill-opacity: 1; stroke-opacity: 0; }}
  .words, .tagline, .screen {{ animation: none; opacity: 1; }}
  .glow, .bar, .dot, .rec {{ animation: none; }}
}}
</style>
<defs>
  <linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ffa51c"/><stop offset=".55" stop-color="#f98a12"/><stop offset="1" stop-color="#f4400a"/></linearGradient>
  <linearGradient id="img" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#f98a12"/><stop offset="1" stop-color="#b91c1c"/></linearGradient>
  <filter id="blur" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="14"/></filter>
</defs>
<svg x="22" y="10" width="216" height="280" viewBox="0 0 1756 2272">
  <g class="glow" filter="url(#blur)"><g transform="{group}" fill="#f98a12">{''.join(f'<path d="{d}"/>' for d in paths)}</g></g>
  <g transform="{group}">{logo_paths('outline')}</g>
</svg>
<g class="words"><path d="{title_d}" fill="{ink}"/><path d="{fleet_d}" fill="url(#g)"/></g>
<g class="tagline"><path d="{tag_d}" fill="{muted}"/></g>
{''.join(screens)}
</svg>
'''

for theme in ('dark', 'light'):
    (OUT / f'hero-{theme}.svg').write_text(hero(theme))
print('ok', len(paths))

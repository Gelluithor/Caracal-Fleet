"""The tour of the README GIF (docs/fleet.gif), recorded from a fresh demo (python dev/demo.py).

    pip install playwright && playwright install chromium ffmpeg
    DEMO_URL=http://127.0.0.1:8090 python dev/record_tour.py out/
    ffmpeg -i out/<video>.webm -vf "setpts=PTS/1.4,fps=10,scale=960:-1:flags=lanczos,mpdecimate=hi=64*24:lo=64*8:frac=0.2,\
      split[a][b];[a]palettegen=max_colors=160:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle" \
      -fps_mode vfr docs/fleet.gif
"""
import os
import sys
import time
from playwright.sync_api import sync_playwright
H = os.getenv('DEMO_URL', 'http://127.0.0.1:8090')
CURSOR = """
window.addEventListener('DOMContentLoaded', () => {
  const c = document.createElement('div');
  c.id = '__cur';
  c.style.cssText = 'position:fixed;left:0;top:0;width:22px;height:22px;z-index:2147483647;pointer-events:none;transform:translate(-3px,-2px);transition:transform .05s';
  c.innerHTML = '<svg viewBox="0 0 24 24" width="22" height="22"><path d="M3 2l7 19 2.5-7.5L20 11z" fill="#111" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
  document.documentElement.append(c);
  document.addEventListener('mousemove', e => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; }, true);
  document.addEventListener('mousedown', e => {
    const r = document.createElement('div');
    r.style.cssText = `position:fixed;left:${e.clientX - 18}px;top:${e.clientY - 18}px;width:36px;height:36px;border-radius:50%;border:3px solid #f98a12;z-index:2147483646;pointer-events:none;opacity:.9;transition:all .45s ease-out`;
    document.documentElement.append(r);
    requestAnimationFrame(() => { r.style.transform = 'scale(1.6)'; r.style.opacity = '0'; });
    setTimeout(() => r.remove(), 600);
  }, true);
});
"""
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.getenv('CHROMIUM') or None, args=['--no-sandbox'])
    ctx = b.new_context(viewport={'width': 1440, 'height': 900}, record_video_dir=sys.argv[1] if len(sys.argv) > 1 else 'out', record_video_size={'width': 1440, 'height': 900})
    ctx.add_init_script(CURSOR)
    ctx.add_init_script("try { localStorage.setItem('caracalTheme', 'dark'); } catch (e) {}")
    pg = ctx.new_page()
    pos = [720, 450]

    def move(x, y, steps=22):
        pg.mouse.move(x, y, steps=steps)
        pos[:] = [x, y]

    def click(sel, pause=.6, nth=0):
        loc = pg.locator(sel).nth(nth)
        loc.scroll_into_view_if_needed()
        bb = loc.bounding_box()
        move(bb['x'] + bb['width'] / 2, bb['y'] + bb['height'] / 2)
        time.sleep(.15)
        pg.mouse.down(); pg.mouse.up()
        time.sleep(pause)

    def typ(sel, text, delay=55):
        click(sel, .2)
        pg.keyboard.type(text, delay=delay)

    def scroll(dy, steps=12):
        for _ in range(steps):
            pg.mouse.wheel(0, dy / steps); time.sleep(.03)

    pg.goto(H + '/'); time.sleep(1)
    click('#login [data-lang="en"]', .5)
    typ('#loginUser', 'admin'); typ('#loginPass', 'admin-password', 35)
    click('#loginForm button.primary', 2.2)
    move(900, 500); time.sleep(1.2); scroll(500); time.sleep(1.5); scroll(-500, 8); time.sleep(.5)
    click('#nav a[data-nav="devices"]', 1.6)
    click('tr[data-href] >> text=Recepce', 1.8)
    click('#devTabs a >> nth=1', 1.8)
    click('#devTabs a >> text=What plays when', 2.2)
    click('#nav a[data-nav="wall"]', 3)
    click('#nav a[data-nav="global"]', 1.2)
    click('tr[data-href] >> text=Recepce – den', 1.8)
    click('[data-do=globalAddItem][data-kind=widget]', 1.5)
    click('label.wt:has(input[value=weather])', .4)
    typ('input[name=city]', 'Prague', 70); time.sleep(3.2)
    click('label.wt:has(input[value=rss])', .3)
    typ('input[name=url_rss]', 'https://feeds.bbci.co.uk/news/world/rss.xml', 18); time.sleep(3.5)
    pg.keyboard.press('Escape'); time.sleep(.6)
    click('[data-do=globalDeploy]', .8)
    click('[data-tsel=all]', .3); click('input[name=mode][value=replace]', .3)
    click('#diffBox summary', 2.6)
    pg.keyboard.press('Escape'); time.sleep(.5)
    click('#nav a[data-nav="approvals"]', 2.4)
    click('#nav a[data-nav="timeline"]', 2.4)
    click('#nav a[data-nav="plays"]', 2.2)
    click('#plBy [data-by=device]', 1.6)
    click('#nav a[data-nav="library"]', 2.2)
    click('#nav a[data-nav="takeover"]', 1)
    typ('#toForm input[name=title]', 'Evacuate the building', 45)
    typ('#toForm textarea[name=message]', 'Please leave by the nearest exit and follow the staff.', 22)
    click('#toForm input[name=scope][value=pick]', .6)
    click('#toPick .target-list label:has-text("real node")', .4)
    click('#toForm button.danger', .8)
    click('#modal #mSubmit', 2.5)
    real = pg.evaluate("S.devices.find(d => d.name.includes('real')).id")
    click('#nav a[data-nav="devices"]', 1)
    click('tr[data-href] >> text=CARACAL (real node)', 2.5)
    click('[data-do=takeoverEnd]', .6)
    click('#modal #mSubmit', 2)
    click('#nav a[data-nav="overview"]', 2)
    path = pg.video.path()
    ctx.close(); b.close()
    print(path)

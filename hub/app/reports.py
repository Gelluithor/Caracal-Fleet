"""Monthly reports: a PDF with the availability of the screens, their outages and problems and what they played, for
a month and optionally one location or group. Sent by e-mail on the first day of a month when switched on.
"""
import io
import time
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from .core import audit, cfg, current_user, db, save_cfg

router = APIRouter()
LANGS = ('cs', 'en', 'sk', 'de', 'pl')
TX = {
    'cs': {'title': 'Měsíční report', 'period': 'Období', 'devices': 'Obrazovky', 'availability': 'Dostupnost', 'outages': 'Výpadky',
           'downtime': 'Mimo provoz', 'problems': 'Problémy', 'plays': 'Přehrání', 'time': 'Na obrazovce', 'content': 'Obsah',
           'device': 'Obrazovka', 'location': 'Lokalita', 'top': 'Nejčastější obsah', 'none': 'Žádná data', 'generated': 'Vytvořeno',
           'all': 'všechny obrazovky', 'subject': 'CARACAL Fleet: report za {m}', 'body': 'V příloze je report CARACAL Fleet za {m}.'},
    'en': {'title': 'Monthly report', 'period': 'Period', 'devices': 'Screens', 'availability': 'Availability', 'outages': 'Outages',
           'downtime': 'Down', 'problems': 'Problems', 'plays': 'Plays', 'time': 'On screen', 'content': 'Content',
           'device': 'Screen', 'location': 'Location', 'top': 'Most shown content', 'none': 'No data', 'generated': 'Generated',
           'all': 'all screens', 'subject': 'CARACAL Fleet: report for {m}', 'body': 'The CARACAL Fleet report for {m} is attached.'},
}


def _month(value):
    from .schedule import zone
    tz = zone()
    try:
        start = datetime.strptime(value, '%Y-%m').replace(tzinfo=tz) if value else None
    except ValueError:
        raise HTTPException(400, 'invalid_value')
    if not start:
        now = datetime.now(tz)
        start = now.replace(month=now.month - 1, day=1) if now.month > 1 else now.replace(year=now.year - 1, month=12, day=1)
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return start, end


def data(month='', location='', group='', allowed=None):
    """The numbers of a report; allowed: device ids the reader may see (None = all)."""
    start, end = _month(month)
    t0, t1 = start.timestamp(), min(end.timestamp(), time.time())
    with db() as c:
        rows = [x for x in c.execute('SELECT * FROM devices ORDER BY name COLLATE NOCASE').fetchall()
                if (allowed is None or x['id'] in allowed) and (not location or x['location'] == location) and (not group or x['device_group'] == group)]
        devices = []
        for row in rows:
            since = max(t0, row['created'] or t0)
            span = max(0.0, t1 - since)
            down, outages, problems, open_at = 0.0, 0, 0, None
            # an outage that started before the month counts from its first day
            before = c.execute("SELECT kind FROM events WHERE device_id=? AND code='offline' AND ts<? ORDER BY ts DESC LIMIT 1", (row['id'], t0)).fetchone()
            if before and before['kind'] == 'start':
                open_at = since
            for e in c.execute('SELECT * FROM events WHERE device_id=? AND ts>=? AND ts<? ORDER BY ts', (row['id'], t0, t1)):
                if e['code'] == 'offline':
                    if e['kind'] == 'start' and open_at is None:
                        open_at, outages = e['ts'], outages + 1
                    elif e['kind'] == 'end' and open_at is not None:
                        down, open_at = down + e['ts'] - max(open_at, since), None
                elif e['kind'] == 'start' and e['level'] in ('critical', 'warning'):
                    problems += 1
            if open_at is not None:
                down += t1 - max(open_at, since)
            devices.append({'id': row['id'], 'name': row['name'], 'location': row['location'] or '', 'availability':
                            round(100 * (1 - down / span), 2) if span else None, 'down': down, 'outages': outages, 'problems': problems})
        ids = [d['id'] for d in devices]
        top, total = [], {'count': 0, 'seconds': 0}
        if ids:
            marks = ','.join('?' * len(ids))
            for x in c.execute(f'SELECT name, kind, COUNT(*) n, SUM(MIN(ended, ?) - MAX(started, ?)) s FROM plays WHERE device_id IN ({marks}) '
                               'AND started<? AND ended>? GROUP BY name, kind ORDER BY s DESC', (t1, t0, *ids, t1, t0)):
                total['count'] += x['n']
                total['seconds'] += x['s'] or 0
                if len(top) < 20:
                    top.append({'name': x['name'], 'kind': x['kind'], 'count': x['n'], 'seconds': x['s'] or 0})
    avail = [d['availability'] for d in devices if d['availability'] is not None]
    return {'month': start.strftime('%Y-%m'), 'from': t0, 'to': t1, 'location': location, 'group': group, 'devices': devices,
            'availability': round(sum(avail) / len(avail), 2) if avail else None, 'outages': sum(d['outages'] for d in devices),
            'problems': sum(d['problems'] for d in devices), 'plays': total, 'top': top}


def _hours(sec):
    sec = int(sec or 0)
    return f'{sec // 3600} h {sec % 3600 // 60} min' if sec >= 3600 else f'{sec // 60} min'


def pdf(d, lang='cs'):
    """The report as PDF (reportlab, the fonts of the report support Czech)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    tx = TX.get(lang, TX['en'])
    font, bold = 'Helvetica', 'Helvetica-Bold'
    for path, b in (('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'),):
        try:
            pdfmetrics.registerFont(TTFont('Report', path))
            pdfmetrics.registerFont(TTFont('Report-Bold', b))
            font, bold = 'Report', 'Report-Bold'
        except Exception:
            pass
    orange, ink, muted, line = colors.HexColor('#e8641a'), colors.HexColor('#141821'), colors.HexColor('#5d6677'), colors.HexColor('#dfe3ea')
    h1 = ParagraphStyle('h1', fontName=bold, fontSize=22, leading=26, textColor=ink)
    h2 = ParagraphStyle('h2', fontName=bold, fontSize=13, leading=16, textColor=ink, spaceBefore=12, spaceAfter=6)
    small = ParagraphStyle('s', fontName=font, fontSize=9, leading=12, textColor=muted)
    cell = ParagraphStyle('c', fontName=font, fontSize=9, leading=11, textColor=ink)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
                            title=f"CARACAL Fleet – {tx['title']} {d['month']}")
    scope = ' · '.join(x for x in (d['location'], d['group']) if x) or tx['all']
    story = [Paragraph(f"CARACAL Fleet – {tx['title']}", h1),
             Paragraph(f"{tx['period']}: {d['month']} · {scope} · {tx['generated']} {datetime.now().strftime('%Y-%m-%d %H:%M')}", small), Spacer(1, 8)]
    kpi = [[tx['devices'], tx['availability'], tx['outages'], tx['problems'], tx['plays'], tx['time']],
           [str(len(d['devices'])), f"{d['availability']:.2f} %" if d['availability'] is not None else '—', str(d['outages']),
            str(d['problems']), f"{d['plays']['count']:,}".replace(',', ' '), _hours(d['plays']['seconds'])]]
    t = Table(kpi, colWidths=[29 * mm] * 6)
    t.setStyle(TableStyle([('FONT', (0, 0), (-1, 0), font, 8), ('TEXTCOLOR', (0, 0), (-1, 0), muted), ('FONT', (0, 1), (-1, 1), bold, 15),
                           ('TEXTCOLOR', (1, 1), (1, 1), orange), ('LINEBELOW', (0, 1), (-1, 1), 1, line), ('BOTTOMPADDING', (0, 1), (-1, 1), 8)]))
    story += [t, Paragraph(tx['devices'], h2)]
    rows = [[tx['device'], tx['location'], tx['availability'], tx['outages'], tx['downtime'], tx['problems']]]
    rows += [[Paragraph(x['name'], cell), Paragraph(x['location'], cell), f"{x['availability']:.2f} %" if x['availability'] is not None else '—',
              str(x['outages']), _hours(x['down']), str(x['problems'])] for x in d['devices']]
    story.append(_table(rows, [52 * mm, 38 * mm, 24 * mm, 18 * mm, 24 * mm, 18 * mm], font, bold, line, muted, tx))
    story.append(Paragraph(tx['top'], h2))
    rows = [[tx['content'], tx['plays'], tx['time']]] + [[Paragraph(x['name'] or '—', cell), f"{x['count']:,}".replace(',', ' '), _hours(x['seconds'])] for x in d['top']]
    story.append(_table(rows, [118 * mm, 28 * mm, 28 * mm], font, bold, line, muted, tx))
    doc.build(story)
    return buf.getvalue()


def _table(rows, widths, font, bold, line, muted, tx):
    from reportlab.platypus import Paragraph, Table, TableStyle
    from reportlab.lib.styles import ParagraphStyle
    if len(rows) == 1:
        rows.append([Paragraph(tx['none'], ParagraphStyle('n', fontName=font, fontSize=9, textColor=muted))] + [''] * (len(widths) - 1))
    t = Table(rows, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([('FONT', (0, 0), (-1, 0), bold, 8.5), ('TEXTCOLOR', (0, 0), (-1, 0), muted), ('FONT', (0, 1), (-1, -1), font, 9),
                           ('LINEBELOW', (0, 0), (-1, -1), .5, line), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                           ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4)]))
    return t


def settings():
    s = cfg().get('reports') or {}
    return {'enabled': bool(s.get('enabled')), 'recipients': [x for x in s.get('recipients') or [] if isinstance(x, str)][:20],
            'language': s.get('language') if s.get('language') in LANGS else 'cs', 'last': s.get('last') or ''}


@router.get('/api/reports/monthly.pdf')
def monthly(r: Request, month: str = '', location: str = '', group: str = ''):
    u = current_user(r)
    with db() as c:
        allowed = {x['id'] for x in c.execute('SELECT * FROM devices').fetchall() if u['access'].on('view', x)}
    d = data(month, location, group, allowed)
    lang = u.get('language') or 'en'
    return Response(pdf(d, lang), media_type='application/pdf',
                    headers={'Content-Disposition': f'attachment; filename="caracal-report-{d["month"]}.pdf"'})


@router.get('/api/reports/settings')
def get_settings(r: Request):
    current_user(r, 'settings')
    return settings()


@router.put('/api/reports/settings')
async def put_settings(r: Request):
    u = current_user(r, 'settings')
    try:
        d = await r.json()
    except ValueError:
        raise HTTPException(400, 'invalid_json')
    recipients = [x.strip() for x in str(d.get('recipients') or '').replace(';', ',').split(',') if '@' in x][:20] \
        if isinstance(d.get('recipients'), str) else [x for x in d.get('recipients') or [] if isinstance(x, str) and '@' in x][:20]
    conf = cfg()
    conf['reports'] = {**settings(), 'enabled': bool(d.get('enabled')), 'recipients': recipients,
                       'language': d.get('language') if d.get('language') in LANGS else 'cs'}
    save_cfg(conf)
    audit(u, 'reports.settings', '', {'enabled': conf['reports']['enabled'], 'recipients': len(recipients)})
    return settings()


def send(month='', recipients=None, send_mail=None):
    """The report of a month by e-mail (the SMTP server of the alerts)."""
    import smtplib
    import ssl
    from email.message import EmailMessage
    from .monitor import config as alerts
    s = settings()
    to = recipients or s['recipients']
    smtp = alerts()['smtp']
    if not to or not smtp.get('host'):
        raise HTTPException(400, 'smtp_not_set')
    d = data(month)
    tx = TX.get(s['language'], TX['en'])
    msg = EmailMessage()
    msg['Subject'], msg['From'], msg['To'] = tx['subject'].replace('{m}', d['month']), smtp['sender'] or smtp['username'], ', '.join(to)
    msg.set_content(tx['body'].replace('{m}', d['month']))
    msg.add_attachment(pdf(d, s['language']), maintype='application', subtype='pdf', filename=f'caracal-report-{d["month"]}.pdf')
    if send_mail:
        return send_mail(msg)
    ctx = ssl.create_default_context()
    cls = smtplib.SMTP_SSL if smtp['security'] == 'ssl' else smtplib.SMTP
    with cls(smtp['host'], smtp['port'], timeout=30, **({'context': ctx} if smtp['security'] == 'ssl' else {})) as m:
        if smtp['security'] == 'starttls':
            m.starttls(context=ctx)
        if smtp['username']:
            m.login(smtp['username'], smtp['password'])
        m.send_message(msg)
    return d['month']


@router.post('/api/reports/send')
async def send_now(r: Request):
    u = current_user(r, 'settings')
    d = await r.json()
    month = send(str(d.get('month') or ''))
    audit(u, 'reports.send', month)
    return {'ok': True, 'month': month}


def tick(now=None):
    """On the 1st of a month after 7:00 the report of the month before goes out (once)."""
    from .schedule import local_now
    s = settings()
    now_l = local_now(now)
    if not s['enabled'] or now_l.day != 1 or now_l.hour < 7:
        return None
    month = _month('')[0].strftime('%Y-%m')
    if s['last'] == month:
        return None
    try:
        send(month)
    except Exception as e:
        print('monthly report not sent:', e, flush=True)
    conf = cfg()
    conf['reports'] = {**s, 'last': month}
    save_cfg(conf)
    return month

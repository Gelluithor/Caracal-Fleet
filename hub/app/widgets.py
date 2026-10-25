"""Widgets: the preview of the widget page in Fleet reads feeds (weather, RSS, CSV) through the hub, like the node does
for itself. Only public addresses: the hub must not become a way into the internal network."""
import ipaddress
import socket
import time
import urllib.parse
import urllib.request

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from .core import current_user

router = APIRouter()
MAX = 2 * 1024 * 1024
_cache = {}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _check(url):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise HTTPException(400, 'invalid_url')
    try:
        addrs = {x[4][0] for x in socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)}
    except OSError:
        raise HTTPException(502, 'feed_unavailable')
    for a in addrs:
        ip = ipaddress.ip_address(a.split('%')[0])
        if not ip.is_global:
            raise HTTPException(400, 'private_address')


def fetch(url):
    url = str(url or '').strip()[:4000]
    hit = _cache.get(url)
    if hit and time.time() - hit[0] < 120:
        return hit[1]
    _check(url)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(urllib.request.Request(url, headers={'User-Agent': 'CARACAL Fleet widget'}), timeout=12) as r:
            raw = r.read(MAX + 1)
            charset = r.headers.get_content_charset() or 'utf-8'
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, 'feed_unavailable')
    if len(raw) > MAX:
        raise HTTPException(413, 'file_too_large')
    text = raw.decode(charset, errors='replace')
    if len(_cache) > 100:
        _cache.clear()
    _cache[url] = (time.time(), text)
    return text


@router.get('/api/widgets/feed')
async def feed(r: Request, url: str = ''):
    current_user(r, 'content')
    return {'text': await run_in_threadpool(fetch, url)}


@router.get('/api/widgets/qr')
def qr(text: str = ''):
    """A QR code as SVG for the preview of the QR widget (the node draws its own)."""
    import io
    import segno
    from fastapi.responses import Response
    text = str(text or '')[:1000]
    if not text:
        raise HTTPException(400, 'invalid_value')
    buf = io.BytesIO()
    segno.make(text, error='m').save(buf, kind='svg', scale=10, border=2, dark='#000', light='#fff', xmldecl=False, svgns=True)
    return Response(buf.getvalue(), media_type='image/svg+xml', headers={'Cache-Control': 'max-age=86400'})

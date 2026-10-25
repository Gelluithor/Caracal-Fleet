"""Presentations and documents as content: a PDF, PowerPoint, Impress or Word file becomes one picture per page (PDF
with poppler, the others through LibreOffice first). The pages go to the media library and from there to playlists.
"""
import hashlib
import secrets
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from .core import FILES, audit, current_user, db

router = APIRouter()
OFFICE = ('.pptx', '.ppt', '.odp', '.key', '.docx', '.doc', '.odt', '.rtf')
MAX_DOC = 300 * 1024 * 1024
MAX_PAGES = 150


def convert(src: Path, out: Path):
    """PNG pages of the document (in order); raises HTTPException with a reason."""
    pdf = src
    if src.suffix.lower() in OFFICE:
        office = shutil.which('soffice') or shutil.which('libreoffice')
        if not office:
            raise HTTPException(501, 'office_missing')
        r = subprocess.run([office, '--headless', '--norestore', f'-env:UserInstallation=file://{out}/lo', '--convert-to', 'pdf',
                            '--outdir', str(out), str(src)], capture_output=True, timeout=300)
        pdf = out / (src.stem + '.pdf')
        if r.returncode or not pdf.exists():
            raise HTTPException(422, 'document_unreadable')
    elif src.suffix.lower() != '.pdf':
        raise HTTPException(400, 'unsupported_file')
    if not shutil.which('pdftoppm'):
        raise HTTPException(501, 'pdf_tools_missing')
    r = subprocess.run(['pdftoppm', '-png', '-scale-to', '1920', '-l', str(MAX_PAGES), str(pdf), str(out / 'page')],
                       capture_output=True, timeout=300)
    pages = sorted(out.glob('page*.png'), key=lambda p: int(''.join(ch for ch in p.stem if ch.isdigit()) or 0))
    if r.returncode or not pages:
        raise HTTPException(422, 'document_unreadable')
    return pages


@router.post('/api/documents')
async def upload_document(r: Request):
    """The document in the body (X-File-Name with its name): its pages as pictures in the media library."""
    u = current_user(r, 'content')
    name = unquote(r.headers.get('X-File-Name', 'document'))[:200] or 'document'
    suffix = Path(name).suffix.lower()
    if suffix not in OFFICE + ('.pdf',):
        raise HTTPException(400, 'unsupported_file')
    work = Path(tempfile.mkdtemp(prefix='caracal-doc-'))
    try:
        src, size = work / ('source' + suffix), 0
        with src.open('wb') as f:
            async for chunk in r.stream():
                size += len(chunk)
                if size > MAX_DOC:
                    raise HTTPException(413, 'file_too_large')
                f.write(chunk)
        if not size:
            raise HTTPException(400, 'empty_file')
        pages = await run_in_threadpool(convert, src, work)
        title, out = Path(name).stem, []
        with db() as c:
            for i, page in enumerate(pages, start=1):
                data = page.read_bytes()
                fid = secrets.token_hex(16)
                (FILES / fid).write_bytes(data)
                pname = f'{title} – {i}' if len(pages) > 1 else title
                c.execute('INSERT INTO files(id, name, kind, size, sha256, origin, created, pinned, library, tenant_id) VALUES(?,?,?,?,?,?,?,1,1,?)',
                          (fid, pname + '.png', 'image', len(data), hashlib.sha256(data).hexdigest(), 'document:' + u['username'], time.time(),
                           u.get('tenant_id')))
                out.append({'id': fid, 'name': pname, 'page': i})
    finally:
        shutil.rmtree(work, ignore_errors=True)
    audit(u, 'document.upload', name, {'pages': len(out)})
    return {'name': title, 'pages': out}

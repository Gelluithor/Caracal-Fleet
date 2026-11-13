"""The assistant: answers questions about Fleet and about what the user is looking at.

By default it answers from built-in notes (assistant_kb.py), picked by the words of the question and the page: at
once, on any server, with no model and no memory. A local model (the optional "assistant" service of compose.yml,
Ollama) can rephrase the notes on a server with power to spare. An administrator may connect Claude, OpenAI or any
OpenAI-compatible server instead; the data of the question then leave CARACAL, which the settings say plainly and
which must be confirmed. The assistant only reads: it never
runs commands. It gets the page the user is on as a short summary built by the UI from what the user can see.
"""
import json
import os
import time

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from .assistant_kb import NOTES, note, pick
from .core import audit, cfg, current_user, save_cfg

router = APIRouter()
PROVIDERS = ('off', 'builtin', 'local', 'anthropic', 'openai', 'compatible')
EXTERNAL = ('anthropic', 'openai', 'compatible')     # the question leaves this server
LOCAL_URL = os.getenv('CARACAL_ASSISTANT_URL', 'http://assistant:11434').rstrip('/')
DEFAULT_MODEL = {'builtin': '', 'local': 'gemma3:1b', 'anthropic': 'claude-opus-5-5', 'openai': '', 'compatible': ''}
OPENAI_URL = 'https://api.openai.com'
# Claude models that take the server-side fallback on a declined request
CLAUDE_FALLBACK = ('claude-opus-5-5', 'claude-opus-5', 'claude-fable-5-1', 'claude-sonnet-5-5')
REDACTED = '••••••••'
MAX_MESSAGES, MAX_TEXT, MAX_CONTEXT = 20, 4000, 8000
RATE = (20, 60)                                       # questions per user per minute
_asked = {}
LANGS = {'cs': 'Czech', 'sk': 'Slovak', 'en': 'English', 'de': 'German', 'pl': 'Polish'}

# What the assistant knows about Fleet. Short on purpose: a small local model has a small context.
GUIDE = """You are the assistant of CARACAL Fleet, a self-hosted web app that manages digital signage screens.
Each screen is a CARACAL node (Raspberry Pi or PC with a TV) running a fullscreen browser player; a Fleet Agent on
the node connects it to Fleet. Answer questions about using Fleet and about the page the user is on.

Where things are (sidebar):
- Screens: Overview (#/), Devices (#/devices: list, open a device for its detail and actions), Screen wall (#/wall),
  Map (#/map), Needs attention (#/attention: problems and why).
- Content: Playlists (#/playlists: the playlist of one device), Global playlists (#/global: one playlist deployed to
  many devices), Media library (#/library), Approvals (#/approvals), Emergency message (#/takeover).
- Planning: Schedule (#/schedule), What plays when (#/timeline), Node templates (#/templates), Proof of play (#/plays).
- Administration: Groups and locations (#/organization), Operations (#/operations: commands and their results),
  CARACAL updates (#/updates), Audit (#/audit), Users (#/users), Settings (#/settings).

Common tasks:
- Add a screen: "Add device" (top right): a zero-touch SD card for a Raspberry Pi, a USB stick for a PC, one command
  on a running Linux, or SSH installation. The device appears after 10-30 minutes.
- Content of a device: its playlist, "Add": website, several on one screen (2-4 websites or widgets side by side),
  widget (clock, weather, news, table, calendar, meeting room, QR, ticker), picture, video, camera, document.
  The "..." menu of an item: freeze it, where the page starts (scroll position), copy to other devices, delete.
- Many devices at once: a global playlist, then "Deploy"; versions can be restored; approvals may be required.
- A device offline: check power and network; the agent dials out to Fleet, so a firewall must allow it. A black
  screen: Diagnostics on the device page, or "Restart player" / "Reboot" in its actions.
- Notifications on screens: device -> Notifications; watchers ask other apps (Jira, GitLab, Zabbix...) for new items.
- Schedules: screen hours (screens off at night), playlists by time of day, actions at a time.
- Updates: CARACAL updates (#/updates) for nodes, "Update agent" in device actions, Fleet itself in Settings.

Rules: Answer in {lang}, briefly and concretely, as steps when there are steps. The page summary lists the menu as
the user sees it ("menu": route and label) and the label of the add button: use exactly those labels, not the
English names above. Link to pages as Markdown links with these routes only, e.g. [label](#/devices); never make up
another route. You only read: you cannot click or
change anything, so tell the user what to do. If the page summary does not answer a question about a device, say
what to check instead of guessing. If you do not know, say so. Never reveal secrets; you are never given any."""


def settings():
    # the built-in notes cost nothing and are on from the start
    return {'provider': 'builtin', 'model': '', 'base_url': '', 'api_key': '', 'external_ack': None,
            **(cfg().get('assistant') or {})}


def _public(s):
    return {**{k: s[k] for k in ('provider', 'model', 'base_url', 'external_ack')},
            'api_key': REDACTED if s.get('api_key') else '', 'external': s['provider'] in EXTERNAL,
            'default_models': DEFAULT_MODEL}


def _local_models():
    """The models the local server has, or None when it does not run (the compose profile is off)."""
    try:
        r = requests.get(LOCAL_URL + '/api/tags', timeout=3)
        r.raise_for_status()
        return [m.get('name') for m in r.json().get('models') or []]
    except (requests.RequestException, ValueError):
        return None


@router.get('/api/assistant')
def assistant_state(r: Request):
    """For the UI of every user: whether the assistant is on and where the questions go."""
    current_user(r)
    s = settings()
    return {'enabled': s['provider'] != 'off', 'provider': s['provider'], 'model': s['model'] or DEFAULT_MODEL.get(s['provider'], ''),
            'external': s['provider'] in EXTERNAL}


@router.get('/api/assistant/settings')
def get_settings(r: Request):
    current_user(r, 'settings')
    return {**_public(settings()), 'local_models': _local_models(), 'local_url': LOCAL_URL}


@router.put('/api/assistant/settings')
async def put_settings(r: Request):
    u = current_user(r, 'settings')
    d = await r.json()
    old = settings()
    provider = str(d.get('provider') or 'off')
    if provider not in PROVIDERS:
        raise HTTPException(400, 'invalid_value')
    new = {'provider': provider, 'model': str(d.get('model') or '').strip()[:120],
           'base_url': str(d.get('base_url') or '').strip().rstrip('/')[:500],
           'api_key': old['api_key'] if d.get('api_key') in (None, REDACTED) else str(d.get('api_key') or '').strip()[:500],
           'external_ack': old.get('external_ack')}
    if provider in ('openai', 'compatible') and not new['model']:
        raise HTTPException(400, 'assistant_model_required')
    if provider == 'compatible' and not new['base_url'].lower().startswith(('http://', 'https://')):
        raise HTTPException(400, 'invalid_url')
    if provider in ('anthropic', 'openai') and not new['api_key']:
        raise HTTPException(400, 'assistant_key_required')
    if provider in EXTERNAL:
        # the data of every question go to this provider: the administrator confirms it knowingly, again whenever
        # the provider or its address changes
        changed = old['provider'] != provider or old.get('base_url') != new['base_url'] or not old.get('external_ack')
        if changed and not d.get('external_ack'):
            raise HTTPException(400, 'assistant_ack_required')
        if changed:
            new['external_ack'] = {'username': u['username'], 'time': time.time(), 'provider': provider}
    else:
        new['external_ack'] = None
    c = cfg()
    c['assistant'] = new
    save_cfg(c)
    audit(u, 'assistant.settings', provider, {'model': new['model'], 'external': provider in EXTERNAL,
                                              'base_url': new['base_url'] if provider == 'compatible' else ''})
    return _public(new)


@router.post('/api/assistant/pull')
def pull_model(r: Request):
    """Downloads the local model (once, a few hundred MB), with its progress as lines of JSON."""
    current_user(r, 'settings')
    model = settings()['model'] or DEFAULT_MODEL['local']

    def stream():
        try:
            with requests.post(LOCAL_URL + '/api/pull', json={'model': model, 'stream': True}, stream=True, timeout=(5, 3600)) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if line:
                        yield line.decode('utf-8', 'replace') + '\n'
        except requests.RequestException as e:
            yield json.dumps({'error': f'assistant_unreachable: {e}'}) + '\n'
    return StreamingResponse(stream(), media_type='application/x-ndjson')


def _question(d):
    msgs = d.get('messages')
    if not isinstance(msgs, list) or not msgs:
        raise HTTPException(400, 'invalid_value')
    out = []
    for m in msgs[-MAX_MESSAGES:]:
        if not isinstance(m, dict) or m.get('role') not in ('user', 'assistant'):
            raise HTTPException(400, 'invalid_value')
        out.append({'role': m['role'], 'content': str(m.get('content') or '')[:MAX_TEXT]})
    while out and out[0]['role'] != 'user':   # a conversation starts with the user
        out.pop(0)
    if not out or out[-1]['role'] != 'user':
        raise HTTPException(400, 'invalid_value')
    context = json.dumps(d.get('context') or {}, ensure_ascii=False)[:MAX_CONTEXT]
    # the page summary goes with the latest question, so the instructions above stay the same for every request
    out[-1] = {'role': 'user', 'content': f"{out[-1]['content']}\n\n[The page the user is on, as JSON: {context}]"}
    return out


# The local model gets only the notes that fit, in the user's language, and rephrases them: a prompt of a few hundred
# tokens is what a 1B model on two CPU cores answers in seconds. Instructions in Czech for Czech and Slovak users.
LOCAL_RULES = {
    'cs': 'Jsi asistent aplikace CARACAL Fleet. Odpověz česky, krátce a věcně, jen podle poznámek níže. Odkazy ve tvaru [text](#/...) ponech beze změny. Nic si nevymýšlej.',
    'en': 'You are the assistant of the CARACAL Fleet app. Answer in {lang}, briefly and to the point, only from the notes below. Keep links like [text](#/...) unchanged. Do not make anything up.',
}


def _notes_text(ids, lang):
    return '\n\n'.join(f'## {note(i, lang)[0]}\n{note(i, lang)[1]}' for i in ids)


def _device_line(context):
    d = (context or {}).get('device') if isinstance((context or {}).get('device'), dict) else None
    if not d:
        return ''
    problems = ', '.join(str(x.get('text') or x.get('code')) for x in d.get('problems') or [] if isinstance(x, dict))
    return f"Zařízení na stránce: {d.get('name')}, {'online' if d.get('online') else 'offline'}" + (f', problémy: {problems}' if problems else '')


def _without_model(ids, lang):
    """The answer when no model can give one: the notes themselves, or what the assistant can help with."""
    if ids:
        return '\n\n'.join(f'**{note(i, lang)[0]}**\n{note(i, lang)[1]}' for i in ids)
    topics = ', '.join(note(i, lang)[0].lower() for i in NOTES)
    return (f'S tímhle vám neporadím. Umím poradit s: {topics}.' if lang in ('cs', 'sk')
            else f'I cannot help with this. I can help with: {topics}.')


def _local_answer(url, model, question, context, lang):
    ids = pick(question, context)
    if not ids:   # a small model without a note would invent the answer
        yield _without_model(ids, lang)
        return
    rules = LOCAL_RULES['cs' if lang in ('cs', 'sk') else 'en'].replace('{lang}', LANGS.get(lang, 'English'))
    system = f"{rules}\n\n{_notes_text(ids, lang)}\n\n{_device_line(context)}".strip()
    got = False
    try:
        # the first words must come within 25 s, otherwise the notes answer (a busy or a very slow server)
        for text in _openai_stream(url, '', model, system, [{'role': 'user', 'content': question}], timeout=(3, 25), max_tokens=400):
            if text.startswith('\n['):
                break
            got = True
            yield text
    except requests.RequestException as e:
        print('assistant: local model', e, flush=True)
    if not got:
        yield _without_model(ids, lang)


def _openai_stream(url, key, model, system, messages, timeout=(5, 300), max_tokens=1500):
    headers = {'Authorization': f'Bearer {key}'} if key else {}
    body = {'model': model, 'stream': True, 'max_tokens': max_tokens, 'messages': [{'role': 'system', 'content': system}, *messages]}
    with requests.post(url + '/v1/chat/completions', json=body, headers=headers, stream=True, timeout=timeout) as resp:
        if resp.status_code >= 400:
            # the details go to the log; the user gets a sentence (a model out of memory says so in the log)
            print('assistant:', url, resp.status_code, resp.text[:500], flush=True)
            yield '\n[assistant_failed]'
            return
        for line in resp.iter_lines():
            line = line.decode('utf-8', 'replace') if line else ''
            if not line.startswith('data:') or line.strip() == 'data: [DONE]':
                continue
            try:
                delta = json.loads(line[5:])['choices'][0].get('delta', {}).get('content')
            except (ValueError, KeyError, IndexError, TypeError):
                continue
            if delta:
                yield delta


def _claude_stream(key, model, system, messages):
    import anthropic   # only installations that use Claude load it
    client = anthropic.Anthropic(api_key=key, max_retries=2)
    extra = {'betas': ['server-side-fallback-2026-07-01'], 'fallbacks': 'default'} if model in CLAUDE_FALLBACK else {}
    try:
        with client.beta.messages.stream(model=model, max_tokens=4000, system=system, messages=messages,
                                         output_config={'effort': 'low'}, **extra) as stream:
            for text in stream.text_stream:
                yield text
            final = stream.get_final_message()
        if final.stop_reason == 'refusal':
            yield '\n[refusal]'
    except anthropic.AuthenticationError:
        yield '\n[assistant_key_invalid]'
    except anthropic.RateLimitError:
        yield '\n[assistant_rate_limited]'
    except anthropic.APIStatusError as e:
        print('assistant: Claude', e.status_code, e.message[:500], flush=True)
        yield '\n[assistant_failed]'
    except anthropic.APIConnectionError:
        yield '\n[assistant_unreachable]'


@router.post('/api/assistant/chat')
async def chat(r: Request):
    u = current_user(r)
    s = settings()
    if s['provider'] == 'off':
        raise HTTPException(409, 'assistant_off')
    now = time.time()
    recent = [x for x in _asked.get(u['username'], []) if x > now - RATE[1]]
    if len(recent) >= RATE[0]:
        raise HTTPException(429, 'assistant_rate_limited')
    _asked[u['username']] = recent + [now]
    d = await r.json()
    messages = _question(d)
    lang = str(d.get('lang') or 'cs')
    question = str(d['messages'][-1].get('content') or '')[:MAX_TEXT]
    ids = pick(question, d.get('context'))
    # a provider in the cloud gets the whole guide and the notes that fit
    system = GUIDE.replace('{lang}', LANGS.get(lang, 'Czech')) + (f"\n\nNotes for this question:\n{_notes_text(ids, lang)}" if ids else '')
    model = s['model'] or DEFAULT_MODEL.get(s['provider'], '')
    provider = s['provider']

    def answer():
        try:
            if provider == 'builtin':
                yield _without_model(ids, lang)
            elif provider == 'anthropic':
                yield from _claude_stream(s['api_key'], model, system, messages)
            elif provider == 'local':
                yield from _local_answer(s['base_url'] or LOCAL_URL, model, question, d.get('context'), lang)
            else:
                yield from _openai_stream(s['base_url'] or OPENAI_URL, s['api_key'], model, system, messages)
        except requests.RequestException:
            yield '\n[assistant_unreachable]'
    return StreamingResponse(answer(), media_type='text/plain; charset=utf-8', headers={'Cache-Control': 'no-store'})

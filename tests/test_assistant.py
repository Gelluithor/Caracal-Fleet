"""The assistant: its settings (a provider outside CARACAL must be confirmed, the key never comes back), answers from
an OpenAI-compatible server (the local model) and from Claude, and its limits."""
import json

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from conftest import serve


def settings(client, h, **d):
    return client.put('/api/assistant/settings', headers=h, json=d)


def test_settings(client, admin_headers):
    h = admin_headers
    # the built-in help is on from the start: no model, nothing leaves the server
    assert client.get('/api/assistant', headers=h).json() == {'enabled': True, 'provider': 'builtin', 'model': '', 'external': False}
    assert settings(client, h, provider='nope').json()['detail'] == 'invalid_value'
    assert settings(client, h, provider='anthropic').json()['detail'] == 'assistant_key_required'
    # data leave CARACAL: not without a confirmation, and again for another provider
    assert settings(client, h, provider='anthropic', api_key='sk-ant-test').json()['detail'] == 'assistant_ack_required'
    r = settings(client, h, provider='anthropic', api_key='sk-ant-test', external_ack=True).json()
    assert r['external'] is True and r['api_key'] != 'sk-ant-test' and r['external_ack']['username'] == 'admin'
    got = client.get('/api/assistant/settings', headers=h).json()
    assert 'sk-ant-test' not in json.dumps(got) and got['local_models'] is None   # no local service in the tests
    assert settings(client, h, provider='anthropic', api_key=got['api_key']).status_code == 200   # unchanged key kept
    assert settings(client, h, provider='compatible', model='m', base_url='http://ai.lan').json()['detail'] == 'assistant_ack_required'
    assert settings(client, h, provider='openai').json()['detail'] == 'assistant_model_required'
    state = client.get('/api/assistant', headers=h).json()
    assert state == {'enabled': True, 'provider': 'anthropic', 'model': 'claude-opus-5-5', 'external': True}
    assert settings(client, h, provider='off').json()['external_ack'] is None


def ask(client, h, question, **context):
    return client.post('/api/assistant/chat', headers=h, json={'lang': 'cs', 'context': context, 'messages': [{'role': 'user', 'content': question}]})


def test_built_in_help(client, admin_headers):
    """No model: the notes that fit the question (Czech word forms included) or what the help knows about."""
    from app import assistant
    assistant._asked.clear()
    h = admin_headers
    assert settings(client, h, provider='builtin').status_code == 200
    r = ask(client, h, 'Jak dám stejný obsah na víc obrazovek?')
    assert r.status_code == 200 and 'Stejný obsah na víc obrazovek' in r.text and '[Globální playlisty](#/global)' in r.text
    assert 'Plán a vypínání' in ask(client, h, 'jak vypnout obrazovky večer').text
    assert 'S tímhle vám neporadím' in ask(client, h, 'Kolik stojí pivo?').text
    # on the page of an offline device a vague question gets what to check
    assert 'Zařízení je offline' in ask(client, h, 'Co je s tím zařízením?', device={'name': 'Sklad', 'online': False}).text
    r = client.post('/api/assistant/chat', headers=h, json={'lang': 'en', 'messages': [{'role': 'user', 'content': 'How do I add a new screen?'}]})
    assert 'Adding a screen' in r.text


def test_chat_with_a_local_model(client, admin_headers):
    from app import assistant
    assistant._asked.clear()
    h = admin_headers
    seen = []
    api = FastAPI()

    @api.post('/v1/chat/completions')
    async def completions(req: Request):
        seen.append(await req.json())
        chunks = ['Klikněte na ', '**Přidat zařízení**.']
        def stream():
            for c in chunks:
                yield 'data: ' + json.dumps({'choices': [{'delta': {'content': c}}]}) + '\n\n'
            yield 'data: [DONE]\n\n'
        return StreamingResponse(stream(), media_type='text/event-stream')
    url, _ = serve(api)
    assert settings(client, h, provider='local', base_url=url).status_code == 200
    r = ask(client, h, 'Jak přidám novou obrazovku?', page='devices')
    assert r.status_code == 200 and r.text == 'Klikněte na **Přidat zařízení**.'
    body = seen[-1]
    assert body['model'] == 'gemma3:1b' and body['stream'] is True and body['max_tokens'] == 400
    # a small prompt: the Czech instructions and only the note that fits, then the question alone
    system = body['messages'][0]['content']
    assert 'Odpověz česky' in system and 'Přidání obrazovky' in system and 'Nouzové hlášení' not in system and len(system) < 1200
    assert body['messages'][-1] == {'role': 'user', 'content': 'Jak přidám novou obrazovku?'}
    # a question no note fits never reaches the model
    n = len(seen)
    assert 'S tímhle vám neporadím' in ask(client, h, 'Kolik stojí pivo?').text and len(seen) == n
    # the model fails or is not there: the note answers
    assert settings(client, h, provider='local', base_url='http://127.0.0.1:9').status_code == 200
    assert 'Přidání obrazovky' in ask(client, h, 'Jak přidám novou obrazovku?').text
    assert settings(client, h, provider='local', base_url=url).status_code == 200
    # malformed questions and too many of them
    assert client.post('/api/assistant/chat', headers=h, json={'messages': [{'role': 'system', 'content': 'x'}]}).status_code == 400
    assert client.post('/api/assistant/chat', headers=h, json={'messages': []}).status_code == 400
    codes = [client.post('/api/assistant/chat', headers=h, json={'messages': [{'role': 'user', 'content': 'a'}]}).status_code for _ in range(25)]
    assert 429 in codes
    settings(client, h, provider='off')
    assert client.post('/api/assistant/chat', headers=h, json={'messages': [{'role': 'user', 'content': 'a'}]}).json()['detail'] == 'assistant_off'


def test_chat_with_claude(client, admin_headers, monkeypatch):
    """The Claude path goes through the official SDK with the server-side fallback; here against a stand-in server."""
    from app import assistant
    h = admin_headers
    seen = []
    api = FastAPI()

    def event(kind, data):
        return f'event: {kind}\ndata: {json.dumps({"type": kind, **data})}\n\n'

    @api.post('/v1/messages')
    async def messages(req: Request):
        seen.append({'body': await req.json(), 'beta': req.headers.get('anthropic-beta', ''), 'key': req.headers.get('authorization')})
        def stream():
            yield event('message_start', {'message': {'id': 'msg_1', 'type': 'message', 'role': 'assistant', 'model': 'claude-opus-5-5', 'content': [],
                                                      'stop_reason': None, 'stop_sequence': None, 'usage': {'input_tokens': 10, 'output_tokens': 1}}})
            yield event('content_block_start', {'index': 0, 'content_block': {'type': 'text', 'text': ''}})
            for text in ('Klikněte na ', '**Přidat zařízení**.'):
                yield event('content_block_delta', {'index': 0, 'delta': {'type': 'text_delta', 'text': text}})
            yield event('content_block_stop', {'index': 0})
            yield event('message_delta', {'delta': {'stop_reason': 'end_turn', 'stop_sequence': None}, 'usage': {'output_tokens': 6}})
            yield event('message_stop', {})
        return StreamingResponse(stream(), media_type='text/event-stream')
    url, _ = serve(api)
    monkeypatch.setenv('ANTHROPIC_BASE_URL', url)
    assistant._asked.clear()
    assert settings(client, h, provider='anthropic', api_key='sk-ant-test', external_ack=True).status_code == 200
    r = client.post('/api/assistant/chat', headers=h, json={'lang': 'en', 'messages': [{'role': 'user', 'content': 'How do I add a screen?'}]})
    assert r.status_code == 200 and r.text == 'Klikněte na **Přidat zařízení**.'
    req = seen[-1]
    assert req['key'] == 'Bearer sk-ant-test' and 'server-side-fallback-2026-07-01' in req['beta']
    assert req['body']['model'] == 'claude-opus-5-5' and req['body']['fallbacks'] == 'default' and req['body']['stream'] is True
    assert req['body']['output_config'] == {'effort': 'low'} and 'Answer in English' in req['body']['system']
    # another Claude model without the fallback gets no fallback parameter
    assert settings(client, h, provider='anthropic', model='claude-haiku-5-5').status_code == 200
    client.post('/api/assistant/chat', headers=h, json={'messages': [{'role': 'user', 'content': 'Hi'}]})
    assert 'fallbacks' not in seen[-1]['body'] and seen[-1]['body']['model'] == 'claude-haiku-5-5'
    settings(client, h, provider='off')


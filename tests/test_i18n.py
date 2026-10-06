"""Every translation key used by the UI must exist in Czech and English."""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / 'hub' / 'app' / 'static'


def dictionaries():
    src = (STATIC / 'i18n.js').read_text(encoding='utf-8')
    cs, en = src.split('\n  en: {')
    keys = lambda block: set(re.findall(r"([A-Za-z0-9_]+): '", block))
    return keys(cs), keys(en)


def test_languages_have_same_keys():
    cs, en = dictionaries()
    assert cs - en == set() and en - cs == set()


def test_all_used_keys_are_translated():
    cs, _ = dictionaries()
    app = (STATIC / 'app.js').read_text(encoding='utf-8')
    html = (STATIC / 'index.html').read_text(encoding='utf-8')
    used = set(re.findall(r"\bt\(\s*'([A-Za-z0-9_]+)'\s*[,)]", app)) | set(re.findall(r'data-i="([^"]+)"', html))
    assert used - cs == set()
    for prefix, values in {
        'act_': ['next', 'unfreeze', 'show', 'freeze', 'show_collection', 'freeze_collection', 'restart_player',
                 'reboot', 'add_web', 'add_media', 'update_asset', 'delete_asset', 'reorder', 'add_collection',
                 'update_collection', 'delete_collection', 'import_playlist', 'export_assets', 'update_agent'],
        'att_': ['offline', 'local_api', 'player', 'temperature', 'disk', 'ram', 'cpu', 'commands_failed',
                 'agent_outdated'],
        'state_': ['queued', 'delivered', 'completed', 'failed', 'expired', 'timeout', 'cancelled', 'running',
                   'interrupted'],
        'role_': ['admin', 'manager', 'operator', 'viewer'],
    }.items():
        for v in values:
            assert prefix + v in cs, prefix + v
            if prefix == 'att_':
                assert 'hint_' + v in cs


def test_backend_error_codes_are_translated():
    cs, _ = dictionaries()
    root = STATIC.parents[0]
    codes = set()
    for f in root.glob('*.py'):
        codes |= set(re.findall(r"HTTPException\(\d+, '([a-z_]+)'\)", f.read_text(encoding='utf-8')))
    assert codes and {'err_' + c for c in codes} - cs == set()

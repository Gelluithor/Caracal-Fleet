"""Every third-party module the hub imports must be in hub/requirements.txt (the runtime image installs only that)."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = {'fastapi': 'fastapi', 'starlette': 'fastapi', 'uvicorn': 'uvicorn', 'paramiko': 'paramiko',
        'requests': 'requests'}


def test_hub_imports_are_runtime_requirements():
    required = {re.split(r'[\[=<>]', line)[0].strip().lower()
                for line in (ROOT / 'hub' / 'requirements.txt').read_text().splitlines() if line.strip()}
    for path in (ROOT / 'hub' / 'app').glob('*.py'):
        for module in re.findall(r'^(?:import|from) ([A-Za-z_][A-Za-z0-9_]*)', path.read_text(encoding='utf-8'), re.M):
            if module in sys.stdlib_module_names:
                continue
            assert module in DIST, f'{path.name}: unknown third-party module {module}'
            assert DIST[module] in required, f'{path.name} imports {module}, missing in hub/requirements.txt'

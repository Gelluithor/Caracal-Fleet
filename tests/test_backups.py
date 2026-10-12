"""Backups the hub makes itself every day."""
import zipfile
from datetime import datetime, timezone


def test_scheduled_backups(client, admin_headers):
    from app import system
    h = admin_headers
    client.put('/api/schedules/settings', headers=h, json={'timezone': 'UTC'})
    assert client.put('/api/backups/settings', headers=h, json={'enabled': True, 'hour': 25}).json()['detail'] == 'invalid_value'
    assert client.put('/api/backups/settings', headers=h, json={'enabled': True, 'hour': 2, 'keep': 2}).status_code == 200
    at = lambda d, hh: datetime(2026, 10, d, hh, 10, tzinfo=timezone.utc).timestamp()
    assert system.backup_tick(at(1, 1)) is None              # not its hour
    first = system.backup_tick(at(1, 2))
    assert first and first.name == 'caracal-fleet-backup-20261001-0210.zip'
    assert system.backup_tick(at(1, 2) + 60) is None         # once a day
    with zipfile.ZipFile(first) as z:
        assert {'manifest.json', 'hub.db', 'config.json'} <= set(z.namelist())
    system.backup_tick(at(2, 2))
    system.backup_tick(at(3, 2))
    listed = client.get('/api/backups', headers=h).json()['backups']
    assert [x['name'] for x in listed] == ['caracal-fleet-backup-20261003-0210.zip', 'caracal-fleet-backup-20261002-0210.zip']
    r = client.get('/api/backups/' + listed[0]['name'], headers=h)
    assert r.status_code == 200 and r.content[:2] == b'PK'
    assert client.get('/api/backups/..%2Fhub.db', headers=h).status_code == 404
    assert client.delete('/api/backups/' + listed[1]['name'], headers=h).status_code == 200
    assert client.post('/api/backups/run', headers=h).status_code == 200
    client.put('/api/backups/settings', headers=h, json={'enabled': False})

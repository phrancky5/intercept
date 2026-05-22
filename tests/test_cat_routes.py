"""Tests for the CAT blueprint and Supervisor enforcement.

These tests use Flask's test client and mock out the underlying
``RigDriver`` so no real serial port is opened.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import app as app_module
import routes.cat as cat_routes
from utils.cat.base import RigState
from utils.cat.supervisor import Supervisor


def _login(client) -> None:
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['username'] = 'test'
        sess['role'] = 'admin'


@pytest.fixture(autouse=True)
def _reset_cat_state():
    """Ensure a clean module-level state for each test."""
    app_module.cat_driver = None
    cat_routes._supervisor = Supervisor()  # defaults: TX locked, band guard on
    yield
    app_module.cat_driver = None


def _fake_driver(connected: bool = True) -> MagicMock:
    drv = MagicMock()
    drv.is_running.return_value = connected
    drv.state.return_value = RigState(
        connected=connected, rig_id='kenwood_ts850',
        vfo_a_hz=14_060_000, vfo_b_hz=14_070_000,
        mode='USB', s_meter=3,
    )
    drv.send_raw.return_value = 'OK;'
    return drv


def test_rigs_endpoint_lists_ts850(client):
    _login(client)
    resp = client.get('/cat/rigs')
    assert resp.status_code == 200
    data = resp.get_json()
    rig_ids = [r['rig_id'] for r in data['rigs']]
    assert 'kenwood_ts850' in rig_ids
    ts850 = next(r for r in data['rigs'] if r['rig_id'] == 'kenwood_ts850')
    assert ts850['implemented'] is True


def test_select_rejects_unimplemented(client):
    _login(client)
    resp = client.post('/cat/select', json={'rig_id': 'icom_ic7300'})
    assert resp.status_code == 400
    assert resp.get_json()['error_type'] == 'driver_unavailable'


def test_select_accepts_ts850(client):
    _login(client)
    resp = client.post('/cat/select', json={'rig_id': 'kenwood_ts850'})
    assert resp.status_code == 200
    assert resp.get_json()['selected'] == 'kenwood_ts850'


def test_status_without_driver(client):
    _login(client)
    resp = client.get('/cat/status')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['connected'] is False
    assert 'supervisor' in data
    assert data['supervisor']['tx_locked'] is True


def test_set_vfo_blocked_by_band_guard(client):
    _login(client)
    app_module.cat_driver = _fake_driver()
    # 1 MHz is outside all default amateur bands.
    resp = client.post('/cat/vfo', json={'which': 'A', 'hz': 1_000_000})
    assert resp.status_code == 403
    assert resp.get_json()['error_type'] == 'band_guard'
    app_module.cat_driver.set_vfo.assert_not_called()


def test_set_vfo_allowed_inside_band(client):
    _login(client)
    drv = _fake_driver()
    app_module.cat_driver = drv
    resp = client.post('/cat/vfo', json={'which': 'A', 'hz': 14_060_000})
    assert resp.status_code == 200
    drv.set_vfo.assert_called_once_with('A', 14_060_000)


def test_ptt_blocked_when_tx_locked(client):
    _login(client)
    drv = _fake_driver()
    app_module.cat_driver = drv
    resp = client.post('/cat/ptt', json={'tx': True})
    assert resp.status_code == 403
    assert resp.get_json()['error_type'] == 'tx_locked'
    drv.ptt.assert_not_called()


def test_ptt_allowed_when_unlocked(client):
    _login(client)
    drv = _fake_driver()
    app_module.cat_driver = drv
    cat_routes._supervisor.tx_locked = False
    resp = client.post('/cat/ptt', json={'tx': True})
    assert resp.status_code == 200
    drv.ptt.assert_called_once_with(True)


def test_raw_blocked_when_tx_locked(client):
    _login(client)
    drv = _fake_driver()
    app_module.cat_driver = drv
    resp = client.post('/cat/raw', json={'cmd': 'TX;'})
    assert resp.status_code == 403
    drv.send_raw.assert_not_called()


def test_raw_query_allowed_while_tx_locked(client):
    """tx_locked must only refuse keying commands, not queries / mode / VFO."""
    _login(client)
    drv = _fake_driver()
    app_module.cat_driver = drv
    # tx_locked is True by default in this test module's setup.
    for cmd in ('IF;', 'AI1;', 'FA;', 'MD2;', 'ID;'):
        drv.send_raw.reset_mock()
        resp = client.post('/cat/raw', json={'cmd': cmd})
        assert resp.status_code == 200, f'{cmd} should be allowed'
        drv.send_raw.assert_called_once_with(cmd)


def test_raw_allowed_when_unlocked(client):
    _login(client)
    drv = _fake_driver()
    app_module.cat_driver = drv
    cat_routes._supervisor.tx_locked = False
    resp = client.post('/cat/raw', json={'cmd': 'IF;'})
    assert resp.status_code == 200
    drv.send_raw.assert_called_once_with('IF;')


def test_power_capped_by_supervisor(client):
    _login(client)
    drv = _fake_driver()
    app_module.cat_driver = drv
    cat_routes._supervisor.max_power_w = 10
    resp = client.post('/cat/power', json={'watts': 100})
    assert resp.status_code == 200
    drv.set_power.assert_called_once_with(10)
    data = resp.get_json()
    assert data['watts'] == 10
    assert data['requested'] == 100


def test_supervisor_check_freq(client):
    _login(client)
    resp = client.get('/cat/supervisor/check_freq?hz=14060000')
    assert resp.status_code == 200
    assert resp.get_json()['allowed'] is True
    resp = client.get('/cat/supervisor/check_freq?hz=100000')
    assert resp.get_json()['allowed'] is False


def test_supervisor_update_persists_in_memory(client):
    _login(client)
    resp = client.post('/cat/supervisor', json={'tx_locked': False, 'max_power_w': 5})
    assert resp.status_code == 200
    snap = resp.get_json()
    assert snap['tx_locked'] is False
    assert snap['max_power_w'] == 5


def test_endpoints_return_409_when_not_connected(client):
    _login(client)
    for path, payload in [
        ('/cat/vfo', {'which': 'A', 'hz': 14_060_000}),
        ('/cat/mode', {'mode': 'USB'}),
        ('/cat/split', {'on': True}),
        ('/cat/ptt', {'tx': False}),
        ('/cat/refresh', {}),
    ]:
        resp = client.post(path, json=payload)
        assert resp.status_code == 409, path

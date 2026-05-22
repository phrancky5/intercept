"""Tests for the Kenwood TS-850S CAT driver — parser correctness only.

We don't exercise the serial worker thread here (that needs a real port).
Instead we instantiate the driver, bypass ``start()``, and feed canned
frames into ``_parse()`` directly.
"""

from __future__ import annotations

import pytest

from utils.cat.kenwood_ts850 import MODE_CODES, KenwoodTS850Driver


@pytest.fixture
def driver():
    # Avoid touching real hardware; pass dummy port/baud and never call start().
    drv = KenwoodTS850Driver.__new__(KenwoodTS850Driver)
    # Reproduce __init__ side-effects without opening a port.
    import queue as _q
    import threading as _t

    from utils.cat.base import RigState
    drv.port = 'unused'
    drv.baud = 4800
    drv.on_status = None
    drv.on_io = None
    drv.use_auto_info = True
    drv.assert_rts = False
    drv.assert_dtr = False
    drv._ser = None
    drv._tx_q = _q.Queue()
    drv._stop = _t.Event()
    drv._thread = None
    drv._state = RigState(rig_id='kenwood_ts850')
    drv._state_lock = _t.Lock()
    drv._rx_buf = b''
    drv._pending = {}
    drv._pending_lock = _t.Lock()
    drv._last_poll = 0.0
    return drv


def test_parse_fa_sets_vfo_a(driver):
    driver._parse('FA00014250000;')
    assert driver._state.vfo_a_hz == 14_250_000


def test_parse_fb_sets_vfo_b(driver):
    driver._parse('FB00021200500;')
    assert driver._state.vfo_b_hz == 21_200_500


def test_parse_md_sets_mode(driver):
    driver._parse('MD2;')   # USB
    assert driver._state.mode == 'USB'
    driver._parse('MD3;')   # CW
    assert driver._state.mode == 'CW'


def test_parse_if_extracts_full_state(driver):
    # 11-digit freq + tail: step(4) rit(5) ritOn xitOn ?(4) tx mode rxVfo ?  split
    # frame:                    "0000" "+0000" "1" "0" "0000" "0" "2"  "0" "0" "0"
    # full: IF FFFFFFFFFFF S(4) RIT(5) R X ???? T M V ? P ;
    # We reuse a known-good real frame layout from the manual.
    frame = 'IF00014250000' + '0000' + '+0050' + '1' + '0' + '0000' + '0' + '2' + '0' + '0' + '0' + ';'
    driver._parse(frame)
    assert driver._state.vfo_a_hz == 14_250_000
    assert driver._state.rit_hz == 50
    assert driver._state.rit_on is True
    assert driver._state.xit_on is False
    assert driver._state.ptt is False
    assert driver._state.mode == 'USB'
    assert driver._state.active_vfo == 'A'
    assert driver._state.split is False


def test_parse_sm_sets_smeter(driver):
    driver._parse('SM0007;')
    assert driver._state.s_meter == 7


def test_parse_rt_xt_split(driver):
    driver._parse('RT1;'); assert driver._state.rit_on is True
    driver._parse('RT0;'); assert driver._state.rit_on is False
    driver._parse('XT1;'); assert driver._state.xit_on is True


def test_parse_fr_selects_active_vfo(driver):
    driver._parse('FR1;')
    assert driver._state.active_vfo == 'B'
    driver._parse('FR0;')
    assert driver._state.active_vfo == 'A'


def test_parse_ft_marks_split_when_tx_vfo_differs(driver):
    driver._parse('FR0;')          # RX = A
    driver._parse('FT1;')          # TX = B → split
    assert driver._state.tx_vfo == 'B'
    assert driver._state.split is True


def test_parse_extended_controls(driver):
    driver._parse('GT001;'); assert driver._state.agc == 1
    driver._parse('FL02;'); assert driver._state.filter_slot == 2
    driver._parse('NB1;'); assert driver._state.nb is True
    driver._parse('RA02;'); assert driver._state.attenuator == 2
    driver._parse('AG0128;'); assert driver._state.af_gain == 128
    driver._parse('RG200;'); assert driver._state.rf_gain == 200
    driver._parse('SQ0050;'); assert driver._state.squelch == 50
    driver._parse('KS022;'); assert driver._state.keyer_wpm == 22
    driver._parse('PC050;'); assert driver._state.power_w == 50
    driver._parse('MC005;'); assert driver._state.memory_ch == 5


def test_mode_codes_table_is_consistent():
    # Sanity: the reverse map should round-trip.
    for code, name in MODE_CODES.items():
        from utils.cat.kenwood_ts850 import MODE_REVERSE
        assert MODE_REVERSE[name] == code


def test_set_vfo_enqueues_correctly_formatted_command(driver):
    driver.set_vfo('A', 7_030_000)
    cmd = driver._tx_q.get_nowait()
    assert cmd == b'FA00007030000;'


def test_set_vfo_rejects_invalid_which(driver):
    with pytest.raises(ValueError):
        driver.set_vfo('C', 14_000_000)


def test_set_mode_rejects_unknown(driver):
    with pytest.raises(ValueError):
        driver.set_mode('DRM')

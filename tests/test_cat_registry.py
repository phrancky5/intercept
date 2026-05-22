"""Tests for the CAT rig registry."""

from __future__ import annotations

from utils.cat.kenwood_ts850 import KenwoodTS850Driver
from utils.cat.registry import (
    RIG_REGISTRY,
    get_descriptor,
    list_descriptors,
)


def test_registry_contains_ts850():
    desc = get_descriptor('kenwood_ts850')
    assert desc is not None
    assert desc.vendor == 'Kenwood'
    assert desc.driver_class is KenwoodTS850Driver
    assert desc.implemented is True
    assert 4800 in desc.supported_bauds
    assert desc.default_baud == 4800


def test_registry_stubs_have_no_driver():
    """Every non-TS-850 rig is currently a documentation stub."""
    for rig_id, desc in RIG_REGISTRY.items():
        if rig_id == 'kenwood_ts850':
            continue
        assert desc.driver_class is None, f'{rig_id} should be a stub'
        assert desc.implemented is False


def test_list_descriptors_is_sorted_and_complete():
    listed = list_descriptors()
    assert len(listed) == len(RIG_REGISTRY)
    vendors = [d.vendor for d in listed]
    assert vendors == sorted(vendors, key=str.lower)


def test_descriptor_to_dict_round_trip():
    desc = get_descriptor('kenwood_ts850')
    payload = desc.to_dict()
    assert payload['rig_id'] == 'kenwood_ts850'
    assert payload['implemented'] is True
    assert 'capabilities' in payload
    assert isinstance(payload['capabilities'], list)


def test_unknown_rig_returns_none():
    assert get_descriptor('made_up_rig') is None

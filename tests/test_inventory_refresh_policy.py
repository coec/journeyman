"""Regression coverage for dispatch-time inventory refresh policies."""
from types import SimpleNamespace

import pytest

from app.services import inventory_refresh_policy as policy
from app.services import inventory_resolver as resolver
from app.services.inventory_cache import InventoryCacheMissingError


def inventory(id, kind):
    return SimpleNamespace(id=id, name='inventory-{}'.format(id),
                           inventory_type=kind, enabled=True)


def test_never_uses_cached_resolution(monkeypatch):
    node = inventory(1, 'ovirt')
    monkeypatch.setattr(policy, 'resolve_inventory', lambda *a, **k: {'cached': True})
    monkeypatch.setattr(policy, 'refresh_inventory', lambda *a, **k: pytest.fail('unexpected refresh'))
    assert policy.resolve_with_policy(node, 'never') == {'cached': True}


def test_always_refreshes(monkeypatch):
    node = inventory(1, 'ovirt')
    monkeypatch.setattr(policy, 'refresh_inventory', lambda *a, **k: {'live': True})
    assert policy.resolve_with_policy(node, 'always') == {'live': True}


def test_missing_refreshes_only_missing_source_in_composite(monkeypatch):
    root = inventory(1, 'composite')
    cached = inventory(2, 'ovirt')
    missing = inventory(3, 'ovirt')
    calls = []

    monkeypatch.setattr(resolver, '_composite_source_inventories', lambda node: [cached, missing])
    monkeypatch.setattr(resolver, '_apply_composite_inventory', lambda node, sources: dict(sources))
    monkeypatch.setattr(resolver, '_apply_inventory_output_transforms', lambda node, data: data)

    def load(node):
        if node.id == 3:
            raise InventoryCacheMissingError('no cache')
        return {'host': 'cached'}

    monkeypatch.setattr('app.services.inventory_cache.load_inventory_cache', load)

    def refresh(node, **kwargs):
        calls.append(node.id)
        return {'host': 'refreshed'}

    monkeypatch.setattr(policy, 'refresh_inventory', refresh)
    result = policy.resolve_with_policy(root, 'if_missing')
    assert calls == [3]
    assert result['inventory-2'] == {'host': 'cached'}
    assert result['inventory-3'] == {'host': 'refreshed'}


def test_invalid_policy_rejected():
    with pytest.raises(ValueError):
        policy.resolve_with_policy(inventory(1, 'ovirt'), 'invalid')

"""TO-003: tenant resolution from credentials (panoptis.api.tenancy)."""

from __future__ import annotations

import pytest

from panoptis.api.tenancy import TenancyError, parse_tenant_keys, resolve

KEYS = parse_tenant_keys("acme=key-acme-123, globex=key-globex-456")


def r(**kw):
    base = {"cloud_mode": False, "api_key": None, "tenant_header": None, "tenant_keys": {}}
    return resolve(**{**base, **kw})


def test_single_tenant_is_always_default():
    assert r().tenant_id == "default"
    assert r(api_key="anything").tenant_id == "default"


@pytest.mark.parametrize("header", ["acme", "other-tenant", "default "])
def test_header_cannot_select_another_tenant(header):
    if header.strip() == "default":
        assert r(tenant_header=header).tenant_id == "default"
        return
    with pytest.raises(TenancyError) as exc:
        r(tenant_header=header)
    assert exc.value.status == 403


def test_tenant_key_map_resolves_the_keys_tenant():
    assert r(api_key="key-acme-123", tenant_keys=KEYS).tenant_id == "acme"
    assert (
        r(api_key="key-globex-456", tenant_keys=KEYS, tenant_header="globex").tenant_id == "globex"
    )


def test_tenant_key_map_rejects_a_mismatched_selector():
    with pytest.raises(TenancyError) as exc:
        r(api_key="key-acme-123", tenant_keys=KEYS, tenant_header="globex")
    assert exc.value.status == 403


def test_unmapped_key_is_the_default_tenant_not_a_header_choice():
    assert r(api_key="key-unknown", tenant_keys=KEYS).tenant_id == "default"
    with pytest.raises(TenancyError):
        r(api_key="key-unknown", tenant_keys=KEYS, tenant_header="acme")


def test_cloud_mode_uses_only_the_validated_key():
    lookup = {"k1": "t1"}.get
    assert r(cloud_mode=True, api_key="k1", cloud_lookup=lookup).tenant_id == "t1"
    for bad in (None, "", "revoked-or-unknown"):
        with pytest.raises(TenancyError) as exc:
            r(cloud_mode=True, api_key=bad, cloud_lookup=lookup, tenant_header="t1")
        assert exc.value.status == 401, "no key must never fall back to the header"
    with pytest.raises(TenancyError) as exc:
        r(cloud_mode=True, api_key="k1", cloud_lookup=lookup, tenant_header="t2")
    assert exc.value.status == 403


@pytest.mark.parametrize("raw", ["acme", "acme=", "=key", "bad tenant!=k", "a=k,b=k"])
def test_invalid_key_maps_are_configuration_errors(raw):
    with pytest.raises(ValueError):
        parse_tenant_keys(raw)


def test_key_map_parsing():
    assert parse_tenant_keys(" a=k1 \n b = k2 ,") == {"k1": "a", "k2": "b"}
    assert parse_tenant_keys(None) == {}

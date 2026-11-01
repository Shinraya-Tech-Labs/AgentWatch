"""TO-006: the in-memory TenantStore (single-tenant setups, tests) behaves like the SQL one."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from panoptis.api.tenant_auth import TenantStore
from panoptis.models.tenant import TenantPlan, TenantStatus


@pytest.fixture
def store() -> TenantStore:
    return TenantStore()


def test_tenant_lifecycle(store):
    t = store.create_tenant("acme", plan=TenantPlan.STARTER, metadata={"x": 1})
    assert store.get_tenant(t.tenant_id).name == "acme"
    assert [x.tenant_id for x in store.list_tenants()] == [t.tenant_id]
    assert store.update_tenant(t.tenant_id, name="acme-2").name == "acme-2"
    assert store.update_tenant("missing", name="x") is None
    assert store.list_tenants(status=TenantStatus.SUSPENDED) == []
    assert store.suspend_tenant(t.tenant_id) and not store.suspend_tenant("missing")
    assert [x.tenant_id for x in store.list_tenants(status=TenantStatus.SUSPENDED)] == [t.tenant_id]


def test_keys_validate_until_revoked_expired_or_suspended(store):
    t = store.create_tenant("acme")
    k1, raw1 = store.create_api_key(t.tenant_id, "a", scopes=["session:read"])
    k2, raw2 = store.create_api_key(t.tenant_id, "b")
    assert store.validate_api_key(raw1).scopes == ["session:read"]
    assert store.validate_api_key(raw1).last_used_at is not None
    assert raw1 not in k1.key_hash, "only the hash is kept"
    assert store.revoke_api_key(k1.key_id) and not store.revoke_api_key("missing")
    assert store.validate_api_key(raw1) is None
    k2.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert store.validate_api_key(raw2) is None
    _, raw3 = store.create_api_key(t.tenant_id, "c")
    store.suspend_tenant(t.tenant_id)
    assert store.validate_api_key(raw3) is None
    assert len(store.list_api_keys(t.tenant_id)) == 3


@pytest.mark.parametrize("bad", ["", "aw_unknown_key", "x" * 64])
def test_unknown_keys_are_rejected(store, bad):
    store.create_api_key(store.create_tenant("acme").tenant_id)
    assert store.validate_api_key(bad) is None


def test_tampered_key_with_the_same_prefix_is_rejected(store):
    t = store.create_tenant("acme")
    _, raw = store.create_api_key(t.tenant_id)
    assert store.validate_api_key(raw[:20] + "Z" * (len(raw) - 20)) is None


def test_usage_and_limits(store):
    t = store.create_tenant("acme")
    assert store.get_usage(t.tenant_id) is None
    store.record_usage(t.tenant_id, tokens=7, usd_cost=0.25, events=2)
    u = store.record_usage(t.tenant_id, tokens=3, events=1)
    assert (u.tokens_used, u.events_count, u.requests_count) == (10, 3, 2)
    assert store.check_usage_limits(t.tenant_id) == (True, "OK")
    t.config.max_events_per_day = 3
    assert store.check_usage_limits(t.tenant_id) == (False, "Daily event limit exceeded")
    t.config.max_events_per_day = 10_000
    t.config.max_tokens_per_month = 5
    assert store.check_usage_limits(t.tenant_id) == (False, "Monthly token limit exceeded")
    t.config.max_tokens_per_month = 10**9
    t.config.max_usd_per_month = 0.1
    assert store.check_usage_limits(t.tenant_id) == (False, "Monthly cost limit exceeded")
    assert store.check_usage_limits("missing") == (False, "Tenant not found")
    store.suspend_tenant(t.tenant_id)
    assert store.check_usage_limits(t.tenant_id)[0] is False


def test_one_tenants_config_change_does_not_leak_into_the_plan_or_other_tenants(store):
    from panoptis.models.tenant import PLAN_DEFAULTS

    default = PLAN_DEFAULTS[TenantPlan.FREE].max_usd_per_month
    a = store.create_tenant("a")
    b = store.create_tenant("b")
    a.config.max_usd_per_month = 0.01
    assert b.config.max_usd_per_month == default
    assert PLAN_DEFAULTS[TenantPlan.FREE].max_usd_per_month == default
    assert store.create_tenant("c").config.max_usd_per_month == default

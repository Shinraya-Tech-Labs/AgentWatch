"""TO-010: request body and payload limits."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from panoptis.api.limits import BodySizeLimitMiddleware, PayloadTooLarge, check_json_limits


def _echo_app(limit: int):
    reads: list[int] = []

    async def echo(request: Request) -> JSONResponse:
        body = await request.body()
        reads.append(len(body))
        return JSONResponse({"bytes": len(body)})

    app = Starlette(routes=[Route("/echo", echo, methods=["POST"])])
    app.add_middleware(BodySizeLimitMiddleware, limit=limit)
    return TestClient(app), reads


def test_declared_oversize_is_refused_before_the_app_reads_it():
    client, reads = _echo_app(100)
    r = client.post("/echo", content=b"x" * 101)
    assert r.status_code == 413 and "exceeds 100 bytes" in r.json()["detail"]
    assert reads == [], "the handler must not run"


def test_exactly_the_limit_is_accepted():
    client, reads = _echo_app(100)
    assert client.post("/echo", content=b"x" * 100).json() == {"bytes": 100}


def test_chunked_body_is_cut_off_once_it_passes_the_limit():
    client, reads = _echo_app(100)

    def chunks():
        for _ in range(10):
            yield b"y" * 30

    r = client.post("/echo", content=chunks())
    assert r.status_code == 413
    assert reads == []

    small = client.post("/echo", content=(b"z" * 10 for _ in range(5)))
    assert small.status_code == 200 and small.json() == {"bytes": 50}


def test_invalid_content_length_is_a_400():
    client, _ = _echo_app(100)
    r = client.post("/echo", content=b"abc", headers={"content-length": "not-a-number"})
    assert r.status_code in (400, 413)


@pytest.mark.parametrize(
    "value,limits",
    [
        ({"a": {"b": {"c": 1}}}, {"max_depth": 2}),
        (["x" * 11], {"max_string": 10}),
        (list(range(11)), {"max_items": 10}),
        ({str(i): i for i in range(11)}, {"max_items": 10}),
    ],
)
def test_structural_limits(value, limits):
    with pytest.raises(PayloadTooLarge):
        check_json_limits(value, **limits)


def test_structural_limits_are_iterative_not_recursive():
    deep: list = []
    node = deep
    for _ in range(50_000):  # far beyond Python's recursion limit
        child: list = []
        node.append(child)
        node = child
    with pytest.raises(PayloadTooLarge):
        check_json_limits(deep, max_depth=64)
    check_json_limits({"ok": [1, 2, {"x": "y"}]}, max_depth=64)


# ── the real API ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def api(tmp_path, monkeypatch):
    from panoptis.api import server
    from panoptis.api import v3 as v3api
    from panoptis.api.middleware.rate_limiter import InMemoryBackend
    from panoptis.runtime.engine import Engine
    from panoptis.storage.store import Store

    server.reset_rate_limiter_for_tests()
    monkeypatch.setattr(server._rate_limiter, "backend", InMemoryBackend())
    store = Store(f"sqlite:///{(tmp_path / 'aw.db').as_posix()}")
    v3api.set_engine(Engine(store))
    with TestClient(server.app) as c:
        yield c
    v3api.set_engine(None)
    store.close()


def _obs(payload) -> dict:
    return {
        "sensor": {"sensor_type": "test", "sensor_version": "1", "instance_id": "t1"},
        "source_kind": "test.kind",
        "payload": payload,
    }


def test_api_rejects_an_oversized_body_with_413(api, monkeypatch):
    monkeypatch.setenv("AGENTWATCH_MAX_BODY_BYTES", "2048")
    r = api.post("/api/v3/observations", json={"observations": [_obs("x" * 4000)]})
    assert r.status_code == 413
    ok = api.post("/api/v3/observations", json={"observations": [_obs("small")]})
    assert ok.status_code == 200, ok.text


def test_api_bounds_batch_size_depth_and_strings(api, monkeypatch):
    monkeypatch.setenv("AGENTWATCH_MAX_BATCH_ITEMS", "3")
    assert (
        api.post(
            "/api/v3/observations", json={"observations": [_obs(i) for i in range(4)]}
        ).status_code
        == 422
    )
    assert (
        api.post(
            "/api/v3/observations", json={"observations": [_obs(i) for i in range(3)]}
        ).status_code
        == 200
    )
    deep: dict = {}
    node = deep
    for _ in range(80):
        node["k"] = {}
        node = node["k"]
    assert api.post("/api/v3/observations", json={"observations": [_obs(deep)]}).status_code == 422
    monkeypatch.setenv("AGENTWATCH_MAX_STRING_BYTES", "100")
    assert (
        api.post("/api/v3/observations", json={"observations": [_obs("s" * 101)]}).status_code
        == 422
    )


def test_api_malformed_and_pathologically_deep_json_never_500(api):
    assert (
        api.post(
            "/api/v3/observations",
            content=b"{not json",
            headers={"content-type": "application/json"},
        ).status_code
        == 422
    )
    bomb = "[" * 20000 + "]" * 20000
    r = api.post(
        "/api/v3/observations", content=bomb.encode(), headers={"content-type": "application/json"}
    )
    assert 400 <= r.status_code < 500, r.status_code


def test_v1_event_free_form_fields_are_bounded(api):
    deep: dict = {}
    node = deep
    for _ in range(80):
        node["k"] = {}
        node = node["k"]
    r = api.post(
        "/api/v1/events",
        json={"session_id": "lim-1", "agent_id": "a", "event_type": "tool.call", "metadata": deep},
    )
    assert r.status_code == 413
    assert json.loads(r.text)["detail"].startswith("nesting deeper")


def test_serve_caps_websocket_message_size(monkeypatch):
    import uvicorn
    from typer.testing import CliRunner

    from panoptis.cli.main import app

    seen: dict = {}
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: seen.update(k))
    monkeypatch.setenv("AGENTWATCH_WS_MAX_BYTES", "4096")
    result = CliRunner().invoke(app, ["server", "start"])
    assert result.exit_code == 0, result.output
    assert seen.get("ws_max_size") == 4096
